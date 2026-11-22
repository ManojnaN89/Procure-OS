"""The job worker: durable jobs in the control database, one loop for every tenant.

Jobs survive a restart because they are rows. A worker claims due jobs with FOR UPDATE SKIP LOCKED, so two workers
never take the same job. A job claimed by a worker that died is picked up again after the lock times out.
"""

import hashlib
import json
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, overload

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
    created_at: datetime | None = None  # when the job was queued (a handler can tell how old its work is)


Handler = Callable[[JobContext], None]
HANDLERS: dict[str, Handler] = {}


def register(kind: str) -> Callable[[Handler], Handler]:
    def wrap(fn: Handler) -> Handler:
        HANDLERS[kind] = fn
        return fn

    return wrap


def load_handlers() -> None:
    """Import the modules that register handlers."""
    from p2p.agents import capabilities, intake, planning, supplier_comms  # noqa: F401, PLC0415 - agent jobs
    from p2p.approvals import chasing  # noqa: F401, PLC0415 - approval reminders
    from p2p.invoices import reading  # noqa: F401, PLC0415 - reads invoice mails into drafts
    from p2p.orders import jobs as order_jobs  # noqa: F401, PLC0415
    from p2p.platform import alerts, emails, events, notifications, outbox, setting_changes, tasks  # noqa: F401, PLC0415
    from p2p.requests import jobs  # noqa: F401, PLC0415
    from p2p.sourcing import jobs as sourcing_jobs  # noqa: F401, PLC0415
    from p2p.sourcing import reminders  # noqa: F401, PLC0415


@overload
def enqueue(tenant: str, kind: str, payload: dict[str, Any], run_at: datetime | None = None) -> int: ...


@overload
def enqueue(
    tenant: str, kind: str, payload: dict[str, Any], run_at: datetime | None = None, *, key: str | None
) -> int | None: ...


def enqueue(
    tenant: str, kind: str, payload: dict[str, Any], run_at: datetime | None = None, *, key: str | None = None
) -> int | None:
    """Queue a job. With a `key`, nothing is queued while a job of this kind and key waits or runs (the unique index of
    control 0006 decides, so two callers at once still make one job): returns None then."""
    engine = tenants.control_engine()
    with engine.begin() as c:
        got = c.execute(
            text(
                "insert into job (tenant_slug, kind, payload, run_at, dedupe_key) "
                "values (:t, :k, cast(:p as jsonb), coalesce(:r, now()), :key) "
                "on conflict (tenant_slug, kind, dedupe_key) where dedupe_key is not null "
                "and status in ('queued', 'running') do nothing returning id"
            ),
            {"t": tenant, "k": kind, "p": json.dumps(payload, default=str), "r": run_at, "key": key},
        ).scalar()
        return None if got is None else int(got)


def payload_key(payload: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()


def enqueue_unique(
    tenant: str,
    kind: str,
    payload: dict[str, Any],
    run_at: datetime,
    *,
    exclude_id: int | None = None,
    same_payload: bool = False,
) -> int | None:
    """Queue a job unless the tenant already has one of this kind waiting (used for self-rescheduling polls); with
    `same_payload`, one of this kind for the same payload (one job per business key). `exclude_id` is the running job
    that reschedules itself: its own key is released first, so the next run can be queued."""
    key = payload_key(payload) if same_payload else "*"
    if exclude_id is not None:
        with tenants.control_engine().begin() as c:
            c.execute(text("update job set dedupe_key = null where id = :i"), {"i": exclude_id})
    return enqueue(tenant, kind, payload, run_at, key=key)


def settle(job_id: int | None) -> None:
    """A queued job whose work was already done by its caller: finished now, so no worker takes it."""
    if job_id is None:
        return
    with tenants.control_engine().begin() as c:
        c.execute(
            text("update job set status = 'done', finished_at = now() where id = :i and status = 'queued'"),
            {"i": job_id},
        )


def _claim(
    now: datetime, limit: int, tenant: str | None = None
) -> list[tuple[int, str, str, dict[str, Any], datetime]]:
    engine = tenants.control_engine()
    with engine.begin() as c:
        rows = c.execute(
            text(
                "select id, tenant_slug, kind, payload, created_at from job "
                "where ((status = 'queued' and run_at <= :now) or (status = 'running' and locked_at < :stale)) "
                "and (cast(:t as text) is null or tenant_slug = :t) "
                # a company in cutover or suspended runs nothing: its jobs wait (D-04)
                "and tenant_slug not in (select slug from tenant where state in ('cutover', 'suspended')) "
                "order by run_at, id limit :n for update skip locked"
            ),
            {"now": now, "stale": now - LOCK_TIMEOUT, "n": limit, "t": tenant},
        ).all()
        ids = [r[0] for r in rows]
        if ids:
            c.execute(
                text(
                    "update job set status = 'running', locked_at = :now, attempts = attempts + 1 where id = any(:ids)"
                ),
                {"now": now, "ids": ids},
            )
        return [(r[0], r[1], r[2], r[3], r[4]) for r in rows]


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


def run_once(now: datetime | None = None, limit: int = 10, tenant: str | None = None) -> int:
    """Run the jobs that are due (only one company's when `tenant` is given). Returns how many were taken. One failing
    job never stops the others."""
    load_handlers()
    now = now or datetime.now(UTC)
    taken = _claim(now, limit, tenant)
    for job_id, tenant, kind, payload, created_at in taken:
        try:
            HANDLERS[kind](JobContext(job_id, tenant, payload, now, created_at))
            _finish(job_id, now, None)
        except Exception as exc:  # noqa: BLE001 - recorded on the job and retried
            log.warning("job %s (%s) failed: %s", job_id, kind, exc)
            _finish(job_id, now, f"{type(exc).__name__}: {str(exc)[:300]}")
    return len(taken)


def run_forever(poll_seconds: float = 5.0) -> None:  # pragma: no cover - the loop itself
    while True:
        if run_once() == 0:
            time.sleep(poll_seconds)
