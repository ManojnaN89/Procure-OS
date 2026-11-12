"""Chasing an approval that waits: one reminder, then escalation, both counted in working days on the business calendar.

When a step opens, a reminder is queued for `approval_reminder_days` working days later (default 1) and the step's task is
due `approval_escalation_days` working days later (default 2). The reminder goes once (approval_step.reminded_at), is
recorded in the audit log, and is skipped when the step was decided meanwhile. At the due time the task moves on
(platform/tasks.py) to the approver's own manager, else to the next approver in the chain, never to the person who asked;
that person may then decide the step too (workflow.on_escalated).
"""

from datetime import datetime
from decimal import Decimal

from sqlalchemy import text
from sqlalchemy.orm import Session

from p2p.formulas.calendar import BusinessCalendar
from p2p.platform import audit, notifications, reference, routing, settings, worker
from p2p.platform.sod import Actor

REMINDER = Actor("service", "approval-reminder")


def reminder_days(db: Session) -> Decimal:
    return settings.number(db, "approval_reminder_days", "1")


def escalation_days(db: Session) -> Decimal:
    return settings.number(db, "approval_escalation_days", "2")


def calendar(db: Session) -> BusinessCalendar:
    return reference.calendar_for(db)


def escalation_target(db: Session, tenant: str, no: str, round_no: int, step: int, who: str) -> int | None:
    """The login an approval moves to when it waits too long: the decider's own manager, else the next approver in the
    chain; never the requester and never the decider themselves. None when there is nobody (the task then goes overdue)."""
    row = db.execute(
        text(
            "select r.requester, (select manager_code from org_user where code = :who and active), "
            "(select approver from approval_step where request_no = :n and round = :r and step = :s + 1) "
            "from request r where r.no = :n"
        ),
        {"n": no, "r": round_no, "s": step, "who": who},
    ).one()
    requester, manager, following = row
    for person in (manager, following):
        if person and person not in (requester, who):
            login = notifications.login_of(db, tenant, str(person))
            if login is not None:
                return login
    return None


def queue_reminder(db: Session, tenant: str, no: str, round_no: int, step: int, opened_at: datetime) -> None:
    due = calendar(db).working_days_later(opened_at, reminder_days(db))
    worker.enqueue(tenant, "approval_remind", {"request_no": no, "round": round_no, "step": step}, run_at=due)


@worker.register("approval_remind")
def _remind(ctx: worker.JobContext) -> None:
    """Runs at the reminder time. Safe to run twice: it reminds once, only while the step still waits."""
    from p2p.approvals import approval_email  # noqa: PLC0415 - the email module reads the workflow

    no, round_no, step = str(ctx.payload["request_no"]), int(ctx.payload["round"]), int(ctx.payload["step"])
    with Session(routing.engine_for(ctx.tenant)) as db:
        row = (
            db.execute(
                text(
                    "select s.id, s.status, s.opened_at, s.reminded_at, s.approver, s.delegate, r.status as req_status, "
                    "r.approval_round from approval_step s join request r on r.no = s.request_no "
                    "where s.request_no = :n and s.round = :r and s.step = :s for update of s"
                ),
                {"n": no, "r": round_no, "s": step},
            )
            .mappings()
            .first()
        )
        if (
            row is None
            or row["status"] != "pending"
            or row["reminded_at"] is not None
            or row["req_status"] != "submitted"
            or row["approval_round"] != round_no
            or row["opened_at"] is None
        ):
            db.rollback()
            return
        days = reminder_days(db)
        due = calendar(db).working_days_later(row["opened_at"], days)
        if ctx.now < due:  # queued early (the step reopened): try again at the right time
            db.rollback()
            worker.enqueue(ctx.tenant, "approval_remind", ctx.payload, run_at=due)
            return
        who = str(row["delegate"] or row["approver"])
        login = notifications.login_of(db, ctx.tenant, who)
        db.execute(text("update approval_step set reminded_at = :t where id = :i"), {"t": ctx.now, "i": row["id"]})
        audit.record(
            db, REMINDER, "request.remind", "request", no,
            after={"approver": who, "step": step, "round": round_no, "automatic": True, "told": login is not None},
            reason=f"no decision after {days.normalize()} working day(s)",
        )  # fmt: skip
        db.commit()
        if login is not None:
            notifications.notify(
                db, ctx.tenant, login, "approval", f"Reminder: request {no} is waiting for you",
                link=f"#/requisitions/{no}", email=False,
            )  # fmt: skip
            approval_email.send(db, ctx.tenant, no, round_no, step, login, "reminder")
