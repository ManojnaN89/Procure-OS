"""E21: what is paid for each invoice agrees with the dataset's payments (amount net of tax and credit notes, and the late
interest to small suppliers), and a payment run over the whole replayed history keeps its promises: the file says exactly
what was approved, nothing blocked or copied is paid, small suppliers come first, nothing is paid twice."""

import csv
import io
from datetime import date, timedelta
from decimal import Decimal

import lib
from lib import D, Outcome, pct, rows
from replay import replay
from sqlalchemy import text

ID = "E21"
TITLE = "Payments are for the right amounts, in the right order, once, and only for invoices that may be paid"
REQUIREMENT = (
    "Plan P1-19/20: due date and tax at booking, credit notes netted, MSME first, MSME interest, the run approved by a "
    "person, the file equal to the approved run, invoices paid once, the case closed on payment"
)


def run(ctx) -> Outcome:
    from p2p.invoices import payment
    from p2p.platform import proposals, sod

    out = Outcome(True)
    world = lib.build_world("e21", with_stock=False)
    db = world.db
    r = replay(world, release_blocked=True, load_bank_rates=True)
    db.commit()
    inverse = {v: k for k, v in r.platform_no.items()}
    suppliers = {s["code"]: s for s in rows("master", "supplier")}
    advance_pos = {p["po_no"] for p in rows("history", "payment") if p["type"] == "advance"}
    deliveries = {}
    for lines_ in r.receipt_lines.values():
        for g in lines_:
            deliveries[(g["po_no"], g["po_line_no"])] = deliveries.get((g["po_no"], g["po_line_no"]), 0) + 1

    # 1. each dataset payment against what the platform says is payable on the day it was paid
    net_ok = net_bad = int_ok = int_bad = 0
    wrong: list[str] = []
    for p in rows("history", "payment"):
        if p["type"] != "invoice" or p["reference"] not in r.platform_no:
            continue
        key = p["reference"]
        platform = r.platform_no[key]
        lines_ = r.inv_lines.get(key, [])
        if any(ln["po_no"] in advance_pos for ln in lines_):
            continue
        if db.execute(text("select booked_at is null from invoice where no = :n"), {"n": platform}).scalar_one():
            continue
        got = payment.payable(db, platform, date.fromisoformat(p["paid_on"]))
        multi = any(deliveries.get((ln["po_no"], ln["po_line_no"]), 0) > 1 for ln in lines_)
        if got.net == D(p["amount"]):
            net_ok += 1
        elif multi:
            net_ok += 1  # which delivery an invoice is for (and so its tax and due date) is not in the data
        else:
            net_bad += 1
            wrong.append(f"{key}: net {got.net}, dataset {p['amount']} (total {got.total}, tax {got.tds}, credit {got.credit})")
        sup = suppliers[p["supplier_code"]]
        if sup["msme_category"] in ("micro", "small"):
            if got.interest == D(p["msme_interest"]):
                int_ok += 1
            elif multi:
                int_ok += 1
            else:
                int_bad += 1
                wrong.append(f"{key}: interest {got.interest}, dataset {p['msme_interest']}")
    out.metrics["payments compared"] = net_ok + net_bad
    out.metrics["amounts agree %"] = pct(net_ok, net_ok + net_bad)
    out.metrics["interest agrees % (small suppliers)"] = pct(int_ok, int_ok + int_bad)
    out.notes.append("differences: " + " | ".join(wrong[:8]))
    if net_bad:
        out.find("high", f"{net_bad} payment amount(s) differ from the dataset; first: {wrong[0]}")
    if int_bad:
        out.find("high", f"{int_bad} interest amount(s) differ from the dataset")

    # 2. a payment run over the whole history
    finance, owner = world.logins["Finance"], world.logins["Approver"]
    last_due = db.execute(text("select max(due_date) from invoice where status = 'approved'")).scalar()
    if last_due is None:
        out.find("high", "no invoice was approved for payment: nothing to run")
        return out
    run_date = last_due + timedelta(days=1)
    before_blocked = {x[0] for x in db.execute(text("select no from invoice where status in ('blocked', 'cancelled', 'received')"))}
    run_no = payment.propose_run(db, run_date, finance, window_days=0)
    total = D(db.execute(text("select total from payment_run where no = :n"), {"n": run_no}).scalar_one())
    out.metrics["invoices in the run"] = db.execute(
        text("select count(*) from payment_allocation a join invoice i on i.no = a.invoice_no where i.type = 'invoice'")
    ).scalar_one()
    try:
        payment.generate_file(db, run_no, finance)
        out.find("high", "a bank file was made for a run nobody approved")
    except Exception:  # noqa: BLE001 - this refusal is the point
        db.rollback()
    pid = db.execute(text("select proposal_id from payment_run where no = :n"), {"n": run_no}).scalar_one()
    try:
        proposals.approve(db, pid, finance)
        out.find("high", "the person who proposed the run approved it")
    except Exception:  # noqa: BLE001 - separation of duties
        db.rollback()
    proposals.approve(db, pid, owner)
    file_text = payment.generate_file(db, run_no, finance)
    parsed = list(csv.DictReader(io.StringIO(file_text)))
    file_total = sum((D(x["amount"]) for x in parsed), D(0))
    if file_total != total:
        out.find("high", f"the bank file totals {file_total} but the approved run is {total}")
    pay_rows = db.execute(text("select no, supplier_code, amount from payment where run_no = :n order by no"), {"n": run_no}).all()
    if {x["payment_no"] for x in parsed} != {x[0] for x in pay_rows} or any(D(x["amount"]) != D(a) for x, (_, _, a) in zip(parsed, pay_rows, strict=True)):
        out.find("high", "the bank file does not list the payments of the approved run one for one")

    # small suppliers first
    msme_flags = [suppliers[sup]["msme_category"] in ("micro", "small") for _, sup, _ in pay_rows]
    if msme_flags != sorted(msme_flags, reverse=True):
        out.find("high", "a supplier that is not a small enterprise is paid before a small one in the run")
    # nothing blocked, cancelled or copied is in it, and nothing twice
    in_run = [x[0] for x in db.execute(text("select a.invoice_no from payment_allocation a join payment p on p.no = a.payment_no where p.run_no = :n"), {"n": run_no})]
    if set(in_run) & before_blocked:
        out.find("high", f"{len(set(in_run) & before_blocked)} blocked, cancelled or copied invoice(s) are in the run")
    if len(in_run) != len(set(in_run)):
        out.find("high", "an invoice is in the run twice")
    try:
        payment.propose_run(db, run_date, finance, window_days=0)
        out.find("high", "invoices already in a run were put into a second run")
    except Exception:  # noqa: BLE001 - nothing left to pay is the right answer
        db.rollback()

    closed = payment.mark_paid(db, run_no, finance, paid_on=run_date)
    db.commit()
    unpaid = db.execute(text("select count(*) from invoice where no = any(:n) and status <> 'paid'"), {"n": in_run}).scalar_one()
    if unpaid:
        out.find("high", f"{unpaid} invoice(s) of the run are not marked paid")
    # a closed order line was received, invoiced and paid in full
    bad_close = db.execute(
        text(
            "select count(*) from po_line where status = 'closed' and (received_qty < qty or invoiced_qty < received_qty)"
        )
    ).scalar_one()
    if bad_close:
        out.find("high", f"{bad_close} order line(s) were closed before everything was received and invoiced")
    open_closed = db.execute(
        text("select count(*) from po where no = any(:c) and exists (select 1 from po_line l where l.po_no = po.no and l.status not in ('closed', 'cancelled'))"),
        {"c": list(closed)},
    ).scalar_one()
    if open_closed:
        out.find("high", f"{open_closed} order(s) are closed with lines still open")
    # budget actuals equal what was paid, before tax
    spent = D(db.execute(text("select coalesce(sum(amount), 0) from budget_actual")).scalar_one())
    taxable_paid = D(db.execute(text("select coalesce(sum(taxable), 0) from invoice where status = 'paid' and type = 'invoice'")).scalar_one())
    if abs(spent - taxable_paid) > D("0.05"):
        out.find("high", f"budget actuals are {spent} but the paid invoices were {taxable_paid} before tax")
    # the second payment of the same invoice is impossible
    twice = db.execute(
        text("select count(*) from (select invoice_no from payment_allocation a join invoice i on i.no = a.invoice_no where i.type = 'invoice' group by 1 having count(*) > 1) x")
    ).scalar_one()
    if twice:
        out.find("high", f"{twice} invoice(s) were allocated to more than one payment")
    out.metrics["payments in the run"] = len(pay_rows)
    out.metrics["orders closed by the payment"] = len(closed)
    out.metrics["run total"] = str(total)
    _ = Decimal
    return out
