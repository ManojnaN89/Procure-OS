"""Strategic sourcing on screen (block 5): confirming RFQs to send (D42), period RFQs (12.1) and award decisions (12.2).

Services in sourcing/service.py (confirm_many), sourcing/period.py and sourcing/award_approval.py; the award itself is
`POST /api/rfqs/{no}/award` (platform/ui_api.py), which on a period RFQ makes the award decision decided here.
"""

from datetime import date
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import text
from sqlalchemy.orm import Session

from p2p.formulas.calendar import business_today
from p2p.platform import routing
from p2p.platform.permissions import PermissionDenied, require
from p2p.platform.sod import user
from p2p.sourcing import award_approval, period, price_review, service

router = APIRouter(prefix="/api", tags=["strategic sourcing"])
Db = Annotated[Session, Depends(routing.tenant_session)]
Buyer = Annotated[routing.TenantContext, Depends(require("rfq.create"))]
Sender = Annotated[routing.TenantContext, Depends(require("rfq.send"))]
Approver = Annotated[routing.TenantContext, Depends(require("po.approve"))]


def _day(value: Any, name: str) -> date:
    try:
        return date.fromisoformat(str(value))
    except ValueError:
        raise HTTPException(422, f"{name} must be a date (yyyy-mm-dd)") from None


def _http(exc: period.PeriodError | price_review.ReviewError | award_approval.AwardError) -> HTTPException:
    return HTTPException(exc.status, str(exc))


# ---- D42: the buyer confirms sending -------------------------------------------------------------------------------


@router.post("/rfqs/confirm-send")
def confirm_send(payload: dict[str, Any], db: Db, ctx: Sender) -> dict[str, Any]:
    """Confirm and send several RFQs waiting for a buyer; each result says sent or why not."""
    names = payload.get("rfqs")
    if not isinstance(names, list) or not all(isinstance(n, str) for n in names):
        raise HTTPException(422, "rfqs is a list of RFQ numbers")
    try:
        done = service.confirm_many(db, names, ctx.user_id)
    except service.SourcingError as exc:
        raise HTTPException(422, str(exc)) from None
    except PermissionDenied as exc:
        raise HTTPException(403, str(exc)) from None
    return {"results": [r.as_json() for r in done]}


# ---- period RFQs ----------------------------------------------------------------------------------------------------


@router.get("/rfqs/period/form")
def form(db: Db, ctx: Buyer) -> dict[str, Any]:
    """What the New period RFQ form offers: the locations and the items a period RFQ may ask for (not capital)."""
    return {
        "locations": [
            {"code": r[0], "name": r[1]} for r in db.execute(text("select code, name from location order by code"))
        ],
        "items": [
            {"code": r[0], "name": r[1], "category": r[2], "uom": r[3]}
            for r in db.execute(
                text(
                    "select code, name, category_code, coalesce(purchase_uom, base_uom) from item "
                    "where coalesce(active, true) and not coalesce(capex, false) order by code"
                )
            )
        ],
    }


@router.get("/rfqs/period/suggest")
def suggest(
    db: Db, ctx: Buyer, item_code: str = "", location_code: str = "", period_from: str = "", period_to: str = ""
) -> dict[str, Any]:
    start, end = _day(period_from, "period_from"), _day(period_to, "period_to")
    if end < start:
        raise HTTPException(422, "the period ends before it starts")
    if not item_code or not location_code:
        raise HTTPException(422, "name the item and the location")
    try:
        return period.suggest(db, item_code, location_code, start, end, business_today()).as_json()
    except period.PeriodError as exc:
        raise _http(exc) from None


@router.post("/rfqs/period", status_code=201)
def create_period_rfq(payload: dict[str, Any], db: Db, ctx: Buyer) -> dict[str, Any]:
    """A buyer asks prices for a period with no request; the RFQ waits for a buyer to confirm sending (D42)."""
    lines = payload.get("lines")
    if not isinstance(lines, list) or not all(isinstance(x, dict) for x in lines):
        raise HTTPException(422, "lines is a list of {item_code, qty}")
    review = payload.get("review_task_id")
    try:
        got = period.create(
            db, ctx.slug, user(ctx.user_id), location=str(payload.get("location_code") or ""),
            period_from=_day(payload.get("period_from"), "period_from"),
            period_to=_day(payload.get("period_to"), "period_to"), lines=lines, as_of=business_today(),
            review_task_id=int(review) if review not in (None, "") else None, note=payload.get("note"),
        )  # fmt: skip
    except (period.PeriodError, price_review.ReviewError) as exc:
        db.rollback()
        raise _http(exc) from None
    except ValueError as exc:
        db.rollback()
        raise HTTPException(422, str(exc)) from None
    return {
        "rfq_no": got.rfq_no, "status": "draft", "proposal_id": got.proposal_id, "invited": got.invited,
        "min_quotes": got.min_quotes, "note": got.note, "lines": got.lines,
    }  # fmt: skip


@router.post("/rfqs/{no}/cancel")
def cancel(no: str, payload: dict[str, Any], db: Db, ctx: Buyer) -> dict[str, str]:
    try:
        period.cancel(db, no, user(ctx.user_id), str(payload.get("reason") or ""))
    except period.PeriodError as exc:
        db.rollback()
        raise _http(exc) from None
    return {"status": "cancelled"}


@router.get("/reviews/{task_id}/period-rfq")
def prefill(task_id: int, db: Db, ctx: Buyer) -> dict[str, Any]:
    """What a period RFQ started from a strategic review would ask (the buyer may change any of it)."""
    try:
        return period.prefill_from_review(db, task_id, business_today())
    except period.PeriodError as exc:
        raise _http(exc) from None


# ---- the award decision ---------------------------------------------------------------------------------------------


def _decide(call: Any) -> Any:
    try:
        return call()
    except award_approval.AwardError as exc:
        raise _http(exc) from None
    except PermissionDenied as exc:
        raise HTTPException(403, str(exc)) from None


@router.post("/rfqs/{no}/award/approve")
def approve_award(no: str, payload: dict[str, Any], db: Db, ctx: Approver) -> dict[str, Any]:
    return dict(_decide(lambda: award_approval.approve(db, ctx.slug, no, ctx.user_id, payload.get("note") or None)))


@router.post("/rfqs/{no}/award/reject")
def reject_award(no: str, payload: dict[str, Any], db: Db, ctx: Approver) -> dict[str, str]:
    _decide(lambda: award_approval.reject(db, ctx.slug, no, ctx.user_id, str(payload.get("reason") or "")))
    return {"status": "rejected"}


@router.post("/rfqs/{no}/award/withdraw")
def withdraw_award(no: str, payload: dict[str, Any], db: Db, ctx: Buyer) -> dict[str, str]:
    _decide(lambda: award_approval.withdraw(db, no, ctx.user_id, str(payload.get("reason") or "")))
    return {"status": "withdrawn"}
