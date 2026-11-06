import time
import uuid
from collections.abc import Iterator
from datetime import datetime, timedelta

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

from p2p.formulas.calendar import IST
from p2p.platform import cases, notifications, routing, tasks, tenants, users, worker
from p2p.platform.sod import Actor

SYSTEM = Actor("service", "test")
FRIDAY_5PM = datetime(2026, 11, 6, 17, 0, tzinfo=IST)
MONDAY_4PM = datetime(2026, 11, 9, 16, 0, tzinfo=IST)  # Friday 5 pm + 8 working hours


class Env:
    def __init__(self) -> None:
        self.slug = "t" + uuid.uuid4().hex[:8]
        tenants.create_tenant(self.slug, load_reference=False)
        self.db = Session(routing.engine_for(self.slug))

    def user(self, name: str) -> int:
        return users.create_user(
            self.slug, f"{name}.{uuid.uuid4().hex[:6]}@example.com", "correct horse battery", name, "Approver"
        )

    def scalar(self, sql: str, **params: object) -> object:
        return self.db.execute(text(sql), params).scalar_one()

    def due(self, task_id: int) -> datetime:
        self.db.expire_all()
        return self.db.execute(text("select due_at from task where id = :i"), {"i": task_id}).scalar_one()  # type: ignore[no-any-return]


@pytest.fixture
def env(control_db: str) -> Iterator[Env]:
    engine = tenants.control_engine()
    with engine.begin() as c:
        c.execute(text("delete from job"))  # each test starts with an empty queue
    engine.dispose()
    e = Env()
    yield e
    e.db.close()
    routing.clear_engine_cache()


# ---- case flow -------------------------------------------------------------------------------


def test_a_case_moves_through_its_states_and_every_move_is_recorded(env: Env) -> None:
    cid = cases.create_case(env.db, "CASE-1", SYSTEM, title="Bearings")
    for state in ("approval", "order", "supplier", "receipt", "invoice", "payment", "closed"):
        assert cases.transition(env.db, cid, state, SYSTEM) == state

    events = (
        env.db.execute(text("select to_state from case_event where case_id = :c order by id"), {"c": cid})
        .scalars()
        .all()
    )
    assert events == ["request", "approval", "order", "supplier", "receipt", "invoice", "payment", "closed"]
    assert env.scalar("select count(*) from audit_event where action = 'case.transition'") == 7


def test_a_case_cannot_skip_a_step_or_leave_the_closed_state(env: Env) -> None:
    cid = cases.create_case(env.db, "CASE-2", SYSTEM)
    with pytest.raises(cases.TransitionError, match="cannot move"):
        cases.transition(env.db, cid, "payment", SYSTEM)
    assert cases.state_of(env.db, cid) == "request"
    cases.transition(env.db, cid, "closed", SYSTEM)
    with pytest.raises(cases.TransitionError):
        cases.transition(env.db, cid, "request", SYSTEM)


def test_an_approval_can_be_returned_to_the_requester(env: Env) -> None:
    cid = cases.create_case(env.db, "CASE-3", SYSTEM)
    cases.transition(env.db, cid, "approval", SYSTEM)
    assert cases.transition(env.db, cid, "request", SYSTEM, event="returned") == "request"


# ---- tasks and escalation (FR41) -------------------------------------------------------------


def test_a_task_not_acted_on_by_its_due_time_escalates_to_the_next_person(env: Env) -> None:
    first, second, third = env.user("first"), env.user("second"), env.user("third")
    tid = tasks.create_task(
        env.db,
        env.slug,
        kind="approve",
        title="Approve REQ-9",
        assignee=first,
        sla_hours=8,
        escalation_chain=[second, third],
        now=FRIDAY_5PM,
    )
    due = env.scalar("select due_at from task where id = :i", i=tid)
    assert due == MONDAY_4PM

    worker.run_once(now=MONDAY_4PM - timedelta(minutes=1))  # not due yet: only the notification emails go out
    assert env.scalar("select assignee from task where id = :i", i=tid) == first
    worker.run_once(now=MONDAY_4PM + timedelta(minutes=1))  # the escalation job runs

    env.db.expire_all()
    assert env.scalar("select assignee from task where id = :i", i=tid) == second
    assert env.scalar("select escalation_level from task where id = :i", i=tid) == 1
    assert notifications.unread(env.db, second)[0][1] == "task_escalated"
    assert any(n[1] == "task_escalated" for n in notifications.unread(env.db, first))
    assert env.scalar("select count(*) from audit_event where action = 'task.escalate'") == 1


def test_the_chain_is_walked_to_its_end_and_then_the_task_is_marked_overdue(env: Env) -> None:
    first, second = env.user("first"), env.user("second")
    tid = tasks.create_task(
        env.db,
        env.slug,
        kind="approve",
        title="Approve",
        assignee=first,
        sla_hours=8,
        escalation_chain=[second],
        now=FRIDAY_5PM,
    )

    t = MONDAY_4PM + timedelta(minutes=1)
    worker.run_once(now=t)  # first to second
    t2 = env.due(tid) + timedelta(minutes=1)
    worker.run_once(now=t2)  # second has nobody left

    assert env.scalar("select status from task where id = :i", i=tid) == "overdue"
    assert notifications.unread(env.db, second)[0][1] == "task_overdue"


def test_a_task_completed_in_time_does_not_escalate(env: Env) -> None:
    first, second = env.user("first"), env.user("second")
    tid = tasks.create_task(
        env.db,
        env.slug,
        kind="approve",
        title="Approve",
        assignee=first,
        sla_hours=8,
        escalation_chain=[second],
        now=FRIDAY_5PM,
    )
    tasks.complete_task(env.db, tid, first, now=FRIDAY_5PM + timedelta(hours=1))

    worker.run_once(now=MONDAY_4PM + timedelta(hours=1))

    assert env.scalar("select assignee from task where id = :i", i=tid) == first
    assert env.scalar("select status from task where id = :i", i=tid) == "done"


# ---- jobs survive a restart ------------------------------------------------------------------


def test_queued_jobs_survive_a_restart_of_the_application(env: Env) -> None:
    first, second = env.user("first"), env.user("second")
    tid = tasks.create_task(
        env.db,
        env.slug,
        kind="approve",
        title="Approve",
        assignee=first,
        sla_hours=8,
        escalation_chain=[second],
        now=FRIDAY_5PM,
    )

    routing.clear_engine_cache()  # everything held in memory is gone, as after a restart
    env.db.close()

    assert worker.run_once(now=MONDAY_4PM + timedelta(minutes=1)) >= 1
    fresh = Session(routing.engine_for(env.slug))
    assert fresh.execute(text("select assignee from task where id = :i"), {"i": tid}).scalar_one() == second


def test_a_job_held_by_a_worker_that_died_is_taken_again_after_the_lock_expires(env: Env) -> None:
    jid = worker.enqueue(
        env.slug, "send_email", {"to": "nobody@example.com", "subject": "s", "body": "b"}, run_at=FRIDAY_5PM
    )
    engine = tenants.control_engine()
    with engine.begin() as c:
        c.execute(text("update job set status = 'running', locked_at = :t where id = :i"), {"t": FRIDAY_5PM, "i": jid})

    assert worker.run_once(now=FRIDAY_5PM + timedelta(minutes=2)) == 0  # still locked
    assert worker.run_once(now=FRIDAY_5PM + timedelta(minutes=6)) == 1  # lock timed out
    with engine.begin() as c:
        assert c.execute(text("select status from job where id = :i"), {"i": jid}).scalar_one() == "done"
    engine.dispose()


def test_two_workers_never_take_the_same_job(env: Env) -> None:
    jid = worker.enqueue(
        env.slug, "send_email", {"to": "nobody@example.com", "subject": "s", "body": "b"}, run_at=FRIDAY_5PM
    )
    engine = tenants.control_engine()
    with engine.connect() as holder, holder.begin():
        holder.execute(text("select id from job where id = :i for update"), {"i": jid})  # another worker is claiming it
        assert worker.run_once(now=FRIDAY_5PM + timedelta(minutes=1)) == 0
    assert worker.run_once(now=FRIDAY_5PM + timedelta(minutes=1)) == 1
    engine.dispose()


def test_a_failing_job_is_retried_later_and_then_given_up(env: Env) -> None:
    jid = worker.enqueue(env.slug, "no_such_job", {}, run_at=FRIDAY_5PM)
    engine = tenants.control_engine()
    t = FRIDAY_5PM
    for _ in range(worker.MAX_ATTEMPTS):
        t += timedelta(hours=1)
        assert worker.run_once(now=t) == 1
    with engine.begin() as c:
        row = c.execute(text("select status, attempts, last_error from job where id = :i"), {"i": jid}).one()
    engine.dispose()
    assert row[0] == "failed" and row[1] == worker.MAX_ATTEMPTS and "no_such_job" in row[2]


# ---- notifications ---------------------------------------------------------------------------


def _mailpit_up() -> bool:
    try:
        return httpx.get("http://127.0.0.1:8025/api/v1/info", timeout=2).status_code == 200
    except httpx.HTTPError:
        return False


@pytest.mark.skipif(not _mailpit_up(), reason="Mailpit is not running (docker compose up -d)")
def test_a_notification_is_stored_and_also_arrives_as_an_email(env: Env) -> None:
    uid = env.user("mailed")
    engine = tenants.control_engine()
    with engine.begin() as c:
        address = c.execute(text("select email from app_user where id = :i"), {"i": uid}).scalar_one()
    engine.dispose()

    notifications.notify(env.db, env.slug, uid, "info", "Your request was approved", "REQ-12 is approved.")
    assert notifications.unread(env.db, uid) == [(1, "info", "Your request was approved")]
    worker.run_once()

    deadline = time.time() + 10
    found = []
    while time.time() < deadline and not found:
        found = httpx.get("http://127.0.0.1:8025/api/v1/search", params={"query": f"to:{address}"}, timeout=5).json()[
            "messages"
        ]
        time.sleep(0.3)
    assert found and found[0]["Subject"] == "Your request was approved"

    notifications.mark_read(env.db, uid, 1)
    assert notifications.unread(env.db, uid) == []
