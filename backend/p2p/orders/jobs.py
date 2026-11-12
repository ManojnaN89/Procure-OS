"""The job that makes purchase orders once a request's lines are ready to be bought."""

import logging

from sqlalchemy.orm import Session

from p2p.formulas.calendar import IST
from p2p.orders import delivery_reminders, dispatch, service  # noqa: F401 - these register the send and reminder jobs
from p2p.platform import routing, worker

log = logging.getLogger(__name__)
JOB = "order_request"


@worker.register(JOB)
def _run(ctx: worker.JobContext) -> None:
    with Session(routing.engine_for(ctx.tenant)) as db:
        made = service.order_request(
            db, ctx.tenant, str(ctx.payload["request_no"]), as_of=ctx.now.astimezone(IST).date()
        )
        for note in made.skipped:
            log.info("ordering %s: %s", made.request_no, note)
