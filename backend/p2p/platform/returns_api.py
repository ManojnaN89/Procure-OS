"""Returns to a supplier, goods above the order, recovery claims, scrap and the ERP receipt import (9.2-9.9).

The screens' API for block 9: the excess a buyer accepts by revision or returns, the return challan and its outcome,
the claims (read), scrapping blocked stock, and the ERP's receipt file. Every action goes through its service, which
writes the audit row; a company's numbers never answer another company's session.
"""

import tempfile
from collections.abc import Callable
from decimal import Decimal
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from sqlalchemy.orm import Session

from p2p.formulas.calendar import business_today
from p2p.formulas.money import format_qty
from p2p.platform import proposals, routing
from p2p.platform.permissions import can_anywhere, require
from p2p.receiving import erp_receipts, excess, quality, returns

router = APIRouter(tags=["goods receipt and returns"])
Db = Annotated[Session, Depends(routing.tenant_session)]
Ctx = Annotated[routing.TenantContext, Depends(routing.tenant_context)]
Store = Annotated[routing.TenantContext, Depends(require("grn.create"))]
Buyer = Annotated[routing.TenantContext, Depends(require("po.create"))]
Quality = Annotated[routing.TenantContext, Depends(require("stock.quality"))]
Reader = Annotated[routing.TenantContext, Depends(require("grn.create"))]


def _num(value: Any, name: str) -> Decimal:
    try:
        number = Decimal(str(value))
    except (ArithmeticError, ValueError):
        raise HTTPException(422, f"{name} must be a number") from None
    if not number.is_finite():
        raise HTTPException(422, f"{name} must be a number")
    return number


def _call[T](call: Callable[[], T]) -> T:
    try:
        return call()
    except (returns.ReturnError, excess.ExcessError) as exc:
        raise HTTPException(exc.status, str(exc)) from None
    except quality.QualityError as exc:
        raise HTTPException(exc.status, str(exc)) from None
    except proposals.ProposalError as exc:
        raise HTTPException(409, str(exc)) from None


# ---- goods above the order -------------------------------------------------------------------


@router.get("/api/receipt-excess")
def list_excess(db: Db, ctx: Ctx, open: bool = False) -> list[dict[str, Any]]:
    del ctx
    return [
        {
            "id": r["id"],
            "gr_no": r["gr_no"],
            "po_no": r["po_no"],
            "line": r["po_line_no"],
            "qty": format_qty(r["qty"]),
            "uom": r["uom"],
            "status": r["status"],
            "hold_id": r["hold_id"],
            "item": r["description"],
            "item_code": r["item_code"],
            "supplier": r["supplier"],
            "version_made": r["version_made"],
            "version": r["version"],
            "return_no": r["return_no"],
            "proposal_id": r["proposal_id"],
        }
        for r in excess.listing(db, open_only=open)
    ]


@router.post("/api/receipt-excess/{excess_id}/accept")
def accept_excess(excess_id: int, payload: dict[str, Any], db: Db, ctx: Buyer) -> dict[str, Any]:
    return _call(
        lambda: excess.propose_accept(
            db,
            ctx.slug,
            excess_id,
            ctx.user_id,
            int(payload.get("version") or 0),
            str(payload.get("reason") or ""),
            as_of=business_today(),
        )
    )


@router.post("/api/receipt-excess/{excess_id}/return")
def return_excess(excess_id: int, payload: dict[str, Any], db: Db, ctx: Buyer) -> dict[str, Any]:
    return _call(
        lambda: excess.send_back(
            db, ctx.slug, excess_id, ctx.user_id, str(payload.get("reason") or ""), as_of=business_today()
        )
    )


# ---- return documents ------------------------------------------------------------------------


def _line_out(line: dict[str, Any]) -> dict[str, Any]:
    return {
        "line_no": line["line_no"],
        "item_code": line["item_code"],
        "name": line["description"],
        "qty": format_qty(line["qty"]),
        "uom": line["uom"],
        "quality": line["quality"],
        "gr_no": line["gr_no"],
        "po_no": line["po_no"],
        "po_line_no": line["po_line_no"],
        "counted": line["counted"],
        "outcome": line["outcome"],
        "claim_no": line["claim_no"],
        "serials": line["serials"] or [],
        "unordered_no": line["unordered_no"],
    }


@router.get("/api/returns")
def list_returns(db: Db, ctx: Ctx, status: str | None = None) -> list[dict[str, Any]]:
    del ctx
    return [
        {
            "no": r["no"],
            "supplier": r["supplier"],
            "supplier_code": r["supplier_code"],
            "location": r["location_code"],
            "status": r["status"],
            "source": r["source"],
            "reason": r["reason"],
            "challan": r["challan"],
            "vehicle": r["vehicle"],
            "dispatched_on": r["dispatched_on"].isoformat() if r["dispatched_on"] else None,
            "lines": r["lines"],
            "qty": format_qty(r["qty"]),
        }
        for r in returns.listing(db, status=status)
    ]


@router.get("/api/returns/{no}")
def get_return(no: str, db: Db, ctx: Ctx) -> dict[str, Any]:
    del ctx
    found = _call(lambda: returns.detail(db, no))
    return {
        "no": found["no"],
        "supplier": found["supplier"],
        "supplier_code": found["supplier_code"],
        "location": found["location_code"],
        "location_name": found["location_name"],
        "status": found["status"],
        "source": found["source"],
        "reason": found["reason"],
        "challan": found["challan"],
        "vehicle": found["vehicle"],
        "dispatched_on": found["dispatched_on"].isoformat() if found["dispatched_on"] else None,
        "created_by": found["created_by"],
        "dispatched_by": found["dispatched_by"],
        "lines": [_line_out(line) for line in found["lines"]],
    }


@router.post("/api/returns", status_code=201)
def create_return(payload: dict[str, Any], db: Db, ctx: Store) -> dict[str, Any]:
    raw_lines = payload.get("lines")
    if not isinstance(raw_lines, list) or not raw_lines:
        raise HTTPException(422, "a return needs at least one line")
    try:
        lines = [
            returns.ReturnLineIn(
                int(raw["hold_id"]),
                _num(raw.get("qty"), "qty"),
                [str(s) for s in raw.get("serials") or []] or None,
                raw.get("gr_no"),
                int(raw["po_line_no"]) if raw.get("po_line_no") not in (None, "") else None,
                raw.get("supplier_code"),
            )
            for raw in raw_lines
        ]
    except (KeyError, TypeError, ValueError):
        raise HTTPException(422, "each line needs a hold_id and a qty") from None
    made = _call(
        lambda: returns.create(
            db, ctx.slug, ctx.user_id, str(payload.get("reason") or ""), lines, as_of=business_today()
        )
    )
    return {"return_no": made.no}


@router.post("/api/returns/{no}/dispatch")
def dispatch_return(no: str, payload: dict[str, Any], db: Db, ctx: Store) -> dict[str, Any]:
    done = _call(
        lambda: returns.dispatch(
            db,
            ctx.slug,
            no,
            ctx.user_id,
            str(payload.get("challan") or ""),
            str(payload.get("vehicle") or ""),
            as_of=business_today(),
        )
    )
    return {"status": "dispatched", "claims": done.claims}


@router.post("/api/returns/{no}/outcome")
def decide_outcome(no: str, payload: dict[str, Any], db: Db, ctx: Buyer) -> dict[str, Any]:
    raw = payload.get("lines") or {}
    if not isinstance(raw, dict) or not raw:
        raise HTTPException(422, "say replace or no_replacement for at least one line")
    claims = _call(
        lambda: returns.outcome(
            db, ctx.slug, no, ctx.user_id, {int(k): str(v) for k, v in raw.items()}, as_of=business_today()
        )
    )
    return {"claims": claims}


@router.post("/api/returns/{no}/cancel")
def cancel_return(no: str, payload: dict[str, Any], db: Db, ctx: Store) -> dict[str, Any]:
    _call(lambda: returns.cancel(db, no, ctx.user_id, str(payload.get("reason") or "")))
    return {"status": "cancelled"}


# ---- recovery claims, scrap, the ERP import ---------------------------------------------------


@router.get("/api/recovery-claims")
def list_claims(db: Db, ctx: Ctx, supplier: str | None = None) -> list[dict[str, Any]]:
    if not (can_anywhere(db, ctx.user_id, "invoice.view") or can_anywhere(db, ctx.user_id, "po.create")):
        raise HTTPException(403, "role does not allow invoice.view or po.create")
    return [
        {
            "no": r["no"],
            "supplier": r["supplier"],
            "supplier_code": r["supplier_code"],
            "po_no": r["po_no"],
            "line": r["po_line_no"],
            "qty": format_qty(r["qty"]),
            "taxable": str(r["taxable"]),
            "cgst": str(r["cgst"]),
            "sgst": str(r["sgst"]),
            "igst": str(r["igst"]),
            "total": str(r["total"]),
            "status": r["status"],
            "invoices": list(r["invoices"] or []),
            "return_no": r["return_no"],
            "debit_note_no": r["debit_note_no"],
        }
        for r in returns.claims(db, supplier=supplier)
    ]


@router.post("/api/inventory/holds/{hold_id}/scrap")
def scrap_hold(hold_id: int, payload: dict[str, Any], db: Db, ctx: Quality) -> dict[str, Any]:
    left = _call(
        lambda: quality.scrap(
            db,
            ctx.user_id,
            hold_id,
            _num(payload.get("qty"), "qty"),
            str(payload.get("reason") or ""),
            today=business_today(),
        )
    )
    return {"left_qty": format_qty(left)}


@router.post("/api/receiving/import")
async def import_receipts(
    file: Annotated[UploadFile, File()], db: Db, ctx: Store, dry_run: Annotated[bool, Form()] = False
) -> dict[str, Any]:
    """The ERP's receipt file: what loaded, what was already there, what changed, what was left out, and reversals."""
    suffix = Path(file.filename or "goods_receipt.csv").suffix or ".csv"
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as handle:
        handle.write(await file.read())
        path = Path(handle.name)
    try:
        report = erp_receipts.import_receipts(db, ctx.slug, path, user_id=ctx.user_id, dry_run=dry_run)
    finally:
        path.unlink(missing_ok=True)
    return {
        "loaded": report.loaded.get(erp_receipts.KIND, 0),
        "already": report.already,
        "changed": report.changed_rows,
        "left_out": [{"row": p.line, "reason": p.reason} for p in report.rejected],
        "reversed": report.reversed,
    }
