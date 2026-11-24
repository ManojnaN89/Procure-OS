"""Reconciliation (plan step 13): the order trace, received not invoiced, supplier statements, bank lines nobody matched,
and small suppliers' invoices against the MSMED Act deadline."""

from datetime import date
from decimal import Decimal
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, UploadFile
from fastapi.responses import Response
from sqlalchemy.orm import Session

from p2p.formulas.calendar import business_today
from p2p.formulas.fy import financial_year
from p2p.invoices import bank_statement, reconciliation, trace, vendor_statement
from p2p.invoices.service import InvoiceError
from p2p.platform import files, ratelimit, routing, settings
from p2p.platform.permissions import need_any, require

router = APIRouter(prefix="/api", tags=["reconciliation"])
Db = Annotated[Session, Depends(routing.tenant_session)]
Ctx = Annotated[routing.TenantContext, Depends(routing.tenant_context)]
TRACE = ("po.create", "po.approve", "request.view", "grn.create", "invoice.view", "payment.view", "audit.view")
BOOKS = ("invoice.view", "payment.view")


def wire(value: Any) -> Any:
    """Money and quantities as exact text, dates as ISO text."""
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, dict):
        return {k: wire(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [wire(v) for v in value]
    return value


def _day(value: str | None, field: str) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise HTTPException(422, f"{field} must be a date like 2026-11-20") from None


@router.get("/trace/{no}")
def order_trace(no: str, db: Db, ctx: Ctx) -> dict[str, Any]:
    """The whole chain one document belongs to, from the production order to the payment."""
    need_any(db, ctx.user_id, *TRACE)
    found = trace.chain(db, no)
    if found is None:
        raise HTTPException(404, f"No document has the number {no}")
    return wire(found)  # type: ignore[no-any-return]


@router.get("/reconciliation/received-not-invoiced")
def received_not_invoiced(db: Db, ctx: Ctx, as_of: str | None = None, supplier: str | None = None) -> dict[str, Any]:
    """Per order line, the value received and not invoiced (and invoiced and not received) on a date, with its age."""
    need_any(db, ctx.user_id, *BOOKS)
    return wire(reconciliation.received_vs_invoiced(db, _day(as_of, "as_of") or business_today(), supplier or None))  # type: ignore[no-any-return]


@router.get("/reconciliation/msme")
def msme(db: Db, ctx: Ctx, as_of: str | None = None, within: int | None = None) -> dict[str, Any]:
    """Unpaid invoices of micro and small suppliers past their deadline, or reaching it within the days given."""
    need_any(db, ctx.user_id, *BOOKS)
    days = within if within is not None else int(settings.number(db, "msme_warn_days", "7"))
    if days < 0:
        raise HTTPException(422, "within must be 0 or more days")
    on = _day(as_of, "as_of") or business_today()
    return wire({"as_of": on, "within": days, "invoices": reconciliation.msme_due(db, on, days)})  # type: ignore[no-any-return]


@router.get("/reconciliation/vendor-statements/template")
def statement_template(db: Db, ctx: Ctx) -> Response:
    need_any(db, ctx.user_id, "invoice.book")
    return Response(
        content=("ï»¿" + vendor_statement.TEMPLATE).encode("utf-8"),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="supplier-statement.csv"'},
    )


@router.get("/reconciliation/vendor-statements")
def list_statements(db: Db, ctx: Ctx, supplier: str | None = None) -> list[dict[str, Any]]:
    need_any(db, ctx.user_id, *BOOKS)
    return wire(vendor_statement.statements(db, supplier or None))  # type: ignore[no-any-return]


@router.get("/reconciliation/vendor-statements/{statement_id}")
def get_statement(statement_id: int, db: Db, ctx: Ctx) -> dict[str, Any]:
    need_any(db, ctx.user_id, *BOOKS)
    found = vendor_statement.statement(db, statement_id)
    if found is None:
        raise HTTPException(404, "Not found")
    return wire(found)  # type: ignore[no-any-return]


@router.post("/reconciliation/vendor-statements", dependencies=[ratelimit.limited("upload")])
async def import_statement(
    file: UploadFile,
    db: Db,
    ctx: Annotated[routing.TenantContext, Depends(require("invoice.book"))],
    supplier: str,
    period_from: str | None = None,
    period_to: str | None = None,
) -> dict[str, Any]:
    """A supplier's statement of account (CSV or Excel in the template), matched against our books and kept."""
    data = await file.read(files.MAX_BYTES + 1)
    if len(data) > files.MAX_BYTES:
        raise HTTPException(413, "File is too large")
    try:
        done = vendor_statement.import_statement(
            db, supplier, data, file.filename or "statement.csv", ctx.user_id,
            period_from=_day(period_from, "period_from"), period_to=_day(period_to, "period_to"),
        )  # fmt: skip
    except InvoiceError as exc:
        raise HTTPException(404 if "does not exist" in str(exc) else 422, str(exc)) from None
    return wire(done)  # type: ignore[no-any-return]


@router.get("/reconciliation/bank-lines")
def bank_lines(db: Db, ctx: Ctx) -> list[dict[str, Any]]:
    """Bank statement lines no payment fitted, each with the payments it could be."""
    need_any(db, ctx.user_id, "payment.view", "payment.propose")
    return wire(bank_statement.open_lines(db))  # type: ignore[no-any-return]


@router.post("/reconciliation/bank-lines/{line_id}/confirm")
def confirm_bank_line(
    line_id: int,
    payload: dict[str, Any],
    db: Db,
    ctx: Annotated[routing.TenantContext, Depends(require("payment.propose"))],
) -> dict[str, Any]:
    """A person matches an open bank line to a payment (the amounts must agree); a complete run is then marked paid."""
    payment_no = payload.get("payment")
    if not isinstance(payment_no, str) or not payment_no.strip():
        raise HTTPException(422, "name the payment in 'payment'")
    try:
        return bank_statement.confirm(
            db, line_id, payment_no.strip(), ctx.user_id, today=business_today(), tenant=ctx.slug
        )
    except InvoiceError as exc:
        message = str(exc)
        status = 404 if "does not exist" in message else 403 if message.startswith("segregation") else 409
        raise HTTPException(status, message) from None


@router.post("/reconciliation/bank-lines/{line_id}/not-ours")
def not_ours_bank_line(
    line_id: int,
    payload: dict[str, Any],
    db: Db,
    ctx: Annotated[routing.TenantContext, Depends(require("payment.propose"))],
) -> dict[str, Any]:
    """A person says an open bank line is no payment of ours (bank charges), with a reason (R3.3)."""
    reason = payload.get("reason")
    if not isinstance(reason, str) or not reason.strip():
        raise HTTPException(422, "a reason is needed to mark a bank line as not ours")
    try:
        return wire(bank_statement.not_ours(db, line_id, reason, ctx.user_id, tenant=ctx.slug))  # type: ignore[no-any-return]
    except InvoiceError as exc:
        message = str(exc)
        raise HTTPException(404 if "does not exist" in message else 409, message) from None


@router.post("/reconciliation/vendor-statements/{statement_id}/lines/{line_id}/resolve")
def resolve_statement_line(
    statement_id: int,
    line_id: int,
    payload: dict[str, Any],
    db: Db,
    ctx: Annotated[routing.TenantContext, Depends(require("invoice.book"))],
) -> dict[str, Any]:
    """AP marks a difference on a supplier's statement explained, with a note (R3.2)."""
    try:
        return wire(vendor_statement.resolve(db, statement_id, line_id, payload.get("note"), ctx.user_id))  # type: ignore[no-any-return]
    except InvoiceError as exc:
        message = str(exc)
        raise HTTPException(
            404 if "not on statement" in message else 409 if "already" in message or "matched" in message else 422,
            message,
        ) from None


@router.get("/reconciliation/msme/year-end")
def msme_year_end(db: Db, ctx: Ctx, fy: str | None = None) -> dict[str, Any]:
    """Small suppliers at 31 March: principal unpaid, past the deadline, and the interest due and paid (R3.4)."""
    need_any(db, ctx.user_id, *BOOKS)
    from p2p.invoices import period_close  # noqa: PLC0415 - the year-end figures are block 13's

    year = fy or financial_year(business_today())
    try:
        return wire(period_close.msme_year_end(db, year))  # type: ignore[no-any-return]
    except period_close.PeriodCloseError as exc:
        raise HTTPException(exc.status, str(exc)) from None


@router.get("/reconciliation/msme/year-end-banner")
def msme_banner(db: Db, ctx: Ctx) -> dict[str, Any]:
    """From 1 March: how many small-supplier invoices will be past their deadline at 31 March (R3.4)."""
    need_any(db, ctx.user_id, *BOOKS)
    from p2p.invoices import period_close  # noqa: PLC0415

    return wire(period_close.banner(db))  # type: ignore[no-any-return]
