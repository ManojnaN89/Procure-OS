"""The daily payment job: a run not filed on its date is cancelled at 18:30 India time (OP-19).

The job reschedules itself, so a company gets it without anyone starting anything again.
"""

from datetime import UTC, date, datetime, time, timedelta

from sqlalchemy.orm import Session

from p2p.formulas.calendar import IST, business_today
from p2p.invoices import runs
from p2p.platform import routing, worker

JOB = "payment_run_lapse"
AT = time(18, 30)


def next_run(now: datetime, at: time = AT) -> datetime:
    tomorrow: date = now.astimezone(IST).date() + timedelta(days=1)
    return datetime.combine(tomorrow, at, tzinfo=IST).astimezone(UTC)


def schedule(tenant: str, run_at: datetime | None = None) -> int | None:
    """Queue the daily lapse check for a company unless one is already waiting."""
    return worker.enqueue_unique(tenant, JOB, {}, run_at or datetime.now(UTC))


@worker.register(JOB)
def _run(ctx: worker.JobContext) -> None:
    with Session(routing.engine_for(ctx.tenant)) as db:
        runs.lapse(db, business_today())
    worker.enqueue_unique(ctx.tenant, JOB, {}, next_run(ctx.now), exclude_id=ctx.id)
