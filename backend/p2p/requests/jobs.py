"""The daily replenishment job. It runs every morning for a company and, when the company has switched on
auto_replenishment, creates the draft requests. It always queues the next morning's run, so switching the setting on
later takes effect without anyone starting anything again."""

from datetime import UTC, date, datetime, time, timedelta

from sqlalchemy import text
from sqlalchemy.orm import Session

from p2p.formulas.calendar import IST
from p2p.platform import routing, worker
from p2p.requests import replenishment

JOB = "replenishment"
RUN_AT = time(6, 0)  # India time


def next_run(now: datetime) -> datetime:
    """06:00 India time on the next calendar day (the run itself skips nothing: stock does not take weekends off)."""
    tomorrow: date = now.astimezone(IST).date() + timedelta(days=1)
    return datetime.combine(tomorrow, RUN_AT, tzinfo=IST).astimezone(UTC)


def schedule(tenant: str, run_at: datetime | None = None) -> int | None:
    """Queue the daily job for a company unless one is already waiting."""
    return worker.enqueue_unique(tenant, JOB, {}, run_at or datetime.now(UTC))


@worker.register(JOB)
def _run(ctx: worker.JobContext) -> None:
    with Session(routing.engine_for(ctx.tenant)) as db:
        on = db.execute(text("select value from app_setting where key = 'auto_replenishment'")).scalar()
        if on == "true":
            replenishment.run(db, as_of=ctx.now.astimezone(IST).date(), tenant=ctx.tenant, submit=True)
    worker.enqueue_unique(ctx.tenant, JOB, {}, next_run(ctx.now), exclude_id=ctx.id)
