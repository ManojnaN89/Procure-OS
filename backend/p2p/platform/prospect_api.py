"""Prospective suppliers and where suppliers are approved (D-03, task 4.4; D44, task 12.4): registering a prospect, its
independent identity check, clearing a flag, inviting it to an RFQ, and asking for a category approval with a scope.
Each write is a service that writes the audit log; the rules are in masters/prospects.py and masters/categories.py."""

from datetime import date
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from p2p.formulas.calendar import business_today
from p2p.formulas.scope import Scope
from p2p.masters import categories, prospects, regions
from p2p.platform import routing
from p2p.platform.permissions import require
from p2p.platform.sod import user
from p2p.sourcing import prospects as rfq_prospects

router = APIRouter(prefix="/api", tags=["suppliers"])
Db = Annotated[Session, Depends(routing.tenant_session)]
Ctx = Annotated[routing.TenantContext, Depends(routing.tenant_context)]

PROSPECT_FIELDS = (
    "legal_name", "trade_name", "legal_form", "pan", "gst_reg_type", "gstin", "state_code", "address", "city",
    "pincode", "contact_name", "email", "phone", "category", "account_no", "ifsc", "payment_terms_code",
)  # fmt: skip


def _http(exc: prospects.ProspectError | categories.CategoryError) -> HTTPException:
    return HTTPException(exc.status, str(exc))


@router.get("/regions")
def list_regions(db: Db, ctx: Ctx) -> list[dict[str, Any]]:
    """Every region with its locations (D44)."""
    return regions.regions(db)


@router.post("/vendors/prospects")
def register_prospect(
    payload: dict[str, Any], db: Db, ctx: Annotated[routing.TenantContext, Depends(require("supplier.propose"))]
) -> dict[str, Any]:
    """A buyer registers a prospect to quote before it is onboarded (D-03 R1). A duplicate or a sanctioned name is
    refused; a possible conflict is created with a flag for independent review (R7, R8)."""
    bank: dict[str, Any] = payload["bank"] if isinstance(payload.get("bank"), dict) else {}
    data = {k: payload.get(k) for k in PROSPECT_FIELDS if payload.get(k) not in (None, "")}
    data.update({k: v for k, v in bank.items() if k in ("account_no", "ifsc") and v})
    try:
        code, flags = prospects.register(db, data, ctx.user_id)
    except prospects.ProspectError as exc:
        db.rollback()
        raise _http(exc) from None
    return {
        "code": code,
        "status": "prospect",
        "flags": [{"kind": f.kind, "other_ref": f.other_ref, "detail": f.detail} for f in flags],
    }


@router.post("/vendors/{code}/identity-check")
def identity_check(
    code: str,
    payload: dict[str, Any],
    db: Db,
    ctx: Annotated[routing.TenantContext, Depends(require("supplier.verify_docs"))],
) -> dict[str, Any]:
    """The independent basic identity check of a prospect: not by the buyer who registered or invited it (R2, R3)."""
    try:
        ids = [int(x) for x in payload.get("document_ids") or []]
    except (TypeError, ValueError):
        raise HTTPException(422, "document_ids must be numbers") from None
    try:
        return prospects.check_identity(
            db, code, ctx.user_id, method=str(payload.get("method") or ""), document_ids=ids,
            note=str(payload.get("note") or ""),
        )  # fmt: skip
    except prospects.ProspectError as exc:
        db.rollback()
        raise _http(exc) from None


@router.post("/vendors/{code}/flags/{flag_id}/clear")
def clear_flag(
    code: str,
    flag_id: int,
    payload: dict[str, Any],
    db: Db,
    ctx: Annotated[routing.TenantContext, Depends(require("supplier.verify_docs"))],
) -> dict[str, Any]:
    try:
        return prospects.clear_flag(db, code, flag_id, ctx.user_id, str(payload.get("reason") or ""))
    except prospects.ProspectError as exc:
        db.rollback()
        raise _http(exc) from None


@router.get("/vendors/{code}/identity")
def identity(code: str, db: Db, ctx: Ctx) -> dict[str, Any]:
    found = prospects.identity(db, code)
    if not found:
        raise HTTPException(404, "Not found")
    return {**found, "methods": prospects.methods(db)}


@router.get("/rfqs/{no}/prospects")
def rfq_prospect_choices(no: str, db: Db, ctx: Ctx) -> list[dict[str, Any]]:
    return rfq_prospects.prospects_for(db, no)


@router.post("/rfqs/{no}/prospects")
def invite_prospect(
    no: str, payload: dict[str, Any], db: Db, ctx: Annotated[routing.TenantContext, Depends(require("rfq.create"))]
) -> dict[str, Any]:
    """A buyer invites a prospect of the RFQ's category; on an RFQ already out it goes to the new invitee only."""
    try:
        return rfq_prospects.invite(db, no, str(payload.get("supplier_code") or ""), ctx.user_id)
    except prospects.ProspectError as exc:
        db.rollback()
        raise _http(exc) from None


@router.get("/vendors/{code}/categories")
def vendor_categories(code: str, db: Db, ctx: Ctx) -> list[dict[str, Any]]:
    return categories.categories_of(db, code)


@router.post("/vendors/{code}/categories", status_code=202)
def ask_category(
    code: str,
    payload: dict[str, Any],
    db: Db,
    ctx: Annotated[routing.TenantContext, Depends(require("supplier.propose"))],
) -> dict[str, Any]:
    """Ask for a supplier's approval for a category, everywhere or only at some locations and regions. Another person
    with `supplier.activate` approves it (D44)."""
    scope: dict[str, Any] = payload["scope"] if isinstance(payload.get("scope"), dict) else {}
    try:
        start = date.fromisoformat(str(payload["approved_from"])) if payload.get("approved_from") else business_today()
    except ValueError:
        raise HTTPException(422, "approved_from is a date like 2026-12-01") from None
    try:
        pid = categories.propose(
            db, user(ctx.user_id), code, str(payload.get("category") or ""), start,
            Scope.of(scope.get("locations") or [], scope.get("regions") or []), str(payload.get("reason") or ""),
        )  # fmt: skip
    except categories.CategoryError as exc:
        db.rollback()
        raise _http(exc) from None
    return {"status": "waiting", "proposal_id": pid}
