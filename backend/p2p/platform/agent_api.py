"""The API of the agents that advise people: asking the invoice exceptions agent why an invoice is blocked."""

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException
from fastapi.concurrency import run_in_threadpool
from sqlalchemy import text
from sqlalchemy.orm import Session

from p2p.agents import invoice_exceptions
from p2p.invoices import service as invoices
from p2p.platform import ratelimit, routing
from p2p.platform.permissions import can_anywhere

router = APIRouter(prefix="/api", tags=["agents"])
Db = Annotated[Session, Depends(routing.tenant_session)]
Ctx = Annotated[routing.TenantContext, Depends(routing.tenant_context)]


def _json(d: invoice_exceptions.Diagnosis) -> dict[str, Any]:
    return {
        "invoice": d.invoice_no,
        "cause": d.cause,
        "fix": d.fix,
        "explanation": d.explanation,
        "vendor_email": {"subject": d.vendor_subject, "body": d.vendor_body},
        "source": d.source,
    }


def _run(slug: str, user_id: int, no: str) -> dict[str, Any] | None:
    from p2p.platform.ui_api import assistant_gateway  # noqa: PLC0415 - one gateway for the whole process

    with Session(routing.engine_for(slug)) as db:
        if not can_anywhere(db, user_id, "invoice.match"):
            raise HTTPException(403, "Not allowed: invoice.match")
        db.commit()  # the agent runtime creates an index, which would wait for any transaction still open here
        done = invoice_exceptions.explain(db, slug, user_id, no, assistant_gateway())
        return _json(done) if done else None


@router.post("/invoices/{no}/explain", dependencies=[ratelimit.limited("model")])
async def explain_invoice(no: str, ctx: Ctx) -> dict[str, Any]:
    """Look into a blocked invoice: why it is blocked, what to do about it, and a draft of the query to the supplier.

    It only advises. The block stays until a person releases it. (No database session is held open while the agent
    runs, and the permission is checked inside, which is why this does not use the usual permission dependency.)"""
    found = await run_in_threadpool(_run, ctx.slug, ctx.user_id, no)
    if found is None:
        raise HTTPException(409, "Only a blocked invoice can be looked into")
    return found


@router.get("/invoices/{no}/diagnosis")
def last_diagnosis(no: str, db: Db, ctx: Ctx) -> dict[str, Any]:
    if db.execute(text("select 1 from invoice where no = :n"), {"n": no}).first() is None:
        raise HTTPException(404, "Not found")
    if not invoices.can_read(db, ctx.user_id, no):
        raise HTTPException(403, "Not allowed: invoice.view, invoice.match or invoice.approve")
    return {"diagnosis": invoice_exceptions.latest(db, no)}
