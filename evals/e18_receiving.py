"""E18: goods receipt against an independent reading of the dataset: tolerance by category, rejections, inspection,
stock, and the progress of every line and order."""

from collections import defaultdict
from decimal import Decimal
from fractions import Fraction

import lib
import pipeline
from fastapi.testclient import TestClient
from lib import D, Outcome, rows
from sqlalchemy import text

ID = "E18"
TITLE = "Goods receipt: tolerance, rejections, inspection, stock and order progress are right"
REQUIREMENT = (
    "Plan: goods are received against a sent order; over-delivery is accepted up to the category's tolerance; "
    "rejected goods stay out of stock; items needing inspection wait; the order line and the stock follow what was accepted"
)


def half_up4(x: Fraction) -> Decimal:
    n = (x * 10000 + Fraction(1, 2)).__floor__()
    return Decimal(n) / Decimal(10000)


def run(ctx) -> Outcome:
    from p2p.main import app

    out = Outcome(True)
    world = lib.build_world("e18")
    # the plant's catalogue has no item that needs inspection, so some stocked items and one category are marked here
    # (block 9 spec 9.1 and 10.1: the item flag or the category flag)
    stocked = sorted(r["code"] for r in rows("master", "item") if r["stocked"] == "true")
    flagged = set(stocked[::6])
    categories = sorted({r["category_code"] for r in rows("master", "item") if r["category_code"]})
    category_flagged = {categories[len(categories) // 2]}
    world.db.execute(text("update item set inspection_required = true where code = any(:c)"), {"c": sorted(flagged)})
    world.db.execute(
        text("update item_category set inspection_required = true where code = any(:c)"), {"c": sorted(category_flagged)}
    )
    world.db.commit()
    state = pipeline.run_pipeline(world)
    world, db = state.world, state.world.db
    db.commit()  # the API runs in its own session: nothing may stay open here
    client = TestClient(app)
    buyer, store, quality = world.headers("Buyer"), world.headers("Store"), world.headers("Quality")

    # the oracle's own reading of the dataset
    items = {
        r["code"]: dict(
            r,
            inspection_required=(
                "true"
                if r["code"] in flagged or r["category_code"] in category_flagged
                else r["inspection_required"]
            ),
        )
        for r in rows("master", "item")
    }
    tolerance: dict[str, Fraction] = {}
    for r in rows("master", "tolerance"):
        if r["key"] == "receipt_over_pct":
            tolerance[r["category_code"]] = Fraction(D(r["value"]))

    def limit(item_code: str, ordered: Decimal) -> Decimal:
        pct = tolerance.get(items[item_code]["category_code"], tolerance.get("ANY", Fraction(0)))
        return half_up4(Fraction(ordered) * (100 + pct) / 100)

    # send every approved order (the previous step): receipts need a sent order
    for (no,) in db.execute(text("select no from po where status = 'approved'")).all():
        client.post(f"/api/purchase-orders/{no}/send", headers=buyer)
    db.commit()
    lines = db.execute(
        text(
            "select l.po_no, l.line_no, l.item_code, l.qty, l.base_qty, po.ship_to_location, po.status "
            "from po_line l join po on po.no = l.po_no where po.status in ('sent') and l.item_code is not null "
            "order by l.po_no, l.line_no"
        )
    ).all()
    out.metrics["order lines to receive"] = len(lines)

    # not-sent orders cannot be received
    refused_wrong = []
    for no, status in db.execute(text("select no, status from po where status <> 'sent' limit 8")).all():
        before = db.execute(text("select count(*) from goods_receipt")).scalar_one()
        r = client.post(
            f"/api/purchase-orders/{no}/receive", json={"lines": [{"line_no": 1, "qty_received": "1"}]}, headers=store
        )
        if r.status_code != 409 or db.execute(text("select count(*) from goods_receipt")).scalar_one() != before:
            refused_wrong.append(f"{no} ({status}) -> {r.status_code}")
    if refused_wrong:
        out.find("high", f"goods were accepted against an order that was not sent: {refused_wrong[:3]}")

    # stock before
    def on_hand() -> dict[tuple[str, str], Fraction]:
        db.expire_all()
        return {
            (i, loc): Fraction(q)
            for i, loc, q in db.execute(text("select item_code, location_code, on_hand from stock_level")).all()
        }

    before_stock = on_hand()
    expected_stock: dict[tuple[str, str], Fraction] = defaultdict(Fraction)
    expected_accepted: dict[tuple[str, int], Fraction] = defaultdict(Fraction)
    expected_excess: dict[tuple[str, int], Fraction] = defaultdict(Fraction)
    scenario_count: dict[str, int] = defaultdict(int)
    wrong: list[str] = []

    def post(po: str, line: int, qty: Decimal, rejected: Decimal | None = None, reason: str | None = None):
        body = {"lines": [{"line_no": line, "qty_received": str(qty)}]}
        if rejected is not None:
            body["lines"][0]["qty_rejected"] = str(rejected)
            body["lines"][0]["reject_reason"] = reason or "Damaged"
        return client.post(f"/api/purchase-orders/{po}/receive", json=body, headers=store)

    for n, (po, line, item_code, ordered, base_qty, loc, _) in enumerate(lines):
        ordered = Decimal(ordered)
        item = items[item_code]
        ratio = Fraction(base_qty) / Fraction(ordered) if base_qty else Fraction(1)
        stocked = item["stocked"] == "true"
        inspect = item["inspection_required"] == "true"
        kind = ["full", "over_limit", "partial_then_rest", "reject", "over_excess"][n % 5]
        if inspect:
            kind = "inspect"
        scenario_count[kind] += 1
        key = (po, line)

        def credit(accepted: Fraction) -> None:
            expected_accepted[key] += accepted
            if stocked:
                expected_stock[(item_code, loc)] += accepted * ratio

        if kind == "full":
            r = post(po, line, ordered)
            if r.status_code != 200:
                wrong.append(f"{po}/{line} full receipt answered {r.status_code} {r.text[:80]}")
                continue
            credit(Fraction(ordered))
        elif kind in ("over_limit", "over_excess"):
            top = limit(item_code, ordered)
            if kind == "over_excess":
                # block 9 (spec 9.2, 10.1): one unit above the room is held in quarantine as excess, not refused
                too_much = top + Decimal("1")
                r = post(po, line, too_much)
                if r.status_code != 200:
                    wrong.append(f"{po}/{line}: {too_much} (limit {top}) was refused: {r.status_code} {r.text[:80]}")
                    continue
                body = r.json()
                if Decimal(body.get("accepted", {}).get(str(line), "0")) != top or Decimal(
                    body.get("excess", {}).get(str(line), "0")
                ) != Decimal("1"):
                    wrong.append(f"{po}/{line}: the excess was not split from the counted goods: {body}")
                expected_excess[key] += Fraction(1)
                credit(Fraction(top))
                continue
            r = post(po, line, top)
            if r.status_code != 200:
                wrong.append(f"{po}/{line}: the limit {top} itself was refused: {r.status_code} {r.text[:80]}")
                continue
            credit(Fraction(top))
        elif kind == "partial_then_rest":
            first = half_up4(Fraction(ordered) / 2)
            rest = ordered - first
            r1 = post(po, line, first) if first > 0 else None
            r2 = post(po, line, rest) if rest > 0 else None
            for r in (r1, r2):
                if r is not None and r.status_code != 200:
                    wrong.append(f"{po}/{line} part receipt answered {r.status_code} {r.text[:80]}")
            credit(
                Fraction(first if r1 is not None and r1.status_code == 200 else 0)
                + Fraction(rest if r2 is not None and r2.status_code == 200 else 0)
            )
        elif kind == "reject":
            bad = half_up4(Fraction(ordered) / 4)
            r = post(po, line, ordered, rejected=bad)
            if bad == 0:
                continue
            if r.status_code != 200:
                wrong.append(f"{po}/{line} receipt with rejection answered {r.status_code} {r.text[:80]}")
                continue
            credit(Fraction(ordered - bad))
        else:  # inspection: nothing counts until the inspection says so
            r = post(po, line, ordered)
            if r.status_code != 200 or r.json()["pending_inspection"] != [line]:
                wrong.append(f"{po}/{line} inspected item was not held for inspection: {r.status_code} {r.text[:100]}")
                continue
            gr = r.json()["receipt"]
            stock_now = on_hand()
            if (
                stock_now.get((item_code, loc), Fraction(0))
                != before_stock.get((item_code, loc), Fraction(0)) + expected_stock[(item_code, loc)]
            ):
                wrong.append(f"{po}/{line}: stock moved before the inspection")
            bad = half_up4(Fraction(ordered) / 5)
            ins = client.post(
                f"/api/receipts/{gr}/inspect",
                json={
                    "lines": [
                        {
                            "line_no": line,
                            "qty_accepted": str(ordered - bad),
                            "qty_rejected": str(bad),
                            "reason": "Out of spec",
                        }
                    ]
                },
                headers=quality,  # block 9 (spec 9.1, 10.1): the receiver cannot inspect their own receipt
            )
            if ins.status_code != 200:
                wrong.append(f"{po}/{line} inspection answered {ins.status_code} {ins.text[:80]}")
                continue
            credit(Fraction(ordered - bad))
    out.metrics["scenarios"] = dict(scenario_count)
    if wrong:
        out.find("high", f"{len(wrong)} receipt(s) behaved differently from the plan; first: {wrong[0]}")

    # invariants over the whole result
    db.commit()
    after_stock = on_hand()
    stock_diffs = []
    for k in set(expected_stock) | {k for k in after_stock if after_stock[k] != before_stock.get(k, Fraction(0))}:
        moved = after_stock.get(k, Fraction(0)) - before_stock.get(k, Fraction(0))
        if moved != expected_stock.get(k, Fraction(0)):
            stock_diffs.append(f"{k}: stock moved {float(moved)}, expected {float(expected_stock.get(k, 0))}")
    out.metrics["stock positions changed"] = len(
        [k for k in after_stock if after_stock[k] != before_stock.get(k, Fraction(0))]
    )
    if stock_diffs:
        out.find("high", f"{len(stock_diffs)} stock position(s) differ from what was accepted; first: {stock_diffs[0]}")

    line_diffs, status_diffs = [], []
    for po, line, qty, received, status in db.execute(
        text("select po_no, line_no, qty, received_qty, status from po_line")
    ).all():
        want = expected_accepted.get((po, line), Fraction(0))
        if Fraction(received) != want:
            line_diffs.append(f"{po}/{line}: received_qty {received}, expected {float(want)}")
        want_status = "received" if want >= Fraction(qty) else ("part_received" if want > 0 else None)
        if want_status and status != want_status:
            status_diffs.append(f"{po}/{line}: {status}, expected {want_status}")
        if not want_status and status not in ("open", "cancelled"):
            status_diffs.append(f"{po}/{line}: {status}, expected open")
    if line_diffs:
        out.find(
            "high",
            f"{len(line_diffs)} order line(s) have a received quantity that differs from what was accepted; first: {line_diffs[0]}",
        )
    if status_diffs:
        out.find("high", f"{len(status_diffs)} line status(es) are wrong; first: {status_diffs[0]}")

    order_diffs = []
    for no, status in db.execute(
        text("select no, status from po where status in ('sent', 'part_received', 'received')")
    ).all():
        statuses = [
            r[0]
            for r in db.execute(
                text("select status from po_line where po_no = :n and status <> 'cancelled'"), {"n": no}
            )
        ]
        want = (
            "received"
            if statuses and all(s == "received" for s in statuses)
            else ("part_received" if any(s in ("received", "part_received") for s in statuses) else "sent")
        )
        if status != want:
            order_diffs.append(f"{no}: {status}, expected {want}")
    if order_diffs:
        out.find("high", f"{len(order_diffs)} order status(es) are wrong; first: {order_diffs[0]}")

    # the books agree with themselves
    sums = db.execute(
        text(
            "select l.po_no, l.po_line_no, sum(coalesce(l.qty_accepted, 0) - coalesce(l.qty_excess, 0)) "
            "from gr_line l group by 1, 2"
        )
    ).all()
    mismatched = [
        f"{p}/{ln}"
        for p, ln, s in sums
        if Fraction(
            db.execute(
                text("select received_qty from po_line where po_no = :p and line_no = :l"), {"p": p, "l": ln}
            ).scalar_one()
        )
        != Fraction(s)
    ]
    receipts = db.execute(text("select count(*) from goods_receipt")).scalar_one()
    audits = db.execute(text("select count(*) from audit_event where action = 'grn.post'")).scalar_one()
    numbers = [r[0] for r in db.execute(text("select no from goods_receipt")).all()]
    movements = db.execute(text("select count(*) from stock_movement where source = 'platform'")).scalar_one()
    refs = db.execute(
        text(
            "select count(*) from stock_movement where source = 'platform' and ref_doc not in (select no from goods_receipt)"
        )
    ).scalar_one()
    out.metrics["receipts posted"] = receipts
    if mismatched:
        out.find(
            "high", f"received quantities differ from the sum of accepted quantities on the receipts: {mismatched[:3]}"
        )
    if receipts != audits:
        out.find("high", f"{receipts} receipts but {audits} audit rows")
    if len(set(numbers)) != len(numbers):
        out.find("high", "receipt numbers are not unique")
    if refs:
        out.find("high", f"{refs} stock movement(s) point at a receipt that does not exist")
    if movements == 0:
        out.find("medium", "no stock movement was recorded: the scenarios did not reach stocked items")
    return out
