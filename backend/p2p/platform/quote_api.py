"""Quotes and supplier selection (block 6): extending an RFQ's closing date, the buyer's choice of path A or B, the buyer's
mark on a negotiation brief, and what the RFQ and request screens show of them. Services in sourcing/closing.py,
sourcing/service.py (`decide_path`) and sourcing/notes.py."""

from datetime import date
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import text
from sqlalchemy.orm import Session

from p2p.agents import recommendations
from p2p.formulas.calendar import business_today
from p2p.platform import routing
from p2p.platform.permissions import PermissionDenied, require
from p2p.sourcing import closing, notes
from p2p.sourcing import service as sourcing

router = APIRouter(prefix="/api", tags=["quotes"])
Db = Annotated[Session, Depends(routing.tenant_session)]
Sender = Annotated[routing.TenantContext, Depends(require("rfq.send"))]
Buyer = Annotated[routing.TenantContext, Depends(require("rfq.create"))]


@router.post("/rfqs/{no}/extend")
def extend_rfq(no: str, payload: dict[str, Any], db: Db, ctx: Sender) -> dict[str, Any]:
    """Move the closing date to a later working day, with a reason; every invited supplier not declined is told."""
    try:
        new_date = date.fromisoformat(str(payload.get("due_date") or ""))
    except ValueError:
        raise HTTPException(422, "due_date is a date (YYYY-MM-DD)") from None
    try:
        return closing.extend(db, no, ctx.user_id, new_date, str(payload.get("reason") or ""), today=business_today())
    except closing.ClosingError as exc:
        db.rollback()
        raise HTTPException(exc.status, str(exc)) from None


@router.post("/requests/{no}/lines/{line_no}/path")
def choose_path(no: str, line_no: int, payload: dict[str, Any], db: Db, ctx: Buyer) -> dict[str, Any]:
    """The buyer chooses path A (buy on the contract, with a reason) or B (ask for quotes) for a held line."""
    try:
        return sourcing.decide_path(
            db, ctx.slug, no, line_no, str(payload.get("path") or "").upper(), ctx.user_id,
            reason=payload.get("reason"), as_of=business_today(),
        )  # fmt: skip
    except sourcing.PathError as exc:
        db.rollback()
        raise HTTPException(exc.status, str(exc)) from None
    except sourcing.SourcingError as exc:
        db.rollback()
        raise HTTPException(409, str(exc)) from None


@router.post("/recommendations/{rec_id}/decide")
def decide_brief(rec_id: int, payload: dict[str, Any], db: Db, ctx: Buyer) -> dict[str, str]:
    """The buyer marks a negotiation brief used or dismissed. It never sets a price and never mails anyone."""
    rec = recommendations.get(db, rec_id, lock=True)
    if rec is None:
        raise HTTPException(404, "Not found")
    if rec["kind"] != "negotiation_brief":
        raise HTTPException(409, "only a negotiation brief is marked used or dismissed here")
    used = payload.get("used")
    if not isinstance(used, bool):
        raise HTTPException(422, "used is true or false")
    try:
        recommendations.decide(db, rec_id, ctx.user_id, accepted=used, note=payload.get("note") or None)
    except recommendations.RecommendationError as exc:
        db.rollback()
        raise HTTPException(exc.status, str(exc)) from None
    except PermissionDenied as exc:
        db.rollback()
        raise HTTPException(403, str(exc)) from None
    db.commit()
    return {"status": "accepted" if used else "rejected"}


# ---- for the screens ------------------------------------------------------------------------------------------------


def path_json(db: Session, rec_id: int | None) -> dict[str, Any] | None:
    rec = recommendations.get(db, int(rec_id)) if rec_id else None
    if rec is None:
        return None
    return {
        k: v for k, v in (notes.as_json(rec) or {}).items() if k in ("id", "status", "summary", "ai_note", "figures")
    }


def _iso(value: Any) -> str | None:
    return value.isoformat() if value is not None else None


def rfq_extras(db: Session, rfq: dict[str, Any]) -> dict[str, Any]:
    """What block 6 adds to the RFQ page: whether it has closed, how each quote came, the records, the open case."""
    no = str(rfq["no"])
    quotes = [
        {
            "quote_no": q["no"], "supplier_code": q["supplier_code"], "revision": q["revision"],
            "channel": q["channel"], "sender_address": q["sender_address"], "sender_auth": q["sender_auth"],
            "received_on": _iso(q["received_on"]), "valid_until": _iso(q["valid_until"]),
            "entered_by_name": q["entered_by_name"],
        }
        for q in db.execute(
            text(
                "select q.*, (select p.display_name from user_profile p where p.user_id::text = q.entered_by) "
                "as entered_by_name from quote q where q.rfq_no = :n "
                "order by q.supplier_code, q.revision"
            ),
            {"n": no},
        ).mappings()
    ]  # fmt: skip
    task = db.execute(
        text(
            "select id, title, status, payload from task where kind = :k and payload->>'rfq_no' = :n "
            "order by id desc limit 1"
        ),
        {"k": closing.NO_RESPONSE, "n": no},
    ).mappings().first()  # fmt: skip
    return {
        "closed": rfq["due_date"] < business_today(),
        "quotes": quotes,
        "recommendations": notes.for_rfq(db, no),
        "no_response_task": {
            "id": task["id"],
            "title": task["title"],
            "status": task["status"],
            **(task["payload"] or {}),
        }
        if task
        else None,
    }
