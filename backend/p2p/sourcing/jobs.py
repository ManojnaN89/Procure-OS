"""The job that sources a request as soon as it is approved, and the daily strategic review detectors (task 12.5)."""

import logging
from datetime import timedelta

from sqlalchemy.orm import Session

from p2p.formulas.calendar import IST, business_today
from p2p.platform import routing, worker
from p2p.sourcing import award_approval, price_review, reminders, service  # noqa: F401 - award_approval: its event route

log = logging.getLogger(__name__)
JOB = "source_request"


@worker.register(JOB)
def _run(ctx: worker.JobContext) -> None:
    with Session(routing.engine_for(ctx.tenant)) as db:
        try:
            service.source_request(db, ctx.tenant, str(ctx.payload["request_no"]), as_of=business_today())
        except service.SourcingError as exc:
            # not approved (any more) or gone: nothing to do, and trying again later would not change that
            db.rollback()
            log.info("sourcing skipped for %s: %s", ctx.payload.get("request_no"), exc)
    reminders.schedule(ctx.tenant)  # the daily reminder job keeps itself going once it has started
    schedule_reviews(ctx.tenant)  # so do the strategic review detectors


@worker.register("award_note")
def _award_note(ctx: worker.JobContext) -> None:
    """The AI step rewords a new award's note for its approver; the template stays on any failure (block 5)."""
    from p2p.ai_steps import award_note  # noqa: PLC0415
    from p2p.ai_steps.gateway import Gateway  # noqa: PLC0415 - only the worker needs a gateway of its own

    with Session(routing.engine_for(ctx.tenant)) as db:
        award_note.explain(db, int(ctx.payload["decision_id"]), Gateway())


@worker.register("read_supplier_reply")
def _read_reply(ctx: worker.JobContext) -> None:
    from p2p.agents import capabilities  # noqa: PLC0415 - the switch imports the agents
    from p2p.ai_steps.gateway import Gateway
    from p2p.sourcing import replies

    with Session(routing.engine_for(ctx.tenant)) as db:
        if capabilities.job_allowed(db, ctx.tenant, "read_supplier_reply", ctx.payload):  # else handed to a person
            replies.read_reply(
                db, ctx.tenant, int(ctx.payload["email_id"]), Gateway(), as_of=ctx.now.astimezone(IST).date()
            )


REVIEW_JOB = "price_review_daily"
REVIEW_ITEM_JOB = "price_review_item"


@worker.register(REVIEW_JOB)
def _reviews(ctx: worker.JobContext) -> None:
    """Every contract price in force against the counting benchmarks, and contracts near their end, once a day."""
    with Session(routing.engine_for(ctx.tenant)) as db:
        price_review.run(db, ctx.tenant, ctx.now.astimezone(IST).date())
    worker.enqueue_unique(ctx.tenant, REVIEW_JOB, {}, reminders.next_run(ctx.now), exclude_id=ctx.id)


LAPSE_JOB = "prospect_lapse_daily"


@worker.register(LAPSE_JOB)
def _lapse(ctx: worker.JobContext) -> None:
    """Conditional awards whose prospect was not activated in time lapse; the buyer decides again (D-03 R10)."""
    from p2p.sourcing import prospects  # noqa: PLC0415 - prospects builds on the sourcing service

    with Session(routing.engine_for(ctx.tenant)) as db:
        prospects.lapse_due(db, ctx.tenant, ctx.now.astimezone(IST).date())
    worker.enqueue_unique(ctx.tenant, LAPSE_JOB, {}, reminders.next_run(ctx.now), exclude_id=ctx.id)


@worker.register(REVIEW_ITEM_JOB)
def _review_item(ctx: worker.JobContext) -> None:
    """A benchmark was just verified: test that item's contract prices now."""
    with Session(routing.engine_for(ctx.tenant)) as db:
        price_review.run(db, ctx.tenant, ctx.now.astimezone(IST).date(), item=str(ctx.payload["item"]))


def schedule_reviews(tenant: str) -> int | None:
    from datetime import UTC, datetime

    worker.enqueue_unique(tenant, LAPSE_JOB, {}, datetime.now(UTC))
    return worker.enqueue_unique(tenant, REVIEW_JOB, {}, datetime.now(UTC))


def queue(tenant: str, request_no: str, *, delay: timedelta | None = None) -> int:
    """Ask the worker to source a request that has just been approved."""
    from datetime import UTC, datetime

    return worker.enqueue(tenant, JOB, {"request_no": request_no}, run_at=datetime.now(UTC) + (delay or timedelta(0)))
