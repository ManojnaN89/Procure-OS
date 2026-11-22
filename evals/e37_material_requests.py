"""E37: the store check on the main demo company's production orders, against a shortfall worked out from the raw files."""

from collections import defaultdict
from datetime import date
from decimal import Decimal

import lib
from fastapi.testclient import TestClient
from lib import D, Outcome, rows
from sqlalchemy import text

ID = "E37"
TITLE = "Stock is set aside earliest need-by first and only the shortfall is bought"
REQUIREMENT = (
    "Plan P1-30 and step 2 of the flow: each imported production order gets a material request; the store check sets "
    "free stock aside earliest need-by first and never more than is on the shelf; only the shortfall becomes a purchase "
    "request, priced at the contract price else the last price paid else the standard price, sent to the approver the "
    "rule table names; the plan's MO-1 example (Rs 72,930 for four items short) holds; issue at production start takes "
    "exactly what was set aside; nothing is set aside or bought twice; only the store may do it; supply counts for a "
    "need only when due by its date (rule Q17): set aside plus timely coming never exceeds the need"
)
PLAN_EXAMPLE = {"RM-01": D(50), "RM-06": D(60), "RM-08": D(40), "PK-01": D(30)}  # casting, bearing, copper wire, carton
PLAN_VALUE = D("72930.00")


def on_hand_at_cutoff() -> dict[str, Decimal]:
    """Opening stock plus the movement history from its date, item by item."""
    stock: dict[str, Decimal] = defaultdict(Decimal)
    since: dict[str, str] = {}
    for r in rows("master", "stock_opening"):
        stock[r["item_code"]] += D(r["qty"])
        since[r["item_code"]] = r["as_of"]
    for m in rows("history", "stock_movement"):
        if m["date"] < since.get(m["item_code"], ""):
            continue
        sign = 1 if m["type"] in ("receipt", "transfer_in", "adjust") else -1
        stock[m["item_code"]] += sign * D(m["qty"])
    return dict(stock)


def contract_price(item: str, on: date) -> Decimal | None:
    contracts = {
        c["code"]: c
        for c in rows("master", "contract")
        if c["status"] == "active" and c["valid_from"] <= on.isoformat() <= (c["valid_to"] or "9999")
    }
    prices = [
        D(p["price"])
        for p in rows("master", "price_record")
        if p["item_code"] == item
        and p["contract_code"] in contracts
        and p["valid_from"] <= on.isoformat() <= (p["valid_to"] or "9999")
    ]
    return min(prices) if prices else None


def expected(
    on: date,
) -> tuple[dict[str, dict[str, tuple[Decimal, Decimal]]], dict[str, dict[str, tuple[Decimal, Decimal]]]]:
    """The oracle. Per order: item -> (set aside, short), and item -> (quantity, price) to buy. The materials come from the
    bill of materials times the order quantity; orders take stock earliest need-by first. No orders are loaded in this
    company, so there is no last price paid: an item without a contract price is estimated at its standard price."""
    stock = on_hand_at_cutoff()
    items = {r["code"]: r for r in rows("master", "item")}
    bom: dict[str, list[dict[str, str]]] = defaultdict(list)
    for r in rows("inputs", "bom"):
        bom[r["product_code"]].append(r)
    orders = sorted(rows("inputs", "production_order"), key=lambda o: o["need_by"])  # a stable sort keeps file order
    held: dict[str, dict[str, tuple[Decimal, Decimal]]] = {}
    buy: dict[str, dict[str, tuple[Decimal, Decimal]]] = {}
    for order in orders:
        held[order["order_no"]], buy[order["order_no"]] = {}, {}
        for part in bom[order["product_code"]]:
            item = part["item_code"]
            need = D(part["qty"]) * D(order["qty"])
            take = min(need, max(stock.get(item, D(0)), D(0)))
            stock[item] = stock.get(item, D(0)) - take
            held[order["order_no"]][item] = (take, need - take)
            if need - take > 0:
                qty = max(need - take, D(items[item]["moq"] or "0"))
                price = contract_price(item, on) or D(items[item]["standard_price"])
                buy[order["order_no"]][item] = (qty, price)
    return held, buy


def first_approver(value: Decimal, on: date) -> str:
    """The first role of the request rule whose band holds the value (the rule table, read straight from the file)."""
    for r in rows("master", "approval_rule"):
        if (
            r["document"] == "request"
            and D(r["min_value"]) <= value <= D(r["max_value"])
            and r["effective_from"] <= on.isoformat() <= (r["effective_to"] or "9999")
        ):
            return r["approver_chain"].split(">")[0].strip()
    return ""


def run(ctx) -> Outcome:
    from p2p.main import app
    from p2p.requests import duplicates

    out = Outcome(True)
    if not (lib.DATASET / "inputs" / "production_order.csv").is_file():
        out.notes.append(f"{lib.DATASET.name} has no production orders: nothing to check")
        return out
    world = lib.build_world("e37")
    db = world.db
    client = TestClient(app)
    store = world.headers("Store")
    held_want, buy_want = expected(lib.TODAY)

    # 1. the ERP's orders come in; each gets a material request
    made: list[str] = []
    for kind in ("production_order", "production_order_line"):
        resp = client.post(
            f"/api/demand/import/{kind}",
            files={"file": (f"{kind}.csv", (lib.DATASET / "inputs" / f"{kind}.csv").read_bytes(), "text/csv")},
            headers=store,
        )
        if resp.status_code != 200 or resp.json()["rejected"]:
            out.find("high", f"importing {kind} answered {resp.status_code}: {resp.text[:200]}")
            return out
        made += resp.json()["material_requests"]
    by_order = dict(db.execute(text("select order_no, no from material_request")).all())
    if sorted(by_order) != sorted(held_want) or len(set(made)) != len(held_want):
        out.find("high", f"material requests {by_order} for orders {sorted(held_want)} (import said {made})")
        return out

    # 2. only the store may run the check
    if client.post("/api/material-requests/check", json={}, headers=world.headers("Requester")).status_code != 403:
        out.find("high", "a requester can run the store check")

    # 3. the store check
    resp = client.post("/api/material-requests/check", json={}, headers=store)
    if resp.status_code != 200:
        out.find("high", f"the store check answered {resp.status_code}: {resp.text[:200]}")
        return out
    checked = {c["order_no"]: c for c in resp.json()["checked"]}
    got_held = {
        order: {r[0]: (r[1], max(r[2] - r[1], D(0))) for r in db.execute(
            text("select item_code, reserved_qty, need_qty from material_request_line where mr_no = :n"), {"n": no}
        )}
        for order, no in by_order.items()
    }  # fmt: skip
    wrong = [
        f"{o} {i}: set aside/short {got_held[o].get(i)} expected {w}"
        for o in held_want
        for i, w in held_want[o].items()
        if got_held[o].get(i) != w
    ]
    if wrong:
        out.find("high", f"{len(wrong)} line(s) set aside differently from the oracle; first: {wrong[0]}")

    # 4. never more set aside than is on the shelf
    over = db.execute(
        text(
            "select l.item_code from material_request_line l join stock_level s on s.item_code = l.item_code "
            "and s.location_code = l.location_code where l.status = 'open' "
            "group by l.item_code, s.on_hand having sum(l.reserved_qty) > s.on_hand"
        )
    ).all()
    if over:
        out.find("high", f"more set aside than is on hand for {[r[0] for r in over]}")

    # 4b. rule Q17 (WP-6, block 2): every line's cover is split by date, timely plus late is all that is coming, set aside
    # plus timely coming never exceeds the need, and nothing is at risk before any supplier promised a date
    listed = client.get("/api/material-requests", headers=store).json()["requests"]
    phased = [ln | {"order": r["order_no"]} for r in listed for ln in r["lines"] if ln["status"] == "open"]
    split_wrong = [
        f"{ln['order']} {ln['item_code']}: timely {ln['timely_qty']} + late {ln['late_qty']} != {ln['requested_qty']}"
        for ln in phased
        if D(ln["timely_qty"]) + D(ln["late_qty"]) != D(ln["requested_qty"])
    ]
    over_need = [
        f"{ln['order']} {ln['item_code']}" for ln in phased
        if D(ln["reserved_qty"]) + D(ln["timely_qty"]) > D(ln["need_qty"])
    ]  # fmt: skip
    at_risk = [f"{ln['order']} {ln['item_code']}" for ln in phased if D(ln["at_risk_qty"]) != 0]
    if split_wrong:
        out.find("high", f"cover split by date does not add up for {len(split_wrong)} line(s); first: {split_wrong[0]}")
    if over_need:
        out.find("high", f"set aside plus timely coming is above the need for {over_need[:4]}")
    if at_risk:
        out.find("high", f"lines at risk with no promise from any supplier: {at_risk[:4]}")

    # 5. one purchase request per order for what is short, at the estimate, to the approver of the rule table
    values: dict[str, Decimal] = {}
    for order, want in buy_want.items():
        pr = checked.get(order, {}).get("purchase_request")
        if not want:
            if pr:
                out.find("high", f"{order} has nothing short but bought {pr}")
            continue
        if not pr:
            out.find("high", f"{order} is short of {sorted(want)} but no purchase request was made")
            continue
        lines = {
            r[0]: (r[1], r[2])
            for r in db.execute(
                text("select item_code, qty, est_price from request_line where request_no = :n"), {"n": pr["no"]}
            )
        }
        if lines != want:
            out.find("high", f"{order}: {pr['no']} asks for {lines}, expected {want}")
        value = sum((q * p for q, p in want.values()), D(0)).quantize(D("0.01"))
        values[order] = value
        if D(pr["total_value"]) != value:
            out.find("high", f"{order}: {pr['no']} is valued at {pr['total_value']}, expected {value}")
        role = db.execute(
            text("select role from approval_step where request_no = :n order by step limit 1"), {"n": pr["no"]}
        ).scalar()
        rule_role = first_approver(value, lib.TODAY)
        if pr["outcome"] != "submitted" or role != rule_role:
            out.find(
                "high",
                f"{order}: {pr['no']} went to {role} ({pr['outcome'] or pr['submit_error']}), the rule table says {rule_role}",
            )

    # 6. the plan's example: MO-1 lacks 50 castings, 60 bearings, 40 kg copper wire and 30 cartons, Rs 72,930
    mo1 = buy_want.get("MO-1", {})
    named = {i: q for i, (q, _) in mo1.items() if i in PLAN_EXAMPLE}
    named_value = sum((q * p for i, (q, p) in mo1.items() if i in PLAN_EXAMPLE), D(0)).quantize(D("0.01"))
    if named != PLAN_EXAMPLE or named_value != PLAN_VALUE:
        out.find("high", f"the dataset no longer gives the plan's MO-1 example: {named}, Rs {named_value}")
    extra = sorted(set(mo1) - set(PLAN_EXAMPLE))
    if extra:
        out.notes.append(
            f"MO-1 in the dataset has {len(mo1)} items short, not 4: the bill of materials also has "
            f"{', '.join(extra)}, so its request is Rs {values.get('MO-1')} (the plan's four items are Rs {named_value})"
        )

    # 7. a second check sets nothing aside twice and buys nothing twice
    before = db.execute(text("select count(*), coalesce(sum(reserved_qty), 0) from material_request_line")).one()
    requests_before = db.execute(text("select count(*) from request")).scalar_one()
    again = client.post("/api/material-requests/check", json={}, headers=store).json()["checked"]
    after = db.execute(text("select count(*), coalesce(sum(reserved_qty), 0) from material_request_line")).one()
    if any(c["purchase_request"] for c in again) or tuple(after) != tuple(before):
        out.find("high", "a second store check set aside or bought again")
    if db.execute(text("select count(*) from request")).scalar_one() != requests_before:
        out.find("high", "a second store check made purchase requests")

    # 8. requests for different orders are not repeats of each other
    flagged = []
    for c in checked.values():
        if c["purchase_request"]:
            flagged += [d.other_request for d in duplicates.find_duplicates(db, c["purchase_request"]["no"])]
    if flagged:
        out.find("medium", f"shortfall requests of different orders flagged as repeats: {flagged}")

    # 9. issue at production start takes exactly what was set aside
    first = min(held_want, key=lambda o: next(r["need_by"] for r in rows("inputs", "production_order") if r["order_no"] == o))
    stock_before = dict(db.execute(text("select item_code, on_hand from stock_level")).all())
    resp = client.post(f"/api/material-requests/{by_order[first]}/issue", json={}, headers=store)
    if resp.status_code != 200:
        out.find("high", f"issuing {first} answered {resp.status_code}: {resp.text[:200]}")
    else:
        stock_after = dict(db.execute(text("select item_code, on_hand from stock_level")).all())
        moved = {i: stock_before[i] - stock_after[i] for i in stock_before if stock_before[i] != stock_after[i]}
        want_moved = {i: q for i, (q, _) in held_want[first].items() if q}
        if moved != want_moved:
            out.find("high", f"issuing {first} moved {moved}, expected {want_moved}")
        left = db.execute(
            text("select coalesce(sum(reserved_qty), 0) from material_request_line where mr_no = :n"),
            {"n": by_order[first]},
        ).scalar_one()
        if left:
            out.find("high", f"{left} still set aside for {first} after its issue")

    # 10. every step is in the audit log
    audited = dict(
        db.execute(
            text(
                "select action, count(*) from audit_event where action like 'material_request.%' group by action"
            )
        ).all()
    )
    if audited.get("material_request.create") != len(by_order) or audited.get("material_request.check", 0) < len(by_order):
        out.find("high", f"material requests audited as {audited}")
    if audited.get("material_request.issue") != 1:
        out.find("high", f"the issue is audited {audited.get('material_request.issue')} time(s)")

    out.metrics = {
        "production orders": len(by_order),
        "lines checked against the oracle": sum(len(v) for v in held_want.values()),
        "lines set aside differently": len(wrong),
        "items short per order": {o: len(v) for o, v in buy_want.items()},
        "purchase request value per order": {o: str(v) for o, v in values.items()},
        "MO-1, the plan's four items": f"Rs {named_value}",
        "lines with cover split by date (Q17)": len(phased),
    }
    return out
