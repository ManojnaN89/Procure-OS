"""E38: combined requisition lines and the budget per location, on the main demo company, against an oracle from the raw
files."""

import csv
import io
from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal

import lib
from e37_material_requests import contract_price, on_hand_at_cutoff
from fastapi.testclient import TestClient
from lib import D, Outcome, rows
from sqlalchemy import text

ID = "E38"
TITLE = "Shortfalls of several orders combine into one line, and the budget is checked per location"
REQUIREMENT = (
    "Plan P1-31 and step 3 of the flow: shortfall lines for the same item and delivery location needed within the "
    "combining window (setting, default 7 days) become one requisition line that keeps each order's own quantity; lines "
    "further apart stay separate; cancelling one order takes its part off the combined line (and the stock it held "
    "covers, earliest need-by first, the parts of the others, which are cut by as much); the budget is checked per "
    "department and per location (decision D36: warn at 90% used, over above 100%), naming the location, the department "
    "and the months of the financial year it counted; every combination and reduction is audited"
)
EXTRA_ORDERS = [("MO-1B", 3, "20"), ("MO-1C", 8, "10")]  # (order, days after MO-1, pumps of MO-1's product)


def setting(key: str, default: str) -> str:
    return next((r["value"] for r in rows("master", "setting") if r["key"] == key), default)


def orders_with_extras() -> list[dict[str, str]]:
    """The dataset's production orders, plus two more of MO-1's product: three and eight days after it."""
    orders = rows("inputs", "production_order")
    first = min(orders, key=lambda o: o["need_by"])
    extra = [
        {
            **first,
            "order_no": no,
            "qty": qty,
            "need_by": (date.fromisoformat(first["need_by"]) + timedelta(days=d)).isoformat(),
        }
        for no, d, qty in EXTRA_ORDERS
    ]
    return orders + extra


TAKEN: dict[tuple[str, str], Decimal] = {}  # (order, item) -> what the oracle's store check set aside for it


def freed_cuts(lines: list[dict], orders: list[dict[str, str]], victim: str) -> dict[tuple[str, str], Decimal]:
    """After the victim is cancelled: the stock it held goes, earliest need-by first, to the other orders' shortfalls
    (all on requests still open), and each such request part is cut by what it takes. (order, item) -> cut."""
    due = {o["order_no"]: o["need_by"] for o in orders}
    cuts: dict[tuple[str, str], Decimal] = {}
    for item in {ln["item"] for ln in lines}:
        left = TAKEN.get((victim, item), D(0))
        shorts = sorted(
            ((due[o], o, q) for ln in lines if ln["item"] == item for o, q in ln["for"].items() if o != victim),
            key=lambda x: (x[0], x[1]),
        )
        for _, order, short in shorts:
            cut = min(short, left)
            left -= cut
            if cut:
                cuts[(order, item)] = cut
    return cuts


def oracle(orders: list[dict[str, str]], window: int) -> list[dict]:
    """Per combined line: the item, the orders it buys for with their own shortfall, and its quantity and price. Orders
    take stock earliest need-by first; a shortfall joins the first line of its item whose need-by dates stay within the
    window, else starts a line; a line buys the sum of its shortfalls, at least the minimum order."""
    stock = on_hand_at_cutoff()
    items = {r["code"]: r for r in rows("master", "item")}
    bom: dict[str, list[dict[str, str]]] = defaultdict(list)
    for r in rows("inputs", "bom"):
        bom[r["product_code"]].append(r)
    lines: list[dict] = []
    for order in sorted(orders, key=lambda o: o["need_by"]):
        need_by = date.fromisoformat(order["need_by"])
        for part in bom[order["product_code"]]:
            item = part["item_code"]
            need = D(part["qty"]) * D(order["qty"])
            take = min(need, max(stock.get(item, D(0)), D(0)))
            stock[item] = stock.get(item, D(0)) - take
            TAKEN[(order["order_no"], item)] = take
            short = need - take
            if short <= 0:
                continue
            line = next(
                (
                    ln
                    for ln in lines
                    if ln["item"] == item
                    and (max(ln["dates"] + [need_by]) - min(ln["dates"] + [need_by])).days <= window
                ),
                None,
            )
            if line is None:
                line = {"item": item, "dates": [], "for": {}, "moq": D(items[item]["moq"] or "0")}
                line["price"] = contract_price(item, lib.TODAY) or D(items[item]["standard_price"])
                lines.append(line)
            line["dates"].append(need_by)
            line["for"][order["order_no"]] = short
    for ln in lines:
        ln["qty"] = max(sum(ln["for"].values(), D(0)), ln["moq"])
    return lines


def budget_months(gl: str, holder: dict[str, str]) -> Decimal:
    """What the budget file gives the holder for one GL account from April to the month of the check."""
    start = date(lib.TODAY.year if lib.TODAY.month >= 4 else lib.TODAY.year - 1, 4, 1).strftime("%Y-%m")
    return sum(
        (
            D(r["amount"])
            for r in rows("master", "budget")
            if r["gl_code"] == gl
            and start <= r["period"] <= lib.TODAY.strftime("%Y-%m")
            and all(r.get(k, "") == v for k, v in holder.items())
        ),
        D(0),
    )


def upload(client: TestClient, kind: str, data: list[dict[str, str]], headers: dict[str, str]) -> dict:
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=list(data[0]))
    w.writeheader()
    w.writerows(data)
    resp = client.post(
        f"/api/demand/import/{kind}",
        files={"file": (f"{kind}.csv", buf.getvalue().encode(), "text/csv")},
        headers=headers,
    )
    return {"status": resp.status_code, **(resp.json() if resp.status_code == 200 else {"text": resp.text[:200]})}


def run(ctx) -> Outcome:  # noqa: C901 - one scenario, step by step
    from p2p.main import app

    out = Outcome(True)
    if not (lib.DATASET / "inputs" / "production_order.csv").is_file():
        out.notes.append(f"{lib.DATASET.name} has no production orders: nothing to check")
        return out
    world = lib.build_world("e38")
    db = world.db
    client = TestClient(app)
    store = world.headers("Store")
    window = int(setting("combine_window_days", "7"))
    orders = orders_with_extras()
    want = oracle(orders, window)

    # 1. the orders and their lines (the bill of materials times the quantity) come in from the ERP
    bom: dict[str, list[dict[str, str]]] = defaultdict(list)
    for r in rows("inputs", "bom"):
        bom[r["product_code"]].append(r)
    order_rows = [{k: o[k] for k in ("order_no", "product_code", "qty", "need_by")} for o in orders]
    line_rows = [
        {"order_no": o["order_no"], "item_code": p["item_code"], "qty": str(D(p["qty"]) * D(o["qty"]))}
        for o in orders
        for p in bom[o["product_code"]]
    ]
    for kind, data in (("production_order", order_rows), ("production_order_line", line_rows)):
        got = upload(client, kind, data, store)
        if got["status"] != 200 or got.get("rejected"):
            out.find("high", f"importing {kind}: {got}")
            return out
    by_order = dict(db.execute(text("select order_no, no from material_request")).all())

    # 2. the store check
    resp = client.post("/api/material-requests/check", json={}, headers=store)
    if resp.status_code != 200:
        out.find("high", f"the store check answered {resp.status_code}: {resp.text[:200]}")
        return out

    # 3. the requisition lines against the oracle: one line per combination, each order with its own quantity
    got_lines = []
    for r in db.execute(
        text(
            "select l.request_no, l.line_no, l.item_code, l.qty, l.est_price, l.gl_code from request_line l "
            "where l.status <> 'cancelled' order by l.request_no, l.line_no"
        )
    ).mappings():
        linked = {
            o: q
            for o, q in db.execute(
                text(
                    "select h.order_no, sum(b.qty) from material_request_buy b "
                    "join material_request_line m on m.id = b.mr_line_id join material_request h on h.no = m.mr_no "
                    "where b.request_no = :n and b.request_line_no = :l group by h.order_no"
                ),
                {"n": r["request_no"], "l": r["line_no"]},
            )
        }
        got_lines.append({**r, "for": linked})

    def shape(lines: list[dict]) -> list[tuple]:
        return sorted(
            (ln["item"] if "item" in ln else ln["item_code"], ln["qty"], tuple(sorted(ln["for"].items())))
            for ln in lines
        )

    if shape(got_lines) != shape(want):
        missing = sorted(set(shape(want)) - set(shape(got_lines)))[:2]
        extra = sorted(set(shape(got_lines)) - set(shape(want)))[:2]
        out.find("high", f"requisition lines differ from the oracle: expected {missing}, got {extra}")
    combined = [ln for ln in want if len(ln["for"]) > 1]
    if not combined:
        out.find("high", "the scenario has nothing to combine: the oracle found no item short for two orders")
    if any({"MO-1", "MO-1C"} <= set(ln["for"]) for ln in got_lines):
        out.find("high", "MO-1C, eight days after MO-1, was bought on MO-1's line: the window is not kept")

    # 4. the budget of each request: per department and per location, against the budget file
    gl_of = {r["code"]: r["default_gl"] for r in rows("master", "category")}
    item_gl = {r["code"]: r["default_gl"] or gl_of[r["category_code"]] for r in rows("master", "item")}
    location = lib.location()
    warn_pct, block_pct = D(setting("budget_warn_pct", "90")), D(setting("budget_block_pct", "100"))
    committed: dict[str, Decimal] = defaultdict(Decimal)  # GL -> value of the requests submitted before
    requests = db.execute(
        text(
            "select no, budget_check, status from request where status in ('submitted', 'approved') order by submitted_at, no"
        )
    ).all()
    checked_location = 0
    for no, checks, _status in requests:
        asked: dict[str, Decimal] = defaultdict(Decimal)
        for ln in got_lines:
            if ln["request_no"] == no:
                asked[item_gl[ln["item_code"]]] += ln["qty"] * ln["est_price"]
        for gl, value in asked.items():
            entry = next((c for c in checks or [] if c["level"] == "location" and c["gl_code"] == gl), None)
            budget = budget_months(gl, {"location_code": location, "cost_centre_code": ""})
            if not budget:
                continue
            if entry is None:
                out.find("high", f"{no}: no location budget check for GL {gl}")
                continue
            checked_location += 1
            used = (committed[gl] + value) * 100 / budget
            status = "over" if used > block_pct else "warn" if used >= warn_pct else "ok"
            got = (D(entry["budget"]), D(entry["committed"]), D(entry["requested"]), entry["status"], entry["location"])
            cents = D("0.01")
            if got != (budget, committed[gl].quantize(cents), value.quantize(cents), status, location):
                out.find(
                    "high", f"{no} GL {gl}: location check {got}, expected {(budget, committed[gl], value, status)}"
                )
            if entry["used_pct"] != str(used.quantize(D("0.01"))):
                out.find("medium", f"{no} GL {gl}: {entry['used_pct']}% used, expected {used:.2f}")
            if "FY 2026-27" not in entry["period"] or entry["holder"] != f"location {location}":
                out.find("medium", f"{no}: the check does not say whose budget and which months: {entry}")
            dept = next((c for c in checks or [] if c["level"] == "department" and c["gl_code"] == gl), None)
            if dept is None or dept["location"] != location:
                out.find(
                    "high", f"{no} GL {gl}: no check of the department's budget at {location} beside the location's"
                )
        for gl, value in asked.items():
            committed[gl] += value
    if not checked_location:
        out.find("high", "no request was checked against a location budget: the dataset has none")

    # 5. cancelling one order takes its part off the combined line, and the stock it held is set aside at once for the
    # other orders, so their parts of the lines are cut by as much (never stocked and bought twice)
    extras = {no for no, _, _ in EXTRA_ORDERS}
    victim = next((o for ln in combined for o in ln["for"] if o in extras), None)
    reduced = 0
    if victim:
        cuts = freed_cuts(want, orders, victim)
        resp = client.post(f"/api/material-requests/{by_order[victim]}/cancel", json={"reason": "eval"}, headers=store)
        if resp.status_code != 200:
            out.find("high", f"cancelling {victim} answered {resp.status_code}: {resp.text[:200]}")
        for ln in combined:
            if victim not in ln["for"]:
                continue
            rest = {o: q - cuts.get((o, ln["item"]), D(0)) for o, q in ln["for"].items() if o != victim}
            left = sum(rest.values(), D(0))
            expect = min(max(left, ln["moq"]), ln["qty"]) if left else D(0)
            match = next((g for g in got_lines if g["item_code"] == ln["item"] and g["for"] == ln["for"]), None)
            if match is None:
                continue
            qty, status = db.execute(
                text("select qty, status from request_line where request_no = :n and line_no = :l"),
                {"n": match["request_no"], "l": match["line_no"]},
            ).one()
            now = D(0) if status == "cancelled" else qty
            if now != expect:
                out.find(
                    "high", f"after cancelling {victim}, {ln['item']} is {now}, expected {expect} (was {ln['qty']})"
                )
            if now != match["qty"]:
                reduced += 1
    else:
        out.find("high", "none of the eval's orders was combined, so cancelling one could not be checked")

    # 6. the audit trail
    audited = dict(
        db.execute(
            text(
                "select action, count(*) from audit_event where action in ('request.combine', 'request.line_reduce') group by action"
            )
        ).all()
    )
    joins = sum(len(ln["for"]) - 1 for ln in want)
    if audited.get("request.combine", 0) != joins:
        out.find("high", f"{audited.get('request.combine', 0)} combine rows in the audit log, expected {joins}")
    if victim and audited.get("request.line_reduce", 0) < 1:
        out.find("high", "the reduction after cancelling is not in the audit log")

    out.metrics = {
        "orders": len(orders),
        "requisition lines (oracle)": len(want),
        "combined lines": len(combined),
        "joins": joins,
        "location budget checks verified": checked_location,
        "lines reduced on cancelling": reduced,
        "window days": window,
    }
    return out
