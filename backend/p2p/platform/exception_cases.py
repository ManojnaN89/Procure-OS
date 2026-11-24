"""Every stuck item is a case (block 11, 5.1-5.5): an owner, a deadline, escalation and a re-check before it closes.

A case is a `task` row with a `case_key` (`<kind>:<ref>`, `job:<id>` or `email:<task id>`), at most one open per key.
The hourly sweep gives every exceptions row the task it already is, or a new `exception` case with an assignee from the
row (the person named, else the document's own person, else the first login that may act) and a deadline of
`case_due_days` x 9 working hours on the company calendar. A case that is no longer stuck is completed by the system
after its row goes; nobody closes an `exception` case by hand (the engine re-checks it).

# NEEDS block 9: its task kinds `receipt_excess` and `return_outcome`, and its rows `inspection` and `return_draft`,
# join through `exceptions.collect`; the sweep then gives each its `case_key` like any other task row.
# NEEDS block 10: its task kinds `invoice_authorise`, `invoice_approve` and `invoice_sender_check` do the same.
"""

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from p2p.approvals.workflow import name_of, org_code_of
from p2p.formulas.calendar import IST, BusinessCalendar, sla_due
from p2p.platform import audit, exceptions, notifications, permissions, reference, settings, tasks, tenants, worker
from p2p.platform.rows import rows as _rows
from p2p.platform.sod import Actor

CASE_KINDS = ("exception", "email_failed", "job_failed")
SYSTEM = Actor("service", "case_sweep")
SWEEP_JOB = "case_sweep"
HOURS_PER_DAY = 9


class CaseError(Exception):
    pass


class CasePermission(Exception):
    pass


def _key(s: exceptions.Stuck) -> str:
    return s.case_key or f"{s.kind}:{s.ref}"


def _granted(db: Session, user_id: int) -> set[str]:
    return set(permissions.granted(db, user_id))


def may_act(db: Session, s: exceptions.Stuck, user_id: int) -> bool:
    """Whether this login may act on the row: named on it, or holding its permission and not refused by segregation."""
    return exceptions._may_act(s, user_id, org_code_of(db, user_id), _granted(db, user_id))  # noqa: SLF001


def _own_person(db: Session, tenant: str, s: exceptions.Stuck) -> int | None:
    """The document's own person when they may act: the invoice's enterer, the order's buyer, the receipt's poster."""
    if s.kind == "invoice":
        found = str(db.execute(text("select entered_by from invoice where no = :n"), {"n": s.ref}).scalar() or "")
        return int(found) if found.isdigit() else None
    if s.kind == "unordered":
        found = str(
            db.execute(text("select posted_by from unordered_receipt where no = :n"), {"n": s.ref}).scalar() or ""
        )
        return int(found) if found.isdigit() else None
    if s.kind == "order":
        buyer = db.execute(text("select buyer from po where no = :n"), {"n": s.ref}).scalar()
        return notifications.login_of(db, tenant, str(buyer)) if buyer else None
    return None


def _first_admin(db: Session) -> int | None:
    return db.execute(
        text(
            "select ur.user_id from user_role ur join role_permission rp on rp.role = ur.role "
            "where rp.action = 'admin.settings' order by ur.user_id limit 1"
        )
    ).scalar()


def _active_logins(db: Session) -> list[int]:
    return [
        int(r[0])
        for r in db.execute(
            text(
                "select distinct ur.user_id from user_role ur join user_profile p on p.user_id = ur.user_id "
                "order by ur.user_id"
            )
        )
    ]


def assignee(db: Session, tenant: str, s: exceptions.Stuck) -> tuple[int, bool]:
    """Who takes the case: a person named on the row, else the document's own person, else the first login that may
    act. Nobody may act: the first administrator, and the title says so (returns True for the prefix)."""
    for code in sorted(s.people):
        who = notifications.login_of(db, tenant, code)
        if who and may_act(db, s, who):
            return who, False
    own = _own_person(db, tenant, s)
    if own and may_act(db, s, own):
        return own, False
    for who in _active_logins(db):
        if may_act(db, s, who):
            return who, False
    admin = _first_admin(db)
    if admin is None:
        raise CaseError(f"nobody may act on '{s.reason}': the company has no administrator")
    return int(admin), True


def _manager_chain(db: Session, tenant: str, who: int) -> list[int]:
    """The assignee's manager, then that manager's manager (N-3), as logins that exist."""
    chain: list[int] = []
    code = org_code_of(db, who)
    for _ in range(2):
        if not code:
            break
        code = db.execute(text("select manager_code from org_user where code = :c"), {"c": code}).scalar()
        if not code:
            break
        found = notifications.login_of(db, tenant, str(code))
        if found and found != who and found not in chain:
            chain.append(found)
    return chain


def _sla(db: Session) -> Decimal:
    return settings.number(db, "case_due_days", "2") * HOURS_PER_DAY


def _open_case(db: Session, key: str) -> Any:
    return db.execute(
        text(
            "select id, assignee, due_at, step, status, escalation_chain from task "
            "where case_key = :k and status in ('open', 'overdue') order by id limit 1 for update"
        ),
        {"k": key},
    ).first()


def _calendar(db: Session) -> BusinessCalendar:
    return reference.calendar_for(db)


def sweep(db: Session, tenant: str, now: datetime | None = None) -> dict[str, int]:
    """Give every stuck row its one case, move it to a changed next step, and complete the ones no longer stuck."""
    now = now or datetime.now(UTC)
    today = now.astimezone(IST).date()
    cal = _calendar(db)
    sla = _sla(db)
    rows = exceptions.collect(db, today, cal)
    current: dict[str, exceptions.Stuck] = {}
    created = moved = resolved = 0
    for s in rows:
        key = _key(s)
        current[key] = s
        case = _open_case(db, key)
        if case is None and s.task_id is not None:
            # The row already comes from a task (an approval step, an AI hand-over, a sourcing, demand, supply or mail
            # task): that task is the case; it only gains its key and its step.
            attached = db.execute(
                text("select id from task where id = :i and status in ('open', 'overdue')"),
                {"i": s.task_id},
            ).scalar()
            if attached is not None:
                db.execute(
                    text("update task set case_key = :k, step = :st where id = :i and case_key is null"),
                    {"k": key, "st": s.owner, "i": s.task_id},
                )
                continue
        if case is None:
            who, nobody = assignee(db, tenant, s)
            title = ("No one may act: " if nobody else "") + s.reason
            task_id = tasks.create_task(
                db,
                tenant,
                kind="exception",
                title=title[:200],
                assignee=who,
                sla_hours=sla,
                escalation_chain=_manager_chain(db, tenant, who),
                payload={"kind": s.kind, "ref": s.ref, "link": s.link, "reason": s.reason},
                actor=SYSTEM,
                now=now,
                email=False,  # in-app notice only when a case opens (OP-10)
                calendar=cal,
                case_key=key,
                step=s.owner,
            )
            audit.record(
                db, SYSTEM, "case.open", "task", task_id,
                after={"key": key, "assignee": who, "due_at": "the case's deadline", "step": s.owner},
            )  # fmt: skip
            db.commit()
            created += 1
            continue
        task_id, who, _due, step, _status, _chain = case
        if step != s.owner:
            new_who, _nobody = assignee(db, tenant, s)
            due = sla_due(now, sla, cal)
            db.execute(
                text(
                    "update task set assignee = :a, due_at = :d, escalation_chain = cast(:c as jsonb), "
                    "escalation_level = 0, step = :st where id = :i"
                ),
                {"a": new_who, "d": due, "c": _json(_manager_chain(db, tenant, new_who)), "st": s.owner, "i": task_id},
            )
            audit.record(
                db, SYSTEM, "case.reassign", "task", task_id,
                before={"assignee": who, "step": step}, after={"assignee": new_who, "step": s.owner},
                reason="next step",
            )  # fmt: skip
            notifications.notify(db, tenant, new_who, "case", f"A case moved to you: {s.reason[:160]}")
            db.commit()
            moved += 1
    open_cases = _rows(
        db,
        "select id, case_key from task where kind = any(:k) and case_key is not null "
        "and status in ('open', 'overdue') order by id",
        k=list(CASE_KINDS),
    )
    for case in open_cases:
        if case["case_key"] in current:
            continue
        completed = db.execute(
            text(
                "update task set status = 'done', resolution = 'resolved', completed_at = :n, completed_by = null "
                "where id = :i and status in ('open', 'overdue') returning id"
            ),
            {"n": now, "i": case["id"]},
        ).first()
        if completed:
            audit.record(db, SYSTEM, "case.resolved", "task", case["id"], after={"resolution": "resolved"})
            resolved += 1
    db.commit()
    return {"open": len(current), "created": created, "moved": moved, "resolved": resolved}


def _json(value: Any) -> str:
    import json  # noqa: PLC0415 - one small use

    return json.dumps(value)


def _case(db: Session, task_id: int) -> Any:
    row = db.execute(
        text(
            "select id, kind, case_key, assignee, step, due_at, sla_hours, status, escalation_chain, escalation_level, payload from task where id = :i"
        ),
        {"i": task_id},
    ).first()
    if row is None:
        raise CaseError(f"case {task_id} does not exist")
    return row


def _row_for(db: Session, key: str, today: Any, cal: BusinessCalendar) -> exceptions.Stuck | None:
    return next((s for s in exceptions.collect(db, today, cal) if _key(s) == key), None)


def recheck(db: Session, tenant: str, task_id: int, now: datetime | None = None) -> str:
    """Run the row's source again: gone means resolved; still there is refused with its reason (Rev l.442)."""
    now = now or datetime.now(UTC)
    task = _case(db, task_id)
    if task[1] not in CASE_KINDS:
        raise CaseError("this case is closed by its own outcome, not by a re-check")
    if task[7] not in ("open", "overdue"):
        raise CaseError(f"case {task_id} is already {task[7]}")
    if not task[2]:
        raise CaseError("this task is not a stuck item's case")
    today = now.astimezone(IST).date()
    stuck = _row_for(db, str(task[2]), today, _calendar(db))
    if stuck is not None:
        raise CaseError(f"still stuck: {stuck.reason}")
    db.execute(
        text(
            "update task set status = 'done', resolution = 'resolved', completed_at = :n, completed_by = null "
            "where id = :i and status in ('open', 'overdue')"
        ),
        {"n": now, "i": task_id},
    )
    audit.record(db, SYSTEM, "case.resolved", "task", task_id, after={"resolution": "resolved"}, reason="re-checked")
    db.commit()
    return "resolved"


def _in_chain(db: Session, task: Any, user_id: int, me: str | None) -> bool:
    if int(task[3]) == user_id:
        return True
    chain = task[8] or []
    return any(int(x) == user_id for x in chain)


def reassign(
    db: Session, tenant: str, task_id: int, to: int, reason: str, by: int, now: datetime | None = None
) -> dict[str, Any]:
    """The assignee, or someone above them in the case's chain, gives it to a person who may act on it (N-3)."""
    if not reason.strip():
        raise CaseError("a reason is needed")
    now = now or datetime.now(UTC)
    task = _case(db, task_id)
    me = org_code_of(db, by)
    if not _in_chain(db, task, by, me):
        raise CasePermission("only the assignee or a person above them in the case's chain may reassign it")
    if not task[2]:
        raise CaseError("this task is not a stuck item's case")
    today = now.astimezone(IST).date()
    cal = _calendar(db)
    stuck = _row_for(db, str(task[2]), today, cal)
    if stuck is None:
        raise CaseError("nothing is stuck any more; check it again to close the case")
    if not may_act(db, stuck, to):
        need = stuck.act or "the right to act on it"
        raise CaseError(f"{name_of(db, org_code_of(db, to))} cannot act on this: needs `{need}`")
    sla = Decimal(str(task[6]))
    due = sla_due(now, sla, cal)
    chain = _manager_chain(db, tenant, to)
    db.execute(
        text(
            "update task set assignee = :a, due_at = :d, escalation_chain = cast(:c as jsonb), escalation_level = 0, "
            "step = :st where id = :i"
        ),
        {"a": to, "d": due, "c": _json(chain), "st": stuck.owner, "i": task_id},
    )
    audit.record(
        db, Actor("user", str(by)), "case.reassign", "task", task_id,
        before={"assignee": task[3]}, after={"assignee": to, "due_at": due}, reason=reason,
    )  # fmt: skip
    db.commit()
    notifications.notify(db, tenant, int(task[3]), "case", f"The case was given to someone else: {task[2]}")
    notifications.notify(db, tenant, to, "case", f"The case is yours: {task[2]}")
    return {"assignee": org_code_of(db, to), "due_at": due.isoformat()}


def candidates(db: Session, task_id: int, now: datetime | None = None) -> list[dict[str, Any]]:
    """The people who may take the case: every active login that may act on its row."""
    now = now or datetime.now(UTC)
    task = _case(db, task_id)
    if not task[2]:
        return []
    today = now.astimezone(IST).date()
    stuck = _row_for(db, str(task[2]), today, _calendar(db))
    if stuck is None:
        return []
    out = []
    for who in _active_logins(db):
        if may_act(db, stuck, who):
            code = org_code_of(db, who)
            out.append({"user_id": who, "name": name_of(db, code) if code else f"login {who}"})
    return out


def requeue(db: Session, task_id: int, by: int, now: datetime | None = None) -> dict[str, Any]:
    """Run a failed job again: queued, no attempts used, due now (5.4)."""
    now = now or datetime.now(UTC)
    task = _case(db, task_id)
    if task[1] != "job_failed":
        raise CaseError("this is not a failed job")
    payload = task[10] or {}
    job_id = payload.get("job_id")
    if job_id is None:
        raise CaseError("the case has no job to run again")
    with tenants.control_engine().begin() as c:
        status = c.execute(text("select status from job where id = :i"), {"i": int(job_id)}).scalar()
        if status != "failed":
            raise CaseError(f"job {job_id} is {status}, not failed")
        c.execute(
            text(
                "update job set status = 'queued', attempts = 0, locked_at = null, finished_at = null, run_at = :n "
                "where id = :i"
            ),
            {"n": now, "i": int(job_id)},
        )
    audit.record(db, Actor("user", str(by)), "job.requeue", "job", int(job_id), after={"status": "queued"})
    db.commit()
    return {"job_id": int(job_id), "status": "queued"}


def close_email_failed(db: Session, task_id: int, outcome: str, note: str, by: int, now: datetime | None = None) -> str:
    """A failed email a person handled another way; the note is required (5.3)."""
    if outcome != "handled_otherwise":
        raise CaseError("a failed email is closed as 'handled_otherwise'")
    if not note.strip():
        raise CaseError("a note is needed")
    task = _case(db, task_id)
    if task[1] != "email_failed":
        raise CaseError("only a failed email is closed here")
    now = now or datetime.now(UTC)
    db.execute(
        text(
            "update task set status = 'done', resolution = :r, completed_at = :n, completed_by = :u "
            "where id = :i and status in ('open', 'overdue')"
        ),
        {"r": outcome, "n": now, "u": by, "i": task_id},
    )
    audit.record(db, Actor("user", str(by)), "case.close", "task", task_id, after={"resolution": outcome}, reason=note)
    db.commit()
    return outcome


def open_job_failure(
    tenant: str, job_id: int, kind: str, attempts: int, error: str, now: datetime | None = None
) -> int | None:
    """The worker's last attempt failed: one case for the first administrator, its payload counting the failures."""
    now = now or datetime.now(UTC)
    from p2p.platform import routing  # noqa: PLC0415 - routing imports the platform, not the other way

    key = f"job:{job_id}"
    with Session(routing.engine_for(tenant)) as db:
        existing = db.execute(
            text("select id, payload from task where case_key = :k and status in ('open', 'overdue')"), {"k": key}
        ).first()
        if existing is not None:
            payload = dict(existing[1] or {})
            payload["failures"] = int(payload.get("failures", 0)) + 1
            payload["attempts"] = attempts
            payload["last_error"] = error
            db.execute(
                text("update task set payload = cast(:p as jsonb) where id = :i"),
                {"p": _json(payload), "i": existing[0]},
            )
            db.commit()
            return int(existing[0])
        admin = _first_admin(db)
        if admin is None:
            return None
        task_id = tasks.create_task(
            db,
            tenant,
            kind="job_failed",
            title=f"A background job failed: {kind}"[:200],
            assignee=int(admin),
            sla_hours=8,
            payload={"job_id": job_id, "kind": kind, "attempts": attempts, "last_error": error, "failures": 1},
            actor=audit.SYSTEM,
            now=now,
            case_key=key,
            step="Admin",
        )
        return task_id


@worker.register(SWEEP_JOB)
def _sweep_job(ctx: worker.JobContext) -> None:
    """The hourly sweep, which queues itself again (one waiting run per company)."""
    from datetime import timedelta

    from p2p.platform import routing  # noqa: PLC0415

    with Session(routing.engine_for(ctx.tenant)) as db:
        sweep(db, ctx.tenant, ctx.now)
    worker.enqueue_unique(ctx.tenant, SWEEP_JOB, {}, ctx.now + timedelta(hours=1), exclude_id=ctx.id)


def schedule(tenant: str, run_at: datetime | None = None) -> int | None:
    """Queue the next sweep for a company unless one is waiting."""
    return worker.enqueue_unique(tenant, SWEEP_JOB, {}, run_at or datetime.now(UTC))


def cases_for(db: Session, tenant: str, now: datetime | None = None) -> list[dict[str, Any]]:
    """The open cases, for a screen that wants them apart from the exceptions list."""
    now = now or datetime.now(UTC)
    out = []
    for r in _rows(
        db,
        "select id, kind, case_key, title, assignee, step, due_at, status, escalation_level, payload "
        "from task where case_key is not null and status in ('open', 'overdue') order by due_at, id",
    ):
        code = org_code_of(db, int(r["assignee"]))
        out.append(
            {
                **r,
                "assignee_name": name_of(db, code) if code else None,
                "due_at": r["due_at"].isoformat(),
                "overdue": r["status"] == "overdue" or r["due_at"] < now,
            }
        )
    return out
