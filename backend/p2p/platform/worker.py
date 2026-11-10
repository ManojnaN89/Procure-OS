"""The job worker: durable jobs in the control database, one loop for every tenant.

Jobs survive a restart because they are rows. A worker claims due jobs with FOR UPDATE SKIP LOCKED, so two workers
never take the same job. A job claimed by a worker that died is picked up again after the lock times out.
"""

import json
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import text

from p2p.platform import tenants

log = logging.getLogger(__name__)

LOCK_TIMEOUT = timedelta(minutes=5)
MAX_ATTEMPTS = 5
BACKOFF = timedelta(minutes=1)


@dataclass(frozen=True)
class JobContext:
    id: int
    tenant: str
    payload: dict[str, Any]
    now: datetime


Handler = Callable[[JobContext], None]
HANDLERS: dict[str, Handler] = {}


def register(kind: str) -> Callable[[Handler], Handler]:
    def wrap(fn: Handler) -> Handler:
        HANDLERS[kind] = fn
        return fn

    return wrap


def load_handlers() -> None:
    """Import the modules that register handlers."""
    from p2p.agents import intake  # noqa: F401, PLC0415 - the intake agent's email job
    from p2p.orders import jobs as order_jobs  # noqa: F401, PLC0415
    from p2p.platform import emails, notifications, tasks  # noqa: F401, PLC0415
    from p2p.requests import jobs  # noqa: F401, PLC0415
    from p2p.sourcing import jobs as sourcing_jobs  # noqa: F401, PLC0415
    from p2p.sourcing import reminders  # noqa: F401, PLC0415


def enqueue(tenant: str, kind: str, payload: dict[str, Any], run_at: datetime | None = None) -> int:
    engine = tenants.control_engine()
    with engine.begin() as c:
        return int(
            c.execute(
                text(
                    "insert into job (tenant_slug, kind, payload, run_at) "
                    "values (:t, :k, cast(:p as jsonb), coalesce(:r, now())) returning id"
                ),
                {"t": tenant, "k": kind, "p": json.dumps(payload, default=str), "r": run_at},
            ).scalar_one()
        )


def enqueue_unique(
    tenant: str, kind: str, payload: dict[str, Any], run_at: datetime, *, exclude_id: int | None = None
) -> int | None:
    """Queue a job unless the tenant already has one of this kind waiting (used for self-rescheduling polls)."""
    engine = tenants.control_engine()
    with engine.begin() as c:
        waiting = c.execute(
            text(
                "select 1 from job where tenant_slug = :t and kind = :k and status in ('queued', 'running') "
                "and id <> coalesce(:x, -1) limit 1"
            ),
            {"t": tenant, "k": kind, "x": exclude_id},
        ).first()
        if waiting:
            return None
    return enqueue(tenant, kind, payload, run_at)


def _claim(now: datetime, limit: int) -> list[tuple[int, str, str, dict[str, Any]]]:
    engine = tenants.control_engine()
    with engine.begin() as c:
        rows = c.execute(
            text(
                "select id, tenant_slug, kind, payload from job "
                "where (status = 'queued' and run_at <= :now) or (status = 'running' and locked_at < :stale) "
                "order by run_at, id limit :n for update skip locked"
            ),
            {"now": now, "stale": now - LOCK_TIMEOUT, "n": limit},
        ).all()
        ids = [r[0] for r in rows]
        if ids:
            c.execute(
                text(
                    "update job set status = 'running', locked_at = :now, attempts = attempts + 1 where id = any(:ids)"
                ),
                {"now": now, "ids": ids},
            )
        return [(r[0], r[1], r[2], r[3]) for r in rows]


def _finish(job_id: int, now: datetime, error: str | None) -> None:
    engine = tenants.control_engine()
    with engine.begin() as c:
        if error is None:
            c.execute(
                text("update job set status = 'done', finished_at = :now, last_error = null where id = :i"),
                {"now": now, "i": job_id},
            )
            return
        attempts = c.execute(text("select attempts from job where id = :i"), {"i": job_id}).scalar_one()
        if attempts >= MAX_ATTEMPTS:
            c.execute(
                text("update job set status = 'failed', finished_at = :now, last_error = :e where id = :i"),
                {"now": now, "e": error, "i": job_id},
            )
        else:
            c.execute(
                text("update job set status = 'queued', locked_at = null, last_error = :e, run_at = :r where id = :i"),
                {"e": error, "r": now + BACKOFF * attempts, "i": job_id},
            )


def run_once(now: datetime | None = None, limit: int = 10) -> int:
    """Run the jobs that are due. Returns how many were taken. One failing job never stops the others."""
    load_handlers()
    now = now or datetime.now(UTC)
    taken = _claim(now, limit)
    for job_id, tenant, kind, payload in taken:
        try:
            HANDLERS[kind](JobContext(job_id, tenant, payload, now))
            _finish(job_id, now, None)
        except Exception as exc:  # noqa: BLE001 - recorded on the job and retried
            log.warning("job %s (%s) failed: %s", job_id, kind, exc)
            _finish(job_id, now, f"{type(exc).__name__}: {str(exc)[:300]}")
    return len(taken)


def run_forever(poll_seconds: float = 5.0) -> None:  # pragma: no cover - the loop itself
    while True:
        if run_once() == 0:
            time.sleep(poll_seconds)
