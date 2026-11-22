"""E86: stock items use budget at receipt, the rest at payment (task 3.7, D-10), written without the implementation.

Independence: written from the block 3 spec (sections 3.3 BG1, 4.1, 4.2, 4.5, 9), the plan and the dataset only. The
oracle values each actual itself: accepted quantity x the order price the eval agreed (paise half up) in the month of
the acceptance; an invoice's freight as entered; a service line's taxable value as invoiced. Which items are stocked
comes from `master/item.csv`.

Scenario (Apex Pumps): A0 and BG1's orders (MO-1: RM-01 50 at 650, RM-06 60 at 38, RM-08 40 at 880). On 25 November
2026 the store receives the 40 kg of wire (accepted at once): one `budget_actual` row of Rs 35,200, source receipt,
period 2026-11; MO-2's check moves Rs 35,200 from held by orders to spent, committed unchanged. The 60 bearings arrive
(inspection required): nothing at gate entry; inspection accepts 58, rejects 2: Rs 2,204, the rejected 2 never; the
line holds Rs 76 until its balance is closed, then 0. The wire's invoice (taxable 35,200, freight 500) is paid: one row
of Rs 500 (source payment), nothing more for the wire. A service (SV-01, GL 5240, CC-MNT) is ordered and confirmed:
held by orders until it is paid, then an actual of its taxable value in the invoice's month. One `budget.actual` audit
row per receipt or invoice. Ends with a tenant check against Brightwave.

Mutations: the stock line posted again at payment; an actual at gate entry before inspection.
"""

from decimal import Decimal
from typing import Any

import block1 as b
import block3 as b3
import env
import lib
from block1 import D, Z, js
from lib import Outcome

ID = "E86"
TITLE = "Stock spends its budget when accepted at receipt, at the order price; services and charges when paid"
REQUIREMENT = (
    "D-10 (block 3 spec 3.3 BG1, 4.1, 4.2): a stocked line's actual = accepted x order price in the acceptance month "
    "(source receipt), nothing at gate entry, never the rejected part; held by orders falls by the actual; a balance "
    "closed holds nothing; payment adds only charges for stocked lines and the taxable value of a service in the "
    "invoice's month; audit budget.actual once per receipt or invoice; tenant isolation"
)
PREFIX = "e86"
FREIGHT = Decimal(500)


def run(ctx: Any) -> Outcome:
    env.forget_tenants(PREFIX)
    try:
        return _run(ctx)
    finally:
        env.forget_tenants(PREFIX)


def _run(ctx: Any) -> Outcome:  # one story, told in order
    from fastapi.testclient import TestClient
    from p2p.main import app

    out = Outcome(True)
    check = b.Checker(out)
    lib.capture_mail()
    client = TestClient(app)
    apex = b3.Co3("apex", PREFIX, client)
    st = b3.start3(apex, check)
    bw = b3.Co3("brightwave", PREFIX, client)
    budgets = b3.Budgets(apex.dataset)
    gls = b3.gl_of_items(apex.dataset)
    items = b3.item_rows(apex.dataset)
    check(items["RM-08"]["stocked"] == "true" and items["SV-01"]["stocked"] != "true",
          "item.csv: RM-08 should be stocked and SV-01 not (the eval's premise)", "medium")  # fmt: skip
    reqs = {label: b3.request_no(apex, label) for label in ("MO-1", "MO-2", b3.MRP)}
    values = {label: b3.a0_values(st, label) for label in reqs}
    dept = b3.department_of(apex, reqs[b3.MRP]) or "PRD"
    lines = b3.bg1(apex, check, st)
    if not check({"RM-06", "RM-08"} <= set(lines), f"BG1 setup gave no orders for RM-06 and RM-08: {sorted(lines)}"):
        return out
    actual: dict[str, Decimal] = {}

    def mo2_check(label: str, spent: Decimal) -> None:
        now = apex.ordered_lines(reqs["MO-1"])
        not_ordered = values["MO-1"]["1410"] - sum((b3.r2(D(ln["qty"]) * D(items[i]["standard_price"]))
                                                    for i, ln in lines.items() if gls.get(i) == "1410"), Z)  # fmt: skip
        want = b3.expect_groups(budgets, "1410", "2026-11", dept, hreq=values[b3.MRP]["1410"] + max(not_ordered, Z),
                                hord=b3.held_by(now, gls, "1410", actual), spent=spent,
                                requested=values["MO-2"]["1410"])  # fmt: skip
        b3.compare_groups(check, label, apex.groups(reqs["MO-2"]), want)
        if label.endswith("wire received"):
            b3.compare_truth(check, label, apex, "BG1", b3.RECEIPT_DAY, "MO-2", apex.groups(reqs["MO-2"]), want,
                             step="after_receipt")  # fmt: skip

    wire, bearings = lines["RM-08"], lines["RM-06"]
    with b3.day(b3.AS_OF):
        for po in {str(wire["po_no"]), str(bearings["po_no"]), str(lines.get("RM-01", wire)["po_no"])}:
            apex.issue_po(po)
        committed0 = D(apex.groups(reqs["MO-2"]).get(("1410", "department"), {}).get("committed"))

    # ---- the wire: accepted at once ----
    seen = len(apex.actuals())
    audit0 = apex.audit_count("budget.actual")
    r = apex.receive(str(wire["po_no"]), int(wire["line_no"]), D(wire["qty"]), b3.RECEIPT_DAY)
    check(r.status_code == 200, f"receiving the wire answered {r.status_code} {r.text[:200]}")
    gr_wire = js(r).get("receipt")
    new = apex.actuals()[seen:]
    want_amt = b3.r2(D(wire["qty"]) * D(wire["price"]))
    row = new[0] if len(new) == 1 else {}
    check(len(new) == 1 and D(row.get("amount")) == want_amt and row.get("source") == "receipt"
          and row.get("period") == "2026-11" and str(row.get("po_no")) == str(wire["po_no"])
          and str(row.get("gl_code")) == "1410" and (row.get("gr_no") in (None, gr_wire) or not gr_wire),
          f"wire receipt: budget_actual rows {new}, expected one of {want_amt} source receipt period 2026-11 on "
          f"{wire['po_no']}/{wire['line_no']}")  # fmt: skip
    check(row.get("location_code") in (None, b3.CKN), f"wire receipt: actual location {row.get('location_code')}",
          "medium")  # fmt: skip
    check(apex.audit_count("budget.actual") == audit0 + 1, "wire receipt: not one budget.actual audit row", "medium")
    actual["RM-08"] = want_amt
    with b3.day(b3.RECEIPT_DAY):
        mo2_check("BG1 after the wire received", want_amt)
        after = D(apex.groups(reqs["MO-2"]).get(("1410", "department"), {}).get("committed"))
        check(after == committed0, f"wire receipt: MO-2's 1410 committed went from {committed0} to {after} (unchanged "
              "expected: the order's hold became spent)")  # fmt: skip

    # ---- the bearings: inspection ----
    seen = len(apex.actuals())
    r = apex.receive(str(bearings["po_no"]), int(bearings["line_no"]), D(bearings["qty"]), b3.RECEIPT_DAY)
    check(r.status_code == 200, f"receiving the bearings answered {r.status_code} {r.text[:200]}")
    gr = js(r).get("receipt")
    gate = apex.actuals()[seen:]
    check(not gate, f"bearings at gate entry (inspection pending) posted {gate}; nothing until accepted")
    rejected = Decimal(2)
    accepted = D(bearings["qty"]) - rejected
    with b3.day(b3.RECEIPT_DAY):
        r = apex.call(apex.store(), "POST", f"/api/receipts/{gr}/inspect", {"lines": [{
            "line_no": int(bearings["line_no"]), "qty_accepted": str(accepted), "qty_rejected": str(rejected),
            "reason": "eval: cracked cage"}]})  # fmt: skip
    check(r.status_code == 200, f"inspecting {gr} answered {r.status_code} {r.text[:200]}")
    new = apex.actuals()[seen:]
    want_amt = b3.r2(accepted * D(bearings["price"]))
    check(len(new) == 1 and D(new[0].get("amount")) == want_amt and new[0].get("source") == "receipt"
          and new[0].get("period") == "2026-11",
          f"inspection: budget_actual rows {new}, expected one of {want_amt} (58 x 38; the rejected 2 never)")  # fmt: skip
    actual["RM-06"] = want_amt
    hold = b3.open_value(bearings, want_amt)
    check(hold == Decimal(76), f"oracle: the bearings line should hold Rs 76, holds {hold} (self-check)", "medium")
    with b3.day(b3.RECEIPT_DAY):
        mo2_check("BG1 after inspection", actual["RM-08"] + actual["RM-06"])
        r = apex.call(apex.buyer(), "POST", f"/api/purchase-orders/{bearings['po_no']}/close-balance",
                      {"reason": "eval: the 2 rejected are not replaced"})  # fmt: skip
        check(r.status_code == 200, f"closing the bearings' balance answered {r.status_code} {r.text[:200]}")
        mo2_check("BG1 after the bearings' balance closed", actual["RM-08"] + actual["RM-06"])
        line_now = apex.ordered_lines(reqs["MO-1"]).get("RM-06")
        if line_now is not None:
            check(b3.open_value(line_now, actual["RM-06"]) == 0, f"after close_balance the bearings line holds "
                  f"{b3.open_value(line_now, actual['RM-06'])} by the spec's formula: {line_now}")  # fmt: skip

    # ---- the wire's invoice with freight, and a service, paid ----
    svc = apex.service_order(check, "SV-01", b3.RECEIPT_DAY)
    seen = len(apex.actuals())
    inv_wire, said = apex.invoice(str(wire["po_no"]), [(int(wire["line_no"]), D(wire["qty"]), D(wire["price"]))],
                                  b3.RECEIPT_DAY, "EV86-W1", [{"type": "freight", "amount": str(FREIGHT),
                                                              "gst_rate": "18"}])  # fmt: skip
    check(inv_wire is not None and said.get("status") == "approved", f"the wire's invoice: {said}", "medium")
    inv_svc = None
    svc_value = D(items["SV-01"]["standard_price"])
    if svc:
        _, po_svc, ln_svc = svc
        inv_svc, said = apex.invoice(po_svc, [(ln_svc, Decimal(1), svc_value)], b3.RECEIPT_DAY, "EV86-S1")
        check(inv_svc is not None and said.get("status") == "approved", f"the service invoice: {said}", "medium")
        check(not apex.actuals()[seen:], f"a confirmed service or a booked invoice posted {apex.actuals()[seen:]}; "
              "non-stock spend becomes actual when paid")  # fmt: skip
        _svc_hold(check, apex, svc_value, Z, "before payment")
    run_no = apex.pay(b3.RECEIPT_DAY)
    check(not run_no.startswith("no run") and ":" not in run_no, f"the payment run: {run_no}", "medium")
    rows = apex.actuals()
    by_inv = [r for r in rows if r.get("invoice_no") == inv_wire]
    check(len(by_inv) == 1 and D(by_inv[0].get("amount")) == FREIGHT and by_inv[0].get("source") == "payment",
          f"the wire's paid invoice posted {by_inv}; expected one row of the Rs 500 freight, source payment")  # fmt: skip
    on_wire = sum((D(r.get("amount")) for r in rows if str(r.get("po_no")) == str(wire["po_no"])
                   and r.get("source") == "receipt"), Z)  # fmt: skip
    check(on_wire == actual["RM-08"], f"the wire's receipt actual is {on_wire} after payment, {actual['RM-08']} expected")
    total_wire = sum((D(r.get("amount")) for r in by_inv), Z) + on_wire
    wrong_twice = actual["RM-08"] * 2 + FREIGHT
    check(total_wire != wrong_twice, "the wire was posted again at payment (mutation: stock line posted at payment)")
    if inv_svc:
        mine = [r for r in rows if r.get("invoice_no") == inv_svc]
        month = str(apex.one("select received_on from invoice where no = :n", n=inv_svc))[:7]
        check(len(mine) == 1 and D(mine[0].get("amount")) == svc_value and mine[0].get("period") == month
              and str(mine[0].get("gl_code")) == "5240" and mine[0].get("cost_centre_code") == "CC-MNT",
              f"the paid service posted {mine}; expected one row of {svc_value} for 5240 CC-MNT in {month}")  # fmt: skip
        _svc_hold(check, apex, Z, svc_value, "after payment")
    # one audit row per receipt or invoice: the wire's receipt, the inspection, two paid invoices
    want_audits = 2 + len([x for x in (inv_wire, inv_svc) if x])
    check(apex.audit_count("budget.actual") == audit0 + want_audits,
          f"{apex.audit_count('budget.actual') - audit0} budget.actual audit rows, expected {want_audits}", "medium")  # fmt: skip
    # mutation: an actual at gate entry would have shown a row before the inspection (checked above), and the wrong
    # rule's figure differs from the right one on this case
    check(b3.r2(D(bearings["qty"]) * D(bearings["price"])) != actual["RM-06"], "the inspection case cannot tell an "
          "actual at gate entry from one at acceptance (mutation check)", "medium")  # fmt: skip

    b3.tenant_check3(check, apex, bw)
    out.metrics.update({"checks": check.done, "failed": len(check.failed), "actual rows": len(apex.actuals())})
    out.notes.append(
        "Setup, not a spec rule: SV-01 is bought directly from SUP-006 (agreed by the purchase manager) and the request "
        "line put on CC-MNT as the owner, since the request form takes no cost centre; the services are paid in one run "
        "with the wire's invoice (Finance proposes, the director approves, `manual_mark_paid` as the demo has it)."
    )
    out.notes.append(
        "Spec gap: 4.2 says the receipt actual's period is 'the month of the acceptance date'; for an inspection that is "
        "the inspection day (both in November here)."
    )
    return out


def _svc_hold(check: b.Checker, co: b3.Co3, held: Decimal, spent: Decimal, when: str) -> None:
    """The service in the variance row of CC-MNT 5240: held by orders until paid, then spent."""
    rows = [r for r in b3.listed(co.variance(), "rows")
            if r.get("cost_centre") in ("CC-MNT",) and str(r.get("gl_code")) == "5240"]  # fmt: skip
    got = (D(rows[0].get("held_by_orders")), D(rows[0].get("spent"))) if rows else None
    check(got == (held, spent), f"variance CC-MNT 5240 {when}: (held by orders, spent) {got}, expected {(held, spent)}")


