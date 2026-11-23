"""Reminders to suppliers who have not answered a request for quotation.

A reminder goes out once the days set in 'rfq_reminder_after_days' (working days) have passed since the request or the
last reminder, at most 'rfq_max_reminders' times, and never after the reply date. A supplier who has replied, even if
a buyer has not turned the reply into a quote yet, or who has declined, is left alone. The autonomy kill switch stops
them like every other automatic message. The same daily job looks at the RFQs that closed (sourcing/closing.py).
"""

import logging
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from p2p.formulas.calendar import IST, business_today
from p2p.formulas.sourcing import reminder_due
from p2p.platform import audit, emails, reference, routing, worker
from p2p.platform.sod import Actor
from p2p.sourcing import service

log = logging.getLogger(__name__)
JOB = "rfq_reminders"
RUN_AT = time(9, 30)  # India time, after the working day has started
ACTOR = Actor("service", "rfq-reminders")


@dataclass
class ReminderResult:
    sent: list[tuple[str, str]] = field(default_factory=list)  # (rfq_no, supplier_code)
    failed: list[tuple[str, str]] = field(default_factory=list)
    stopped: str | None = None


def _org_setting(db: Session, key: str, default: str) -> str:
    value = db.execute(text("select value from org_setting where key = :k"), {"k": key}).scalar()
    return str(value) if value not in (None, "") else default


def _app_setting(db: Session, key: str, default: str) -> str:
    value = db.execute(text("select value from app_setting where key = :k"), {"k": key}).scalar()
    return str(value) if value not in (None, "") else default


def _body(rfq: dict[str, Any], lines: list[dict[str, Any]], contact: str | None, company: str, nth: int) -> str:
    return (
        f"Dear {contact or 'Sir or Madam'},\n\n"
        f"This is reminder {nth} of our request for quotation {rfq['no']}. We have not received your reply yet.\n\n"
        f"Please reply by: {rfq['due_date']:%d %b %Y}\n\n"
        f"{service.item_lines(lines)}\n\n"
        "Please reply to this email, keeping the subject line, with the unit price, the GST rate, the delivery time in "
        "days, how long your price is valid and your payment terms. If you are not quoting, a short reply saying so "
        "helps us.\n\n"
        f"Regards,\nProcurement, {company}\n"
    )


def run(db: Session, *, as_of: date, now: datetime | None = None, dry_run: bool = False) -> ReminderResult:
    """Remind every supplier that is due a reminder as of the date. Returns who was reminded."""
    result = ReminderResult()
    if db.execute(text("select kill_switch from autonomy_global where id = 1")).scalar_one():
        result.stopped = "the kill switch is on"
        return result
    after_days = int(_org_setting(db, "rfq_reminder_after_days", "2"))
    max_reminders = int(_app_setting(db, "rfq_max_reminders", "2"))
    calendar = reference.calendar_for(db)
    company = db.execute(text("select coalesce(short_name, legal_name) from company limit 1")).scalar() or "Our company"
    rows = (
        db.execute(
            text(
                "select rs.rfq_no, rs.supplier_code, rs.email, rs.reminders_sent, r.due_date, r.category_code, "
                "(coalesce(rs.last_reminded_at, rs.invited_at) at time zone 'Asia/Kolkata')::date as last_contact, "
                "(select c.name from supplier_contact c where c.supplier_code = rs.supplier_code and c.email = rs.email "
                "limit 1) as contact "
                "from rfq_supplier rs join rfq r on r.no = rs.rfq_no "
                "where r.status = 'sent' and r.confirmed_at is not null and rs.invited_at is not null "
                "and rs.responded_at is null and not rs.declined "
                "and rs.email is not null "
                "and not exists (select 1 from quote_draft d where d.rfq_no = rs.rfq_no "
                "and d.supplier_code = rs.supplier_code) "
                "and not exists (select 1 from email_message m where m.direction = 'in' and m.linked_type = 'rfq' "
                "and m.linked_id = rs.rfq_no and strpos(lower(m.from_addr), lower(rs.email)) > 0) "
                "order by rs.rfq_no, rs.supplier_code"
            )
        )
        .mappings()
        .all()
    )
    for row in rows:
        if not reminder_due(
            last_contact=row["last_contact"],
            reminders_sent=row["reminders_sent"],
            max_reminders=max_reminders,
            after_days=after_days,
            today=as_of,
            due_date=row["due_date"],
            calendar=calendar,
        ):
            continue
        key = (row["rfq_no"], row["supplier_code"])
        if dry_run:
            result.sent.append(key)
            continue
        rfq = {"no": row["rfq_no"], "due_date": row["due_date"]}
        lines = [
            dict(r)
            for r in db.execute(
                text("select * from rfq_line where rfq_no = :n order by line_no"), {"n": row["rfq_no"]}
            ).mappings()
        ]
        category = db.execute(
            text("select name from item_category where code = :c"), {"c": row["category_code"]}
        ).scalar()
        subject = f"Reminder: request for quotation {row['rfq_no']}" + (f": {category}" if category else "")
        nth = row["reminders_sent"] + 1
        emails.send_linked_email(
            db,
            row["email"],
            subject,
            _body(rfq, lines, row["contact"], str(company), nth),
            linked_type="rfq",
            linked_ref=row["rfq_no"],
        )
        db.execute(
            text(
                "update rfq_supplier set reminders_sent = reminders_sent + 1, last_reminded_at = :n "
                "where rfq_no = :r and supplier_code = :s"
            ),
            {"n": now or datetime.now(UTC), "r": row["rfq_no"], "s": row["supplier_code"]},
        )
        audit.record(
            db, ACTOR, "rfq.remind", "rfq", row["rfq_no"], after={"supplier": row["supplier_code"], "number": nth}
        )
        emails.commit_and_send(db)  # the reminder count and its mail together; a refused mail is retried by the job
        result.sent.append(key)
    return result


def next_run(now: datetime) -> datetime:
    tomorrow = now.astimezone(IST).date() + timedelta(days=1)
    return datetime.combine(tomorrow, RUN_AT, tzinfo=IST).astimezone(UTC)


def schedule(tenant: str, run_at: datetime | None = None) -> int | None:
    """Queue the daily reminder job for a company unless one is already waiting."""
    return worker.enqueue_unique(tenant, JOB, {}, run_at or datetime.now(UTC))


@worker.register(JOB)
def _run(ctx: worker.JobContext) -> None:
    from p2p.sourcing import closing  # noqa: PLC0415 - closing reads the quotes, which import the sourcing service

    with Session(routing.engine_for(ctx.tenant)) as db:
        run(db, as_of=ctx.now.astimezone(IST).date(), now=ctx.now)
        closing.run(db, ctx.tenant, business_today())  # RFQs that closed short: a case for the buyer (AT-10)
    worker.enqueue_unique(ctx.tenant, JOB, {}, next_run(ctx.now), exclude_id=ctx.id)
