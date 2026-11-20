"""The invoice screens' API: what can be invoiced, entering an invoice, the exceptions, and releasing a block."""

from collections.abc import Callable
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from p2p.agents import invoice_exceptions
from p2p.formulas.calendar import business_today
from p2p.formulas.money import format_qty
from p2p.invoices import service as invoices
from p2p.platform import routing
from p2p.platform.permissions import can_anywhere, require
from p2p.platform.rows import rows as _rows
from p2p.receiving import kinds

router = APIRouter(prefix="/api", tags=["invoices"])
Db = Annotated[Session, Depends(routing.tenant_session)]
Ctx = Annotated[routing.TenantContext, Depends(routing.tenant_context)]


def _money(value: Any) -> str | None:
    return None if value is None else str(value)


def _num(value: Any, field: str, default: Decimal | None = None) -> Decimal:
    if value in (None, ""):
        if default is None:
            raise HTTPException(422, f"{field} is required")
        return default
    try:
        number = Decimal(str(value))
    except InvalidOperation:
        raise HTTPException(422, f"{field} is not a number") from None
    if not number.is_finite():
        raise HTTPException(422, f"{field} is not a number")
    return number


def _date(value: Any, field: str) -> date:
    try:
        return date.fromisoformat(str(value))
    except ValueError:
        raise HTTPException(422, f"{field} must be a date like 2026-11-20") from None


@router.get("/invoicing/ready")
def ready_to_invoice(db: Db, ctx: Ctx, supplier: str | None = None) -> list[dict[str, Any]]:
    """Order lines with goods accepted that have not been invoiced yet, which is what an invoice can be entered against.
    A service line can be invoiced as soon as the order is with the supplier: the supplier bills before the requester has
    confirmed, and the match holds the invoice until the service is confirmed (two-way match)."""
    rows = _rows(
        db,
        "select l.po_no, l.line_no, l.description, l.item_code, l.uom, l.unit_price, l.gst_rate, "  # noqa: S608 - fixed fragments only
        f"case when {kinds.IS_SERVICE} then greatest(l.qty, l.received_qty) else l.received_qty end as billable, "
        "l.invoiced_qty, po.supplier_code, coalesce(s.trade_name, s.legal_name) as supplier, po.buyer_gstin, po.supplier_gstin "
        f"from po_line l join po on po.no = l.po_no join supplier s on s.code = po.supplier_code {kinds.SERVICE_JOINS} "
        f"where (l.received_qty > l.invoiced_qty or ({kinds.IS_SERVICE} and l.qty > l.invoiced_qty)) "
        "and l.status <> 'cancelled' and po.status not in ('draft', 'submitted', 'approved', 'cancelled') "
        "and (cast(:s as text) is null or po.supplier_code = :s) order by po.supplier_code, l.po_no, l.line_no limit 500",
        s=supplier,
    )
    return [
        {
            "po_no": r["po_no"],
            "line_no": r["line_no"],
            "name": r["description"],
            "item_code": r["item_code"],
            "uom": r["uom"],
            "supplier_code": r["supplier_code"],
            "supplier": r["supplier"],
            "unit_price": _money(r["unit_price"]),
            "gst_rate": _money(r["gst_rate"]),
            "available": format_qty(r["billable"] - r["invoiced_qty"]),
            "buyer_gstin": r["buyer_gstin"],
            "supplier_gstin": r["supplier_gstin"],
        }
        for r in rows
    ]


@router.get("/invoices")
def list_invoices(db: Db, ctx: Ctx, status: str | None = None) -> list[dict[str, Any]]:
    rows = _rows(
        db,
        "select i.no, i.type, i.supplier_code, coalesce(s.trade_name, s.legal_name) as supplier, i.supplier_invoice_no, "
        "i.invoice_date, i.total, i.status, i.payment_block, i.block_reason, i.duplicate_of, i.due_date "
        "from invoice i join supplier s on s.code = i.supplier_code where (cast(:st as text) is null or i.status = :st) "
        "order by i.created_at desc, i.no desc limit 500",
        st=status,
    )

    def next_action(r: dict[str, Any]) -> str | None:
        """What to do about a blocked invoice, worked out by the rules (no model): the list is the exceptions screen."""
        if r["status"] != "blocked":
            return None
        found = invoice_exceptions.diagnose(db, r["no"])
        return invoice_exceptions.NEXT_ACTION.get(found.fix) if found else None

    return [
        {
            "next_action": next_action(r),
            "id": r["no"],
            "type": r["type"],
            "supplier_code": r["supplier_code"],
            "supplier": r["supplier"],
            "number": r["supplier_invoice_no"],
            "date": r["invoice_date"].isoformat(),
            "total": _money(r["total"]),
            "status": r["status"],
            "block": r["payment_block"],
            "reason": r["block_reason"],
            "duplicate_of": r["duplicate_of"],
            "due_date": r["due_date"].isoformat() if r["due_date"] else None,
        }
        for r in rows
    ]


@router.get("/invoices/{no}")
def get_invoice(no: str, db: Db, ctx: Ctx) -> dict[str, Any]:
    rows = _rows(
        db,
        "select i.*, coalesce(s.trade_name, s.legal_name) as supplier from invoice i join supplier s on s.code = i.supplier_code "
        "where i.no = :n",
        n=no,
    )
    if not rows:
        raise HTTPException(404, "Not found")
    i = rows[0]
    lines = _rows(db, "select * from invoice_line where invoice_no = :n order by line_no", n=no)
    charges = _rows(db, "select * from invoice_charge where invoice_no = :n order by id", n=no)
    results = _rows(
        db,
        "select m.*, v.effective_from as tolerance_from from match_result m "
        "left join tolerance_version v on v.version = m.tolerance_version where m.invoice_no = :n order by m.id",
        n=no,
    )
    pending = _rows(
        db,
        "select id, summary from proposal where action = 'invoice.release_block' and status = 'pending' "
        "and payload->>'invoice' = :n",
        n=no,
    )
    can_work = can_anywhere(db, ctx.user_id, "invoice.match")
    return {
        "id": i["no"],
        "type": i["type"],
        "supplier_code": i["supplier_code"],
        "supplier": i["supplier"],
        "number": i["supplier_invoice_no"],
        "date": i["invoice_date"].isoformat(),
        "fy": i["fy"],
        "status": i["status"],
        "block": i["payment_block"],
        "reason": i["block_reason"],
        "duplicate_of": i["duplicate_of"],
        "ref_invoice_no": i["ref_invoice_no"],
        "supplier_gstin": i["supplier_gstin"],
        "buyer_gstin": i["buyer_gstin"],
        "irn": i["irn"],
        "eway_bill_no": i["eway_bill_no"],
        "channel": i["channel"],
        "received_on": i["received_on"].isoformat(),
        "due_date": i["due_date"].isoformat() if i["due_date"] else None,
        "taxable": _money(i["taxable"]),
        "cgst": _money(i["cgst"]),
        "sgst": _money(i["sgst"]),
        "igst": _money(i["igst"]),
        "round_off": _money(i["round_off"]),
        "total": _money(i["total"]),
        "lines": [
            {
                "line_no": ln["line_no"],
                "po_no": ln["po_no"],
                "po_line_no": ln["po_line_no"],
                "name": ln["description"],
                "qty": format_qty(ln["qty"]),
                "uom": ln["uom"],
                "unit_price": _money(ln["unit_price"]),
                "gst_rate": _money(ln["gst_rate"]),
                "taxable": _money(ln["taxable"]),
            }
            for ln in lines
        ],
        "charges": [
            {"type": c["type"], "amount": _money(c["amount"]), "gst_rate": _money(c["gst_rate"])} for c in charges
        ],
        "match": [
            {
                "line_no": r["invoice_line_no"],
                "result": r["result"],
                "reason": r["reason_codes"],
                "price_var": _money(r["price_var"]),
                "qty_var": None if r["qty_var"] is None else format_qty(r["qty_var"]),
                "detail": r["detail"],
                "resolution": r["resolution"],
                "tolerance": None
                if r["tolerance_version"] is None
                else {"version": r["tolerance_version"], "effective_from": r["tolerance_from"].isoformat()},
            }
            for r in results
        ],
        "tolerance_version": i["tolerance_version"],
        "pending_release": pending[0] if pending else None,
        "can_enter": can_anywhere(db, ctx.user_id, "invoice.book"),
        "can_act": can_work and i["status"] == "blocked" and i["payment_block"] != "duplicate" and not pending,
    }


def _error(call: Callable[[], dict[str, Any]]) -> dict[str, Any]:
    try:
        return call()
    except invoices.InvoiceError as exc:
        message = str(exc)
        if "does not exist" in message:
            raise HTTPException(404, message) from None
        raise HTTPException(403 if message.startswith("segregation") else 409, message) from None


@router.post("/invoices")
def enter_invoice(
    payload: dict[str, Any], db: Db, ctx: Annotated[routing.TenantContext, Depends(require("invoice.book"))]
) -> dict[str, Any]:
    raw_lines = payload.get("lines")
    if not isinstance(raw_lines, list):
        raise HTTPException(422, "lines must be a list")
    try:
        data = invoices.InvoiceIn(
            supplier_code=str(payload["supplier_code"]),
            supplier_invoice_no=str(payload["number"]),
            invoice_date=_date(payload["date"], "date"),
            type=str(payload.get("type") or "invoice"),
            supplier_gstin=payload.get("supplier_gstin") or None,
            buyer_gstin=payload.get("buyer_gstin") or None,
            irn=payload.get("irn") or None,
            eway_bill_no=payload.get("eway_bill_no") or None,
            ref_invoice_no=payload.get("ref_invoice_no") or None,
            channel=str(payload.get("channel") or "manual"),
            taxable=_num(payload["taxable"], "taxable") if payload.get("taxable") not in (None, "") else None,
            cgst=_num(payload["cgst"], "cgst") if payload.get("cgst") not in (None, "") else None,
            sgst=_num(payload["sgst"], "sgst") if payload.get("sgst") not in (None, "") else None,
            igst=_num(payload["igst"], "igst") if payload.get("igst") not in (None, "") else None,
            total=_num(payload["total"], "total") if payload.get("total") not in (None, "") else None,
            lines=[
                invoices.InvLine(
                    po_no=str(raw["po_no"]),
                    po_line_no=int(raw["po_line_no"]),
                    qty=_num(raw.get("qty"), "qty"),
                    unit_price=_num(raw.get("unit_price"), "unit_price"),
                    discount=_num(raw.get("discount"), "discount", Decimal(0)),
                    gst_rate=_num(raw["gst_rate"], "gst_rate") if raw.get("gst_rate") not in (None, "") else None,
                )
                for raw in raw_lines
            ],
            charges=[
                invoices.InvCharge(
                    str(c["type"]), _num(c.get("amount"), "amount"), _num(c.get("gst_rate"), "gst_rate", Decimal(0))
                )
                for c in payload.get("charges") or []
            ],
        )
    except (KeyError, TypeError, ValueError):
        raise HTTPException(
            422, "an invoice needs supplier_code, number, date and lines with po_no, po_line_no, qty and unit_price"
        ) from None

    def enter() -> dict[str, Any]:
        done = invoices.enter_invoice(db, data, ctx.user_id, as_of=business_today())
        return {
            "invoice": done.no,
            "status": done.status,
            "result": done.result,
            "reasons": done.reasons,
            "duplicate_of": done.duplicate_of,
        }

    return _error(enter)


@router.post("/invoices/{no}/rematch")
def rematch(
    no: str, db: Db, ctx: Annotated[routing.TenantContext, Depends(require("invoice.match"))]
) -> dict[str, Any]:
    def again() -> dict[str, Any]:
        done = invoices.rematch_invoice(db, no, ctx.user_id)
        return {"invoice": done.no, "status": done.status, "result": done.result, "reasons": done.reasons}

    return _error(again)


@router.post("/invoices/{no}/release")
def release(
    no: str, payload: dict[str, Any], db: Db, ctx: Annotated[routing.TenantContext, Depends(require("invoice.match"))]
) -> dict[str, Any]:
    def ask() -> dict[str, Any]:
        pid = invoices.request_release(db, no, str(payload.get("reason") or ""), ctx.user_id)
        return {"proposal_id": pid, "status": "waiting for approval"}

    return _error(ask)


@router.post("/invoices/{no}/cancel")
def cancel(
    no: str, payload: dict[str, Any], db: Db, ctx: Annotated[routing.TenantContext, Depends(require("invoice.match"))]
) -> dict[str, Any]:
    def go() -> dict[str, Any]:
        invoices.cancel_invoice(db, no, str(payload.get("reason") or ""), ctx.user_id)
        return {"invoice": no, "status": "cancelled"}

    return _error(go)
