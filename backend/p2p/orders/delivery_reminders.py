"""Reminders to suppliers about a delivery date: one before it, and after it while nothing has arrived.

For each order with something still to come, the open lines are grouped by their due date (the date the supplier
promised, else the date needed: formulas/deliveries.line_due); working days are those of the place the order delivers to. A group is reminded once 'delivery_reminder_before_days' working days before its date (not if the
supplier has already said it shipped everything), and when the date has passed and nothing of the group has arrived,
'delivery_overdue_after_days' working days after it and then every 'delivery_overdue_every_days' working days, at most
'delivery_overdue_max' times. A part delivery is chased for its balance by the supplier agent instead. Each reminder is a
row in delivery_reminder, written before the email goes, so it is sent and recorded at most once even if the job runs
twice; a date that moves (a new version) starts again. The autonomy kill switch stops them like every automatic message.
"""

import logging
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from p2p.formulas import deliveries
from p2p.formulas.calendar import IST
from p2p.formulas.money import format_qty
from p2p.masters import suppliers as supplier_records
from p2p.orders import supplier_links
from p2p.platform import audit, emails, reference, routing, worker
from p2p.platform.sod import Actor

log = logging.getLogger(__name__)
JOB = "delivery_reminders"
RUN_AT = time(9, 15)  # India time
ACTOR = Actor("service", "delivery-reminders")


@dataclass
class ReminderResult:
    sent: list[tuple[str, str, int]] = field(default_factory=list)  # (po_no, kind, number)
    failed: list[tuple[str, str]] = field(default_factory=list)
    stopped: str | None = None


def _setting(db: Session, key: str, default: str) -> int:
    value = db.execute(text("select value from org_setting where key = :k"), {"k": key}).scalar()
    return int(str(value)) if value not in (None, "") else int(default)


def _groups(db: Session) -> dict[tuple[str, date], list[dict[str, Any]]]:
    rows = db.execute(
        text(
            "select l.po_no, p.ship_to_location, l.line_no, l.description, l.uom, l.qty, l.received_qty, l.promised_date, "
            "l.need_by, "
            "coalesce((select sum(d.qty) from dispatch_note_line d join dispatch_note n on n.id = d.note_id "
            "where n.po_no = l.po_no and d.po_line_no = l.line_no), 0) as shipped "
            "from po_line l join po p on p.no = l.po_no where p.status in ('sent', 'acknowledged', 'part_received') "
            "and l.status in ('open', 'part_received') and l.received_qty < l.qty "
            "and coalesce(l.promised_date, l.need_by) is not null order by l.po_no, l.line_no"
        )
    ).mappings()
    groups: dict[tuple[str, date], list[dict[str, Any]]] = {}
    for r in rows:
        due, _ = deliveries.line_due(r["promised_date"], r["need_by"])
        if due is not None:
            groups.setdefault((str(r["po_no"]), due), []).append(dict(r))
    return groups


def _sent(db: Session, po_no: str, kind: str, due: date) -> int:
    return int(
        db.execute(
            text("select count(*) from delivery_reminder where po_no = :p and kind = :k and promised_date = :d"),
            {"p": po_no, "k": kind, "d": due},
        ).scalar_one()
    )


def due_reminders(db: Session, as_of: date) -> list[tuple[str, date, str, int, list[dict[str, Any]]]]:
    """What is due today: (order, date, kind, number, lines)."""
    calendars: dict[str, Any] = {}
    before = _setting(db, "delivery_reminder_before_days", "2")
    after, every, most = (
        _setting(db, "delivery_overdue_after_days", "1"),
        _setting(db, "delivery_overdue_every_days", "3"),
        _setting(db, "delivery_overdue_max", "3"),
    )
    due: list[tuple[str, date, str, int, list[dict[str, Any]]]] = []
    for (po_no, when), lines in _groups(db).items():
        place = str(lines[0]["ship_to_location"])
        if place not in calendars:
            calendars[place] = reference.calendar_for_location(db, place)
        calendar = calendars[place]
        all_shipped = all(Decimal(ln["shipped"]) + Decimal(ln["received_qty"]) >= Decimal(ln["qty"]) for ln in lines)
        if not all_shipped and deliveries.before_reminder_due(when, before, as_of, calendar):
            if _sent(db, po_no, "before", when) == 0:
                due.append((po_no, when, "before", 1, lines))
        nothing_arrived = all(Decimal(ln["received_qty"]) == 0 for ln in lines)
        if as_of > when and nothing_arrived:
            sent = _sent(db, po_no, "overdue", when)
            if deliveries.overdue_reminder_due(when, sent, after, every, most, as_of, calendar):
                due.append((po_no, when, "overdue", sent + 1, lines))
    return due


def _body(
    po_no: str, when: date, kind: str, number: int, lines: list[dict[str, Any]], contact: str | None, company: str,
    link: str | None,
) -> str:  # fmt: skip
    rows = "\n".join(
        f"  {ln['line_no']}. {ln['description']}: {format_qty(Decimal(ln['qty']) - Decimal(ln['received_qty']))} {ln['uom'] or ''}".rstrip()
        for ln in lines
    )
    if kind == "before":
        head = f"This is a reminder that the goods below on purchase order {po_no} are due on {when:%d %b %Y}."
        ask = "Please make sure they reach us by then, or tell us at once if they will be late."
    else:
        head = f"The goods below on purchase order {po_no} were due on {when:%d %b %Y} and have not arrived" + (
            f" (reminder {number})." if number > 1 else "."
        )
        ask = "Please tell us by return when they will be delivered."
    record = f"\n\nWhen you ship, please record the dispatch here:\n{link}" if link else ""
    return (
        f"Dear {contact or 'Sir or Madam'},\n\n{head}\n\n{rows}\n\n{ask}{record}\n\nRegards,\nProcurement, {company}\n"
    )


def run(
    db: Session, tenant: str | None = None, *, as_of: date, now: datetime | None = None, dry_run: bool = False
) -> ReminderResult:
    """Send every reminder due on the date. With a tenant, each email carries a link to record the dispatch."""
    result = ReminderResult()
    if db.execute(text("select kill_switch from autonomy_global where id = 1")).scalar_one():
        result.stopped = "the kill switch is on"
        return result
    company = db.execute(text("select coalesce(short_name, legal_name) from company limit 1")).scalar() or "Our company"
    for po_no, when, kind, number, lines in due_reminders(db, as_of):
        if dry_run:
            result.sent.append((po_no, kind, number))
            continue
        supplier = db.execute(text("select supplier_code from po where no = :p"), {"p": po_no}).scalar_one()
        contact = supplier_records.order_contact(db, str(supplier))
        if contact is None:
            result.failed.append((po_no, kind))
            continue
        inserted = db.execute(
            text(
                "insert into delivery_reminder (po_no, kind, promised_date, number, sent_to, sent_at) "
                "values (:p, :k, :d, :n, :t, :at) on conflict do nothing returning id"
            ),
            {"p": po_no, "k": kind, "d": when, "n": number, "t": contact[0], "at": now or datetime.now(UTC)},
        ).first()
        if inserted is None:  # another run sent it first
            db.rollback()
            continue
        link = supplier_links.url(supplier_links.issue(db, tenant, po_no, "dispatch")) if tenant else None
        subject = (
            f"Delivery due {when:%d %b %Y}: purchase order {po_no}"
            if kind == "before"
            else f"Overdue delivery: purchase order {po_no}"
        )
        audit.record(
            db, ACTOR, "po.delivery_reminder", "po", po_no,
            after={"kind": kind, "number": number, "date": when, "to": contact[0]},
        )  # fmt: skip
        emails.send_linked_email(
            db, contact[0], subject, _body(po_no, when, kind, number, lines, contact[1], str(company), link),
            linked_type="po", linked_ref=po_no,
        )  # fmt: skip
        emails.commit_and_send(db)  # the reminder row and its mail together; a refused mail is retried by the job
        result.sent.append((po_no, kind, number))
    return result


def next_run(now: datetime) -> datetime:
    tomorrow = now.astimezone(IST).date() + timedelta(days=1)
    return datetime.combine(tomorrow, RUN_AT, tzinfo=IST).astimezone(UTC)


def schedule(tenant: str, run_at: datetime | None = None) -> int | None:
    """Queue the daily run for a company unless one is already waiting (called when an order is sent)."""
    return worker.enqueue_unique(tenant, JOB, {}, run_at or next_run(datetime.now(UTC)))


@worker.register(JOB)
def _run(ctx: worker.JobContext) -> None:
    with Session(routing.engine_for(ctx.tenant)) as db:
        run(db, ctx.tenant, as_of=ctx.now.astimezone(IST).date(), now=ctx.now)
    worker.enqueue_unique(ctx.tenant, JOB, {}, next_run(ctx.now), exclude_id=ctx.id)
