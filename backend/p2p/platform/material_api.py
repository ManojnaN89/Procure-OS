"""Material requests for the store: the list with what is set aside and what is short, the store check, issue at
production start, cancelling, and making them for production orders already imported."""

from decimal import Decimal, InvalidOperation
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from p2p.formulas.money import format_inr, format_qty
from p2p.platform import routing
from p2p.platform.permissions import can_anywhere, require
from p2p.platform.sod import user
from p2p.receiving.stock import StockError
from p2p.requests import material

router = APIRouter(prefix="/api/material-requests", tags=["material requests"])
Db = Annotated[Session, Depends(routing.tenant_session)]
Viewer = Annotated[routing.TenantContext, Depends(require("stock.view"))]
Store = Annotated[routing.TenantContext, Depends(require("material.manage"))]
QTY_FIELDS = ("need_qty", "reserved_qty", "issued_qty", "requested_qty", "short")


def _shown(head: dict[str, Any]) -> dict[str, Any]:
    """Quantities as text (never a float on the way to the screen), dates as ISO."""
    lines = [
        {
            **{k: v for k, v in ln.items() if k not in QTY_FIELDS and k != "need_by"},
            **{k: format_qty(ln[k]) for k in QTY_FIELDS},
            "need_by": ln["need_by"].isoformat(),
        }
        for ln in head["lines"]
    ]
    return {
        **{k: v for k, v in head.items() if k not in ("lines", "need_by", "created_at")},
        "need_by": head["need_by"].isoformat(),
        "created_at": head["created_at"].isoformat(),
        "lines": lines,
    }


@router.get("")
def list_requests(db: Db, ctx: Viewer, status: str | None = None) -> dict[str, Any]:
    return {
        "can_manage": can_anywhere(db, ctx.user_id, "material.manage"),
        "requests": [_shown(h) for h in material.listing(db, status=status)],
    }


@router.post("/make")
def make(db: Db, ctx: Store) -> dict[str, Any]:
    """Material requests for production orders already imported that have none yet."""
    return {"material_requests": material.make_requests(db, user(ctx.user_id))}


@router.post("/check")
def check(db: Db, ctx: Store, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    """The store check: set free stock aside, earliest need-by first, and send what is short for approval."""
    payload = payload or {}
    result = material.store_check(
        db,
        user(ctx.user_id),
        tenant=ctx.slug,
        location=str(payload.get("location") or "") or None,
        submit=payload.get("submit", True) is not False,
    )
    return {
        "checked": [
            {
                "no": c.mr_no,
                "order_no": c.order_no,
                "reserved_now": {ln.item_code: format_qty(ln.reserved_now) for ln in c.lines if ln.reserved_now},
                "short": {ln.item_code: format_qty(ln.short) for ln in c.lines if ln.short},
                "unpriced": c.unpriced,
                "purchase_request": None
                if c.request is None
                else {
                    "no": c.request.no,
                    "lines": len(c.request.lines),
                    "total_value": str(c.request.total_value),
                    "total_shown": format_inr(c.request.total_value),
                    "route": c.request.route,
                    "outcome": c.request.outcome,
                    "submit_error": c.request.submit_error,
                },
            }
            for c in result.requests
        ]
    }


def _quantities(payload: dict[str, Any]) -> dict[int, Decimal] | None:
    given = payload.get("lines")
    if given is None:
        return None
    if not isinstance(given, dict):
        raise HTTPException(422, "lines must map line numbers to quantities")
    try:
        out = {int(k): Decimal(str(v)) for k, v in given.items()}
    except (ValueError, InvalidOperation):
        raise HTTPException(422, "lines must map line numbers to quantities") from None
    if not all(q.is_finite() for q in out.values()):
        raise HTTPException(422, "quantities must be numbers")
    return out


@router.post("/{no}/issue")
def issue(no: str, db: Db, ctx: Store, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    """Production starts: issue what is set aside (all of it, or {"lines": {line_no: qty}})."""
    try:
        issued = material.issue(db, ctx.user_id, no, _quantities(payload or {}))
    except (material.MaterialError, StockError) as exc:
        raise HTTPException(404 if "there is no" in str(exc) else 409, str(exc)) from None
    return {"issued": {k: format_qty(v) for k, v in issued.items()}}


@router.post("/{no}/cancel")
def cancel(no: str, payload: dict[str, Any], db: Db, ctx: Store) -> dict[str, Any]:
    try:
        left = material.cancel(db, ctx.user_id, no, str(payload.get("reason") or ""))
    except material.MaterialError as exc:
        raise HTTPException(404 if "there is no" in str(exc) else 409, str(exc)) from None
    return {"status": "cancelled", "purchase_requests_left": left}
