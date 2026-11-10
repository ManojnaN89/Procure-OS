"""Human tasks with an SLA and an escalation chain.

A task has an assignee, a due time counted on the business calendar (FR41), and a chain of people to escalate to.
When the due time passes and the task is still open, a worker job moves it to the next person, resets the clock and
tells both people. When the chain is used up the task is marked overdue and the last person is told.
"""

import json
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from p2p.formulas.calendar import BusinessCalendar, sla_due
from p2p.platform import audit, cases, notifications, reference, routing, worker
from p2p.platform.sod import Actor


def _calendar(db: Session) -> BusinessCalendar:
    return reference.calendar_for(db)


def create_task(
    db: Session,
    tenant: str,
    *,
    kind: str,
    title: str,
    assignee: int,
    sla_hours: Decimal | int | str,
    escalation_chain: list[int] | None = None,
    case_id: int | None = None,
    payload: dict[str, Any] | None = None,
    actor: Actor = audit.SYSTEM,
    now: datetime | None = None,
) -> int:
    now = now or datetime.now(UTC)
    due = sla_due(now, sla_hours, _calendar(db))
    task_id = int(
        db.execute(
            text(
                "insert into task (case_id, kind, title, assignee, sla_hours, due_at, escalation_chain, payload) "
                "values (:c, :k, :t, :a, :h, :d, cast(:ch as jsonb), cast(:p as jsonb)) returning id"
            ),
            {
                "c": case_id,
                "k": kind,
                "t": title,
                "a": assignee,
                "h": Decimal(str(sla_hours)),
                "d": due,
                "ch": json.dumps(escalation_chain or []),
                "p": json.dumps(payload, default=str) if payload else None,
            },
        ).scalar_one()
    )
    audit.record(db, actor, "task.create", "task", task_id, after={"assignee": assignee, "due_at": due, "kind": kind})
    if case_id:
        cases.log_event(db, case_id, "task_created", actor, {"task_id": task_id, "assignee": assignee})
    db.commit()
    notifications.notify(db, tenant, assignee, "task", f"New task: {title}", f"Due {due:%d %b %Y %H:%M}.")
    worker.enqueue(tenant, "task_escalate", {"task_id": task_id}, run_at=due)
    return task_id


def complete_task(db: Session, task_id: int, user_id: int, *, now: datetime | None = None) -> None:
    now = now or datetime.now(UTC)
    updated = db.execute(
        text(
            "update task set status = 'done', completed_at = :n, completed_by = :u "
            "where id = :i and status in ('open', 'overdue') returning id"
        ),
        {"n": now, "u": user_id, "i": task_id},
    ).first()
    if updated:
        audit.record(db, Actor("user", str(user_id)), "task.complete", "task", task_id)
    db.commit()


def cancel_task(db: Session, task_id: int, user_id: int | None = None, *, reason: str = "") -> None:
    """A task that is no longer needed (the request it was about was taken back or cancelled)."""
    updated = db.execute(
        text("update task set status = 'cancelled' where id = :i and status in ('open', 'overdue') returning id"),
        {"i": task_id},
    ).first()
    if updated:
        audit.record(
            db, Actor("user", str(user_id)) if user_id is not None else audit.SYSTEM, "task.cancel", "task", task_id,
            reason=reason or None,
        )  # fmt: skip
    db.commit()


@worker.register("task_escalate")
def _escalate(ctx: worker.JobContext) -> None:
    """Runs at the task's due time. Safe to run twice: it does nothing unless the task is open and really overdue."""
    task_id = int(ctx.payload["task_id"])
    with Session(routing.engine_for(ctx.tenant)) as db:
        row = db.execute(
            text(
                "select status, due_at, assignee, escalation_chain, escalation_level, sla_hours, title, case_id "
                "from task where id = :i for update"
            ),
            {"i": task_id},
        ).first()
        if row is None or row[0] != "open" or row[1] > ctx.now:
            db.rollback()
            return
        _status, _due, assignee, chain, level, sla_hours, title, case_id = row
        system = Actor("service", "escalation")
        if level >= len(chain):
            db.execute(text("update task set status = 'overdue' where id = :i"), {"i": task_id})
            audit.record(db, system, "task.overdue", "task", task_id, before={"assignee": assignee})
            db.commit()
            notifications.notify(
                db, ctx.tenant, assignee, "task_overdue", f"Overdue, no one left to escalate to: {title}"
            )
            return
        new_assignee = int(chain[level])
        due = sla_due(ctx.now, sla_hours, _calendar(db))
        db.execute(
            text("update task set assignee = :a, escalation_level = :l, due_at = :d where id = :i"),
            {"a": new_assignee, "l": level + 1, "d": due, "i": task_id},
        )
        audit.record(
            db,
            system,
            "task.escalate",
            "task",
            task_id,
            before={"assignee": assignee},
            after={"assignee": new_assignee, "due_at": due},
        )
        if case_id:
            cases.log_event(
                db, int(case_id), "task_escalated", system, {"task_id": task_id, "from": assignee, "to": new_assignee}
            )
        db.commit()
        notifications.notify(
            db, ctx.tenant, new_assignee, "task_escalated", f"Escalated to you: {title}", f"Due {due:%d %b %Y %H:%M}."
        )
        notifications.notify(
            db, ctx.tenant, int(assignee), "task_escalated", f"Task moved on to the next person: {title}"
        )
        worker.enqueue(ctx.tenant, "task_escalate", {"task_id": task_id}, run_at=due)
