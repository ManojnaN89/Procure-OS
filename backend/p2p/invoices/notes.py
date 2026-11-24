"""Credit and debit notes: entry, a second person's approval, netting and refunds (2.4, D-21, DF-21).

A credit note from the supplier and a debit note from us enter `received`: another person with `invoice.approve`
approves them (the enterer may not). An approved note is netted in a run, per supplier: the notes linked to an invoice
in the payment first, then the rest oldest first, never more than is still to pay, so a payment is never negative. What
is left of a note waits for the next run; a refund of a debit note is a credit line on the bank statement confirmed by
a person who may record payments. A note raised for a recovery claim settles that claim when it is fully used.
"""

from decimal import Decimal
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from p2p.formulas.calendar import business_today
from p2p.formulas.money import format_inr
from p2p.invoices import service as invoices
from p2p.invoices.service import InvoiceError
from p2p.platform import audit
from p2p.platform.sod import user

D = Decimal


def _table_exists(db: Session, name: str) -> bool:
    return bool(db.execute(text("select to_regclass(:n)"), {"n": name}).scalar())


def raise_debit_note(
    *,
    db: Session,
    supplier: str,
    taxable: Decimal,
    gst_pct: Decimal,
    reason: str,
    source_ref: str,
    user_id: int,
    ref_invoice_no: str | None = None,
) -> str:
    """Our debit note for money owed back: the taxable value and GST, entered `received` for a second person.

    # NEEDS block 9: `receiving/returns.py` calls this when it raises a recovery claim (same transaction), and the
    # claim keeps the note's number in `recovery_claim.debit_note_no`; `settle_note` sets the claim to `settled`.
    """
    cgst = (taxable * gst_pct / 200).quantize(D("0.01"))
    sgst = cgst
    total = (taxable + cgst + sgst).quantize(D("1"))
    data = invoices.InvoiceIn(
        supplier_code=supplier,
        supplier_invoice_no=f"DN-{source_ref.replace(':', '-')}",
        invoice_date=business_today(),
        lines=[],
        type="debit_note",
        taxable=taxable,
        cgst=cgst,
        sgst=sgst,
        igst=D(0),
        total=total,
        ref_invoice_no=ref_invoice_no,
        channel="manual",
    )
    done = invoices.enter_invoice(db, data, user_id, commit=False)
    db.execute(
        text("update invoice set reason = :r, source_ref = :s where no = :n"),
        {"r": reason, "s": source_ref, "n": done.no},
    )
    audit.record(
        db,
        user(user_id),
        "invoice.note_source",
        "invoice",
        done.no,
        after={"reason": reason, "source_ref": source_ref},
    )
    return done.no


def approve(db: Session, invoice_no: str, user_id: int) -> dict[str, Any]:
    """A second person approves a note; the enterer never approves their own."""
    inv = (
        db.execute(text("select no, type, status, entered_by from invoice where no = :n for update"), {"n": invoice_no})
        .mappings()
        .first()
    )
    if inv is None:
        raise InvoiceError(f"{invoice_no} does not exist")
    if inv["type"] not in ("credit_note", "debit_note"):
        raise InvoiceError(f"{invoice_no} is an invoice, not a note")
    if inv["status"] != "received":
        raise InvoiceError(f"{invoice_no} is {inv['status']}, not waiting for approval")
    if str(inv["entered_by"]) == str(user_id):
        raise InvoiceError("segregation of duties: the person who entered the note cannot approve it")
    db.execute(text("update invoice set status = 'approved' where no = :n"), {"n": invoice_no})
    audit.record(
        db, user(user_id), "invoice.note_approve", "invoice", invoice_no,
        before={"status": "received"}, after={"status": "approved"},
    )  # fmt: skip
    db.commit()
    return {"invoice": invoice_no, "status": "approved"}


def refund(db: Session, invoice_no: str, bank_line_id: int, amount: Decimal, user_id: int) -> dict[str, Any]:
    """The supplier refunded a debit note: its credit line is confirmed against the note, to the paisa."""
    note = db.execute(text("select * from invoice where no = :n for update"), {"n": invoice_no}).mappings().first()
    if note is None:
        raise InvoiceError(f"{invoice_no} does not exist")
    if note["type"] != "debit_note":
        raise InvoiceError(f"{invoice_no} is not a debit note")
    if note["status"] not in ("approved", "paid"):
        raise InvoiceError(f"{invoice_no} is {note['status']}: only an approved debit note takes a refund")
    left = D(note["total"]) - D(note["offset_used"])
    amount = D(amount)
    if amount <= 0 or amount > left:
        raise InvoiceError(f"the refund must be between 0.01 and {format_inr(left)}")
    line = (
        db.execute(text("select * from bank_statement_line where id = :i for update"), {"i": bank_line_id})
        .mappings()
        .first()
    )
    if line is None:
        raise InvoiceError(f"bank line {bank_line_id} does not exist")
    if line["direction"] != "credit":
        raise InvoiceError("a refund is a credit line on the bank statement")
    if D(line["amount"]) != amount:
        raise InvoiceError(f"the line is {format_inr(line['amount'])}, the refund {format_inr(amount)}")
    used = D(note["offset_used"]) + amount
    db.execute(
        text(
            "update invoice set offset_used = :u, status = case when :u >= total then 'paid' else status end where no = :n"
        ),
        {"u": used, "n": invoice_no},
    )
    db.execute(
        text(
            "update bank_statement_line set status = 'confirmed', payment_no = null, confirmed_by = :u, "
            "confirmed_at = now() where id = :i"
        ),
        {"u": str(user_id), "i": bank_line_id},
    )
    audit.record(
        db, user(user_id), "invoice.note_refund", "invoice", invoice_no,
        after={"amount": amount, "line": bank_line_id, "offset_used": used},
    )  # fmt: skip
    if used >= D(note["total"]):
        settle_note(db, invoice_no, user_id)
    db.commit()
    return {"invoice": invoice_no, "offset_used": used, "paid": used >= D(note["total"])}


def settle_note(db: Session, note_no: str, user_id: int) -> None:
    """A note fully used: the recovery claim it was raised for is settled, and an advance it recovered is recovered."""
    row = db.execute(text("select total, offset_used from invoice where no = :n"), {"n": note_no}).mappings().first()
    if row is None or D(row["offset_used"]) < D(row["total"]):
        return
    # NEEDS block 9: recovery_claim.debit_note_no and claim.settle (receiving/returns.py raises the claim)
    if _table_exists(db, "recovery_claim"):
        db.execute(
            text("update recovery_claim set status = 'settled' where debit_note_no = :n and status = 'open'"),
            {"n": note_no},
        )
        audit.record(db, user(user_id), "claim.settle", "recovery_claim", note_no, after={"status": "settled"})
    db.execute(
        text("update advance_request set status = 'recovered' where recovery_note = :n and status = 'recovering'"),
        {"n": note_no},
    )


def on_note_used(db: Session, note_no: str, user_id: int | None = None) -> None:
    """Called on settlement after a note's offset grew: a fully used note settles what it was raised for."""
    settle_note(db, note_no, user_id or 0)


def balance(db: Session, invoice_no: str) -> dict[str, Any]:
    row = (
        db.execute(
            text("select no, type, status, total, offset_used, reason, source_ref from invoice where no = :n"),
            {"n": invoice_no},
        )
        .mappings()
        .first()
    )
    if row is None:
        raise InvoiceError(f"{invoice_no} does not exist")
    return {**dict(row), "left": D(row["total"]) - D(row["offset_used"])}
