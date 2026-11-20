"""E50: contract price versions (D48) on the main demo company, against an oracle read from the raw price list.

The dataset's rate contract CNT-0001 has version 1 in force (RM-01 650.00, RM-02 480.00 ... to 31 Mar 2027) and a draft
version 2 from 1 Jan 2027 (RM-01 672.00, RM-02 496.00). The oracle reads `price_record.csv` itself (not through the
evals' reader, which hides drafts) and works out, for any day, which price is in force: before activation only version
1; after it, a version 2 price from its first day and the version 1 price it replaces up to the day before. The platform
is asked through its own services (contract prices for sourcing, the estimate of a request line, real requests sourced
and ordered on chosen days) and its screens (the contract endpoints, as the demo logins).

It proves: a draft is never bought at or estimated at before activation; the person who sent the version, the person
who priced it and someone without the permission are refused, a different approver activates it; version 1 then ends
the day before version 2 starts; an order made before activation keeps version 1 and orders on 31 Dec / 1 Jan get
version 1 / version 2; a contract is never activated by who stored it; a price of the same supplier and item sharing one
day with another is refused and the next day is fine; every step is audited with the right person; another company sees
none of it. Two rules are broken on purpose and the eval must notice each.
"""

import csv
import tempfile
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import env
import lib
from lib import D, Outcome
from sqlalchemy import text

ID = "E50"
TITLE = "Contract price versions: a draft is never used, a second person activates it, the old one ends the day before"
REQUIREMENT = (
    "D48: a contract price is a version; a draft is used for nothing until someone other than who stored the contract, "
    "entered a price of the version or sent it activates it; the version it replaces ends the day before it starts; "
    "prices of one supplier for an item never apply on the same day; an order keeps the version it was bought at"
)
CONTRACT = "CNT-0001"
ITEMS = ("RM-01", "RM-02", "RM-03")
EDITOR_PRICE = ("RM-03", D("228.00"))  # a price the purchase manager adds to the dataset's draft version 2
DAYS = (lib.TODAY, date(2026, 12, 31), date(2027, 1, 1), date(2027, 1, 4), date(2027, 3, 31))
DOMAIN = "apexpumps.example"


# ---- the oracle: the raw price list, read here ------------------------------------------------------


def price_list() -> list[dict[str, Any]]:
    with (lib.DATASET / "master" / "price_record.csv").open(encoding="utf-8-sig", newline="") as f:
        out = []
        for r in csv.DictReader(f):
            if r["contract_code"] != CONTRACT:
                continue
            out.append(
                {
                    "item": r["item_code"],
                    "price": D(r["price"]),
                    "version": int(r.get("version") or 1),
                    "status": r.get("status") or "active",
                    "from": date.fromisoformat(r["valid_from"]),
                    "to": date.fromisoformat(r["valid_to"]) if r["valid_to"] else None,
                }
            )
    return out


def in_force(prices: list[dict[str, Any]], item: str, day: date, activated: bool) -> tuple[Decimal, int] | None:
    """The price (and version) of the item on the day. Before activation only version 1 counts; after it, a newer version
    replaces the older one from its first day, which then ends the day before."""
    usable = [p for p in prices if p["item"] == item and (p["version"] == 1 or activated)]
    found = []
    for p in usable:
        end = p["to"]
        newer = [q for q in usable if q["version"] > p["version"] and q["from"] > p["from"]]
        if newer:
            end = min(q["from"] for q in newer) - timedelta(days=1)
        if p["from"] <= day and (end is None or day <= end):
            found.append((p["price"], p["version"]))
    assert len(found) <= 1, (item, day, found)  # the dataset never has two prices on one day
    return found[0] if found else None


# ---- the eval --------------------------------------------------------------------------------------


def run(ctx: lib.Ctx) -> Outcome:
    env.forget_tenants("e50")  # left by an earlier run that stopped half way (the demo logins are unique)
    try:
        return _run(ctx)
    finally:
        env.forget_tenants("e50")


def _run(ctx: lib.Ctx) -> Outcome:  # noqa: C901 - one scenario, step by step
    from fastapi.testclient import TestClient
    from p2p.agents import intake
    from p2p.approvals import workflow as approvals
    from p2p.main import app
    from p2p.orders import service as orders
    from p2p.platform import demo, routing, sod, tenants, users
    from p2p.platform.tokens import create_access_token
    from p2p.requests import estimates
    from p2p.sourcing import service as sourcing
    from sqlalchemy.orm import Session

    out = Outcome(True)
    lib.capture_mail()
    slug = env.slug("e50")
    folder = Path(tempfile.mkdtemp(prefix="e50_"))
    demo.reset_company(demo.company("apex"), folder / "dataset", credentials=None, slug=slug)
    db = Session(routing.engine_for(slug))
    client = TestClient(app)
    who = ("production", "buyer", "planthead", "purchasemanager", "director")
    uid = {k: int(users.user_id_by_email(slug, f"{k}@{DOMAIN}") or 0) for k in who}
    role = {"production": "Requester", "buyer": "Buyer"}
    h = {
        k: {"Authorization": f"Bearer {create_access_token(v, slug, role.get(k, 'Approver'))}"} for k, v in uid.items()
    }
    standard = {r["code"]: D(r["standard_price"]) for r in lib.rows("master", "item")}
    base = f"/api/contracts/{CONTRACT}"

    prices = price_list()
    drafts = [p for p in prices if p["status"] == "draft"]
    out.metrics["dataset draft version"] = ", ".join(f"{p['item']} {p['price']} from {p['from']}" for p in drafts)
    if not drafts or {p["version"] for p in drafts} != {2}:
        out.find("high", f"the dataset has no draft version 2 on {CONTRACT}: nothing to test")
        return out

    stored = db.execute(
        text("select item_code, version, status, price from price_record where contract_code = :c"), {"c": CONTRACT}
    ).all()
    want_rows = sorted((p["item"], p["version"], p["status"], p["price"]) for p in prices)
    if sorted((r[0], r[1], r[2], D(r[3])) for r in stored) != want_rows:
        out.find("high", f"{CONTRACT} loaded as {sorted(stored)}, the price list says {want_rows}")

    def probe(activated: bool, extra: list[dict[str, Any]]) -> list[str]:
        """Contract price and estimate of each item on each day against the oracle."""
        wrong = []
        for item in ITEMS:
            for day in DAYS:
                want = in_force(prices + extra, item, day, activated)
                opts = [o for o in sourcing.contract_options(db, item, "NOS", day) if o.contract_code == CONTRACT]
                got = [D(o.price) for o in opts]
                if got != ([want[0]] if want else []):
                    wrong.append(f"contract price of {item} on {day}: {got}, oracle {want}")
                est = estimates.for_item(db, item, "NOS", D(1), day, standard.get(item))
                if want and (est is None or D(est.price) != want[0]):
                    wrong.append(f"estimate of {item} on {day}: {est}, oracle {want[0]}")
        db.rollback()
        return wrong

    def buy(item: str, on: date) -> tuple[Decimal, int] | str:
        """A request for the item sourced and ordered on the day: the price and contract version its order line got."""
        made = intake.create_from_form(
            db, uid["production"], [(item, D(2))], on + timedelta(days=14), None, "E50", today=lib.TODAY
        )
        no = made.request_nos[0]
        sub = approvals.submit(db, slug, no, sod.user(uid["production"]), today=lib.TODAY)
        if sub.status != "approved":
            return f"{no} is {sub.status}"
        sourcing.source_request(db, slug, no, as_of=on, dispatch=False)
        result = orders.order_request(db, slug, no, as_of=on)
        if len(result.pos) != 1:
            return f"{no} made orders {result.pos} ({result.skipped})"
        return line_of(result.pos[0])

    def line_of(po_no: str) -> tuple[Decimal, int]:
        db.rollback()
        price, version = db.execute(
            text(
                "select l.unit_price, p.version from po_line l left join price_record p on p.id = l.price_record_id "
                "where l.po_no = :n"
            ),
            {"n": po_no},
        ).one()
        return D(price).quantize(D("0.01")), int(version or 0)

    def bought(label: str, item: str, on: date, want: tuple[Decimal, int] | None) -> str | None:
        got = buy(item, on)
        out.metrics[f"order {label}: {item} on {on}"] = got if isinstance(got, str) else f"{got[0]} (version {got[1]})"
        if got != want:
            out.find("high", f"order {label} of {item} on {on}: {got}, oracle {want}")
        return None

    # 1. before activation: the draft is bought at and estimated at nowhere
    wrong = probe(False, [])
    out.metrics["before activation: prices and estimates off the oracle"] = len(wrong)
    for w in wrong[:5]:
        out.find("high", f"before activation, {w}")
    early_po = buy("RM-01", lib.TODAY)
    bought("before activation", "RM-02", date(2027, 1, 4), in_force(prices, "RM-02", date(2027, 1, 4), False))

    # 2. the purchase manager adds a price to the draft version; the plant head sends it; who may activate it
    item, price = EDITOR_PRICE
    added = client.post(
        f"{base}/prices",
        json={"item_code": item, "price": str(price), "uom": "NOS", "valid_from": "2027-01-01"},
        headers=h["purchasemanager"],
    )
    extra = [{"item": item, "price": price, "version": 2, "status": "draft", "from": date(2027, 1, 1), "to": None}]
    extra[0]["to"] = next(p["to"] for p in prices if p["item"] == item)  # the contract's end
    if added.status_code != 200:
        out.find("high", f"adding a price to the draft version answered {added.status_code}: {added.text[:200]}")
    wrong = probe(False, extra)
    if wrong:
        out.find("high", f"a price added to the draft is used before activation: {wrong[0]}")
    sent = client.post(f"{base}/versions/submit", headers=h["planthead"])
    refused = {
        "the plant head who sent it": client.post(f"{base}/versions/activate", json={}, headers=h["planthead"]),
        "the purchase manager who priced it": client.post(
            f"{base}/versions/activate", json={}, headers=h["purchasemanager"]
        ),
        "the buyer (no permission)": client.post(f"{base}/versions/activate", json={}, headers=h["buyer"]),
    }
    db.rollback()
    still = db.execute(
        text("select count(*) from price_record where contract_code = :c and status = 'draft'"), {"c": CONTRACT}
    ).scalar_one()
    out.metrics["activation refused (sender, pricer, no permission)"] = {k: v.status_code for k, v in refused.items()}
    if sent.status_code != 200:
        out.find("high", f"sending the version answered {sent.status_code}: {sent.text[:200]}")
    for name, r in refused.items():
        if r.status_code != 403:
            out.find("high", f"activation by {name} answered {r.status_code}, expected 403: {r.text[:160]}")
    if still != len(drafts) + 1:
        out.find("high", f"after the refused activations {still} draft prices are left, expected {len(drafts) + 1}")

    v1_ids = dict(
        db.execute(
            text("select item_code, id from price_record where contract_code = :c and version = 1"), {"c": CONTRACT}
        ).all()
    )
    done = client.post(f"{base}/versions/activate", json={"note": "E50"}, headers=h["director"])
    replaced = sorted({p["item"] for p in drafts + extra})
    if done.status_code != 200 or done.json().get("version") != 2:
        out.find("high", f"the director's activation answered {done.status_code}: {done.text[:200]}")
    elif sorted(done.json().get("ended", [])) != sorted(v1_ids[i] for i in replaced):
        out.find("high", f"activation ended {done.json().get('ended')}, oracle: the version 1 prices of {replaced}")

    # 3. after activation: version 1 ends the day before version 2, every probe against the oracle
    db.rollback()
    rows = db.execute(
        text(
            "select item_code, version, status, valid_to, activated_by from price_record where contract_code = :c "
            "order by item_code, version"
        ),
        {"c": CONTRACT},
    ).all()
    start2 = min(p["from"] for p in drafts)
    ends = {r[0]: r[3] for r in rows if r[1] == 1}
    bad_end = {i: str(e) for i, e in ends.items() if (e != start2 - timedelta(days=1)) == (i in replaced)}
    out.metrics["version 1 ends (replaced items)"] = sorted({str(ends[i]) for i in replaced})
    if bad_end:
        out.find("high", f"version 1 end dates wrong (replaced items must end {start2 - timedelta(days=1)}): {bad_end}")
    v2 = [r for r in rows if r[1] == 2]
    if any(r[2] != "active" or r[4] != str(uid["director"]) for r in v2) or len(v2) != len(replaced):
        out.find("high", f"version 2 after activation: {v2}")
    wrong = probe(True, extra)
    out.metrics["after activation: prices and estimates off the oracle"] = len(wrong)
    for w in wrong[:5]:
        out.find("high", f"after activation, {w}")
    if isinstance(early_po, str):
        out.find("high", f"the order before activation was not made: {early_po}")
    db.rollback()
    first_po = db.execute(text("select min(po_no) from po_line where item_code = 'RM-01'")).scalar_one()
    kept = line_of(str(first_po))
    out.metrics["order made before activation, after it"] = f"{kept[0]} (version {kept[1]})"
    if kept != in_force(prices, "RM-01", lib.TODAY, False):
        out.find("high", f"the order made before activation changed to {kept}")
    for on in (date(2026, 12, 31), date(2027, 1, 1)):
        bought("after activation", "RM-01", on, in_force(prices, "RM-01", on, True))
    bought("after activation", "RM-02", date(2027, 1, 4), in_force(prices, "RM-02", date(2027, 1, 4), True))

    # 4. a second contract of the same supplier: who stored it cannot activate it; overlap at the edge
    end2 = max(p["to"] for p in drafts)
    made = client.post(
        "/api/contracts",
        json={"supplier_code": "SUP-001", "valid_from": end2.isoformat(), "valid_to": "2027-12-31"},
        headers=h["planthead"],
    )
    k2 = str(made.json().get("id")) if made.status_code == 200 else None
    edges: dict[str, int] = {}
    if k2 is None:
        out.find("high", f"storing a second contract answered {made.status_code}: {made.text[:200]}")
    else:
        for label, start in (("same day", end2), ("next day", end2 + timedelta(days=1))):
            r = client.post(
                f"/api/contracts/{k2}/prices",
                json={"item_code": "RM-01", "price": "680.00", "uom": "NOS", "valid_from": start.isoformat()},
                headers=h["buyer"],
            )
            edges[f"{label} ({start})"] = r.status_code
        out.metrics["a price of RM-01 on a second contract from"] = edges
        if list(edges.values()) != [409, 200]:
            out.find("high", f"overlap at the edge (version 2 ends {end2}): {edges}, expected 409 then 200")
        by_maker = client.post(f"/api/contracts/{k2}/activate", headers=h["planthead"])
        by_other = client.post(f"/api/contracts/{k2}/activate", headers=h["director"])
        out.metrics["second contract activated by who stored it / by the director"] = (
            f"{by_maker.status_code} / {by_other.status_code}"
        )
        if by_maker.status_code != 403 or by_other.status_code != 200:
            out.find(
                "high", f"activating {k2}: by its maker {by_maker.status_code}, by the director {by_other.status_code}"
            )

    # 5. every step audited, by the right person
    db.rollback()
    trail = db.execute(
        text(
            "select action, actor_id, count(*) from audit_event where action in ('contract.price_add', "
            "'contract.price_version_activate', 'contract.activate', 'proposal.create', 'proposal.approved') "
            "group by action, actor_id"
        )
    ).all()
    seen = {(a, str(u)) for a, u, _ in trail}
    want_audit = {
        ("contract.price_add", str(uid["purchasemanager"])),
        ("contract.price_add", str(uid["buyer"])),
        ("proposal.create", str(uid["planthead"])),
        ("proposal.approved", str(uid["director"])),
        ("contract.price_version_activate", str(uid["director"])),
        ("contract.activate", str(uid["director"])),
    }
    out.metrics["audit rows (action, person)"] = len(want_audit & seen)
    if want_audit - seen:
        out.find("high", f"no audit row for {sorted(want_audit - seen)}")
    if ("contract.activate", str(uid["planthead"])) in seen:
        out.find("high", "a refused activation left an audit row as if it had happened")

    # 6. another company sees none of it
    other = env.slug("e50")
    assert tenants.create_tenant(other, load_reference=False).ok
    stranger = users.create_user(other, f"e50.{other}@eval.example", lib.PASSWORD, "Someone Else", "Approver")
    sh = {"Authorization": f"Bearer {create_access_token(stranger, other, 'Approver')}"}
    seen_other = {
        "contract": client.get(base, headers=sh).status_code,
        "activate": client.post(f"{base}/versions/activate", json={}, headers=sh).status_code,
        "list": [c.get("code") for c in client.get("/api/contracts", headers=sh).json()],
    }
    out.metrics["another company: contract / activate / list"] = seen_other
    if seen_other["contract"] != 404 or seen_other["activate"] != 404 or seen_other["list"]:
        out.find("high", f"another company sees {CONTRACT}: {seen_other}")

    if k2:
        out.metrics["mutation checks (rule broken on purpose: caught?)"] = _mutations(db, client, h, k2, out)
    return out


def _mutations(db: Any, client: Any, h: dict[str, dict[str, str]], k2: str, out: Outcome) -> dict[str, str]:
    from p2p.masters import documents_and_contracts as masters

    results: dict[str, str] = {}
    base = f"/api/contracts/{k2}"

    # the person who priced a version may activate it: the eval must see the activation go through
    saved = masters._refuse_maker
    masters._refuse_maker = lambda *a, **k: None
    try:
        client.post(
            f"{base}/prices",
            json={"item_code": "RM-02", "price": "500.00", "uom": "NOS", "valid_from": "2027-04-01"},
            headers=h["purchasemanager"],
        )
        client.post(f"{base}/versions/submit", headers=h["planthead"])
        r = client.post(f"{base}/versions/activate", json={}, headers=h["purchasemanager"])
        results["whoever priced a version may activate it"] = (
            "caught" if r.status_code == 200 else f"NOT caught ({r.status_code})"
        )
    finally:
        masters._refuse_maker = saved

    # two prices of one supplier for an item may share a day: the eval must see the same-day price accepted
    saved_overlap = masters.periods_overlap
    masters.periods_overlap = lambda *a: False
    try:
        r = client.post(
            f"{base}/prices",
            json={"item_code": "RM-01", "price": "681.00", "uom": "NOS", "valid_from": "2027-03-31"},
            headers=h["buyer"],
        )
        results["prices may share a day"] = "caught" if r.status_code == 200 else f"NOT caught ({r.status_code})"
    finally:
        masters.periods_overlap = saved_overlap
    db.rollback()
    for name, verdict in results.items():
        if verdict != "caught":
            out.find("high", f"mutation '{name}' was not caught: the eval cannot tell this rule is broken")
    return results
