"""Supplier documents and contracts: adding, verifying, storing, pricing and activating."""

from collections.abc import Callable
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Form, HTTPException, UploadFile
from sqlalchemy import text
from sqlalchemy.orm import Session

from p2p.formulas.calendar import business_today
from p2p.masters import documents_and_contracts as masters
from p2p.masters import suppliers
from p2p.platform import audit, files, ratelimit, routing
from p2p.platform.permissions import PermissionDenied, can_anywhere, require
from p2p.platform.sod import user as user_actor

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


@router.post("/contracts/{code}/versions/submit")
def submit_price_version(
    code: str, db: Db, ctx: Annotated[routing.TenantContext, Depends(require("contract.manage"))]
) -> dict[str, Any]:
    """Send the draft prices of an active contract to be activated by someone else."""
    return _guard(lambda: {"proposal": masters.submit_version(db, code, ctx.user_id)})


@router.post("/contracts/{code}/versions/activate")
def activate_price_version(
    code: str,
    payload: dict[str, Any],
    db: Db,
    ctx: Annotated[routing.TenantContext, Depends(require("contract.activate"))],
) -> dict[str, Any]:
    def go() -> dict[str, Any]:
        try:
            return masters.activate_version(db, code, ctx.user_id, payload.get("note") or None)
        except PermissionDenied as exc:
            raise HTTPException(403, str(exc)) from None

    return _guard(go)


@router.post("/contracts/{code}/versions/withdraw")
def withdraw_price_version(
    code: str,
    payload: dict[str, Any],
    db: Db,
    ctx: Annotated[routing.TenantContext, Depends(require("contract.manage"))],
) -> dict[str, Any]:
    """The maker drops the draft prices (and the activation they wait for)."""
    return _guard(
        lambda: {"dropped": masters.withdraw_version(db, code, ctx.user_id, str(payload.get("reason") or ""))}
    )


@router.post("/contracts/{code}/versions/reject")
def reject_price_version(
    code: str,
    payload: dict[str, Any],
    db: Db,
    ctx: Annotated[routing.TenantContext, Depends(require("contract.activate"))],
) -> dict[str, Any]:
    reason = str(payload.get("reason") or "")
    return _guard(lambda: {"dropped": masters.withdraw_version(db, code, ctx.user_id, reason, rejected=True)})


# ---- supplier records: the onboarding form, edits and bank details ---------------------------------

SUPPLIER_FIELDS = (
    "legal_name", "trade_name", "legal_form", "pan", "gst_reg_type", "gstin", "msme_category", "udyam_no",
    "agreed_payment_days", "payment_terms_code", "contact_name", "email", "phone", "address", "city", "pincode",
    "account_no", "ifsc",
)  # fmt: skip
FILE_TYPES = (".pdf", ".png", ".jpg", ".jpeg")
Ctx = Annotated[routing.TenantContext, Depends(routing.tenant_context)]


def _supplier_call(call: Callable[[], dict[str, Any]], db: Session) -> dict[str, Any]:
    try:
        return call()
    except suppliers.SupplierError as exc:
        db.rollback()
        message = str(exc)
        if "does not exist" in message:
            raise HTTPException(404, message) from None
        if message.startswith("segregation"):
            raise HTTPException(403, message) from None
        raise HTTPException(
            409 if message.startswith(("possible duplicate", "this bank account is already")) else 422, message
        ) from None


@router.get("/payment-terms")
def payment_terms(db: Db, ctx: Ctx) -> list[dict[str, Any]]:
    rows = db.execute(text("select code, name, days, advance_pct from payment_terms order by days, code")).mappings()
    return [{"code": r["code"], "name": r["name"], "days": r["days"], "advance_pct": r["advance_pct"]} for r in rows]


@router.post("/vendors")
def create_vendor(
    payload: dict[str, Any], db: Db, ctx: Annotated[routing.TenantContext, Depends(require("supplier.propose"))]
) -> dict[str, Any]:
    """Save a new supplier as a draft. It cannot be used until its documents are verified and a person activates it."""
    data = {k: payload.get(k) for k in SUPPLIER_FIELDS if payload.get(k) not in (None, "")}
    return _supplier_call(lambda: {"id": suppliers.create_supplier(db, data, ctx.user_id), "status": "draft"}, db)


@router.put("/vendors/{vendor_id}")
def update_vendor(
    vendor_id: str,
    payload: dict[str, Any],
    db: Db,
    ctx: Annotated[routing.TenantContext, Depends(require("supplier.propose"))],
) -> dict[str, Any]:
    data = {k: payload.get(k) for k in SUPPLIER_FIELDS if k in payload and k not in ("account_no", "ifsc")}
    return _supplier_call(
        lambda: {"id": vendor_id, "changed": suppliers.update_supplier(db, vendor_id, data, ctx.user_id)}, db
    )


@router.post("/vendors/{vendor_id}/bank")
def change_vendor_bank(
    vendor_id: str,
    payload: dict[str, Any],
    db: Db,
    ctx: Annotated[routing.TenantContext, Depends(require("supplier.propose"))],
) -> dict[str, Any]:
    """Set a draft supplier's bank account, or ask for a change on a supplier in use (a person approves it)."""
    data = {"account_no": payload.get("account_no"), "ifsc": payload.get("ifsc")}
    reason = str(payload.get("reason") or "")
    return _supplier_call(lambda: suppliers.change_bank(db, vendor_id, data, user_actor(ctx.user_id), reason), db)


@router.get("/vendors/{vendor_id}/profile")
def vendor_profile(vendor_id: str, db: Db, ctx: Ctx) -> dict[str, Any]:
    """What the edit form and the documents list need: the record as entered, the bank account (masked) and the files."""
    row = db.execute(text("select * from supplier where code = :c"), {"c": vendor_id}).mappings().first()
    if row is None:
        raise HTTPException(404, "Not found")
    contact = (
        db.execute(
            text(
                "select name, email, phone from supplier_contact where supplier_code = :c "
                "order by is_primary desc nulls last, id limit 1"
            ),
            {"c": vendor_id},
        )
        .mappings()
        .first()
    )
    address = (
        db.execute(
            text("select address, city, pincode from supplier_address where supplier_code = :c order by id limit 1"),
            {"c": vendor_id},
        )
        .mappings()
        .first()
    )
    bank = (
        db.execute(
            text(
                "select id, account_no, ifsc, coalesce(verified, false) as verified, submitted_by, verification_method, "
                "verified_at from supplier_bank_account where supplier_code = :c order by id limit 1"
            ),
            {"c": vendor_id},
        )
        .mappings()
        .first()
    )
    # the one who verifies sees the whole number to check it against the proof; everyone else sees the last four digits
    can_verify = (
        bank is not None
        and not bank["verified"]
        and bank["submitted_by"] != str(ctx.user_id)
        and can_anywhere(db, ctx.user_id, "supplier.verify_bank")
    )
    attached = db.execute(
        text(
            "select linked_id, id, filename from document where linked_type = 'supplier_document' and linked_id in "
            "(select id::text from supplier_document where supplier_code = :c) order by id"
        ),
        {"c": vendor_id},
    ).all()
    fields = {
        k: row[k]
        for k in (
            "legal_name", "trade_name", "legal_form", "pan", "gst_reg_type", "gstin", "msme_category", "udyam_no",
            "agreed_payment_days", "payment_terms_code",
        )
    }  # fmt: skip
    return {
        "id": vendor_id,
        "status": row["status"],
        "fields": {
            **fields,
            "contact_name": contact["name"] if contact else None,
            "email": contact["email"] if contact else None,
            "phone": contact["phone"] if contact else None,
            "address": address["address"] if address else None,
            "city": address["city"] if address else None,
            "pincode": address["pincode"] if address else None,
        },
        "frozen_fields": ["pan", "gstin", "gst_reg_type", "msme_category", "udyam_no"]
        if row["status"] != "draft"
        else [],
        "bank": None
        if bank is None
        else {
            "id": bank["id"],
            "account": suppliers.mask(bank["account_no"]),
            "ifsc": bank["ifsc"],
            "verified": bank["verified"],
            "method": bank["verification_method"],
            "verified_at": bank["verified_at"].isoformat() if bank["verified_at"] else None,
            **({"account_no": bank["account_no"]} if can_verify else {}),
        },  # fmt: skip
        "can_verify_bank": can_verify,
        "bank_methods": suppliers.verification_methods(db) if can_verify else [],
        "files": {str(a[0]): {"id": a[1], "filename": a[2]} for a in attached},
        "can_edit": can_anywhere(db, ctx.user_id, "supplier.propose"),
        "can_add_documents": can_anywhere(db, ctx.user_id, "supplier.documents"),
    }


@router.post("/vendors/{vendor_id}/bank/{account_id}/verify", dependencies=[ratelimit.limited("upload")])
async def verify_vendor_bank(
    vendor_id: str,
    account_id: int,
    method: Annotated[str, Form()],
    file: UploadFile,
    db: Db,
    ctx: Annotated[routing.TenantContext, Depends(require("supplier.verify_bank"))],
) -> dict[str, Any]:
    """A second person verifies the supplier's bank account: how it was checked and the proof (PDF or picture)."""
    name = file.filename or "file"
    if not name.lower().endswith(FILE_TYPES):
        raise HTTPException(422, "the proof must be a PDF, PNG or JPG file")
    data = await file.read(files.MAX_BYTES + 1)
    if len(data) > files.MAX_BYTES:
        raise HTTPException(413, "File is too large")
    proof = suppliers.Evidence(ctx.slug, data, name, file.content_type or "application/octet-stream")
    return _supplier_call(
        lambda: suppliers.verify_bank(db, vendor_id, account_id, method.strip(), proof, ctx.user_id), db
    )


@router.post(
    "/vendors/{vendor_id}/documents/{doc_id}/file", status_code=201, dependencies=[ratelimit.limited("upload")]
)
async def attach_document_file(
    vendor_id: str,
    doc_id: int,
    file: UploadFile,
    db: Db,
    ctx: Annotated[routing.TenantContext, Depends(require("supplier.documents"))],
) -> dict[str, Any]:
    """Keep the scan of a supplier document (PDF or picture) with the document record."""
    found = db.execute(
        text("select 1 from supplier_document where id = :i and supplier_code = :s"), {"i": doc_id, "s": vendor_id}
    ).first()
    if found is None:
        raise HTTPException(404, f"document {doc_id} of {vendor_id} does not exist")
    name = file.filename or "file"
    if not name.lower().endswith(FILE_TYPES):
        raise HTTPException(422, "a document scan must be a PDF, PNG or JPG file")
    data = await file.read(files.MAX_BYTES + 1)
    if len(data) > files.MAX_BYTES:
        raise HTTPException(413, "File is too large")
    saved = files.save_file(
        db, ctx.slug, data, filename=name, content_type=file.content_type or "application/octet-stream",
        doc_type="supplier_document", uploaded_by=ctx.user_id, linked_type="supplier_document", linked_id=str(doc_id),
    )  # fmt: skip
    audit.record(
        db,
        user_actor(ctx.user_id),
        "supplier.document_file",
        "supplier",
        vendor_id,
        after={"doc": doc_id, "file": saved.id},
    )
    db.commit()
    return {"id": saved.id, "filename": saved.filename}
