"""What the supplier communication agent read and did about an order, and a button to run its chase now."""

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import text
from sqlalchemy.orm import Session

from p2p.agents import supplier_comms
from p2p.formulas.calendar import business_today
from p2p.platform import routing
from p2p.platform.permissions import require

router = APIRouter(prefix="/api", tags=["supplier agent"])
Db = Annotated[Session, Depends(routing.tenant_session)]
Sender = Annotated[routing.TenantContext, Depends(require("po.send"))]


@router.get("/orders/{po_no}/supplier-messages")
def messages(po_no: str, db: Db, ctx: Sender) -> dict[str, Any]:
    """The supplier's emails about an order, what each was taken to be and what was done."""
    if db.execute(text("select 1 from po where no = :n"), {"n": po_no}).first() is None:
        raise HTTPException(404, "Not found")
    rows = db.execute(
        text(
            "select m.id, m.email_id, m.kind, m.status, m.action, m.task_id, m.reply_draft, m.confidence, m.created_at, "
            "e.subject, e.from_addr from supplier_message m join email_message e on e.id = m.email_id "
            "where m.linked_ref = :n and m.linked_type = 'po' order by m.id desc"
        ),
        {"n": po_no},
    ).mappings()
    commitments = db.execute(
        text(
            "select po_line_no, promised_qty, promised_date, note, created_at from po_commitment where po_no = :n order by id desc"
        ),
        {"n": po_no},
    ).mappings()
    chases = db.execute(
        text("select kind, how, sent_at from supplier_chase where po_no = :n order by id desc"), {"n": po_no}
    ).mappings()
    return {
        "messages": [dict(r) for r in rows],
        "commitments": [dict(r) for r in commitments],
        "chasers": [dict(r) for r in chases],
    }


@router.get("/rfqs/{rfq_no}/supplier-messages")
def rfq_messages(rfq_no: str, db: Db, ctx: Sender) -> dict[str, Any]:
    """The supplier's emails about a request for quotation (not an order), what each was taken to be and what was done."""
    if db.execute(text("select 1 from rfq where no = :n"), {"n": rfq_no}).first() is None:
        raise HTTPException(404, "Not found")
    rows = db.execute(
        text(
            "select m.id, m.email_id, m.kind, m.status, m.action, m.task_id, m.reply_draft, m.confidence, m.created_at, "
            "e.subject, e.from_addr from supplier_message m join email_message e on e.id = m.email_id "
            "where m.linked_ref = :n and m.linked_type = 'rfq' order by m.id desc"
        ),
        {"n": rfq_no},
    ).mappings()
    return {"messages": [dict(r) for r in rows], "commitments": [], "chasers": []}


@router.post("/supplier-agent/chase")
def chase_now(db: Db, ctx: Sender) -> dict[str, Any]:
    """Send the reminders that are due now (the worker does this once a day by itself)."""
    done = supplier_comms.chase_run(db, ctx.slug, as_of=business_today())
    return {"emailed": done.emailed, "tasks": done.tasks, "skipped": done.skipped}
