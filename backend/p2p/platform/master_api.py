"""Supplier documents and contracts: adding, verifying, storing, pricing and activating."""

from collections.abc import Callable
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from p2p.formulas.calendar import business_today
from p2p.masters import documents_and_contracts as masters
from p2p.platform import routing
from p2p.platform.permissions import require

router = APIRouter(prefix="/api", tags=["masters"])
Db = Annotated[Session, Depends(routing.tenant_session)]


def _date(value: Any, field: str, *, required: bool = False) -> date | None:
    if value in (None, ""):
        if required:
            raise HTTPException(422, f"{field} is required")
        return None
    try:
        return date.fromisoformat(str(value))
    except ValueError:
        raise HTTPException(422, f"{field} must be a date like 2026-11-20") from None


def _number(value: Any, field: str, *, required: bool = False) -> Decimal | None:
    if value in (None, ""):
        if required:
            raise HTTPException(422, f"{field} is required")
        return None
    try:
        number = Decimal(str(value))
    except InvalidOperation:
        raise HTTPException(422, f"{field} is not a number") from None
    if not number.is_finite():
        raise HTTPException(422, f"{field} is not a number")
    return number


def _guard(call: Callable[[], dict[str, Any]]) -> dict[str, Any]:
    try:
        return call()
    except masters.MasterError as exc:
        message = str(exc)
        if "does not exist" in message:
            raise HTTPException(404, message) from None
        raise HTTPException(403 if message.startswith("segregation") else 409, message) from None


@router.post("/vendors/{vendor_id}/documents")
def add_document(
    vendor_id: str,
    payload: dict[str, Any],
    db: Db,
    ctx: Annotated[routing.TenantContext, Depends(require("supplier.documents"))],
) -> dict[str, Any]:
    issued, expires = _date(payload.get("issued_on"), "issued_on"), _date(payload.get("expires_on"), "expires_on")
    return _guard(
        lambda: {
            "id": masters.add_document(
                db,
                vendor_id,
                str(payload.get("type") or ""),
                payload.get("number") or None,
                issued,
                expires,
                ctx.user_id,
            ),
            "verified": False,
        }
    )


@router.post("/vendors/{vendor_id}/documents/{doc_id}/verify")
def verify_document(
    vendor_id: str,
    doc_id: int,
    payload: dict[str, Any],
    db: Db,
    ctx: Annotated[routing.TenantContext, Depends(require("supplier.verify_docs"))],
) -> dict[str, Any]:
    verified = payload.get("verified", True) is not False

    def go() -> dict[str, Any]:
        masters.set_verified(db, vendor_id, doc_id, verified, ctx.user_id)
        return {"id": doc_id, "verified": verified}

    return _guard(go)


@router.post("/contracts")
def create_contract(
    payload: dict[str, Any], db: Db, ctx: Annotated[routing.TenantContext, Depends(require("contract.manage"))]
) -> dict[str, Any]:
    try:
        data = {
            "supplier_code": str(payload["supplier_code"]),
            "type": payload.get("type") or "rate_contract",
            "valid_from": _date(payload.get("valid_from"), "valid_from", required=True),
            "valid_to": _date(payload.get("valid_to"), "valid_to"),
            "max_value": _number(payload.get("max_value"), "max_value"),
            "payment_terms_code": payload.get("payment_terms_code") or None,
            "delivery_terms": payload.get("delivery_terms") or None,
            "freight_terms": payload.get("freight_terms") or None,
            "warranty_months": payload.get("warranty_months") or None,
            "sla_text": payload.get("sla_text") or None,
        }
    except KeyError:
        raise HTTPException(422, "a contract needs a supplier_code and a valid_from date") from None
    return _guard(lambda: {"id": masters.create_contract(db, data, ctx.user_id), "status": "draft"})


@router.post("/contracts/{code}/prices")
def add_price(
    code: str,
    payload: dict[str, Any],
    db: Db,
    ctx: Annotated[routing.TenantContext, Depends(require("contract.manage"))],
) -> dict[str, Any]:
    try:
        data = {
            "item_code": str(payload["item_code"]),
            "price": _number(payload.get("price"), "price", required=True),
            "uom": payload.get("uom") or None,
            "min_qty": _number(payload.get("min_qty"), "min_qty"),
            "valid_from": _date(payload.get("valid_from"), "valid_from"),
            "valid_to": _date(payload.get("valid_to"), "valid_to"),
            "lead_days": int(payload["lead_days"]) if payload.get("lead_days") not in (None, "") else None,
        }
    except (KeyError, ValueError):
        raise HTTPException(422, "a price needs an item_code and a price") from None
    return _guard(lambda: {"id": masters.add_price(db, code, data, ctx.user_id)})


@router.post("/contracts/{code}/activate")
def activate_contract(
    code: str, db: Db, ctx: Annotated[routing.TenantContext, Depends(require("contract.activate"))]
) -> dict[str, Any]:
    def go() -> dict[str, Any]:
        masters.activate(db, code, ctx.user_id, today=business_today())
        return {"id": code, "status": "active"}

    return _guard(go)
