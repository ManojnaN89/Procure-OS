"""Events (task 11a.1, D39 rules 1 and 8): something that happened, recorded once by a key from its source.

`record` keeps an event once per type and key; a replay (the mailbox read twice, a store check run again on the same
figures) finds it already there and does nothing. `emit` records it and, when it is new, routes it: to the engine first
and then at most one capability (D49), by the router registered for its type. A router runs in the caller's transaction
(inline) or in the job `event_route`; a router that finds nothing to do writes nothing else (quiet when routine).
"""

import json
import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from p2p.platform import audit, routing, tenants, worker

log = logging.getLogger(__name__)
JOB = "event_route"
GRACE_SECONDS = 600  # a route job younger than this whose event is missing waits for its sender to commit
SOURCE = audit.SYSTEM


@dataclass(frozen=True)
class Recorded:
    id: int
    new: bool


@dataclass(frozen=True)
class EventRow:
    id: int
    type: str
    key: str
    source: str
    payload: dict[str, Any]
    occurred_at: datetime
    status: str
    workflow_id: int | None


Router = Callable[[Session, str, EventRow], None]
ROUTES: dict[str, Router] = {}


def route(event_type: str) -> Callable[[Router], Router]:
    def wrap(fn: Router) -> Router:
        ROUTES[event_type] = fn
        return fn

    return wrap


def record(
    db: Session,
    event_type: str,
    key: str,
    *,
    source: str,
    payload: dict[str, Any] | None = None,
    occurred_at: datetime | None = None,
) -> Recorded:
    """Keep the event unless one of this type and key is already kept. Does not commit."""
    got = db.execute(
        text(
            "insert into event (type, key, source, payload, occurred_at) values (:t, :k, :s, cast(:p as jsonb), :o) "
            "on conflict (type, key) do nothing returning id"
        ),
        {
            "t": event_type, "k": key, "s": source, "p": json.dumps(payload or {}, default=str),
            "o": occurred_at or datetime.now(UTC),
        },
    ).scalar()  # fmt: skip
    if got is None:
        old = db.execute(text("select id from event where type = :t and key = :k"), {"t": event_type, "k": key})
        return Recorded(int(old.scalar_one()), False)
    audit.record(db, SOURCE, "event.record", "event", got, after={"type": event_type, "key": key, "source": source})
    return Recorded(int(got), True)


def emit(
    db: Session,
    event_type: str,
    key: str,
    *,
    source: str,
    payload: dict[str, Any] | None = None,
    occurred_at: datetime | None = None,
    inline: bool = False,
) -> Recorded:
    """Record the event and, when new, queue its `event_route` job (key `event:<id>`). With `inline` it is also routed
    now, in the caller's transaction, so the work it starts is queued at once (as before events), and its job is marked
    done. An inline router that fails is undone on its own and left to the job. Does not commit."""
    got = record(db, event_type, key, source=source, payload=payload, occurred_at=occurred_at)
    if not got.new:
        return got
    tenant = tenants.slug_of(str(db.get_bind().engine.url.database or "")) or ""
    job = worker.enqueue(tenant, JOB, {"event_id": got.id}, key=f"event:{got.id}")
    if inline:
        savepoint = db.begin_nested()
        try:
            _route(db, tenant, got.id)
            savepoint.commit()
            worker.settle(job)  # routed here: the job stays as the record, and no worker spends a turn on it
        except Exception:  # noqa: BLE001 - the job tries again
            savepoint.rollback()
            log.warning("event %s (%s) could not be routed now; the job will", got.id, event_type, exc_info=True)
    return got


def _load(db: Session, event_id: int) -> EventRow | None:
    r = db.execute(
        text(
            "select id, type, key, source, payload, occurred_at, status, workflow_id from event where id = :i for update"
        ),
        {"i": event_id},
    ).first()
    if r is None:
        return None
    return EventRow(int(r[0]), str(r[1]), str(r[2]), str(r[3]), dict(r[4] or {}), r[5], str(r[6]), r[7])


def _route(db: Session, tenant: str, event_id: int) -> str:
    ev = _load(db, event_id)
    if ev is None:
        return "absent"
    if ev.status not in ("new", "failed"):  # a failed one is tried again by the job's retry
        return ev.status
    router = ROUTES.get(ev.type)
    if router is None:
        db.execute(text("update event set status = 'ignored', note = 'no route' where id = :i"), {"i": ev.id})
        return "ignored"
    router(db, tenant, ev)
    db.execute(text("update event set status = 'routed', routed_at = now() where id = :i"), {"i": ev.id})
    return "routed"


def mark_failed(db: Session, event_id: int, note: str) -> None:
    db.execute(
        text("update event set status = 'failed', note = :n where id = :i and status in ('new', 'failed')"),
        {"n": note[:500], "i": event_id},
    )
    audit.record(db, SOURCE, "event.failed", "event", event_id, reason=note[:500])


@worker.register(JOB)
def _route_job(ctx: worker.JobContext) -> None:
    event_id = int(ctx.payload["event_id"])
    with Session(routing.engine_for(ctx.tenant)) as db:
        try:
            got = _route(db, ctx.tenant, event_id)
            db.commit()
        except Exception as exc:
            db.rollback()
            mark_failed(db, event_id, f"{type(exc).__name__}: {str(exc)[:300]}")
            db.commit()
            raise
    young = (ctx.now - (ctx.created_at or ctx.now)).total_seconds() < GRACE_SECONDS
    if got == "absent" and young:
        raise RuntimeError("the event is not committed yet; try again")
