"""Close (R4, R5, R6, R7, R8): GSTR-2B, standard prices, the price measures, the month-end close and the export."""

from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Annotated, Any

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session

from p2p.approvals import budget_periods
from p2p.formulas.calendar import business_today
from p2p.formulas.fy import financial_year
from p2p.invoices import accounting_export, period_close, price_measures
from p2p.invoices import gstr2b as gst
from p2p.masters import standard_prices
from p2p.platform import files, ratelimit, routing
from p2p.platform.permissions import require

router = APIRouter(prefix="/api", tags=["close"])
Db = Annotated[Session, Depends(routing.tenant_session)]
Ctx = Annotated[routing.TenantContext, Depends(routing.tenant_context)]
Close = Annotated[routing.TenantContext, Depends(require("budget.manage"))]


def wire(value: Any) -> Any:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, dict):
        return {k: wire(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [wire(v) for v in value]
    return value


def _refused(exc: Exception) -> HTTPException:
    status = getattr(exc, "status", 422)
    return HTTPException(status, str(exc))


# ---- GSTR-2B ------------------------------------------------------------------------------------


@router.post("/gst/gstr2b", dependencies=[ratelimit.limited("upload")])
async def import_gstr2b(
    db: Db, ctx: Annotated[routing.TenantContext, Depends(require("gst.reconcile"))],
    file: Annotated[UploadFile, File()],
) -> dict[str, Any]:  # fmt: skip
    data = await file.read(files.MAX_BYTES + 1)
    if len(data) > files.MAX_BYTES:
        raise HTTPException(413, "File is too large")
    try:
        done = gst.import_file(db, data, file.filename or "gstr2b.json", ctx.user_id, tenant=ctx.slug)
    except gst.Gstr2bError as exc:
        raise _refused(exc) from None
    return wire(done)  # type: ignore[no-any-return]


@router.get("/gst/gstr2b")
def list_gstr2b(
    db: Db, ctx: Annotated[routing.TenantContext, Depends(require("invoice.view"))],
    period: str | None = None, gstin: str | None = None,
) -> list[dict[str, Any]]:  # fmt: skip
    return wire(gst.current(db, gstin, period))  # type: ignore[no-any-return]


@router.get("/gst/gstr2b/template")
def gstr2b_template(db: Db, ctx: Annotated[routing.TenantContext, Depends(require("gst.reconcile"))]) -> Response:
    return Response(
        content=("ï»¿" + gst.TEMPLATE).encode("utf-8"),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="gstr2b.csv"'},
    )


@router.get("/gst/gstr2b/{import_id}")
def one_gstr2b(
    import_id: int, db: Db, ctx: Annotated[routing.TenantContext, Depends(require("invoice.view"))]
) -> dict[str, Any]:
    found = gst.one(db, import_id)
    if found is None:
        raise HTTPException(404, "Not found")
    return wire(found)  # type: ignore[no-any-return]


@router.get("/gst/not-in-2b")
def not_in_2b(
    db: Db, ctx: Annotated[routing.TenantContext, Depends(require("invoice.view"))],
    as_of: str | None = None, gstin: str | None = None,
) -> list[dict[str, Any]]:  # fmt: skip
    try:
        on = date.fromisoformat(as_of) if as_of else business_today()
    except ValueError:
        raise HTTPException(422, "as_of must be a date like 2026-11-30") from None
    return wire(gst.not_in_2b(db, on, gstin))  # type: ignore[no-any-return]


# ---- standard prices ----------------------------------------------------------------------------


@router.get("/prices/standard")
def standard_versions(
    db: Db, ctx: Annotated[routing.TenantContext, Depends(require("price.view"))], item: str | None = None
) -> Any:
    if item:
        return wire(standard_prices.versions(db, item))
    return wire(
        [
            dict(r)
            for r in db.execute(
                text(
                    "select distinct on (item_code) item_code, price, version, effective_from, effective_to, status "
                    "from item_standard_price where status = 'active' order by item_code, effective_from desc, version desc"
                )
            ).mappings()
        ]
    )


class StandardIn(BaseModel):
    item: str
    price: str
    effective_from: str
    reason: str | None = None


@router.post("/prices/standard", status_code=201)
def propose_standard(
    body: StandardIn, db: Db, ctx: Annotated[routing.TenantContext, Depends(require("price.standard"))]
) -> dict[str, Any]:
    try:
        start = date.fromisoformat(body.effective_from)
        amount = Decimal(body.price)
    except (ValueError, InvalidOperation):
        raise HTTPException(422, "price and effective_from must be a number and a date") from None
    try:
        pid = standard_prices.propose(db, ctx.user_id, body.item, amount, start, body.reason, business_today())
    except standard_prices.StandardPriceError as exc:
        db.rollback()
        raise _refused(exc) from None
    return {"proposal_id": pid, "status": "pending"}


# ---- the measures -------------------------------------------------------------------------------


@router.get("/prices/measures")
def measures(
    db: Db, ctx: Annotated[routing.TenantContext, Depends(require("price.view"))],
    measure: str | None = None, from_: Annotated[str | None, Query(alias="from")] = None,
    to: str | None = None, item: str | None = None, supplier: str | None = None,
) -> dict[str, Any]:  # fmt: skip
    if measure and measure not in price_measures.LABELS:
        raise HTTPException(422, "measure is one of " + ", ".join(price_measures.LABELS))
    try:
        start = date.fromisoformat(from_) if from_ else None
        end = date.fromisoformat(to) if to else None
    except ValueError:
        raise HTTPException(422, "from and to must be dates like 2026-11-30") from None
    return wire(price_measures.measures(db, measure=measure, on_from=start, on_to=end, item=item, supplier=supplier))  # type: ignore[no-any-return]


# ---- the month-end close ------------------------------------------------------------------------


@router.get("/close/periods")
def periods(
    db: Db, ctx: Annotated[routing.TenantContext, Depends(require("payment.view"))], fy: str | None = None
) -> Any:
    today = business_today()
    year = fy or financial_year(today)
    try:
        listed = budget_periods.periods(db, year, today, ctx.user_id)
    except budget_periods.PeriodError as exc:
        raise _refused(exc) from None
    closed = {str(r[0]): r for r in db.execute(text("select period, closed_at, grni_total from period_close")).all()}
    for row in listed["periods"]:
        if row["period"] in closed:
            row["status"] = "closed"
            row["grni_total"] = str(closed[row["period"]][2])
    return wire(listed)


@router.get("/close/periods/{period}")
def period(
    period: str, db: Db, ctx: Annotated[routing.TenantContext, Depends(require("payment.view"))]
) -> dict[str, Any]:
    try:
        return wire(period_close.preview(db, period))  # type: ignore[no-any-return]
    except period_close.PeriodCloseError as exc:
        raise _refused(exc) from None


class CloseIn(BaseModel):
    reason: str | None = None


@router.post("/close/periods/{period}/close")
def close_period(period: str, body: CloseIn, db: Db, ctx: Close) -> dict[str, Any]:
    try:
        return wire(period_close.close(db, period, ctx.user_id, body.reason or ""))  # type: ignore[no-any-return]
    except period_close.PeriodCloseError as exc:
        db.rollback()
        raise _refused(exc) from None
    except accounting_export.ExportError as exc:
        db.rollback()
        raise _refused(exc) from None


@router.get("/close/periods/{period}/grni")
def grni(
    period: str, db: Db, ctx: Annotated[routing.TenantContext, Depends(require("invoice.view"))]
) -> dict[str, Any]:
    found = period_close.grni(db, period)
    if found is None:
        raise HTTPException(404, f"{period} is not closed")
    return wire(found)  # type: ignore[no-any-return]


@router.get("/close/exports/{period}")
def export_file(
    period: str, db: Db, ctx: Annotated[routing.TenantContext, Depends(require("accounts.export"))],
    kind: str = "draft",
) -> Response:  # fmt: skip
    try:
        found = accounting_export.file(db, period, kind, ctx.user_id)
    except accounting_export.ExportError as exc:
        db.rollback()
        raise _refused(exc) from None
    if kind == "final":
        content = str(found["content"])
    else:
        content = str(found["content"])
    return Response(
        content=("ï»¿" + content).encode("utf-8"),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="accounts-{period}-{kind}.csv"'},
    )


@router.get("/close/msme-year-end")
def msme_year_end(
    db: Db, ctx: Annotated[routing.TenantContext, Depends(require("invoice.view"))], fy: str | None = None
) -> dict[str, Any]:
    year = fy or financial_year(business_today())
    try:
        return wire(period_close.msme_year_end(db, year))  # type: ignore[no-any-return]
    except period_close.PeriodCloseError as exc:
        raise _refused(exc) from None
