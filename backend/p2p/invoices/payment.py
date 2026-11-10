"""From an approved invoice to money paid: booking (due date and tax at source), payment runs, the bank file, and closing the case.

An invoice is booked when it becomes approved for payment (matched, or released by a person): its due date follows the
payment terms and the small-supplier rule, and the tax to hold back is worked out. A payment run groups the invoices
that are due, small suppliers first, nets credit notes and tax, and goes to the owner for approval (rule 5: never
automatic). The approved run produces a bank file; when the bank has paid, the invoices are marked paid and the cases
they belong to are closed.
"""

import csv
import io
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from p2p.formulas import payment as rules
from p2p.invoices.service import InvoiceError
from p2p.platform import audit, emails, numbering, proposals
from p2p.platform.sod import Actor, user

D = Decimal
RTGS_FROM = D("200000")  # a payment above this goes by RTGS, below it by NEFT
OPEN_RUN = ("proposed", "approved", "file_generated")


def _setting(db: Session, key: str, default: str) -> str:
    for table in ("org_setting", "app_setting"):
        value = db.execute(text(f"select value from {table} where key = :k"), {"k": key}).scalar()  # noqa: S608
        if value not in (None, ""):
            return str(value)
    return default


# ---- booking ----------------------------------------------------------------------------------


def accepted_on(db: Session, invoice: dict[str, Any]) -> date:
    """The day the goods were accepted: the latest acceptance on the order lines before the invoice arrived."""
    day = db.execute(
        text(
            "select max(g.accepted_on) from goods_receipt g join gr_line l on l.gr_no = g.no "
            "join invoice_line i on i.po_no = l.po_no and i.po_line_no = l.po_line_no "
            "where i.invoice_no = :n and g.accepted_on is not null and g.accepted_on <= :d"
        ),
        {"n": invoice["no"], "d": invoice["received_on"]},
    ).scalar()
    return day or invoice["received_on"]  # type: ignore[no-any-return]


def book_invoice(db: Session, no: str) -> None:
    """Work out the due date and the tax to hold back, once, when an invoice is approved for payment."""
    inv = db.execute(text("select * from invoice where no = :n for update"), {"n": no}).mappings().one()
    if inv["type"] != "invoice" or inv["booked_at"] is not None:
        return
    sup = db.execute(text("select * from supplier where code = :c"), {"c": inv["supplier_code"]}).mappings().one()
    terms = (
        db.execute(text("select days, baseline from payment_terms where code = :c"), {"c": sup["payment_terms_code"]})
        .mappings()
        .first()
    )
    due = rules.due_date(
        baseline=terms["baseline"] if terms else "invoice_date",
        days=int(terms["days"]) if terms else 30,
        invoice_date=inv["invoice_date"],
        accepted_on=accepted_on(db, dict(inv)),
        msme_category=sup["msme_category"],
        msme_agreement=bool(sup["msme_agreement"]),
        agreed_days=sup["agreed_payment_days"],
    )
    tds_amount = D(0)
    if _setting(db, "tds_applicable_on_goods", "true") == "true":
        year = inv["received_on"]
        before = db.execute(
            text(
                "select coalesce(sum(i.taxable), 0) from invoice i join tds_entry t on t.invoice_no = i.no "
                "where i.supplier_code = :s and i.no <> :n and i.status <> 'cancelled' "
                "and t.booked_on >= :a and t.booked_on <= :b"
            ),
            {"s": inv["supplier_code"], "n": no, "a": _fy_start(year), "b": _fy_end(year)},
        ).scalar_one()
        tds = rules.tds_on_goods(
            D(before), D(inv["taxable"]), inv["received_on"], pan_inoperative=sup["pan_status"] == "inoperative"
        )
        db.execute(
            text(
                "insert into tds_entry (invoice_no, section, base, rate, amount, booked_on, quarter) "
                "values (:i, :s, :b, :r, :a, :d, :q)"
            ),
            {
                "i": no, "s": tds.section, "b": tds.base, "r": tds.rate, "a": tds.amount, "d": inv["received_on"],
                "q": rules.quarter_label(inv["received_on"]),
            },
        )  # fmt: skip
        tds_amount = tds.amount
    db.execute(
        text("update invoice set due_date = :d, tds_amount = :t, booked_at = now() where no = :n"),
        {"d": due, "t": tds_amount, "n": no},
    )


def _fy_start(d: date) -> date:
    return date(d.year if d.month >= 4 else d.year - 1, 4, 1)


def _fy_end(d: date) -> date:
    return date(_fy_start(d).year + 1, 3, 31)


# ---- what is payable --------------------------------------------------------------------------


@dataclass
class Payable:
    invoice_no: str
    total: Decimal
    tds: Decimal
    credit: Decimal
    interest: Decimal
    due: date | None
    credit_notes: list[str] = field(default_factory=list)

    @property
    def net(self) -> Decimal:
        return max(self.total - self.tds - self.credit, D(0))


def _bank_rates(db: Session) -> list[tuple[date, Decimal]]:
    return [(r[0], D(r[1])) for r in db.execute(text("select effective_from, rate_pct from ref_rbi_bank_rate"))]


def payable(db: Session, no: str, on: date) -> Payable:
    """What is paid for an invoice on a date: its total, less the tax held back and credit notes against it; plus interest
    if it is paid to a micro or small supplier after the due date (the interest is paid on top, as its own amount)."""
    inv = db.execute(text("select * from invoice where no = :n"), {"n": no}).mappings().one()
    credits = db.execute(
        text(
            "select c.no, c.total from invoice c where c.type = 'credit_note' and c.ref_invoice_no = :n and c.status = 'approved' "
            "and not exists (select 1 from payment_allocation a where a.invoice_no = c.no) order by c.no"
        ),
        {"n": no},
    ).all()
    sup = db.execute(text("select msme_category from supplier where code = :c"), {"c": inv["supplier_code"]}).scalar()
    interest = D(0)
    rates = _bank_rates(db)
    if sup in rules.MSME_CATEGORIES and inv["due_date"] and rates:
        interest = rules.msme_interest(D(inv["total"]), inv["due_date"], on, rates)
    return Payable(
        no, D(inv["total"]), D(inv["tds_amount"]), sum((D(c[1]) for c in credits), D(0)), interest, inv["due_date"],
        [c[0] for c in credits],
    )  # fmt: skip


# ---- payment runs -----------------------------------------------------------------------------


def due_invoices(db: Session, run_date: date, window_days: int | None = None) -> list[dict[str, Any]]:
    """Approved invoices with a due date up to the run date plus the window, not already in a run, small suppliers first."""
    window = window_days if window_days is not None else int(_setting(db, "payment_run_window_days", "7"))
    rows = db.execute(
        text(
            "select i.no, i.supplier_code, i.due_date, i.total, (s.msme_category in ('micro', 'small')) as msme "
            "from invoice i join supplier s on s.code = i.supplier_code "
            "where i.type = 'invoice' and i.status = 'approved' and i.payment_block = 'none' and i.due_date is not null "
            "and i.due_date <= :d + make_interval(days => :w) and not exists ("
            "  select 1 from payment_allocation a join payment p on p.no = a.payment_no join payment_run r on r.no = p.run_no "
            "  where a.invoice_no = i.no and r.status = any(:open)) "
            "order by (s.msme_category in ('micro', 'small')) desc, i.due_date, i.no"
        ),
        {"d": run_date, "w": window, "open": list(OPEN_RUN)},
    ).mappings()
    return [dict(r) for r in rows]


def propose_run(db: Session, run_date: date, user_id: int, *, window_days: int | None = None) -> str:
    """Group what is due into a payment run and send it for approval. Nothing is paid until the owner approves."""
    due = due_invoices(db, run_date, window_days)
    if not due:
        raise InvoiceError("no approved invoice is due in this window")
    by_supplier: dict[str, list[Payable]] = {}
    for row in due:
        by_supplier.setdefault(row["supplier_code"], []).append(payable(db, row["no"], run_date))
    run_no = numbering.next_number(db, "payment_run", run_date)
    account = db.execute(
        text(
            "select code from company_bank_account where purpose is null or purpose = 'payments' order by code limit 1"
        )
    ).scalar()
    db.execute(
        text(
            "insert into payment_run (no, run_date, company_bank_account, proposed_by, status) values (:n, :d, :a, :u, 'proposed')"
        ),
        {"n": run_no, "d": run_date, "a": account, "u": str(user_id)},
    )
    total = D(0)
    for supplier, items in by_supplier.items():
        net = sum((p.net for p in items), D(0))
        pay_no = numbering.next_number(db, "payment", run_date)
        db.execute(
            text(
                "insert into payment (no, run_no, type, supplier_code, amount, tds_amount, msme_interest, mode, reference) "
                "values (:n, :r, 'invoice', :s, :a, :t, :i, :m, :ref)"
            ),
            {
                "n": pay_no, "r": run_no, "s": supplier, "a": net + sum((p.interest for p in items), D(0)),
                "t": sum((p.tds for p in items), D(0)), "i": sum((p.interest for p in items), D(0)),
                "m": "rtgs" if net > RTGS_FROM else "neft", "ref": ", ".join(p.invoice_no for p in items)[:200],
            },
        )  # fmt: skip
        for p in items:
            db.execute(
                text("insert into payment_allocation (payment_no, invoice_no, amount) values (:p, :i, :a)"),
                {"p": pay_no, "i": p.invoice_no, "a": p.total - p.tds},
            )
            for credit_no in p.credit_notes:
                credit = D(db.execute(text("select total from invoice where no = :n"), {"n": credit_no}).scalar_one())
                db.execute(
                    text("insert into payment_allocation (payment_no, invoice_no, amount) values (:p, :i, :a)"),
                    {"p": pay_no, "i": credit_no, "a": -credit},
                )
        total += net + sum((p.interest for p in items), D(0))
    pid = proposals.propose(
        db,
        user(user_id),
        action="payment_run.approve",
        summary=f"Pay {len(due)} invoice(s) to {len(by_supplier)} supplier(s), {total:,.2f} in all, on {run_date:%d %b %Y}",
        payload={"run_no": run_no},
        required_permission="payment.approve",
        risk="high",
    )
    db.execute(
        text("update payment_run set total = :t, proposal_id = :p where no = :n"), {"t": total, "p": pid, "n": run_no}
    )
    audit.record(
        db, user(user_id), "payment_run.propose", "payment_run", run_no, after={"invoices": len(due), "total": total}
    )
    db.commit()
    return run_no


@proposals.executor("payment_run.approve")
def _approve_run(db: Session, payload: dict[str, Any], actor: Actor) -> dict[str, Any]:
    no = str(payload["run_no"])
    run = db.execute(text("select status, proposed_by from payment_run where no = :n for update"), {"n": no}).first()
    if run is None or run[0] != "proposed":
        raise InvoiceError(f"{no} is not waiting for approval")
    if run[1] == actor.id:
        raise InvoiceError("segregation of duties: the person who proposed the run cannot approve it")
    db.execute(
        text("update payment_run set status = 'approved', approved_by = :u, approved_at = now() where no = :n"),
        {"u": actor.id, "n": no},
    )
    audit.record(
        db, actor, "payment_run.approve", "payment_run", no, before={"status": "proposed"}, after={"status": "approved"}
    )
    db.commit()
    return {"run": no, "status": "approved"}


# ---- the bank file, the payment, the closing --------------------------------------------------

FILE_HEADER = ["payment_no", "beneficiary", "account_no", "ifsc", "amount", "mode", "reference"]


def generate_file(db: Session, no: str, user_id: int) -> str:
    """The bank instruction for an approved run: one line per supplier, exactly the approved amounts."""
    run = db.execute(text("select * from payment_run where no = :n for update"), {"n": no}).mappings().first()
    if run is None:
        raise InvoiceError(f"{no} does not exist")
    if run["status"] != "approved":
        raise InvoiceError(f"{no} is {run['status']}; a file is made only for an approved run")
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\n")
    writer.writerow(FILE_HEADER)
    for p in db.execute(text("select * from payment where run_no = :n order by no"), {"n": no}).mappings():
        bank = db.execute(
            text(
                "select b.account_no, b.ifsc, coalesce(s.trade_name, s.legal_name) from supplier_bank_account b "
                "join supplier s on s.code = b.supplier_code where b.supplier_code = :c and coalesce(b.verified, false) "
                "order by b.verified_on desc nulls last limit 1"
            ),
            {"c": p["supplier_code"]},
        ).first()
        if bank is None:
            raise InvoiceError(f"{p['supplier_code']} has no verified bank account, so it cannot be paid")
        writer.writerow([p["no"], bank[2], bank[0], bank[1], p["amount"], p["mode"].upper(), p["reference"]])
    text_ = out.getvalue()
    db.execute(
        text("update payment_run set status = 'file_generated', file_text = :f where no = :n"), {"f": text_, "n": no}
    )
    audit.record(db, user(user_id), "payment_run.file", "payment_run", no, after={"lines": text_.count("\n") - 1})
    db.commit()
    return text_


def mark_paid(db: Session, no: str, user_id: int, *, paid_on: date, utrs: dict[str, str] | None = None) -> list[str]:
    """The bank has paid the run: record the bank references, settle the invoices and close what is finished."""
    run = db.execute(text("select * from payment_run where no = :n for update"), {"n": no}).mappings().first()
    if run is None:
        raise InvoiceError(f"{no} does not exist")
    if run["status"] != "file_generated":
        raise InvoiceError(f"{no} is {run['status']}; only a run whose bank file was made can be marked paid")
    payments = [
        dict(r) for r in db.execute(text("select * from payment where run_no = :n order by no"), {"n": no}).mappings()
    ]
    closed: list[str] = []
    for n, p in enumerate(payments, start=1):
        utr = (utrs or {}).get(p["no"]) or f"UTR{paid_on:%y%m%d}{no[-4:]}{n:04d}"
        db.execute(
            text("update payment set utr = :u, paid_on = :d where no = :p"), {"u": utr, "d": paid_on, "p": p["no"]}
        )
        for a in db.execute(text("select invoice_no from payment_allocation where payment_no = :p"), {"p": p["no"]}):
            db.execute(
                text("update invoice set status = 'paid', paid_on = :d where no = :i and status = 'approved'"),
                {"d": paid_on, "i": a[0]},
            )
            _budget_actual(db, a[0])
            closed += close_case(db, a[0], user_id)
        p["utr"] = utr
    db.execute(text("update payment_run set status = 'paid' where no = :n"), {"n": no})
    audit.record(
        db, user(user_id), "payment_run.paid", "payment_run", no, after={"paid_on": paid_on, "payments": len(payments)}
    )
    db.commit()  # the payment stands whatever happens to the emails
    for p in payments:
        _remit(db, p, paid_on, p["utr"])
    return closed


def _budget_actual(db: Session, invoice_no: str) -> None:
    """What was spent, by cost centre and GL account, so the budget can show actuals."""
    inv = db.execute(text("select received_on, type from invoice where no = :n"), {"n": invoice_no}).first()
    if inv is None or inv[1] != "invoice":
        return
    spent: list[tuple[Any, Any, Decimal]] = [
        (r[0], r[1], D(r[2]))
        for r in db.execute(
            text(
                "select rl.cost_centre_code as cc, coalesce(rl.gl_code, pl.gl_code) as gl_code, sum(il.taxable) as amount "
                "from invoice_line il join po_line pl on pl.po_no = il.po_no and pl.line_no = il.po_line_no "
                "left join request_line rl on rl.request_no = pl.request_no and rl.line_no = pl.request_line_no "
                "where il.invoice_no = :n group by 1, 2"
            ),
            {"n": invoice_no},
        ).all()
    ]
    charges = D(
        db.execute(
            text("select coalesce(sum(amount), 0) from invoice_charge where invoice_no = :n"), {"n": invoice_no}
        ).scalar_one()
    )
    if spent and charges:  # freight and packing are spent too: they go with the first line's cost centre and GL account
        spent[0] = (spent[0][0], spent[0][1], spent[0][2] + charges)
    for cc, gl, amount in spent:
        db.execute(
            text(
                "insert into budget_actual (invoice_no, cost_centre_code, gl_code, period, amount) values (:i, :c, :g, :p, :a)"
            ),
            {"i": invoice_no, "c": cc, "g": gl, "p": inv[0].strftime("%Y-%m"), "a": amount},
        )


def close_case(db: Session, invoice_no: str, user_id: int) -> list[str]:
    """When an order line has been received, invoiced and paid in full its line is closed, and the order and request with it."""
    closed: list[str] = []
    for po_no, line_no in db.execute(
        text("select distinct po_no, po_line_no from invoice_line where invoice_no = :n"), {"n": invoice_no}
    ).all():
        line = db.execute(
            text(
                "select qty, received_qty, invoiced_qty, status from po_line where po_no = :p and line_no = :l for update"
            ),
            {"p": po_no, "l": line_no},
        ).first()
        if line is None or line[3] in ("closed", "cancelled"):
            continue
        unpaid = db.execute(
            text(
                "select count(*) from invoice_line l join invoice i on i.no = l.invoice_no "
                "where l.po_no = :p and l.po_line_no = :l and i.type = 'invoice' and i.status in ('approved', 'blocked', 'received')"
            ),
            {"p": po_no, "l": line_no},
        ).scalar_one()
        if D(line[1]) >= D(line[0]) and D(line[2]) >= D(line[1]) and unpaid == 0:
            db.execute(
                text("update po_line set status = 'closed' where po_no = :p and line_no = :l"),
                {"p": po_no, "l": line_no},
            )
        else:
            continue
        open_lines = db.execute(
            text("select count(*) from po_line where po_no = :p and status not in ('closed', 'cancelled')"),
            {"p": po_no},
        ).scalar_one()
        if open_lines == 0:
            db.execute(text("update po set status = 'closed' where no = :p"), {"p": po_no})
            closed.append(po_no)
            audit.record(db, user(user_id), "po.close", "po", po_no, after={"status": "closed"})
    return closed


def _remit(db: Session, payment: dict[str, Any], paid_on: date, utr: str) -> None:
    """Tell the supplier what was paid and for which invoices."""
    contact = db.execute(
        text(
            "select email, name from supplier_contact where supplier_code = :c and email is not null "
            "order by is_primary desc nulls last, id limit 1"
        ),
        {"c": payment["supplier_code"]},
    ).first()
    if contact is None:
        return
    rows = db.execute(
        text(
            "select i.supplier_invoice_no, i.type, a.amount from payment_allocation a join invoice i on i.no = a.invoice_no "
            "where a.payment_no = :p order by i.no"
        ),
        {"p": payment["no"]},
    ).all()
    company = db.execute(text("select coalesce(short_name, legal_name) from company limit 1")).scalar() or "Our company"
    lines = "\n".join(f"  {r[0]} ({r[1].replace('_', ' ')}): Rs {r[2]:,.2f}" for r in rows)
    body = (
        f"Dear {contact[1] or 'Sir or Madam'},\n\n{company} has paid Rs {payment['amount']:,.2f} on {paid_on:%d %b %Y}.\n"
        f"Bank reference: {utr}\n\nThis covers:\n{lines}\n"
        + (f"\nTax deducted at source: Rs {payment['tds_amount']:,.2f}\n" if D(payment["tds_amount"]) else "")
        + f"\nRegards,\nAccounts, {company}\n"
    )
    try:
        emails.send_linked_email(
            db, contact[0], f"Payment advice {payment['no']}", body, linked_type="invoice", linked_ref=payment["no"]
        )
        db.execute(
            text("update payment set remittance_sent_at = :n where no = :p"),
            {"n": datetime.now(UTC), "p": payment["no"]},
        )
        db.commit()
    except OSError:
        db.rollback()  # the payment stands; the advice can be sent again
