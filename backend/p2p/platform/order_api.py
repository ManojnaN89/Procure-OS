"""The order's document, its versions, and changing it after it was made: revise, cancel, close the balance; the changes a
supplier asked for and its dispatch notes."""

from collections.abc import Callable
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy import text
from sqlalchemy.orm import Session

from p2p.approvals.workflow import ApprovalError
from p2p.orders import changes, confirmation, dispatch_notes, document
from p2p.platform import routing
from p2p.platform.permissions import PermissionDenied, can_anywhere, require

router = APIRouter(prefix="/api", tags=["orders"])
Db = Annotated[Session, Depends(routing.tenant_session)]
Ctx = Annotated[routing.TenantContext, Depends(routing.tenant_context)]
Buyer = Annotated[routing.TenantContext, Depends(require("po.create"))]


def _guard(call: Callable[[], dict[str, Any]]) -> dict[str, Any]:
    try:
        return call()
    except changes.ChangeError as exc:
        message = str(exc)
        raise HTTPException(404 if "does not exist" in message else 409, message) from None
    except ApprovalError as exc:
        raise HTTPException(409, str(exc)) from None


@router.get("/purchase-orders/{no}/pdf")
def order_pdf(no: str, db: Db, ctx: Ctx) -> Response:
    try:
        pdf = document.order_document(db, no)
    except document.DocumentError:
        raise HTTPException(404, "Not found") from None
    return Response(pdf, media_type="application/pdf", headers={"Content-Disposition": f'inline; filename="{no}.pdf"'})


@router.get("/purchase-orders/{no}/controls")
def order_controls(no: str, db: Db, ctx: Ctx) -> dict[str, Any]:
    """The order's version and history, and what the caller may do to it now."""
    po = db.execute(text("select status, version from po where no = :n"), {"n": no}).first()
    if po is None:
        raise HTTPException(404, "Not found")
    may = can_anywhere(db, ctx.user_id, "po.create")
    received = db.execute(
        text("select coalesce(sum(received_qty), 0) from po_line where po_no = :n and status <> 'cancelled'"), {"n": no}
    ).scalar_one()
    return {
        "version": po[1],
        "history": changes.history(db, no),
        "can_revise": may and po[0] in changes.REVISABLE,
        "can_cancel": may and po[0] in changes.CANCELLABLE and received == 0,
        "can_close_balance": may and po[0] in changes.CLOSABLE and received > 0,
    }


def _date(value: Any, field: str) -> date | None:
    if value in (None, ""):
        return None
    try:
        return date.fromisoformat(str(value))
    except ValueError:
        raise HTTPException(422, f"{field} must be a date like 2026-11-20") from None


def _qty(value: Any, field: str) -> Decimal | None:
    if value in (None, ""):
        return None
    try:
        number = Decimal(str(value))
    except InvalidOperation:
        raise HTTPException(422, f"{field} is not a number") from None
    if not number.is_finite():
        raise HTTPException(422, f"{field} is not a number")
    return number


@router.post("/purchase-orders/{no}/revise")
def revise_order(no: str, payload: dict[str, Any], db: Db, ctx: Buyer) -> dict[str, Any]:
    raw = payload.get("lines")
    if not isinstance(raw, list) or not raw:
        raise HTTPException(422, "lines is required: a list of {line_no, qty and/or need_by}")
    edits: list[changes.LineChange] = []
    for item in raw:
        if not isinstance(item, dict) or not str(item.get("line_no", "")).isdigit():
            raise HTTPException(422, "each line needs a line_no")
        edits.append(
            changes.LineChange(
                int(item["line_no"]), _qty(item.get("qty"), "qty"), _date(item.get("need_by"), "need_by")
            )
        )
    return _guard(lambda: changes.revise(db, ctx.slug, no, ctx.user_id, payload.get("reason"), edits))


@router.post("/purchase-orders/{no}/cancel")
def cancel_order(no: str, payload: dict[str, Any], db: Db, ctx: Buyer) -> dict[str, Any]:
    return _guard(
        lambda: changes.cancel(
            db, ctx.slug, no, ctx.user_id, payload.get("reason"), reorder=bool(payload.get("reorder"))
        )
    )


@router.post("/purchase-orders/{no}/close-balance")
def close_balance(no: str, payload: dict[str, Any], db: Db, ctx: Buyer) -> dict[str, Any]:
    return _guard(lambda: changes.close_balance(db, ctx.slug, no, ctx.user_id, payload.get("reason")))


# ---- the supplier's answer: proposed changes and dispatch notes -------------------------------------


@router.get("/purchase-orders/{no}/supplier-answer")
def supplier_answer(no: str, db: Db, ctx: Ctx) -> dict[str, Any]:
    """The changes the supplier asked for (and who may decide them) and its dispatch notes."""
    if db.execute(text("select 1 from po where no = :n"), {"n": no}).first() is None:
        raise HTTPException(404, "Not found")
    return {
        "changes": confirmation.list_changes(db, no, ctx.user_id),
        "dispatch_notes": dispatch_notes.notes(db, no),
    }


@router.post("/supplier-changes/{change_id}/decide")
def decide_supplier_change(change_id: int, payload: dict[str, Any], db: Db, ctx: Ctx) -> dict[str, Any]:
    """A buyer accepts or rejects a change; a price above the tolerance then waits for the purchase manager."""
    accept = payload.get("accept")
    if not isinstance(accept, bool):
        raise HTTPException(422, "accept must be true or false")
    try:
        return confirmation.decide(db, ctx.slug, change_id, ctx.user_id, accept=accept, note=payload.get("note"))
    except confirmation.ConfirmationError as exc:
        raise HTTPException(404 if "does not exist" in str(exc) else 409, str(exc)) from None
    except PermissionDenied as exc:
        raise HTTPException(403, str(exc)) from None
