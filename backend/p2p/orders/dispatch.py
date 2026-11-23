"""Sending an approved order to the supplier, and recording that the supplier acknowledged it.

An order goes out only when it is approved, not held, and the supplier is still active with good documents. By default a
buyer clicks Send (the setting 'po_send_requires_confirmation'); when a company turns that off, the worker sends the order
as soon as it is approved, unless the kill switch is on. The email goes to the supplier's order contact and carries a
signed link where the supplier accepts, declines or asks for a change with a one-time code sent to that address
(orders/supplier_links.py, orders/confirmation.py); sending an order starts the daily delivery reminders
(orders/delivery_reminders.py).

Each mailing is of one version: its document is stored once and attached as stored (orders/versions.py), and its link
answers that version only. An acknowledgement is of one version: one for a version the order has moved on from is
refused (DF-07), and a revision voids the acknowledgement of the version before it. It carries a date per open line
(none given: the line's own date), each checked (formulas/deliveries.check_promised), written to the line and kept as a
commitment; the version's row keeps how it came (link and code, email and its sender check, the buyer's note).
`acknowledge` is the one service that records an acknowledgement, whatever the way.
"""

import json
import logging
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from p2p.formulas import deliveries
from p2p.formulas.calendar import IST, BusinessCalendar, business_today
from p2p.formulas.money import format_inr, format_qty
from p2p.formulas.words import count
from p2p.invoices import tolerances
from p2p.masters import suppliers as supplier_records
from p2p.orders import delivery_reminders, document, supplier_links, versions
from p2p.platform import audit, emails, outbox, reference, routing, worker
from p2p.platform.sod import Actor
from p2p.sourcing import documents

log = logging.getLogger(__name__)
SERVICE = Actor("service", "po-dispatch")
JOB = "send_po"


class DispatchError(Exception):
    def __init__(self, message: str, status: int = 409) -> None:
        super().__init__(message)
        self.status = status


SOURCE = {"link": "ack_link", "email": "ack_email", "app": "ack_app"}


@dataclass(frozen=True)
class AckDetail:
    """How an acknowledgement came, kept on its version's row (D53): the contact's address, the link and code used, the
    email and its sender check, the supplier's changes an accepted answer was made of."""

    address: str | None = None
    link_id: int | None = None
    code_id: int | None = None
    email_id: int | None = None
    sender_auth: dict[str, Any] | None = None
    change_ids: tuple[int, ...] = ()


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
        + (
            "Or accept it, decline it or ask for a change here (the link works once and asks for a code we email to "
            f"you):\n{link}\n\n"
            if link
            else ""
        )
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
    unapproved = db.execute(
        text("select max(version) from po_revision where po_no = :n and reapproval and version > coalesce(:a, 0)"),
        {"n": po_no, "a": po["approved_version"]},
    ).scalar()
    if unapproved is not None:  # a safety net: revise already sends such an order back to approval (DF-07)
        raise DispatchError(f"version {unapproved} changed materially after approval: it must be approved first")
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
    contact = supplier_records.order_contact(db, po["supplier_code"])
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
    issued = versions.issue_document(db, tenant or outbox.tenant_of(db), po_no)
    if issued is None:  # sent as text; the document's failure is audited, and the order is locked again
        po = db.execute(text("select * from po where no = :n for update"), {"n": po_no}).mappings().one()
    version = f" (version {po['version']})" if int(po["version"]) > 1 else ""
    link = (
        supplier_links.url(supplier_links.issue(db, tenant, po_no, "respond", sent_to=contact[0])) if tenant else None
    )
    message_id = emails.send_linked_email(
        db,
        contact[0],
        f"Purchase order {po_no}{version}",
        _body(dict(po), lines, charges, contact[1], str(company), str(ship_to), link),
        linked_type="po",
        linked_ref=po_no,
        document_ids=[issued.document_id] if issued else None,
    )
    versions.mail_carries(db, message_id, int(po["version"]))
    db.execute(
        text("update po set status = 'sent', sent_at = now(), sent_to = :t where no = :n"),
        {"t": contact[0], "n": po_no},
    )
    versions.mark_sent(db, po_no, int(po["version"]), str(contact[0]))
    audit.record(
        db, actor, "po.send", "po", po_no, before={"status": "approved"}, after={"status": "sent", "to": contact[0]}
    )
    emails.commit_and_send(db)  # the order is sent with its mail; a refused mail is retried by the email job
    if tenant:
        delivery_reminders.schedule(tenant)
    return str(contact[0])


def date_slip(db: Session, on: date) -> int:
    """The later delivery date accepted with an acceptance, in calendar days: the tolerance version in force."""
    try:
        return int(tolerances.in_force(db, on).decimal("supplier_date_days", "3"))
    except tolerances.ToleranceError:
        return int(_setting(db, "supplier_date_days", "3"))


def ship_to_calendar(db: Session, po_no: str) -> tuple[BusinessCalendar, str]:
    """The working days of the place the order delivers to, and its name."""
    code = db.execute(text("select ship_to_location from po where no = :n"), {"n": po_no}).scalar()
    return reference.calendar_for_location(db, code), document.delivery_place(db, str(code))[0]


def line_dates(
    db: Session, po_no: str, promised: Mapping[int, date | None] | None, today: date, *, by_buyer: bool = False
) -> tuple[dict[int, date | None], list[str]]:
    """The date of each open line an acknowledgement records: the one given, else the line's own date (promised, else
    needed). A date given is checked against the date ordered, today and the delivery place's working days; the
    refusals raise DispatchError (422). The buyer recording what the supplier said (`by_buyer`) may give a day the place
    is closed: it is recorded and said back. Returns the dates and what the buyer is told (lines later than ordered, a
    closed day), in words."""
    lines = db.execute(
        text(
            "select line_no, description, need_by, promised_date from po_line where po_no = :n "
            "and status in ('open', 'part_received') order by line_no"
        ),
        {"n": po_no},
    ).all()
    given = dict(promised or {})
    unknown = sorted(set(given) - {int(ln[0]) for ln in lines})
    if unknown:
        raise DispatchError(f"line {unknown[0]} is not an open line of {po_no}", 422)
    calendar, place = ship_to_calendar(db, po_no)
    slip = date_slip(db, today)
    dates: dict[int, date | None] = {}
    refused: list[str] = []
    later: list[str] = []
    for line_no, description, need_by, was in lines:
        ordered, _ = deliveries.line_due(was, need_by)
        day = given.get(int(line_no)) or ordered
        dates[int(line_no)] = day
        if day is None or day == ordered:  # the date we ordered is ours to keep, whatever day it falls on
            continue
        problem = deliveries.check_promised(
            int(line_no), ordered, day, slip, today, calendar, place, closed_ok=by_buyer
        )
        if problem:
            refused.append(problem)
            continue
        if not calendar.is_working_day(day):
            later.append(f"line {line_no} ({description}): {day:%d %b %Y} is not a working day at {place}")
        if ordered is not None and day > ordered:
            later.append(
                f"line {line_no} ({description}): {day:%d %b %Y}, {count((day - ordered).days, 'day')} later than "
                f"ordered ({ordered:%d %b %Y})"
            )
    if refused:
        raise DispatchError("; ".join(refused), 422)
    return dates, later


def acknowledge(
    db: Session,
    po_no: str,
    actor: Actor,
    *,
    version: int | None = None,
    via: str = "app",
    promised: Mapping[int, date | None] | None = None,
    note: str | None,
    today: date | None = None,
    tenant: str | None = None,
    detail: AckDetail | None = None,
) -> list[str]:
    """Record that the supplier confirmed `version` of the order (None: the version it is now, recorded as such), by
    its link, to a buyer in the app, or by email, with a date per open line (`promised`, line number to date; a line
    not given keeps its own date). Refused for any version but the current one (DF-07), for an order not waiting for an
    acknowledgement, and for a date that cannot be accepted (nothing is written). Commits. Returns the lines later than
    ordered, in words."""
    today = today or business_today()
    detail = detail or AckDetail()
    po = db.execute(
        text("select status, version, acknowledged_version from po where no = :n for update"), {"n": po_no}
    ).first()
    if po is None:
        raise DispatchError(f"{po_no} does not exist", 404)
    if via not in versions.VIA:
        db.rollback()
        raise DispatchError(f"an acknowledgement comes by link, app or email, not {via}", 422)
    version = int(po[1]) if version is None else version
    if version != int(po[1]):
        db.rollback()
        raise DispatchError(
            f"{po_no} has changed since that version: it is now version {po[1]}; the supplier answers the new version"
        )
    if po[0] != "sent":
        db.rollback()
        raise DispatchError(f"{po_no} is {po[0]}; only a sent order can be acknowledged")
    try:
        dates, later = line_dates(db, po_no, promised, today, by_buyer=via == "app")
    except DispatchError:
        db.rollback()
        raise
    note = (note or "").strip()[:500] or None
    ack_id = versions.mark_acknowledged(db, po_no, version, actor, via, detail=detail, note=note)
    for line_no, day in dates.items():
        if day is None:
            continue
        db.execute(
            text("update po_line set promised_date = :d where po_no = :n and line_no = :l"),
            {"d": day, "n": po_no, "l": line_no},
        )
        db.execute(
            text(
                "insert into po_commitment (po_no, po_line_no, promised_date, email_id, note, po_version, source, "
                "acknowledgement_id) values (:p, :l, :d, :e, :note, :v, :s, :a)"
            ),
            {
                "p": po_no, "l": line_no, "d": day, "e": detail.email_id, "note": f"acknowledged by {via}",
                "v": version, "s": SOURCE[via], "a": ack_id,
            },
        )  # fmt: skip
    db.execute(
        text(
            "update po set status = 'acknowledged', acknowledged_at = now(), acknowledged_version = :v, ack_note = :note "
            "where no = :n"
        ),
        {"v": version, "note": note, "n": po_no},
    )
    audit.record(
        db, actor, "po.acknowledge", "po", po_no, before={"status": "sent"},
        after={
            "status": "acknowledged", "version": version, "via": via, "by": detail.address or versions.made_by(actor),
            "code": detail.code_id, "email": detail.email_id, "lines": {str(k): v for k, v in dates.items()},
            "warnings": later, "changes": list(detail.change_ids),
        },
        reason=note,
    )  # fmt: skip
    db.commit()
    from p2p.requests import material  # noqa: PLC0415 - material requests sit above orders

    material.after_event(db, tenant, po_no=po_no, why=f"{po_no} was acknowledged")  # a promise may come too late
    close_ack_tasks(db, po_no, "acknowledged")
    return later


def close_ack_tasks(db: Session, po_no: str, why: str) -> None:
    """Tasks about this order's acknowledgement are done once it is acknowledged, revised or cancelled."""
    for (task_id,) in db.execute(
        text(
            "select id from task where kind in ('po_ack_overdue', 'supplier_ack_confirm') and status in ('open', 'overdue') "
            "and payload->>'po_no' = :n"
        ),
        {"n": po_no},
    ).all():
        db.execute(
            text("update task set payload = payload || cast(:p as jsonb) where id = :i"),
            {"p": json.dumps({"closed_because": why}), "i": task_id},
        )
        from p2p.platform import tasks  # noqa: PLC0415 - tasks notify, which this module need not load

        tasks.complete_task(db, int(task_id), None)


def ack_due_on(db: Session, po: Mapping[str, Any]) -> date | None:
    """The day the current version's acknowledgement falls due: `supplier_ack_due_days` working days on the delivery
    place's calendar after the version was emailed. None when it was not emailed."""
    sent = db.execute(
        text("select sent_at from po_version where po_no = :p and version = :v"), {"p": po["no"], "v": po["version"]}
    ).scalar()
    if sent is None:
        return None
    days = int(_setting(db, "supplier_ack_due_days", "2"))
    calendar = reference.calendar_for_location(db, po["ship_to_location"])
    due: date = calendar.add_working_days(sent.astimezone(IST).date(), days)
    return due


def waiting_ack(po: Mapping[str, Any]) -> bool:
    """With the supplier and its current version not acknowledged."""
    return bool(po["status"] == "sent" and po.get("acknowledged_version") != po["version"])


def ack_overdue(db: Session, po: Mapping[str, Any], today: date) -> bool:
    """A sent order whose current version the supplier has not acknowledged within `supplier_ack_due_days` working days
    of its sending (ship-to calendar)."""
    if not waiting_ack(po):
        return False
    due = ack_due_on(db, po)
    return due is not None and today > due


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
