"""Events recorded once and one workflow per business key (tasks 11a.1, 11a.2, D39 rules 1 and 4)."""

import threading
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

from p2p.platform import events, routing, tenants, worker, workflows

ROUTED: list[str] = []


@events.route("test.thing")
def _route_thing(db: Session, tenant: str, ev: events.EventRow) -> None:
    ROUTED.append(ev.key)
    workflows.join_or_start(db, "test.flow", str(ev.payload["doc"]), capability="sourcing", event_id=ev.id)


@events.route("test.broken")
def _route_broken(db: Session, tenant: str, ev: events.EventRow) -> None:
    raise RuntimeError("router fault")


@pytest.fixture
def db(control_db: str) -> Iterator[Session]:
    ROUTED.clear()
    slug = "t" + uuid.uuid4().hex[:8]
    tenants.create_tenant(slug, load_reference=False)
    with Session(routing.engine_for(slug)) as session:
        session.info["slug"] = slug
        yield session
    routing.clear_engine_cache()


def _status(db: Session, event_id: int) -> tuple[str, str | None]:
    db.rollback()
    row = db.execute(text("select status, note from event where id = :i"), {"i": event_id}).one()
    return str(row[0]), row[1]


def test_an_event_recorded_twice_is_one_row_and_a_replay_routes_nothing(db: Session) -> None:
    first = events.emit(db, "test.thing", "DOC-1:v1", source="test", payload={"doc": "DOC-1"})
    again = events.emit(db, "test.thing", "DOC-1:v1", source="test", payload={"doc": "DOC-1"})
    db.commit()

    assert first.new and not again.new and first.id == again.id
    with tenants.control_engine().connect() as c:
        jobs = c.execute(
            text("select count(*) from job where tenant_slug = :t and kind = 'event_route'"), {"t": db.info["slug"]}
        ).scalar_one()
    assert jobs == 1
    worker.run_once(tenant=db.info["slug"])
    assert ROUTED == ["DOC-1:v1"] and _status(db, first.id)[0] == "routed"


def test_twenty_threads_recording_the_same_key_keep_one_row(db: Session) -> None:
    slug = db.info["slug"]

    def rec() -> None:
        with Session(routing.engine_for(slug)) as s:
            events.record(s, "test.race", "K1", source="test")
            s.commit()

    threads = [threading.Thread(target=rec) for _ in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert db.execute(text("select count(*) from event where type = 'test.race'")).scalar_one() == 1


def test_an_event_with_no_route_is_ignored_and_a_failing_route_is_failed_and_retried(db: Session) -> None:
    quiet = events.emit(db, "test.nobody", "X", source="test", inline=True)
    db.commit()
    assert _status(db, quiet.id) == ("ignored", "no route")

    broken = events.emit(db, "test.broken", "Y", source="test", inline=True)  # undone on its own; the job tries again
    db.commit()
    assert _status(db, broken.id)[0] == "new"
    ctx = worker.JobContext(0, db.info["slug"], {"event_id": broken.id}, datetime.now(UTC), datetime.now(UTC))
    with pytest.raises(RuntimeError, match="router fault"):
        events._route_job(ctx)
    status, note = _status(db, broken.id)
    assert status == "failed" and "router fault" in str(note)


def test_two_triggers_for_one_key_join_one_workflow_and_a_new_one_starts_after_it_is_done(db: Session) -> None:
    a = events.emit(db, "test.thing", "DOC-2:v1", source="test", payload={"doc": "DOC-2"}, inline=True)
    b = events.emit(db, "test.thing", "DOC-2:v2", source="test", payload={"doc": "DOC-2"}, inline=True)
    db.commit()
    rows = db.execute(text("select id, status, first_event_id, last_event_id from workflow")).all()
    assert len(rows) == 1 and tuple(rows[0][1:]) == ("open", a.id, b.id)
    linked = db.execute(text("select distinct workflow_id from event where type = 'test.thing'")).scalars().all()
    assert linked == [rows[0][0]]

    assert workflows.close(db, rows[0][0], "done", "finished")
    again = workflows.join_or_start(db, "test.flow", "DOC-2", capability="sourcing")
    db.commit()
    assert again.started and again.id != rows[0][0]


def test_revalidation_is_fresh_on_the_same_figures_and_stale_when_they_change_or_have_a_problem(db: Session) -> None:
    figures: dict[str, Any] = {"on_hand": "10"}
    problems: list[str] = []
    workflows.register_basis(
        "test.basis", lambda s, key: workflows.Basis(workflows.hash_of(figures), dict(figures), list(problems))
    )
    expected = workflows.hash_of({"on_hand": "10"})

    assert isinstance(workflows.revalidate(db, "test.basis", "ITEM", expected), workflows.Fresh)
    figures["on_hand"] = "9"
    stale = workflows.revalidate(db, "test.basis", "ITEM", expected)
    assert isinstance(stale, workflows.Stale) and "changed" in stale.reason
    figures["on_hand"] = "10"
    problems.append("the supplier is no longer active")
    stale = workflows.revalidate(db, "test.basis", "ITEM", expected)
    assert isinstance(stale, workflows.Stale) and stale.reason == "the supplier is no longer active"
    assert isinstance(workflows.revalidate(db, "test.unregistered", "ITEM", "x"), workflows.Fresh)
