"""The job that sources a request as soon as it is approved."""

import logging
from datetime import timedelta

from sqlalchemy.orm import Session

from p2p.formulas.calendar import IST
from p2p.platform import routing, worker
from p2p.sourcing import service

log = logging.getLogger(__name__)
JOB = "source_request"


@worker.register(JOB)
def _run(ctx: worker.JobContext) -> None:
    with Session(routing.engine_for(ctx.tenant)) as db:
        try:
            service.source_request(db, ctx.tenant, str(ctx.payload["request_no"]), as_of=ctx.now.astimezone(IST).date())
        except service.SourcingError as exc:
            # not approved (any more) or gone: nothing to do, and trying again later would not change that
            db.rollback()
            log.info("sourcing skipped for %s: %s", ctx.payload.get("request_no"), exc)


@worker.register("read_supplier_reply")
def _read_reply(ctx: worker.JobContext) -> None:
    from p2p.ai_steps.gateway import Gateway
    from p2p.sourcing import replies

    with Session(routing.engine_for(ctx.tenant)) as db:
        replies.read_reply(
            db, ctx.tenant, int(ctx.payload["email_id"]), Gateway(), as_of=ctx.now.astimezone(IST).date()
        )


def queue(tenant: str, request_no: str, *, delay: timedelta | None = None) -> int:
    """Ask the worker to source a request that has just been approved."""
    from datetime import UTC, datetime

    return worker.enqueue(tenant, JOB, {"request_no": request_no}, run_at=datetime.now(UTC) + (delay or timedelta(0)))
