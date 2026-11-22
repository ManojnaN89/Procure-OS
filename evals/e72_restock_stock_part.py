"""E72: replenishment counts only the stock part of open orders (task 6.7, DF-25), written without the implementation.

Independence: written from the block 2 spec (sections 7, 9.3 D1, 11) and the dataset only. The oracle works out the
restock from the item's plan as the administrators set it (min-max: order up to the maximum when the position is at or
below the reorder point; urgent when nothing usable is free) with the position = usable - set aside + what is on order
for stock: an order or request line counts only by the part not bought for a material request; a line with no
material request counts fully.

Scenario D1 (Apex): T1 (MO-1's request approved and bought: the castings order, bought only for MO-1, is open), then the
administrators plan the castings min-max (reorder 20, max 60) through the planning change, then `replenish`: one urgent
restock request for 60 castings, Rs 39,000, at Chakan (today's code skips it as "already on order"); a second run makes
nothing; the restock request is then approved and bought, and a third run still makes nothing (a restock order blocks a
second restock). Ends with a tenant check. Mutation: open order lines bought for production counted as restock on order.
"""

from datetime import date
from decimal import Decimal
from typing import Any

import block1 as b
import block2 as s2
import env
import lib
from lib import Outcome

ID = "E72"
TITLE = "Replenishment counts only the stock part of open orders (DF-25): production orders never block a restock"
REQUIREMENT = (
    "Task 6.7, DF-25 (block 2 spec 7, 9.3 D1): an open PO line counts as on order for stock only by its request line's "
    "stock part (base qty less linked material request quantities); a line with no request line counts fully; a "
    "restock order still blocks a second restock; a second run makes nothing; tenant isolation"
)
PREFIX = "e72"
CKN = "PLT-CKN"
ACK_DAY = date(2026, 11, 23)


def run(ctx: Any) -> Outcome:
    env.forget_tenants(PREFIX)
    try:
        return _run(ctx)
    finally:
        env.forget_tenants(PREFIX)


def restock(usable: Decimal, reserved: Decimal, on_order: Decimal, point: Decimal, top: Decimal) -> Decimal:
    position = usable - reserved + on_order
    return max(top - position, b.Z) if position <= point else b.Z


def _run(ctx: Any) -> Outcome:
    from fastapi.testclient import TestClient

    from p2p.main import app

    out = Outcome(True)
    check = b.Checker(out)
    lib.capture_mail()
    client = TestClient(app)

    co = s2.Co("apex", PREFIX, client)
    bw = s2.Co("brightwave", PREFIX, client)
    bw_before = bw.snapshot2()
    s2.scenario_known(check, co, "D1-restock")
    st, _ = s2.start2(co, check)
    for no in co.request_of("MO-1"):
        co.approve(no)
    co.source()
    st.mark_buyer(co.with_buyer())
    found = co.po_of("MO-1", "RM-01")
    if not check(found is not None, "D1: no castings order for MO-1 after sourcing"):
        return out
    po = found[0]
    co.advance_po(po)
    r = co.acknowledge(po, date(2026, 12, 8), ACK_DAY)
    check(r.status_code == 200, f"D1: the acknowledgement answered {r.status_code}")
    asked, approved = co.planning("RM-01", CKN, reorder_policy="minmax", reorder_point="20", max_qty="60")
    check(asked.status_code == 202 and approved is not None and approved.status_code == 200, f"D1: the planning change "
          f"answered {asked.status_code} / {getattr(approved, 'status_code', None)}")  # fmt: skip
    plan = co.q("select reorder_policy, reorder_point, max_qty from item_location where item_code = 'RM-01' and "
                "location_code = :l", l=CKN)  # fmt: skip
    check(bool(plan) and plan[0][0] == "minmax" and b.D(plan[0][1]) == 20 and b.D(plan[0][2]) == 60,
          f"D1: RM-01's plan after the change {plan}")  # fmt: skip
    usable, reserved = st.usable("RM-01", CKN), st.reserved_at("RM-01", CKN)
    for_mrs = sum((c.qty for ln in st.lines if ln.item == "RM-01" for c in ln.covers if c.buyer), b.Z)
    right = restock(usable, reserved, b.Z, Decimal(20), Decimal(60))
    wrong = restock(usable, reserved, for_mrs, Decimal(20), Decimal(60))
    price = st.price("RM-01")
    check(right == 60 and b.money(right * price) == Decimal("39000"), f"D1: the oracle restocks {right} worth "
          f"{right * price}; spec 60 and Rs 39,000 (self-check)", "medium")  # fmt: skip
    s2.mutation(check, out, "PO lines for production counted as restock on order", right != wrong)
    before = co.requests_now()
    code, text_ = b.cli("replenish", co.slug)
    check(code == 0, f"D1: `replenish` exited {code}: {text_[-300:]}")
    new = co.requests_now() - before
    made = {n: co.request_lines(n) for n in new}
    hits = [n for n, x in made.items() if "RM-01" in x]
    check(len(hits) == 1 and made[hits[0]] == {"RM-01": right}, f"D1: replenishment made {made}, expected one request "
          f"for RM-01 {b.fq(right)}")  # fmt: skip
    check("already requested or on order" not in text_.lower() or "RM-01" not in text_, "D1: replenishment still says "
          "RM-01 is already on order", "low")  # fmt: skip
    if hits:
        head = co.request_head(hits[0])
        check(head.get("priority") == "urgent" and head.get("location") == CKN and b.money(head.get("total", b.Z))
              == b.money(right * price), f"D1: the restock request is {head}, expected urgent, at {CKN}, Rs 39,000")  # fmt: skip
        linked = co.q("select count(*) from material_request_buy where request_no = :n", n=hits[0])
        check(int(linked[0][0]) == 0, f"D1: the restock request {hits[0]} is linked to a material request")
    s2.truth_requests(check, co, "D1-restock", new)
    s2.second_run(check, co, "D1 replenish again", lambda: b.cli("replenish", co.slug))
    # a restock order still blocks a second restock
    if hits:
        status = co.approve(hits[0])
        check(status == "approved", f"D1: the restock request is {status} after its approvers acted")
        co.source()
        rpo = co.q("select pl.po_no from po_line pl join po p on p.no = pl.po_no where pl.request_no = :n and "
                   "p.status <> 'cancelled'", n=hits[0])  # fmt: skip
        check(bool(rpo), f"D1: no order for the restock request {hits[0]} after sourcing", "medium")
        if rpo:
            co.advance_po(str(rpo[0][0]))
            before = co.requests_now()
            code, _ = b.cli("replenish", co.slug)
            again = [n for n in co.requests_now() - before if "RM-01" in co.request_lines(n)]
            check(
                code == 0 and not again, f"D1: with the restock order open, replenishment made {again} for RM-01 again"
            )
    s2.tenant_check(check, co, bw, bw_before)
    out.metrics.update({"checks": check.done, "failed": len(check.failed)})
    out.notes += [
        "The restock oracle uses min-max as the spec states it (order up to the maximum from the position) and the "
        "item's estimate (contract RM-01 650, spec 9.3); minimum order quantities are not in play for 60 castings.",
    ]
    return out
