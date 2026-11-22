"""Sending an approved order to the supplier, and recording that the supplier acknowledged it.

An order goes out only when it is approved, not held, and the supplier is still active with good documents. By default a
buyer clicks Send (the setting 'po_send_requires_confirmation'); when a company turns that off, the worker sends the order
as soon as it is approved, unless the kill switch is on. The acknowledgement carries the date the supplier promises, which
is copied to the open lines and checked against the date the goods are needed. The email carries a signed link where the
supplier accepts, declines or asks for a change (orders/supplier_links.py, orders/confirmation.py), and sending an order
starts the daily delivery reminders (orders/delivery_reminders.py).
"""

import logging
import smtplib
from datetime import UTC, date, datetime, timedelta
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from p2p.formulas.calendar import IST, business_today
from p2p.formulas.money import format_inr, format_qty
from p2p.orders import delivery_reminders, document, supplier_links
from p2p.platform import audit, emails, reference, routing, worker
from p2p.platform.sod import Actor
from p2p.sourcing import documents

log = logging.getLogger(__name__)
SERVICE = Actor("service", "po-dispatch")
JOB = "send_po"


class DispatchError(Exception):
    pass


def _setting(db: Session, key: str, default: str) -> str:
    value = db.execute(text("select value from org_setting where key = :k"), {"k": key}).scalar()
    return str(value) if value not in (None, "") else default


def requires_confirmation(db: Session) -> bool:
    return _setting(db, "po_send_requires_confirmation", "true") != "false"


def _body(
    po: dict[str, Any],
    lines: list[dict[str, Any]],
    charges: list[dict[str, Any]],
    contact: str | None,
    company: str,
    ship_to: str,
    link: str | None = None,
) -> str:
    rows = "\n".join(
        f"{n:>3}. {ln['description']}"
        + (f" ({ln['item_code']})" if ln["item_code"] else "")
        + f" - {format_qty(ln['qty'])} {ln['uom'] or ''} at {format_inr(ln['unit_price'])}, GST {format_qty(ln['gst_rate'])}%"
        + (f", needed by {ln['need_by']:%d %b %Y}" if ln["need_by"] else "")
        for n, ln in enumerate(lines, start=1)
    )
    extra = "".join(
        f"\n     {'Freight' if c['type'] == 'freight' else 'Packing and forwarding'}: {format_inr(c['amount'])}"
        for c in charges
    )
    tax = po["cgst"] + po["sgst"] + po["igst"]
    return (
        f"Dear {contact or 'Sir or Madam'},\n\n"
        f"{company} places the purchase order below with you.\n\n"
        f"Order: {po['no']}\n"
        f"Deliver to: {ship_to}\n"
        f"Payment terms: {po['payment_terms_code'] or 'as agreed'}\n"
        + (f"Delivery terms: {po['delivery_terms']}\n" if po["delivery_terms"] else "")
        + f"\n{rows}{extra}\n\n"
        f"Total before tax: {format_inr(po['taxable'])}\n"
        f"GST: {format_inr(tax)}\n"
        f"Order total: {format_inr(po['total'])}\n\n"
        "Please acknowledge this order by replying to this email, keeping the subject line, with the date you will deliver.\n\n"
        + (f"Or accept it, decline it or ask for a change here (the link works once):\n{link}\n\n" if link else "")
        + f"Regards,\nProcurement, {company}\n"
    )


def send_po(db: Session, po_no: str, actor: Actor = SERVICE, *, tenant: str | None = None) -> str:
    """Email the order to the supplier's main contact and mark it sent. Returns the address it went to. With the tenant,
    the email carries the supplier's link to answer it, and the delivery reminders are scheduled."""
    po = db.execute(text("select * from po where no = :n for update"), {"n": po_no}).mappings().first()
    if po is None:
        raise DispatchError(f"{po_no} does not exist")
    if po["status"] != "approved":
        raise DispatchError(f"{po_no} is {po['status']}; only an approved order can be sent")
    if po["hold_reason"]:
        raise DispatchError(f"{po_no} is on hold: {po['hold_reason']}")
    supplier = db.execute(
        text("select status, coalesce(trade_name, legal_name) from supplier where code = :c"),
        {"c": po["supplier_code"]},
    ).one()
    if supplier[0] != "active":
        db.rollback()
        audit.record(
            db, actor, "po.issue_refused", "po", po_no, after={"supplier": po["supplier_code"], "status": supplier[0]},
            reason="the order is not sent to a supplier that is not active",
        )  # fmt: skip
        db.commit()
        raise DispatchError(f"the supplier {po['supplier_code']} is {supplier[0]}, so the order cannot be sent")
    check = documents.check_supplier(db, po["supplier_code"], business_today())
    if not check.ok:
        raise DispatchError("the supplier cannot be used: " + "; ".join(check.blocking))
    contact = db.execute(
        text(
            "select email, name from supplier_contact where supplier_code = :c and email is not null "
            "order by is_primary desc nulls last, id limit 1"
        ),
        {"c": po["supplier_code"]},
    ).first()
    if contact is None:
        raise DispatchError(f"the supplier {po['supplier_code']} has no email address on file")
    lines = [
        dict(r)
        for r in db.execute(
            text("select * from po_line where po_no = :n and status <> 'cancelled' order by line_no"), {"n": po_no}
        ).mappings()
    ]
    charges = [
        dict(r)
        for r in db.execute(text("select * from po_charge where po_no = :n order by id"), {"n": po_no}).mappings()
    ]
    company = db.execute(text("select coalesce(short_name, legal_name) from company limit 1")).scalar() or "Our company"
    name, address = document.delivery_place(db, po["ship_to_location"])
    ship_to = f"{name} ({po['ship_to_location']})" + (f", {address}" if address else "")
    try:
        pdf = [(f"{po_no}.pdf", "application/pdf", document.order_document(db, po_no))]
    except Exception:  # noqa: BLE001 - the order still goes out as text; a fault in the document must not stop it
        log.warning("the document of %s could not be made; sending the order as text only", po_no, exc_info=True)
        db.rollback()
        po = db.execute(text("select * from po where no = :n for update"), {"n": po_no}).mappings().one()
        pdf = []
    version = f" (version {po['version']})" if int(po["version"]) > 1 else ""
    link = supplier_links.url(supplier_links.issue(db, tenant, po_no, "respond")) if tenant else None
    try:
        emails.send_linked_email(
            db,
            contact[0],
            f"Purchase order {po_no}{version}",
            _body(dict(po), lines, charges, contact[1], str(company), str(ship_to), link),
            linked_type="po",
            linked_ref=po_no,
            attachments=pdf or None,
        )
    except (OSError, smtplib.SMTPException) as exc:
        db.rollback()
        raise DispatchError(f"the email to {contact[0]} failed: {exc}") from None
    db.execute(
        text("update po set status = 'sent', sent_at = now(), sent_to = :t where no = :n"),
        {"t": contact[0], "n": po_no},
    )
    audit.record(
        db, actor, "po.send", "po", po_no, before={"status": "approved"}, after={"status": "sent", "to": contact[0]}
    )
    db.commit()
    if tenant:
        delivery_reminders.schedule(tenant)
    return str(contact[0])


def acknowledge(
    db: Session,
    po_no: str,
    actor: Actor,
    *,
    promised: date | None,
    note: str | None,
    today: date | None = None,
    tenant: str | None = None,
) -> list[str]:
    """Record that the supplier confirmed the order. Returns warnings (a promised date later than the need-by date)."""
    today = today or business_today()
    po = db.execute(text("select status from po where no = :n for update"), {"n": po_no}).first()
    if po is None:
        raise DispatchError(f"{po_no} does not exist")
    if po[0] != "sent":
        raise DispatchError(f"{po_no} is {po[0]}; only a sent order can be acknowledged")
    slip = int(_setting(db, "supplier_date_days", "3"))
    warnings: list[str] = []
    if promised is not None:
        for line in db.execute(
            text(
                "select line_no, description, need_by from po_line where po_no = :n and status = 'open' order by line_no"
            ),
            {"n": po_no},
        ):
            if line[2] is not None and promised > line[2] + timedelta(days=slip):
                warnings.append(
                    f"line {line[0]} ({line[1]}): promised {promised:%d %b %Y}, needed by {line[2]:%d %b %Y}"
                )
        db.execute(
            text("update po_line set promised_date = :d where po_no = :n and status = 'open'"),
            {"d": promised, "n": po_no},
        )
    db.execute(
        text("update po set status = 'acknowledged', acknowledged_at = now(), ack_note = :note where no = :n"),
        {"note": (note or "").strip() or None, "n": po_no},
    )
    audit.record(
        db,
        actor,
        "po.acknowledge",
        "po",
        po_no,
        before={"status": "sent"},
        after={"status": "acknowledged", "promised": promised, "warnings": warnings},
    )
    db.commit()
    from p2p.requests import material  # noqa: PLC0415 - material requests sit above orders

    material.after_event(db, tenant, po_no=po_no, why=f"{po_no} was acknowledged")  # a promise may come too late
    return warnings


def ack_overdue(db: Session, sent_at: datetime | None, today: date) -> bool:
    """A sent order the supplier has not acknowledged within `supplier_ack_due_days` working days."""
    if sent_at is None:
        return False
    days = int(_setting(db, "supplier_ack_due_days", "2"))
    sent_on = sent_at.astimezone(IST).date()
    return today > reference.calendar_for(db).add_working_days(sent_on, days)


@worker.register(JOB)
def _auto_send(ctx: worker.JobContext) -> None:
    with Session(routing.engine_for(ctx.tenant)) as db:
        if requires_confirmation(db):
            return  # the company wants a person to send orders
        if db.execute(text("select kill_switch from autonomy_global where id = 1")).scalar_one():
            return
        try:
            send_po(db, str(ctx.payload["po_no"]), tenant=ctx.tenant)
        except DispatchError as exc:
            db.rollback()
            log.info("order %s was not sent: %s", ctx.payload.get("po_no"), exc)


def queue_if_automatic(db: Session, tenant: str, po_no: str) -> None:
    """Called when an order has just been approved: queue the send if the company has turned confirmation off."""
    if not requires_confirmation(db):
        worker.enqueue(tenant, JOB, {"po_no": po_no}, run_at=datetime.now(UTC))
