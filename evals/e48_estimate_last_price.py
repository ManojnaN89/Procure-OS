"""E48: the estimate of a shortfall request is the contract price, else the last price paid, else the standard price
(plan step 3), with orders in the company so the last price paid is really used.

E37 checks the store check on a company with no orders, so its estimates only ever come from contracts and standard
prices. Here the main demo company first buys through the platform's own services (request, approval, sourcing, a quote,
the selection, the order): bearings three times at three prices, cartons once and then cancelled, castings by quotation although
they are on contract. Then the store checks the production orders. The oracle reads the contract price from the raw
price records, the last price paid straight from the order rows (not draft, not cancelled, the latest made), else the
item's standard price, and works out quantities from the stock history and `bom.csv` as E37 does.
"""

import tempfile
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

import env
import lib
from e37_material_requests import contract_price, expected, first_approver, on_hand_at_cutoff
from fastapi.testclient import TestClient
from lib import D, Outcome
from sqlalchemy import text

ID = "E48"
TITLE = "Shortfall requests are estimated at the contract price, else the last price paid, else the standard price"
REQUIREMENT = (
    "Plan step 3 (estimated price): the contract price if one is valid, else the last price paid (the latest order that "
    "is not a draft and not cancelled), else the item's standard price; the estimate sets the request's value and so its "
    "approver"
)
BUYS = [  # (item, quantity, price quoted); the cartons are cancelled afterwards
    ("RM-06", "20", "41.00"),  # bearings three times: the highest first, the lowest next ...
    ("RM-06", "20", "37.75"),
    ("RM-06", "20", "39.50"),  # ... and the latest, neither the first, the lowest nor the highest: it counts
    ("PK-01", "30", "47.25"),  # cartons: ordered, then cancelled, so no price paid at all
    ("RM-01", "5", "660.00"),  # castings bought by quotation above the contract: the contract still wins
]
CANCEL = {"PK-01"}
PLANTED_LAST = {"RM-06": D("39.50")}  # what the planted orders must leave as the last price paid


def run(ctx) -> Outcome:
    env.forget_tenants("e48")  # left by an earlier run that stopped half way (the demo logins are unique)
    try:
        return _run(ctx)
    finally:
        env.forget_tenants("e48")


def _run(ctx) -> Outcome:  # noqa: C901 - one scenario, step by step
    from sqlalchemy.orm import Session

    from p2p.agents import intake
    from p2p.approvals import workflow as approvals
    from p2p.formulas.orders import current_gst_rate
    from p2p.main import app
    from p2p.orders import changes, quotes
    from p2p.orders import service as orders
    from p2p.platform import demo, proposals, routing, sod, users
    from p2p.platform.tokens import create_access_token
    from p2p.sourcing import service as sourcing

    out = Outcome(True)
    lib.capture_mail()
    slug = env.slug("e48")
    folder = Path(tempfile.mkdtemp(prefix="e48_"))
    demo.reset_company(demo.company("apex"), folder / "dataset", credentials=None, slug=slug)
    db = Session(routing.engine_for(slug))
    domain = "apexpumps.example"
    uid = {
        k: int(users.user_id_by_email(slug, f"{k}@{domain}") or 0)
        for k in ("production", "buyer", "store", "purchasemanager")
    }
    buyer = sod.user(uid["buyer"])
    items = {r["code"]: r for r in lib.rows("master", "item")}
    problems: list[str] = []

    def settle_holds(po_no: str) -> None:
        """A price held above the estimate is accepted by the purchase manager, as the company's rule says."""
        db.rollback()
        pid = db.execute(
            text("select hold_proposal_id from po where no = :n and status = 'draft'"), {"n": po_no}
        ).scalar()
        if pid:
            proposals.approve(db, int(pid), uid["purchasemanager"])

    def buy(item: str, qty: str, price: str) -> str | None:
        made = intake.create_from_form(
            db, uid["production"], [(item, D(qty))], lib.TODAY + timedelta(days=7), None, "E48 planted order"
        )
        no = made.request_nos[0]
        sub = approvals.submit(db, slug, no, sod.user(uid["production"]), today=lib.TODAY)
        if sub.status != "approved":
            problems.append(f"{no} ({item}) is {sub.status}, expected approved under the automatic limit")
            return None
        sourcing.source_request(db, slug, no, as_of=lib.TODAY)
        method, rfq_no = db.execute(
            text("select method, rfq_no from sourcing_decision where request_no = :n and line_no = 1"), {"n": no}
        ).one()
        if method != "rfq":  # the buyer may always make a route stricter: buy it by quotation
            got = sourcing.change_route(
                db, slug, no, 1, "rfq", buyer, reason="E48: quote it this time", as_of=lib.TODAY
            )
            rfq_no = got["rfq_no"]
        for (pid,) in db.execute(
            text("select id from proposal where action = 'rfq.send' and status = 'pending'")
        ).all():
            proposals.approve(db, int(pid), uid["buyer"])
        db.rollback()
        supplier = db.execute(
            text("select supplier_code from rfq_supplier where rfq_no = :r order by supplier_code limit 1"),
            {"r": rfq_no},
        ).scalar_one()
        it = items[item]
        rate = current_gst_rate(
            D(it["gst_rate_before"]),
            D(it["gst_rate_after"]),
            date.fromisoformat(it["gst_rate_change_date"]) if it["gst_rate_change_date"] else None,
            lib.TODAY,
        )
        quotes.enter_quote(
            db,
            rfq_no,
            quotes.QuoteInput(
                supplier_code=supplier,
                valid_until=lib.TODAY + timedelta(days=30),
                lines=[quotes.QuoteLineInput(1, D(price), rate or D(18), delivery_days=3)],
            ),
            buyer,
        )
        quotes.award(db, rfq_no, {1: supplier}, buyer, reason="E48: one quote is enough here", as_of=lib.TODAY)
        result = orders.order_request(db, slug, no, as_of=lib.TODAY)
        if len(result.pos) != 1:
            problems.append(f"{no} ({item}) made orders {result.pos} ({result.skipped})")
            return None
        settle_holds(result.pos[0])
        return result.pos[0]

    placed: dict[str, list[str]] = {}
    for item, qty, price in BUYS:
        po = buy(item, qty, price)
        if po:
            placed.setdefault(item, []).append(po)
    for item in CANCEL:
        for po in placed.get(item, []):
            changes.cancel(db, slug, po, uid["buyer"], "E48: not needed after all")
    if problems:
        out.find("high", f"the planted orders could not be made: {problems[0]}")
        return out

    # ---- the oracle's last price paid: straight from the order rows ----
    db.rollback()
    paid: dict[str, tuple] = {}
    for item, price, status, line_status, created in db.execute(
        text(
            "select l.item_code, l.unit_price, p.status, l.status, p.created_at from po_line l "
            "join po p on p.no = l.po_no where l.item_code is not null"
        )
    ):
        if status in ("draft", "cancelled") or line_status == "cancelled":
            continue
        if item not in paid or created > paid[item][1]:
            paid[item] = (D(price), created)
    last = {i: p for i, (p, _) in paid.items()}
    for item, want in PLANTED_LAST.items():
        if last.get(item) != want:
            out.find("high", f"the planted orders left {last.get(item)} as the last price of {item}, expected {want}")
    if "PK-01" in last:
        out.find("high", f"the cancelled carton order still counts as paid: {last['PK-01']}")

    def estimate(item: str) -> tuple[Decimal, str]:
        c = contract_price(item, lib.TODAY)
        if c is not None:
            return c, "contract"
        if item in last and last[item] > 0:
            return last[item], "last price paid"
        return D(items[item]["standard_price"]), "standard price"

    _, buy_want = expected(lib.TODAY)
    shelf = dict(db.execute(text("select item_code, on_hand from stock_level")).all())
    replayed = on_hand_at_cutoff()
    if any(D(shelf.get(i, 0)) != replayed.get(i, D(0)) for o in buy_want.values() for i in o):
        out.find("high", "the demo company's stock differs from the replayed history: the quantities cannot be checked")

    # ---- the store check ----
    client = TestClient(app)
    store = {"Authorization": f"Bearer {create_access_token(uid['store'], slug, 'Store')}"}
    resp = client.post("/api/material-requests/check", json={}, headers=store)
    if resp.status_code != 200:
        out.find("high", f"the store check answered {resp.status_code}: {resp.text[:200]}")
        return out
    checked = {c["order_no"]: c for c in resp.json()["checked"]}
    bases: dict[str, str] = {}
    wrong: list[str] = []
    for order, want in buy_want.items():
        pr = checked.get(order, {}).get("purchase_request")
        if not want:
            continue
        if not pr:
            wrong.append(f"{order}: no purchase request for {sorted(want)}")
            continue
        got = {
            r[0]: (D(r[1]), D(r[2]))
            for r in db.execute(
                text("select item_code, qty, est_price from request_line where request_no = :n"), {"n": pr["no"]}
            )
        }
        value = D(0)
        for item, (qty, _) in want.items():
            price, basis = estimate(item)
            bases[item] = basis
            value += qty * price
            if got.get(item) != (qty, price):
                wrong.append(
                    f"{order} {item}: {got.get(item)} (qty, estimate), expected ({qty}, {price}) from the {basis}"
                )
        value = value.quantize(D("0.01"))
        if D(pr["total_value"]) != value:
            wrong.append(f"{order}: {pr['no']} is valued at {pr['total_value']}, expected {value}")
        role = db.execute(
            text("select role from approval_step where request_no = :n order by step limit 1"), {"n": pr["no"]}
        ).scalar()
        if (role or "auto") != first_approver(value, lib.TODAY):
            wrong.append(f"{order}: first approver {role}, the rule table says {first_approver(value, lib.TODAY)}")
    if wrong:
        out.find("high", f"{len(wrong)} disagreement(s) with the oracle; first: {wrong[0]}")
    for item, basis in (("RM-06", "last price paid"), ("PK-01", "standard price"), ("RM-01", "contract")):
        if bases.get(item) != basis:
            out.find("medium", f"the scenario did not exercise {item} on the {basis} (it was {bases.get(item)})")

    out.metrics = {
        "orders planted (one cancelled)": sum(len(v) for v in placed.values()),
        "last price paid per item (from the order rows)": {i: str(p) for i, p in last.items()},
        "estimate basis per short item": bases,
        "disagreements": len(wrong),
        "purchase request value per order": {
            o: c["purchase_request"]["total_value"] for o, c in checked.items() if c["purchase_request"]
        },
    }
    db.close()
    return out
