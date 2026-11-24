"""E55: tolerance versions reproduce past decisions (task 3.2, D-17, X1 rules 6-7), written without the implementation.

Independence: written from the block 0b spec (section 3), the plan and the dataset only. The oracle reads
`master/tolerance.csv` and `master/tolerance_version.csv` itself: a version is the whole set of (key, category) values
from its start date to the day before the next version starts (never used when it is replaced before it starts); the
version of an invoice is the one in force on its `received_on`. Each invoice is judged here, line by line, the way the
spec says the code applies D-17: quantity invoiced so far within the accepted quantity plus `qty_pct`, and the price
within both `price_pct` **and** `price_abs` rupees on the line (a category value overrides ANY within the same version).
Owner decision OP-2: the "both % and Rs" rule is task 3.1, so every line is judged by both tests and a line where they
disagree is blocked.

The whole history of the main demo company is replayed with `as_of = received_on`, each verdict compared with the
dataset's answer key and each version with the oracle; the invoices whose verdict flips between version 1 and 2 must flip
exactly at 1 Jul 2026; two copies of one bearings order are invoiced 3% over the accepted quantity on 30 Jun and 1 Jul;
an administrator proposes version 3 from tomorrow (the second administrator approves it, the maker cannot, a start in the
past or before the latest start is refused, a second change waits behind the first), and re-matching past invoices keeps
their result and version; an invoice received tomorrow is judged under version 3; the audit row carries `tolerance vN`;
Brightwave's version 2 (1 Jan 2027, IT hardware rupee tolerance 1000) is not used on 31 Dec; each company sees only its
own versions. Two broken rules (the latest version instead of the dated one; the version not stored) must be told apart.
"""

from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Any
from zoneinfo import ZoneInfo

import env
import lib
from block0b import Checker, Company, dicts, js, money, on_day, pick
from lib import D, Outcome
from sqlalchemy import text

ID = "E55"
TITLE = "Tolerance versions: each match records the version in force on the day received, and past decisions reproduce"
REQUIREMENT = (
    "Task 3.2, D-17, X1 rules 6-7: tolerances are versioned as a set from a date; an invoice is judged under the version "
    "in force on its received_on and the match stores that version (match row, invoice, audit 'tolerance vN'); a re-match "
    "after a later version gives the same result and version; a change waits for a second administrator and starts today "
    "or later, never before the latest start; one company never sees another's versions"
)

IST = ZoneInfo("Asia/Kolkata")
FLIP = date(2026, 7, 1)  # Apex version 2 (spec 3.6)
TOMORROW = lib.TODAY + timedelta(days=1)


# ---- the oracle: the raw tolerance files ------------------------------------------------------------------------------


@dataclass
class Version:
    number: int
    start: date
    end: date | None

    def covers(self, day: date) -> bool:
        return self.start <= day and (self.end is None or day <= self.end)


class Tolerances:
    """Tolerance versions as the dataset writes them."""

    def __init__(self, tol: list[dict[str, str]], heads: list[dict[str, str]]) -> None:
        starts: dict[int, date] = {
            int(h["version"]): date.fromisoformat(h["effective_from"]) for h in heads
        }
        self.values: dict[int, dict[tuple[str, str], Decimal]] = defaultdict(dict)
        for r in tol:
            v = int(r.get("version") or 1)
            starts.setdefault(
                v, date.fromisoformat(r.get("effective_from") or "2000-01-01")
            )
            self.values[v][(r["key"], r.get("category_code") or "ANY")] = D(r["value"])
        numbers = sorted(starts)
        self.versions = []
        for i, v in enumerate(numbers):
            nxt = starts[numbers[i + 1]] if i + 1 < len(numbers) else None
            self.versions.append(
                Version(v, starts[v], nxt - timedelta(days=1) if nxt else None)
            )

    def on(self, day: date) -> int | None:
        found = [
            v.number
            for v in self.versions
            if (v.end is None or v.end >= v.start) and v.covers(day)
        ]
        return max(found) if found else None

    def latest_started(self, day: date) -> int:
        """Mutation: the newest version that has started, whatever the invoice's date."""
        return max(v.number for v in self.versions if v.start <= day)

    def value(self, version: int, key: str, category: str) -> Decimal:
        got = self.values[version]
        return got.get((key, category), got.get((key, "ANY"), D(0)))


def judge(
    lines: list[dict[str, Any]], tol: Tolerances, version: int
) -> tuple[str, set[str], bool]:
    """(matched | within_tolerance | blocked, reasons, edge): both price tests must hold (D-17)."""
    reasons: set[str] = set()
    within = False
    for ln in lines:
        cat = ln["category"]
        limit = ln["accepted"] * (1 + tol.value(version, "qty_pct", cat) / 100)
        if ln["invoiced_before"] + ln["qty"] > limit:
            reasons.add("qty_over_received")
        over = ln["net_price"] - ln["po_price"]
        if over > 0:
            pct_ok = over * 100 <= tol.value(version, "price_pct", cat) * ln["po_price"]
            abs_ok = over * ln["qty"] <= tol.value(version, "price_abs", cat)
            if pct_ok and abs_ok:
                within = True
            else:
                reasons.add("price_variance")
    if reasons:
        return "blocked", reasons, False
    return ("within_tolerance" if within else "matched"), reasons, False


def history_lines(ds: Any) -> dict[str, list[dict[str, Any]]]:
    """Each history invoice's lines with what had been accepted and invoiced on its order line when it arrived."""
    items = {r["code"]: r for r in ds("master", "item")}
    po_lines = {(r["po_no"], r["line_no"]): r for r in ds("history", "po_line")}
    receipts = {r["no"]: r for r in ds("history", "goods_receipt")}
    events: list[tuple[datetime, int, str]] = [
        (datetime.fromisoformat(r["gate_entry_at"]), 0, r["no"])
        for r in receipts.values()
    ]
    invoices = {r["no"]: r for r in ds("history", "invoice")}
    gr_lines: dict[str, list[dict[str, str]]] = defaultdict(list)
    for r in ds("history", "gr_line"):
        gr_lines[r["gr_no"]].append(r)
    inv_lines: dict[str, list[dict[str, str]]] = defaultdict(list)
    for r in ds("history", "invoice_line"):
        inv_lines[r["invoice_no"]].append(r)
    # the dataset's convention: an invoice is judged once the receipt that names it (supplier_invoice_ref) is posted,
    # even when the paper arrived a few hours before the goods (a replacement sent again the same day)
    receipt_for = {
        (g["po_no"], g["supplier_invoice_ref"]): datetime.fromisoformat(
            g["gate_entry_at"]
        )
        for g in receipts.values()
        if g.get("supplier_invoice_ref")
    }
    for r in invoices.values():
        first_po = inv_lines[r["no"]][0]["po_no"] if inv_lines[r["no"]] else ""
        arrived = datetime.fromisoformat(r["received_at"])
        arrived = max(
            arrived, receipt_for.get((first_po, r["supplier_invoice_no"]), arrived)
        )
        events.append((arrived, 1, r["no"]))
    accepted: dict[tuple[str, str], Decimal] = defaultdict(Decimal)
    invoiced: dict[tuple[str, str], Decimal] = defaultdict(Decimal)
    out: dict[str, list[dict[str, Any]]] = {}
    for _, kind, key in sorted(events):
        if kind == 0:
            for g in gr_lines[key]:
                accepted[(g["po_no"], g["po_line_no"])] += D(g["qty_accepted"])
            continue
        inv = invoices[key]
        if inv["type"] != "invoice" or inv["payment_block"] == "duplicate":
            continue
        lines = []
        for ln in inv_lines[key]:
            pol = po_lines.get((ln["po_no"], ln["po_line_no"]))
            if pol is None or D(ln["qty"]) == 0:
                continue
            k = (ln["po_no"], ln["po_line_no"])
            lines.append(
                {
                    "category": items.get(pol["item_code"], {}).get(
                        "category_code", "ANY"
                    ),
                    "qty": D(ln["qty"]),
                    "net_price": D(ln["taxable"]) / D(ln["qty"]),
                    "po_price": D(pol["unit_price"]),
                    "accepted": accepted[k],
                    "invoiced_before": invoiced[k],
                }
            )
            invoiced[k] += D(ln["qty"])
        out[key] = lines
    return out


# ---- planting an order of our own (a copy of a history order, fully received) -----------------------------------------


def clone_order(
    db: Any, ds: Any, src: dict[str, str], new_no: str, qty: Decimal, received: date
) -> dict[str, Any]:
    """A copy of one history order line under a new number, `qty` ordered and accepted on `received`."""
    from replay import insert

    po = next(r for r in ds("history", "po") if r["no"] == src["po_no"])
    rate = D(src["gst_rate"] or "0")
    taxable = money(qty * D(src["unit_price"]))
    intra = (po["supplier_gstin"] or "")[:2] == (po["buyer_gstin"] or "")[:2]
    half = money(taxable * rate / 200) if intra else D(0)
    igst = D(0) if intra else money(taxable * rate / 100)
    insert(db, "po", {**po, "no": new_no, "taxable": taxable, "cgst": half, "sgst": half, "igst": igst,
                      "total": taxable + 2 * half + igst, "contract_code": ""})  # fmt: skip
    insert(db, "po_line", {**src, "po_no": new_no, "line_no": "1", "qty": qty, "cgst": half, "sgst": half, "igst": igst},
           {"received_qty": qty, "invoiced_qty": 0, "source": "award", "request_no": new_no, "request_line_no": 1,
            "taxable": taxable})  # fmt: skip
    gr = (
        next(
            (r for r in ds("history", "goods_receipt") if r["po_no"] == src["po_no"]),
            None,
        )
        or {}
    )
    at = datetime.combine(received - timedelta(days=5), time(10, 0), IST)
    insert(db, "goods_receipt", {**gr, "no": f"GRN-{new_no}", "po_no": new_no, "gate_entry_at": at.isoformat(),
                                 "accepted_on": (received - timedelta(days=5)).isoformat(), "inspection_status": "not_required",
                                 "supplier_invoice_ref": ""}, {"posted_by": "import"})  # fmt: skip
    insert(db, "gr_line", {"gr_no": f"GRN-{new_no}", "po_no": new_no, "po_line_no": "1", "qty_received": qty,
                           "qty_accepted": qty, "qty_rejected": 0, "reject_reason": ""})  # fmt: skip
    db.commit()
    return {"po": po, "line": src, "no": new_no}


def enter(
    db: Any,
    ap: int,
    order: dict[str, Any],
    qty: Decimal,
    price: Decimal,
    day: date,
    number: str,
    irn: bool,
) -> Any:
    """An invoice for the planted order, received on `day`, through the platform's own intake and match."""
    from p2p.invoices import service as invoices

    po, src = order["po"], order["line"]
    rate = D(src["gst_rate"] or "0")
    taxable = money(qty * price)
    intra = (po["supplier_gstin"] or "")[:2] == (po["buyer_gstin"] or "")[:2]
    half = money(taxable * rate / 200) if intra else D(0)
    igst = D(0) if intra else money(taxable * rate / 100)
    exact = taxable + 2 * half + igst
    total = exact.quantize(D("1"), rounding=ROUND_HALF_UP)
    data = invoices.InvoiceIn(
        supplier_code=po["supplier_code"], supplier_invoice_no=number, invoice_date=day - timedelta(days=2), type="invoice",
        supplier_gstin=po["supplier_gstin"] or None, buyer_gstin=po["buyer_gstin"] or None,
        place_of_supply=(po["buyer_gstin"] or "")[:2] or None, irn=("E" * 64) if irn else None, ack_no=None, ack_date=None,
        eway_bill_no=None, reverse_charge=False, channel="email", received_at=datetime.combine(day, time(11, 0), IST),
        taxable=taxable, cgst=half, sgst=half, igst=igst, cess=D(0), round_off=total - exact, total=total,
        ref_invoice_no=None,
        lines=[invoices.InvLine(po_no=order["no"], po_line_no=1, qty=qty, unit_price=price, discount=D(0), taxable=taxable,
                                gst_rate=rate, cgst=half, sgst=half, igst=igst, description=src["description"],
                                hsn_sac="", uom=src["uom"])],
        charges=[],
    )  # fmt: skip
    return invoices.enter_invoice(db, data, ap, as_of=day)


# ---- the eval -------------------------------------------------------------------------------------------------------


def run(ctx: lib.Ctx) -> Outcome:
    env.forget_tenants("e55")
    try:
        return _run(ctx)
    finally:
        env.forget_tenants("e55")


def _run(ctx: lib.Ctx) -> Outcome:  # noqa: C901 - one scenario, step by step
    from fastapi.testclient import TestClient
    from replay import replay

    from p2p.main import app
    from p2p.platform import users
    from p2p.platform.tokens import create_access_token

    out = Outcome(True)
    check = Checker(out)
    lib.capture_mail()
    client = TestClient(app)
    ds = lambda folder, name: lib.rows(folder, name)  # noqa: E731 - the main demo dataset, as the world loads it

    if (
        "version" not in (ds("master", "tolerance") or [{}])[0]
        or not (lib.DATASET / "master" / "tolerance_version.csv").exists()
    ):
        out.find(
            "high",
            "the dataset has no tolerance versions (tolerance.csv version column, tolerance_version.csv)",
        )
        return out
    tol = Tolerances(ds("master", "tolerance"), ds("master", "tolerance_version"))
    out.metrics["dataset versions"] = [
        f"v{v.number} {v.start}..{v.end or ''}" for v in tol.versions
    ]
    check(
        tol.on(FLIP - timedelta(days=1)) == 1 and tol.on(FLIP) == 2,
        f"Apex versions are not v1 to 30 Jun, v2 from 1 Jul: {out.metrics['dataset versions']}",
    )

    # ---- 1. the whole history replayed, received_on as the day -------------------------------------------------------
    world = lib.build_world("e55", with_stock=False)
    db = world.db
    got = replay(world)
    lines_of = history_lines(ds)
    truth = got.truth
    by_no = got.by_no
    db.rollback()
    match_rows: dict[str, list[tuple[str, Any]]] = defaultdict(list)
    for inv, result, version in db.execute(
        text(
            "select invoice_no, result, tolerance_version from match_result order by id"
        )
    ):
        match_rows[str(inv)].append((result, version))
    inv_version = dict(
        db.execute(text("select no, tolerance_version from invoice")).all()
    )

    scored = version_ok = result_ok = oracle_vs_key = 0
    wrong_version: list[str] = []
    wrong_result: list[str] = []
    disagree: list[str] = []
    flips: list[tuple[str, date, str, str]] = []
    mutant_latest = mutant_none = 0
    for key, done in got.entered.items():
        inv = by_no[key]
        if (
            inv["type"] != "invoice"
            or key not in lines_of
            or getattr(done, "duplicate_of", None)
        ):
            continue
        if (truth.get(key) or {}).get("planted") == "gst_mismatch":
            continue  # the GST answer key is E19/E161's; this eval judges price and quantity only
        day = date.fromisoformat(inv["received_on"])
        want_v = tol.on(day)
        pno = got.platform_no[key]
        versions_stored = {v for _, v in match_rows.get(pno, [])} | {
            inv_version.get(pno)
        }
        if versions_stored == {want_v}:
            version_ok += 1
        else:
            wrong_version.append(
                f"{key} received {day}: stored {sorted(map(str, versions_stored))}, oracle v{want_v}"
            )
        if want_v != tol.latest_started(lib.TODAY) and versions_stored == {want_v}:
            mutant_latest += 1
        if None not in versions_stored:
            mutant_none += 1
        r1, _, e1 = judge(lines_of[key], tol, 1)
        r2, _, e2 = judge(lines_of[key], tol, 2)
        if r1 != r2:
            flips.append((key, day, r1, r2))
        result, reasons, _edge = judge(lines_of[key], tol, want_v or 1)
        scored += 1
        key_row = truth.get(key)
        platform = getattr(done, "result", None)
        if (
            key_row is not None
            and key_row.get("tolerance_version")
            and int(key_row["tolerance_version"]) != want_v
        ):
            disagree.append(
                f"{key}: answer key version {key_row['tolerance_version']}, oracle v{want_v}"
            )
        expected = key_row["result"] if key_row is not None else result
        if key_row is not None and key_row["result"] != result:
            oracle_vs_key += 1
        if platform == expected:
            result_ok += 1
        else:
            wrong_result.append(
                f"{key} received {day} (v{want_v}): platform {platform}, answer key {expected}, oracle {result} {sorted(reasons)}"
            )
    out.metrics["history invoices: version right / scored"] = (
        f"{version_ok} / {version_ok + len(wrong_version)}"
    )
    out.metrics["history invoices: result right / scored"] = f"{result_ok} / {scored}"
    out.metrics["oracle and answer key disagree"] = oracle_vs_key
    for w in wrong_version[:6]:
        out.find("high", f"history: {w}")
    for w in wrong_result[:6]:
        out.find("high", f"history: {w}")
    for w in disagree[:3]:
        out.find(
            "medium", f"the dataset's answer key and the tolerance files disagree: {w}"
        )
    if oracle_vs_key:
        out.notes.append(
            f"{oracle_vs_key} scored invoices where this oracle's simple replay differs from the answer key; the platform was held to the key"
        )

    # the planted edge invoices flip exactly at 1 Jul 2026
    clean_flips = [
        f
        for f in flips
        if str((truth.get(f[0]) or {}).get("planted", "")).startswith("tolerance_edge")
    ]
    out.metrics["invoices whose verdict differs between v1 and v2"] = [
        f"{k} {d} v1 {a} / v2 {b}" for k, d, a, b in clean_flips
    ]
    check(
        any(d < FLIP for _, d, _, _ in clean_flips)
        and any(d >= FLIP for _, d, _, _ in clean_flips),
        f"the dataset should plant invoices that flip between v1 and v2 on both sides of {FLIP}: {clean_flips}",
        "medium",
    )
    for key, day, r1, r2 in clean_flips:
        want = r1 if day < FLIP else r2
        platform = getattr(got.entered[key], "result", None)
        check(
            platform == want,
            f"planted {key} received {day}: platform {platform}, the version in force says {want}",
        )

    # ---- 2. the same lines received on 30 Jun and on 1 Jul -------------------------------------------------------------
    src = next(
        (
            ln
            for ln in ds("history", "po_line")
            if ln["item_code"] == "RM-06"
            and next(p for p in ds("history", "po") if p["no"] == ln["po_no"])[
                "supplier_code"
            ]
            == "SUP-002"
        ),
        None,
    )
    ap = world.logins["AP"]
    edge_invoices: dict[str, tuple[str, int]] = {}
    if check(
        src is not None,
        "no history order of SUP-002 bearings (RM-06) to copy",
        "medium",
    ):
        qty, over = (
            D(100),
            D(103),
        )  # 3% over the accepted quantity at the order's own price
        price = D(src["unit_price"])
        for tag, day in (("A", FLIP - timedelta(days=1)), ("B", FLIP)):
            order = clone_order(db, ds, src, f"PO-E55-{tag}", qty, day)
            done = enter(db, ap, order, over, price, day, f"E55/{tag}", irn=False)
            db.commit()
            version = tol.on(day) or 1
            line = {
                "category": "RAW",
                "qty": over,
                "net_price": price,
                "po_price": price,
                "accepted": qty,
                "invoiced_before": D(0),
            }
            want = judge([line], tol, version)[0]
            stored = (
                db.execute(
                    text(
                        "select distinct tolerance_version from match_result where invoice_no = :n"
                    ),
                    {"n": done.no},
                )
                .scalars()
                .all()
            )
            out.metrics[f"bearings 3% over received {day}"] = (
                f"{done.result} v{stored} (oracle {want} v{version})"
            )
            check(
                done.result == want,
                f"bearings 3% over received {day}: platform {done.result}, oracle {want} under v{version}",
            )
            check(
                list(stored) == [version],
                f"bearings 3% over received {day}: match stores version {stored}, oracle {version}",
            )
            edge_invoices[tag] = (done.no, version)
            trail = [a["rule_version"] for a in _audit(db, "invoice.match", done.no)]
            check(
                bool(trail) and trail[-1].startswith(f"tolerance v{version}"),
                f"{done.no}: invoice.match audit rule_version {trail}, expected 'tolerance v{version}'",
            )

    # ---- 3. version 3 from tomorrow, by two administrators -------------------------------------------------------------
    admin2 = users.create_user(
        world.slug,
        f"admin2{world.slug}@eval.example",
        lib.PASSWORD,
        "Second Admin",
        "Admin",
    )
    h = {"admin": world.headers("Admin"), "ap": world.headers("AP"),
         "admin2": {"Authorization": f"Bearer {create_access_token(admin2, world.slug, 'Admin')}"}}  # fmt: skip

    def versions(headers: dict[str, str]) -> list[dict[str, Any]]:
        r = client.get("/api/setup/tolerances", headers=headers)
        found = js(r) if r.status_code == 200 else {}
        if not pick(found, "versions"):
            found = pick(
                js(client.get("/api/setup/policies", headers=headers)),
                "tolerances",
                default={},
            )
        return list(pick(found, "versions", default=[]) or [])

    def put(key: str, body: dict[str, Any], who: str = "admin") -> Any:
        return client.put(
            f"/api/setup/tolerances/{key}",
            json={"category": "ANY", "reason": "E55", **body},
            headers=h[who],
        )

    before = versions(h["admin"])
    shown = sorted((int(v["version"]), str(v["effective_from"])) for v in before)
    check(shown == sorted((v.number, str(v.start)) for v in tol.versions),
          f"the screen's versions {shown} are not the dataset's {[(v.number, str(v.start)) for v in tol.versions]}")  # fmt: skip
    for v in before:
        n = int(v["version"])
        vals = {
            (x["key"], x.get("category") or "ANY"): D(x["value"])
            for x in v.get("values", [])
        }
        check(
            vals == tol.values.get(n, {}),
            f"version {n} values on screen {vals} differ from tolerance.csv {tol.values.get(n)}",
            "medium",
        )

    refusals = {
        "start in the past": put(
            "price_pct",
            {
                "value": "1.00",
                "effective_from": (lib.TODAY - timedelta(days=1)).isoformat(),
            },
        ),
        "unknown key": put(
            "no_such_key", {"value": "1.00", "effective_from": TOMORROW.isoformat()}
        ),
    }
    out.metrics["refused (past start, unknown key)"] = {
        k: r.status_code for k, r in refusals.items()
    }
    check(
        refusals["start in the past"].status_code == 422,
        f"a start date in the past answered {refusals['start in the past'].status_code}",
    )
    check(
        refusals["unknown key"].status_code == 404,
        f"an unknown key answered {refusals['unknown key'].status_code}",
        "medium",
    )

    r = put("price_pct", {"value": "1.00", "effective_from": TOMORROW.isoformat()})
    body = js(r)
    pid = body.get("proposal_id")
    check(
        r.status_code == 202 and body.get("status") == "waiting" and pid,
        f"proposing version 3 answered {r.status_code} {r.text[:200]}",
    )
    v2_price = tol.value(2, "price_pct", "ANY")

    # the spec names old and new but not their shape: a bare value or the D-22 proposal's {"value": ...}
    def shown(x: Any) -> Any:
        return x.get("value") if isinstance(x, dict) else x

    check(D(shown(body.get("old")) or -1) == v2_price and D(shown(body.get("new")) or -1) == D("1.00"),
          f"the proposal shows old {body.get('old')} new {body.get('new')}; expected old {v2_price} (v2) and new 1.00", "medium")  # fmt: skip
    check(
        len(versions(h["admin"])) == len(before),
        "a version was added before the second administrator approved",
    )
    second = put(
        "qty_pct", {"value": "1.00", "effective_from": TOMORROW.isoformat()}, "admin2"
    )
    out.metrics["a second tolerance change while one waits"] = second.status_code
    # owner decision 10 (22 Nov 2026): the tolerance set is one setting, so a change to any other key while one waits
    # is refused too; a second change to the same key is checked below.
    check(
        second.status_code == 409,
        f"a qty_pct change while a price_pct change waits answered {second.status_code}, expected 409 (decision 10)",
    )
    if second.status_code == 202:
        late = js(second).get("proposal_id")
        if late:
            client.post(
                f"/api/admin/proposals/{late}/reject",
                json={"reason": "E55: not this case"},
                headers=h["admin"],
            )
    same = put(
        "price_pct", {"value": "1.50", "effective_from": TOMORROW.isoformat()}, "admin2"
    )
    out.metrics["a second change to the same tolerance while one waits"] = (
        same.status_code
    )
    check(
        same.status_code == 409,
        f"a second price_pct change while one waits answered {same.status_code}, expected 409",
    )
    if pid:
        own = client.post(
            f"/api/admin/proposals/{pid}/approve", json={}, headers=h["admin"]
        )
        check(
            own.status_code == 403,
            f"the maker approved their own tolerance change ({own.status_code})",
        )
        check(
            len(versions(h["admin"])) == len(before),
            "the maker's refused approval added a version",
        )
        done = client.post(
            f"/api/admin/proposals/{pid}/approve", json={}, headers=h["admin2"]
        )
        check(
            done.status_code == 200,
            f"the second administrator's approval answered {done.status_code} {done.text[:200]}",
        )
    after = {int(v["version"]): v for v in versions(h["admin"])}
    v3 = after.get(3)
    if check(v3 is not None, f"no version 3 after approval: {sorted(after)}"):
        want3 = {**tol.values[2], ("price_pct", "ANY"): D("1.00")}
        got3 = {
            (x["key"], x.get("category") or "ANY"): D(x["value"])
            for x in v3.get("values", [])
        }
        check(
            str(v3["effective_from"]) == TOMORROW.isoformat(),
            f"version 3 starts {v3['effective_from']}, expected {TOMORROW}",
        )
        check(got3 == want3, f"version 3 is not version 2 with price_pct 1.00: {got3}")
        check(
            str(after[2].get("effective_to")) == lib.TODAY.isoformat(),
            f"version 2 ends {after[2].get('effective_to')}, expected {lib.TODAY}",
        )
        check(
            int(pick(after[2], "version")) == 2 and bool(after[2].get("in_force")),
            "version 2 should still be in force today",
            "medium",
        )
        early = put(
            "qty_pct", {"value": "1.00", "effective_from": lib.TODAY.isoformat()}
        )
        check(
            early.status_code == 422,
            f"a start today, before the latest version's start ({TOMORROW}), answered {early.status_code}",
        )
        tol.versions[-1].end = lib.TODAY  # the oracle learns version 3 as approved
        tol.versions.append(Version(3, TOMORROW, None))
        tol.values[3] = want3

    # ---- 4. re-matching past invoices keeps the result and the version -----------------------------------------------
    rematched: dict[str, str] = {}
    keep = [
        (got.platform_no[k], tol.on(d) or 1, getattr(got.entered[k], "result", None))
        for k, d, _, _ in clean_flips
    ]
    keep += [(no, v, None) for no, v in edge_invoices.values()]
    for no, version, _ in keep:
        db.rollback()
        before_rows = (
            db.execute(
                text(
                    "select result from match_result where invoice_no = :n order by id"
                ),
                {"n": no},
            )
            .scalars()
            .all()
        )
        status = db.execute(
            text("select status from invoice where no = :n"), {"n": no}
        ).scalar()
        r = client.post(f"/api/invoices/{no}/rematch", headers=h["ap"])
        db.rollback()
        rows_ = db.execute(
            text(
                "select result, tolerance_version from match_result where invoice_no = :n order by id"
            ),
            {"n": no},
        ).all()
        latest = rows_[-1] if rows_ else (None, None)
        detail = js(client.get(f"/api/invoices/{no}", headers=h["ap"]))
        shown_v = [
            pick(d["tolerance"], "version")
            for d in dicts(detail)
            if isinstance(d.get("tolerance"), dict)
        ]
        rematched[no] = (
            f"{r.status_code}: {latest[0]} v{latest[1]} (before {before_rows[-1] if before_rows else '-'}); shown {shown_v}"
        )
        # spec 3.3.4: a re-match is "a block re-checked later"; an invoice no longer blocked keeps its match (409)
        rematchable = status == "blocked"
        check(r.status_code == (200 if rematchable else 409),
              f"re-match of {no} ({status}) answered {r.status_code} {r.text[:150]}", "medium")  # fmt: skip
        check(
            latest[1] == version,
            f"re-match of {no} after version 3 used version {latest[1]}, the original is v{version}",
        )
        check(
            not before_rows or latest[0] == before_rows[-1],
            f"re-match of {no} changed the result {before_rows[-1] if before_rows else ''} -> {latest[0]}",
        )
        check(bool(shown_v) and all(int(x) == version for x in shown_v if x is not None),
              f"GET /api/invoices/{no} shows tolerance versions {shown_v}, expected {version}", "medium")  # fmt: skip
    out.metrics["re-matched after version 3"] = rematched

    # an invoice received tomorrow is judged under version 3
    if src is not None and v3 is not None:
        with on_day(TOMORROW):
            order = clone_order(db, ds, src, "PO-E55-C", D(10), TOMORROW)
            done = enter(
                db, ap, order, D(10), D(src["unit_price"]), TOMORROW, "E55/C", irn=False
            )
            db.commit()
        stored = (
            db.execute(
                text(
                    "select distinct tolerance_version from match_result where invoice_no = :n"
                ),
                {"n": done.no},
            )
            .scalars()
            .all()
        )
        out.metrics["exact invoice received tomorrow"] = (
            f"{done.result} v{list(stored)}"
        )
        check(
            list(stored) == [3],
            f"an invoice received {TOMORROW} stored version {stored}, expected 3",
        )
        trail = [a["rule_version"] for a in _audit(db, "invoice.match", done.no)]
        check(
            bool(trail) and trail[-1] == "tolerance v3; match rules 2",
            f"{done.no}: audit rule_version {trail}, expected 'tolerance v3; match rules 2'",
        )

    # ---- 5. Brightwave: version 2 waits for 1 Jan 2027; each company sees only its own versions ----------------------
    bw = Company("brightwave", "e55", client)
    bw_tol = Tolerances(
        bw.rows("master", "tolerance"), bw.rows("master", "tolerance_version")
    )
    bw_seen = sorted(
        (int(v["version"]), str(v["effective_from"]))
        for v in versions(bw.headers("U06"))
    )
    want_bw = sorted((v.number, str(v.start)) for v in bw_tol.versions)
    out.metrics["Brightwave versions on screen"] = bw_seen
    check(
        bw_seen == want_bw,
        f"Brightwave's screen shows {bw_seen}, its files say {want_bw} (another company's versions must never show)",
    )
    check(bw_tol.on(date(2026, 12, 31)) == 1 and bw_tol.on(date(2027, 1, 1)) == 2,
          f"Brightwave's files do not put version 2 on 1 Jan 2027: {[(v.number, v.start) for v in bw_tol.versions]}", "medium")  # fmt: skip
    check(
        bw_tol.value(2, "price_abs", "ITH") == D("1000.00"),
        "Brightwave version 2 has no ITH price_abs 1000.00 row",
        "medium",
    )
    apex_seen = {
        (int(v["version"]), str(v["effective_from"])) for v in versions(h["admin"])
    }
    check(
        not (apex_seen & {(2, "2027-01-01")}) and (2, "2026-07-01") not in bw_seen,
        "a company sees another company's tolerance versions",
    )
    bw_src = next(
        (ln for ln in bw.rows("history", "po_line") if ln["item_code"] == "LP-01"), None
    )
    if check(
        bw_src is not None,
        "Brightwave has no LP-01 order in its history to copy",
        "medium",
    ):
        bw_ds = bw.rows
        for tag, day in (("D", date(2026, 12, 31)), ("E", date(2027, 1, 1))):
            with on_day(day):
                order = clone_order(bw.db, bw_ds, bw_src, f"PO-E55-{tag}", D(1), day)
                done = enter(
                    bw.db,
                    bw.user_id("U04"),
                    order,
                    D(1),
                    D(bw_src["unit_price"]),
                    day,
                    f"E55/{tag}",
                    irn=True,
                )
                bw.db.commit()
            stored = bw.q(
                "select distinct tolerance_version from match_result where invoice_no = :n",
                n=done.no,
            )
            out.metrics[f"Brightwave laptop invoice received {day}"] = (
                f"{done.result} v{[s[0] for s in stored]}"
            )
            check(
                [s[0] for s in stored] == [bw_tol.on(day)],
                f"Brightwave invoice received {day} stored version {stored}, expected {bw_tol.on(day)}",
            )
        other = client.get(
            f"/api/invoices/{edge_invoices.get('A', ('x', 0))[0]}",
            headers=bw.headers("U04"),
        )
        check(
            other.status_code in (403, 404),
            f"Brightwave's AP read an Apex invoice ({other.status_code})",
        )

    # ---- 6. two broken rules the eval must tell from the right one ----------------------------------------------------
    caught = {
        "the latest version instead of the dated one": mutant_latest > 0
        and not wrong_version,
        "the version not stored": mutant_none > 0
        and mutant_none == version_ok + len(wrong_version),
    }
    out.metrics["mutations caught"] = caught
    out.metrics["invoices that tell the latest-version reader apart"] = mutant_latest
    for name, ok in caught.items():
        check(ok, f"mutation '{name}' would not be caught by these cases", "medium")
    out.metrics["checks"] = f"{check.done - len(check.failed)} of {check.done} passed"
    return out


def _audit(db: Any, action: str, entity: str) -> list[dict[str, Any]]:
    db.rollback()
    found = db.execute(
        text(
            "select rule_version from audit_event where action = :a and entity_id = :e order by id"
        ),
        {"a": action, "e": entity},
    ).all()
    return [{"rule_version": r[0]} for r in found]
