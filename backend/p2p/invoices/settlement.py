"""Recording a payment and settling it only on the bank's evidence (2.1, DF-01, DF-02, Q18).

A payment is `in_file` once the bank file is made. A person who may record payments may write down its bank reference
and value date with a proof (the recording step is optional), or the bank's statement settles it. Nothing here invents a
UTR: a reference is either typed by a person or comes from the bank's own line. On settlement the invoices are paid, the
notes it netted grow their offset, the budget takes the actual, the case closes, an advance is used against the order's
invoices and the supplier is told. A payment the bank returns reopens its invoices and their orders; a reversed
settlement undoes the same work.
"""

from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from p2p.approvals import budget_ledger
from p2p.formulas import payment as rules
from p2p.formulas.calendar import business_today
from p2p.formulas.money import format_inr
from p2p.invoices import payment, tds
from p2p.invoices.service import InvoiceError
from p2p.platform import audit, emails, files, tasks, users
from p2p.platform.sod import Actor, user

D = Decimal
RECORDED = "recorded"


@dataclass(frozen=True)
class Evidence:
    data: bytes
    filename: str
    content_type: str


# ---- who may touch a payment -----------------------------------------------------------------


def _run_people(db: Session, payment_no: str) -> set[str]:
    other = db.execute(text("select run_no from payment where no = :n"), {"n": payment_no}).scalar()
    if other is None:
        return set()
    return {
        str(r[0])
        for r in db.execute(
            text(
                "select approver from payment_run_approval where run_no = :r union "
                "select delegate from payment_run_approval where run_no = :r and delegate is not null"
            ),
            {"r": other},
        )
    }


def _recorder_ok(db: Session, payment_no: str, user_id: int) -> None:
    from p2p.approvals.workflow import org_code_of  # noqa: PLC0415 - one lookup, no cycle at import

    me = org_code_of(db, user_id)
    if me and me in _run_people(db, payment_no):
        raise InvoiceError("segregation of duties: this person approved the run")


def _pay(db: Session, payment_no: str) -> dict[str, Any]:
    row = db.execute(text("select p.*, r.run_date from payment p join payment_run r on r.no = p.run_no where p.no = :n"), {"n": payment_no}).mappings().first()  # fmt: skip
    if row is None:
        raise InvoiceError(f"payment {payment_no} does not exist")
    return dict(row)


# ---- record ----------------------------------------------------------------------------------


def record(
    db: Session,
    payment_no: str,
    user_id: int,
    *,
    utr: str | None,
    value_date: date,
    evidence: Evidence | None,
    tenant: str | None = None,
) -> dict[str, Any]:
    """A person writes the bank's reference and value date, with a proof: the payment is recorded, not settled."""
    pay = (
        db.execute(
            text(
                "select p.*, r.run_date from payment p join payment_run r on r.no = p.run_no where p.no = :n for update"
            ),
            {"n": payment_no},
        )
        .mappings()
        .first()
    )
    if pay is None:
        raise InvoiceError(f"payment {payment_no} does not exist")
    if pay["status"] != "in_file":
        raise InvoiceError(f"{payment_no} is {pay['status']}; only a payment in the bank file is recorded")
    if not (utr or "").strip() and evidence is None:
        raise InvoiceError("give the bank reference or a proof of payment")
    if utr and not rules.utr_ok(utr):
        raise InvoiceError("a bank reference is letters and digits, 6 to 30 of them")
    if value_date > business_today():
        raise InvoiceError("the value date cannot be in the future")
    if value_date < pay["run_date"]:
        raise InvoiceError("the value date cannot be before the run date")
    _recorder_ok(db, payment_no, user_id)
    if utr:
        twin = db.execute(
            text("select no from payment where upper(utr) = upper(:u) and no <> :n"),
            {"u": utr.strip(), "n": payment_no},
        ).scalar()
        if twin:
            raise InvoiceError(f"UTR {utr.strip()} is already on {twin}")
    doc_id = None
    if evidence is not None:
        saved = files.save_file(
            db,
            tenant or db.info.get("slug") or "",
            evidence.data,
            filename=evidence.filename,
            content_type=evidence.content_type,
            doc_type="payment_evidence",
            uploaded_by=user_id,
            linked_type="payment",
            linked_id=payment_no,
            commit=False,
        )
        doc_id = saved.id
    db.execute(
        text(
            "update payment set status = :st, utr = coalesce(:u, utr), value_date = :d, recorded_by = :by, "
            "recorded_at = now(), evidence_document_id = :e where no = :n"
        ),
        {
            "st": RECORDED,
            "u": (utr or "").strip() or None,
            "d": value_date,
            "by": str(user_id),
            "e": doc_id,
            "n": payment_no,
        },
    )
    if pay["type"] == "advance":  # the tax on an advance is booked when it is paid (or recorded, whichever first)
        if not db.execute(text("select 1 from tds_entry where payment_no = :p"), {"p": payment_no}).first():
            tds.propose_advance(db, payment_no)
    audit.record(
        db, user(user_id), "payment.record", "payment", payment_no,
        after={"utr": (utr or "").strip() or None, "value_date": value_date, "evidence": doc_id},
    )  # fmt: skip
    if utr:
        _remit(db, payment_no, value_date)
    emails.commit_and_send(db)
    return {"status": RECORDED, "payment": payment_no}


# ---- settle ----------------------------------------------------------------------------------


def _line_for(db: Session, line_id: int) -> dict[str, Any]:
    row = db.execute(text("select * from bank_statement_line where id = :i"), {"i": line_id}).mappings().first()
    if row is None:
        raise InvoiceError(f"bank line {line_id} does not exist")
    return dict(row)


def settle_by_line(
    db: Session, payment_no: str, line_id: int, user_id: int, *, tenant: str | None = None, how: str = "line"
) -> dict[str, Any]:
    """A debit line of the bank's statement settles its payment: amounts to the paisa, date not in the future."""
    line = _line_for(db, line_id)
    return _settle(db, payment_no, user_id, line=line, tenant=tenant, how=how)


def _settle(
    db: Session,
    payment_no: str,
    user_id: int,
    *,
    line: dict[str, Any] | None,
    tenant: str | None,
    how: str,
) -> dict[str, Any]:
    pay = db.execute(text("select * from payment where no = :n for update"), {"n": payment_no}).mappings().first()
    if pay is None:
        raise InvoiceError(f"payment {payment_no} does not exist")
    if pay["status"] not in ("in_file", RECORDED):
        raise InvoiceError(f"{payment_no} is {pay['status']}; only a payment in the bank file settles")
    if line is not None:
        if line["direction"] != "debit":
            raise InvoiceError("a credit line is not a payment")
        if D(line["amount"]) != D(pay["amount"]):
            raise InvoiceError(
                f"the amounts differ: the line is {line['amount']}, payment {payment_no} is {pay['amount']}"
            )
        if line["value_date"] is None or line["value_date"] > business_today():
            raise InvoiceError("the line's date cannot be read or has not come yet")
        if pay["utr"] and line["utr"] and str(pay["utr"]).upper() != str(line["utr"]).upper():
            raise InvoiceError("UTR differs from the one recorded")
        _recorder_ok(db, payment_no, user_id)
        db.execute(
            text("update bank_statement_line set status = 'matched', payment_no = :p, why = null where id = :i"),
            {"p": payment_no, "i": line["id"]},
        )
        db.execute(
            text(
                "update payment set status = 'settled', settled_line_id = :l, settled_at = now(), "
                "utr = coalesce(utr, :u), value_date = :d, paid_on = :d where no = :n"
            ),
            {"l": line["id"], "u": line["utr"], "d": line["value_date"], "n": payment_no},
        )
    today = pay["run_date"] if line is None else line["value_date"]
    _on_settle(db, payment_no, user_id, today, how, tenant)
    emails.commit_and_send(db)
    return {"payment": payment_no, "status": "settled", "value_date": today}


def settle_zero(db: Session, payment_no: str, actor: Actor) -> None:
    """A payment of nothing (advances or notes cover it) is settled when the run's file is made (12.3.4)."""
    pay = db.execute(text("select p.*, r.run_date from payment p join payment_run r on r.no = p.run_no where p.no = :n"), {"n": payment_no}).mappings().first()  # fmt: skip
    if pay is None or D(pay["amount"]) != 0 or pay["status"] not in ("in_file", "planned"):
        return
    user_id = int(actor.id) if actor.kind == "user" else None
    db.execute(
        text("update payment set status = 'settled', value_date = :d, settled_at = now() where no = :n"),
        {"d": pay["run_date"], "n": payment_no},
    )
    _on_settle(db, payment_no, user_id, pay["run_date"], "nothing to pay", None)


def _on_settle(db: Session, payment_no: str, user_id: int | None, on: date, how: str, tenant: str | None) -> None:
    """The bank has paid: the invoices, the notes, the budget, the case and any advance follow."""
    pay = db.execute(text("select * from payment where no = :n"), {"n": payment_no}).mappings().one()
    actor = user(user_id) if user_id else audit.SYSTEM
    closed: list[str] = []
    for a in db.execute(
        text("select a.invoice_no, a.amount, a.discount, i.type, i.total, i.offset_used from payment_allocation a "
             "join invoice i on i.no = a.invoice_no where a.payment_no = :p order by a.invoice_no"),
        {"p": payment_no},
    ).mappings():  # fmt: skip
        if a["type"] == "invoice":
            db.execute(
                text(
                    "update invoice set status = 'paid', paid_on = :d, discount_taken = :x where no = :i and status = 'approved'"
                ),
                {"d": on, "x": a["discount"], "i": a["invoice_no"]},
            )
            budget_ledger.post_payment(db, str(a["invoice_no"]), actor, on)
            closed += payment.close_case(db, str(a["invoice_no"]), user_id or 0)
        else:
            used = D(a["offset_used"]) + (-D(a["amount"]))
            full = used >= D(a["total"])
            db.execute(
                text(
                    "update invoice set offset_used = :u, status = case when :full then 'paid' else status end where no = :i"
                ),
                {"u": used, "full": full, "i": a["invoice_no"]},
            )
            if full:
                from p2p.invoices import notes  # noqa: PLC0415 - notes builds on this module

                notes.settle_note(db, str(a["invoice_no"]), user_id or 0)
    if pay["type"] == "advance":
        db.execute(
            text("update advance set paid_on = :d, settled_on = :d where payment_no = :p"), {"d": on, "p": payment_no}
        )
        req = db.execute(text("select request_id from advance where payment_no = :p"), {"p": payment_no}).scalar()
        db.execute(text("update advance_request set status = 'paid' where id = :i"), {"i": req})
        if not db.execute(text("select 1 from tds_entry where payment_no = :p"), {"p": payment_no}).first():
            tds.propose_advance(db, payment_no)
        from p2p.invoices import advances  # noqa: PLC0415 - advances builds on this module

        advances.use_against_invoices(db, payment_no)
    audit.record(
        db, actor, "payment.settle", "payment", payment_no,
        after={"how": how, "value_date": on, "utr": pay["utr"]},
    )  # fmt: skip
    _run_done(db, str(pay["run_no"]))
    if pay["remittance_sent_at"] is None:
        _remit(db, payment_no, on)
    for po_no in dict.fromkeys(closed):
        payment.notify_closed(db, tenant, po_no)


def _run_done(db: Session, run_no: str) -> None:
    left = db.execute(
        text(
            "select count(*) from payment where run_no = :r and status in ('planned', 'approved', 'in_file', 'recorded')"
        ),
        {"r": run_no},
    ).scalar_one()
    settled = db.execute(
        text("select count(*) from payment where run_no = :r and status = 'settled'"), {"r": run_no}
    ).scalar_one()
    if int(left) == 0 and int(settled) > 0:
        db.execute(
            text("update payment_run set status = 'paid' where no = :r and status <> 'cancelled'"), {"r": run_no}
        )


# ---- failure and reversal ---------------------------------------------------------------------


def failed(
    db: Session,
    payment_no: str,
    user_id: int,
    *,
    reason: str,
    value_date: date,
    bank_line_id: int | None = None,
    evidence: Evidence | None = None,
    tenant: str | None = None,
) -> dict[str, Any]:
    """The bank returned the payment: its invoices are open and due again, and someone is asked to pay again."""
    if not reason.strip():
        raise InvoiceError("a reason is needed")
    if value_date > business_today():
        raise InvoiceError("the value date cannot be in the future")
    if bank_line_id is None and evidence is None:
        raise InvoiceError("give the bank's credit line or a proof of the return")
    pay = db.execute(text("select * from payment where no = :n for update"), {"n": payment_no}).mappings().first()
    if pay is None:
        raise InvoiceError(f"payment {payment_no} does not exist")
    if pay["status"] not in ("in_file", RECORDED):
        raise InvoiceError(f"{payment_no} is {pay['status']}; only a payment the bank has not settled can fail")
    _recorder_ok(db, payment_no, user_id)
    doc_id = None
    if evidence is not None:
        saved = files.save_file(
            db, tenant or db.info.get("slug") or "", evidence.data, filename=evidence.filename,
            content_type=evidence.content_type, doc_type="payment_evidence", uploaded_by=user_id,
            linked_type="payment", linked_id=payment_no, commit=False,
        )  # fmt: skip
        doc_id = saved.id
    if bank_line_id is not None:
        line = _line_for(db, bank_line_id)
        if line["direction"] != "credit":
            raise InvoiceError("a returned payment is a credit line on the bank statement")
    db.execute(
        text(
            "update payment set status = 'failed', failed_by = :by, failed_at = now(), fail_reason = :r, "
            "fail_line_id = :l, evidence_document_id = coalesce(:e, evidence_document_id) where no = :n"
        ),
        {"by": str(user_id), "r": reason.strip(), "l": bank_line_id, "e": doc_id, "n": payment_no},
    )
    invoices = _reopen(db, payment_no)
    if pay["type"] == "advance":
        request_id = db.execute(
            text("select request_id from advance where payment_no = :p"), {"p": payment_no}
        ).scalar()
        db.execute(text("delete from advance where payment_no = :p"), {"p": payment_no})
        tds.remove_advance(db, payment_no)
        db.execute(
            text("update advance_request set status = 'approved', payment_no = null where id = :i"), {"i": request_id}
        )
    supplier = db.execute(
        text("select coalesce(trade_name, legal_name) from supplier where code = :c"), {"c": pay["supplier_code"]}
    ).scalar()
    audit.record(
        db, user(user_id), "payment.fail", "payment", payment_no,
        after={"reason": reason.strip(), "value_date": value_date, "invoices": invoices, "line": bank_line_id},
    )  # fmt: skip
    _return_notice(db, payment_no, reason.strip())
    if tenant:
        tasks.create_task(
            db, tenant, kind="payment_failed",
            title=f"Pay {supplier} again: {reason.strip()}", assignee=users.first_with_role(db, "Finance", "AP"),
            sla_hours=24, payload={"payment": payment_no, "supplier": pay["supplier_code"], "reason": reason.strip()},
            actor=user(user_id),
        )  # fmt: skip
    _run_done(db, str(pay["run_no"]))
    _return_notice(db, payment_no, reason.strip())
    emails.commit_and_send(db)
    return {"status": "failed", "payment": payment_no, "invoices": invoices}


def _reopen(db: Session, payment_no: str) -> list[str]:
    out = []
    for r in db.execute(
        text(
            "select a.invoice_no from payment_allocation a join invoice i on i.no = a.invoice_no "
            "where a.payment_no = :p and i.type = 'invoice'"
        ),
        {"p": payment_no},
    ):
        db.execute(
            text(
                "update invoice set status = 'approved', paid_on = null, discount_taken = 0 "
                "where no = :n and status = 'paid'"
            ),
            {"n": r[0]},
        )
        db.execute(text("update invoice set in_run = null where no = :n"), {"n": r[0]})
        out.append(str(r[0]))
    return out


def reversed(
    db: Session,
    payment_no: str,
    user_id: int,
    *,
    reason: str,
    bank_line_id: int | None = None,
    evidence: Evidence | None = None,
) -> dict[str, Any]:
    """A settlement is undone: the invoices are open, the budget is taken back, the closed order lines reopen."""
    if not reason.strip():
        raise InvoiceError("a reason is needed")
    pay = db.execute(text("select * from payment where no = :n for update"), {"n": payment_no}).mappings().first()
    if pay is None:
        raise InvoiceError(f"payment {payment_no} does not exist")
    if pay["status"] != "settled":
        raise InvoiceError(f"{payment_no} is {pay['status']}; only a settled payment is reversed")
    _recorder_ok(db, payment_no, user_id)
    if bank_line_id is None and evidence is None:
        raise InvoiceError("give the bank's line or a proof of the reversal")
    doc_id = None
    if evidence is not None:
        saved = files.save_file(
            db, db.info.get("slug") or "", evidence.data, filename=evidence.filename,
            content_type=evidence.content_type, doc_type="payment_evidence", uploaded_by=user_id,
            linked_type="payment", linked_id=payment_no, commit=False,
        )  # fmt: skip
        doc_id = saved.id
    if bank_line_id is not None:
        line = _line_for(db, bank_line_id)
        if line["direction"] != "credit":
            raise InvoiceError("a reversed payment is a credit line on the bank statement")
    if pay["type"] == "advance":
        used = (
            db.execute(
                text(
                    "select i.no from advance_adjustment a join invoice i on i.no = a.invoice_no "
                    "where a.advance_payment_no = :p and a.amount > 0"
                ),
                {"p": payment_no},
            )
            .scalars()
            .all()
        )
        if used:
            raise InvoiceError(
                f"the advance was taken off {', '.join(str(n) for n in used)}: raise a debit note to recover it"
            )
    db.execute(
        text(
            "update payment set status = 'reversed', fail_reason = :r, failed_by = :by, failed_at = now(), "
            "fail_line_id = :l, evidence_document_id = coalesce(:e, evidence_document_id) where no = :n"
        ),
        {"r": reason.strip(), "by": str(user_id), "l": bank_line_id, "e": doc_id, "n": payment_no},
    )
    invoices = _reopen(db, payment_no)
    actor = user(user_id)
    for a in db.execute(
        text("select a.invoice_no, i.type from payment_allocation a join invoice i on i.no = a.invoice_no "
             "where a.payment_no = :p and i.type = 'invoice'"), {"p": payment_no}
    ):  # fmt: skip
        budget_ledger.reverse_payment(db, str(a[0]), actor, business_today())
        _reopen_order(db, str(a[0]), actor)
    if pay["type"] == "advance":
        request_id = db.execute(
            text("select request_id from advance where payment_no = :p"), {"p": payment_no}
        ).scalar()
        db.execute(
            text("update advance set paid_on = null, settled_on = null where payment_no = :p"), {"p": payment_no}
        )
        db.execute(text("update advance_request set status = 'approved' where id = :i"), {"i": request_id})
        tds.remove_advance(db, payment_no)
    audit.record(
        db, actor, "payment.reverse", "payment", payment_no, after={"reason": reason.strip(), "invoices": invoices}
    )
    db.commit()
    return {"status": "reversed", "payment": payment_no, "invoices": invoices}


def _reopen_order(db: Session, invoice_no: str, actor: Actor) -> None:
    """The order lines this invoice closed are open again at what was received.

    # NEEDS block 13: orders/closure.reopen replaces this on reversal
    """
    for po_no, line_no in db.execute(
        text("select distinct po_no, po_line_no from invoice_line where invoice_no = :n"), {"n": invoice_no}
    ).all():
        line = db.execute(
            text("select qty, received_qty, status from po_line where po_no = :p and line_no = :l for update"),
            {"p": po_no, "l": line_no},
        ).first()
        if line is None or line[2] != "closed":
            continue
        status = "received" if D(line[1]) >= D(line[0]) else ("part_received" if D(line[1]) > 0 else "open")
        db.execute(
            text("update po_line set status = :s where po_no = :p and line_no = :l"),
            {"s": status, "p": po_no, "l": line_no},
        )
        still = db.execute(
            text("select count(*) from po_line where po_no = :p and status = 'closed'"), {"p": po_no}
        ).scalar_one()
        if int(still) == 0:
            db.execute(text("update po set status = 'received' where no = :p and status = 'closed'"), {"p": po_no})
            audit.record(db, actor, "po.reopen", "po", str(po_no), after={"status": "received"})


# ---- the supplier is told ---------------------------------------------------------------------


def _advice_contact(db: Session, supplier: str) -> tuple[str, str | None] | None:
    row = db.execute(
        text(
            "select email, name from supplier_contact where supplier_code = :c and email is not null "
            "order by (role = 'accounts') desc, is_primary desc nulls last, id limit 1"
        ),
        {"c": supplier},
    ).first()
    return (str(row[0]), row[1]) if row else None


def _remit(db: Session, payment_no: str, on: date) -> None:
    """The payment advice: fixed words, one line per invoice and note, never a reference nobody gave (12.9)."""
    pay = db.execute(text("select * from payment where no = :n"), {"n": payment_no}).mappings().one()
    contact = _advice_contact(db, str(pay["supplier_code"]))
    if contact is None:
        return
    company = db.execute(text("select coalesce(short_name, legal_name) from company limit 1")).scalar() or "Our company"
    link = pay["utr"] or pay["no"]
    body = (
        f"Payment advice {pay['no']}. {company} has paid {format_inr(pay['amount'])} on {on:%d %b %Y}. "
        f"Bank reference: {link}."
    )
    if pay["type"] == "advance":
        body += f"\nAdvance against order {pay['po_no']}."
    for a in db.execute(
        text(
            "select i.no, i.supplier_invoice_no, i.invoice_date, i.type, a.amount, a.discount, i.reason "
            "from payment_allocation a join invoice i on i.no = a.invoice_no where a.payment_no = :p "
            "order by i.type, i.no"
        ),
        {"p": payment_no},
    ).mappings():
        if D(a["amount"]) < 0:
            what = "Your credit note" if a["type"] == "credit_note" else "Our debit note"
            extra = f" ({a['reason']})" if a["reason"] else ""
            body += f"\n{what} {a['supplier_invoice_no']}{extra}: -{format_inr(-D(a['amount']))}"
    total_tds = (
        db.execute(
            text("select coalesce(sum(amount), 0) from tds_entry where payment_no = :p"), {"p": payment_no}
        ).scalar_one()
        if pay["type"] == "advance"
        else D(pay["tds_amount"])
    )
    if D(total_tds):
        body += f"\nTax deducted at source: {format_inr(total_tds)}"
    if D(pay["msme_interest"]):
        body += f"\nInterest to a small supplier: {format_inr(pay['msme_interest'])}"
    body += (
        "\n\nInvoices covered (our number, date, total, tax held, advance taken off, discount):\n"
        if pay["type"] != "advance"
        else "\n"
    )
    for a in db.execute(
        text(
            "select i.supplier_invoice_no, i.invoice_date, i.total, i.tds_amount, i.advance_used, a.discount "
            "from payment_allocation a join invoice i on i.no = a.invoice_no "
            "where a.payment_no = :p and i.type = 'invoice' order by i.no"
        ),
        {"p": payment_no},
    ).mappings():
        body += (
            f"{a['supplier_invoice_no']} dated {a['invoice_date']:%d %b %Y}: {format_inr(a['total'])}, "
            f"TDS {format_inr(a['tds_amount'])}, advance taken off {format_inr(a['advance_used'])}, "
            f"discount {format_inr(a['discount'])}\n"
        )
    body += f"\nTotal paid: {format_inr(pay['amount'])}\n"
    emails.send_linked_email(
        db, contact[0], f"Payment advice {pay['no']}", body, linked_type="payment", linked_ref=payment_no
    )
    db.execute(
        text("update payment set remittance_sent_at = :n where no = :p"), {"n": datetime.now(UTC), "p": payment_no}
    )
    audit.record(db, audit.SYSTEM, "payment.remit", "payment", payment_no, after={"to": contact[0], "ref": link})
    # the caller commits; the outbox sends it then (platform/emails.commit_and_send or the email job)
    db.info.setdefault("queued_mail", []).append(payment_no)


def _return_notice(db: Session, payment_no: str, reason: str) -> None:
    """Told only when an advice went before: the payment came back and the invoices are open again."""
    pay = db.execute(text("select * from payment where no = :n"), {"n": payment_no}).mappings().one()
    if pay["remittance_sent_at"] is None:
        return
    contact = _advice_contact(db, str(pay["supplier_code"]))
    if contact is None:
        return
    on = pay["value_date"] or pay["run_date"]
    body = (
        f"Our payment {pay['no']} of {format_inr(pay['amount'])} on {on:%d %b %Y} "
        f"(reference {pay['utr'] or pay['no']}) was returned by the bank: {reason}. "
        "The invoices are open and will be paid again."
    )
    emails.send_linked_email(
        db, contact[0], f"Payment {pay['no']} was returned", body, linked_type="payment", linked_ref=payment_no
    )
    audit.record(db, audit.SYSTEM, "payment.remit", "payment", payment_no, after={"returned": True, "reason": reason})
