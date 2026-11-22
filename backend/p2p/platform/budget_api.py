"""Budgets (task 3.7): the check of a request in figures, the months (locks), the year end and adjustments.

Seeing budgets needs `budget.view`; locking a month, closing the year and proposing an adjustment need `budget.manage`
(Finance). An adjustment is a proposal another holder of `budget.manage` decides (`/api/admin/proposals`). Money as
strings with two places.
"""

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session

from p2p.approvals import budget, budget_periods
from p2p.formulas.calendar import business_today
from p2p.formulas.fy import financial_year
from p2p.platform import routing
from p2p.platform.permissions import require

router = APIRouter(prefix="/api/budgets", tags=["budgets"])
Db = Annotated[Session, Depends(routing.tenant_session)]
Viewer = Annotated[routing.TenantContext, Depends(require("budget.view"))]
Manager = Annotated[routing.TenantContext, Depends(require("budget.manage"))]


def _refused(exc: budget_periods.PeriodError) -> HTTPException:
    return HTTPException(exc.status, str(exc))


@router.get("/check/{request_no}")
def check(request_no: str, db: Db, ctx: Viewer, final_prices: bool = False) -> dict[str, Any]:
    """Every budget the request is checked against, with what is held by requests, by orders and spent."""
    if db.execute(text("select 1 from request where no = :n"), {"n": request_no}).first() is None:
        raise HTTPException(404, f"request {request_no} not found")
    today = business_today()
    groups = budget.check_request(db, request_no, today, final_prices=final_prices)
    return {
        "request_no": request_no,
        "as_of": today.isoformat(),
        "fy": financial_year(today),
        "groups": [g.as_json() for g in groups],
        "summary": budget.summary(groups),
        "blocked": budget.blocked(groups),
    }


@router.get("/periods")
def periods(db: Db, ctx: Viewer, fy: str | None = None) -> dict[str, Any]:
    today = business_today()
    try:
        return budget_periods.periods(db, fy or financial_year(today), today, ctx.user_id)
    except budget_periods.PeriodError as exc:
        raise _refused(exc) from None


class LockIn(BaseModel):
    reason: str | None = None


@router.post("/periods/{period}/lock")
def lock(period: str, body: LockIn, db: Db, ctx: Manager) -> dict[str, Any]:
    try:
        return budget_periods.lock(db, period, ctx.user_id, body.reason or "", business_today())
    except budget_periods.PeriodError as exc:
        db.rollback()
        raise _refused(exc) from None


class CloseIn(BaseModel):
    note: str | None = None


@router.post("/years/{fy}/close")
def close_year(fy: str, body: CloseIn, db: Db, ctx: Manager) -> dict[str, Any]:
    try:
        return budget_periods.close_year(db, fy, ctx.user_id, body.note, business_today())
    except budget_periods.PeriodError as exc:
        db.rollback()
        raise _refused(exc) from None


@router.get("/years/{fy}")
def year(fy: str, db: Db, ctx: Viewer) -> dict[str, Any]:
    found = budget_periods.year(db, fy)
    if found is None:
        raise HTTPException(404, f"FY {fy} has not been closed")
    return found


class HolderIn(BaseModel):
    gl_code: str | None = None
    cost_centre_code: str | None = None
    location_code: str | None = None
    period: str | None = None


class AdjustmentIn(HolderIn):
    kind: str
    amount: str | None = None
    to: HolderIn | None = None
    reason: str | None = None


@router.post("/adjustments", status_code=201)
def adjust(body: AdjustmentIn, db: Db, ctx: Manager) -> dict[str, Any]:
    try:
        pid = budget_periods.propose_adjustment(db, ctx.user_id, body.model_dump())
    except budget_periods.PeriodError as exc:
        db.rollback()
        raise _refused(exc) from None
    return {"proposal_id": pid, "status": "pending"}
