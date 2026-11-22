"""Forward-buy recommendations (task 13.5): list, read, preview, and the buyer's and approver's decisions.

Reading needs `price.view`, `po.create` or `sourcing.route_approve`; a preview `price.view`; accepting or rejecting
`po.create` (the buyer); approving or declining is for the person the `forward_buy` approval rule named (or their
deputy), never the buyer who accepted. Quantities and prices with 4 places, money with 2, as strings.
"""

from decimal import Decimal, InvalidOperation
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session

from p2p.formulas.calendar import business_today
from p2p.platform import permissions, routing
from p2p.platform.permissions import need_any, require
from p2p.requests import forward_buy as fwd

router = APIRouter(prefix="/api/forward-buys", tags=["forward buying"])
Db = Annotated[Session, Depends(routing.tenant_session)]
Ctx = Annotated[routing.TenantContext, Depends(routing.tenant_context)]
Buyer = Annotated[routing.TenantContext, Depends(require("po.create"))]
PriceViewer = Annotated[routing.TenantContext, Depends(require("price.view"))]
READERS = ("price.view", "po.create", "sourcing.route_approve")


def _refused(exc: fwd.ForwardBuyError) -> HTTPException | JSONResponse:
    if exc.new_id is not None:
        return JSONResponse({"detail": str(exc), "new_id": exc.new_id}, status_code=exc.status)
    return HTTPException(exc.status, str(exc))


def _fail(db: Session, exc: Exception) -> Any:
    db.rollback()
    if isinstance(exc, permissions.PermissionDenied):
        raise HTTPException(403, str(exc)) from None
    assert isinstance(exc, fwd.ForwardBuyError)
    got = _refused(exc)
    if isinstance(got, JSONResponse):
        return got
    raise got from None


@router.get("")
def listing(
    db: Db, ctx: Ctx, status: str | None = None, item: str | None = None, request_no: str | None = None
) -> dict[str, Any]:
    need_any(db, ctx.user_id, *READERS)
    clauses, params = ["true"], {}
    for column, value in (("status", status), ("item_code", item), ("request_no", request_no)):
        if value:
            clauses.append(f"{column} = :{column}")
            params[column] = value
    rows = db.execute(
        text(f"select * from forward_buy where {' and '.join(clauses)} order by id desc limit 200"),  # noqa: S608 - fixed columns
        params,
    ).mappings()
    out = []
    for r in rows:
        out.append(
            {
                "id": r["id"], "item_code": r["item_code"], "location_code": r["location_code"],
                "request_no": r["request_no"], "status": r["status"], "qty": str(r["qty"]), "uom": r["uom"],
                "net_saving": str(r["net_saving"]), "value": str(r["value"]), "created_at": r["created_at"].isoformat(),
                "new_request_no": r["new_request_no"],
            }
        )  # fmt: skip
    return {"forward_buys": out}


@router.get("/preview")
def preview(
    db: Db, ctx: PriceViewer, item: str, location: str, request_no: str | None = None, line_no: int | None = None
) -> dict[str, Any]:
    """What the engine finds now, written nowhere."""
    anchor = None
    if request_no:
        anchor = fwd.anchor_line(db, request_no, line_no or 1)
        if anchor is None:
            raise HTTPException(422, f"line {line_no or 1} of {request_no} is not known")
    try:
        ev = fwd.evaluate(db, item, location, business_today(), anchor=anchor, preview=anchor is None)
    except fwd.ForwardBuyError as exc:
        raise HTTPException(exc.status, str(exc)) from None
    finally:
        db.rollback()
    return fwd.preview_json(ev)


@router.get("/{fid}")
def one(fid: int, db: Db, ctx: Ctx) -> dict[str, Any]:
    need_any(db, ctx.user_id, *READERS)
    rec = fwd.load(db, fid)
    if rec is None:
        raise HTTPException(404, "Not found")
    return fwd.as_json(db, rec)


class AcceptIn(BaseModel):
    qty: str | None = None
    reason: str | None = None


class ReasonIn(BaseModel):
    reason: str | None = None
    note: str | None = None


@router.post("/{fid}/accept", response_model=None)
def accept(fid: int, body: AcceptIn, db: Db, ctx: Buyer) -> Any:
    try:
        qty = None if body.qty in (None, "") else Decimal(str(body.qty))
    except InvalidOperation:
        raise HTTPException(422, "the quantity is a number") from None
    try:
        return fwd.accept(db, ctx.slug, fid, ctx.user_id, qty, body.reason or "", business_today())
    except (fwd.ForwardBuyError, permissions.PermissionDenied) as exc:
        return _fail(db, exc)


@router.post("/{fid}/reject", response_model=None)
def reject(fid: int, body: ReasonIn, db: Db, ctx: Buyer) -> Any:
    try:
        return fwd.reject(db, fid, ctx.user_id, body.reason or "")
    except (fwd.ForwardBuyError, permissions.PermissionDenied) as exc:
        return _fail(db, exc)


@router.post("/{fid}/approve", response_model=None)
def approve(fid: int, body: ReasonIn, db: Db, ctx: Ctx) -> Any:
    try:
        return fwd.approve(db, ctx.slug, fid, ctx.user_id, body.note, business_today())
    except (fwd.ForwardBuyError, permissions.PermissionDenied) as exc:
        return _fail(db, exc)


@router.post("/{fid}/decline", response_model=None)
def decline(fid: int, body: ReasonIn, db: Db, ctx: Ctx) -> Any:
    try:
        return fwd.decline(db, fid, ctx.user_id, body.reason or "")
    except (fwd.ForwardBuyError, permissions.PermissionDenied) as exc:
        return _fail(db, exc)
