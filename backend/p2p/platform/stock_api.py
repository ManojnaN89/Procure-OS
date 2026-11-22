"""The store's stock quality and safety stock (tasks 4.1, 6.2) and the late-supply cases (task 6.1): held lots and their
decisions, asking to use safety stock, and the cases of supply promised too late."""

from decimal import Decimal, InvalidOperation
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from p2p.approvals.workflow import name_of, org_code_of
from p2p.formulas.money import format_qty
from p2p.platform import routing
from p2p.platform.permissions import need_any, require
from p2p.receiving import quality, safety
from p2p.requests import supply_cases

router = APIRouter(tags=["stock"])
Db = Annotated[Session, Depends(routing.tenant_session)]
Viewer = Annotated[routing.TenantContext, Depends(require("stock.view"))]
Quality = Annotated[routing.TenantContext, Depends(require("stock.quality"))]
Ctx = Annotated[routing.TenantContext, Depends(routing.tenant_context)]


def _decimal(payload: dict[str, Any], key: str, default: str | None = None) -> Decimal:
    raw = payload.get(key, default)
    try:
        value = Decimal(str(raw))
    except (InvalidOperation, ValueError):
        raise HTTPException(422, f"{key} must be a number") from None
    if not value.is_finite():
        raise HTTPException(422, f"{key} must be a number")
    return value


def _shown(value: Any) -> Any:
    if isinstance(value, Decimal):
        return format_qty(value)
    return value.isoformat() if hasattr(value, "isoformat") else value


@router.get("/api/inventory/holds")
def list_holds(db: Db, ctx: Viewer, item_code: str | None = None, location: str | None = None) -> dict[str, Any]:
    return {
        "holds": [{k: _shown(v) for k, v in h.items()} for h in quality.holds(db, item=item_code, location=location)]
    }


@router.post("/api/inventory/holds", status_code=201)
def hold(payload: dict[str, Any], db: Db, ctx: Quality) -> dict[str, Any]:
    """Hold usable stock as quarantine or blocked, with a reason."""
    qty = _decimal(payload, "qty")
    try:
        done = quality.hold(
            db, ctx.slug, ctx.user_id, str(payload.get("item_code") or ""), str(payload.get("location") or ""), qty,
            str(payload.get("quality") or ""), str(payload.get("reason") or ""), payload.get("reference"),
        )  # fmt: skip
    except quality.QualityError as exc:
        raise HTTPException(exc.status, str(exc)) from None
    return {
        "hold_id": done.hold_id,
        "released": {mr: {i: format_qty(q) for i, q in v.items()} for mr, v in done.released.items()},
        "store_check": done.store_check,
    }


@router.post("/api/inventory/holds/{hold_id}/decide")
def decide(hold_id: int, payload: dict[str, Any], db: Db, ctx: Quality) -> dict[str, Any]:
    """The quality decision: quarantine to usable and/or blocked, blocked back to usable."""
    to_usable, to_blocked = _decimal(payload, "to_usable", "0"), _decimal(payload, "to_blocked", "0")
    try:
        done = quality.decide(
            db, ctx.slug, ctx.user_id, hold_id, to_usable, to_blocked, str(payload.get("reason") or "")
        )
    except quality.QualityError as exc:
        raise HTTPException(exc.status, str(exc)) from None
    return {
        "hold_id": done.hold_id,
        "left_qty": format_qty(done.left_qty),
        "blocked_hold_id": done.blocked_hold_id,
        "reserved_now": {mr: {i: format_qty(q) for i, q in v.items()} for mr, v in done.reserved_now.items()},
    }


@router.post("/api/inventory/safety-overrides", status_code=202)
def ask_override(payload: dict[str, Any], db: Db, ctx: Ctx) -> dict[str, Any]:
    """Ask the manager to use safety stock: for an issue by hand, or for a material request line."""
    need_any(db, ctx.user_id, "stock.issue" if payload.get("kind") == "issue" else "material.manage")
    try:
        asked = safety.ask(db, ctx.slug, ctx.user_id, payload)
    except safety.OverrideError as exc:
        raise HTTPException(exc.status, str(exc)) from None
    return {"proposal_id": asked.proposal_id, "approver_hint": asked.approver_hint, "status": "waiting"}


# ---- late-supply cases ---------------------------------------------------------------------------------


def _case(db: Session, c: dict[str, Any]) -> dict[str, Any]:
    code = org_code_of(db, int(c["assignee"]))
    return {
        "task_id": c["task_id"], "po_no": c["po_no"], "mr_no": c["mr_no"], "order_no": c["order_no"],
        "supplier": c["supplier"], "assignee": name_of(db, code) if code else f"user {c['assignee']}",
        "due_at": c["due_at"].isoformat(), "lines": c["lines"], "buffer_days": c["buffer_days"],
        "outcome": c["outcome"], "note": c["note"], "title": c["title"],
        "closed_at": c["closed_at"].isoformat() if c["closed_at"] else None,
    }  # fmt: skip


@router.get("/api/supply-risks")
def list_risks(db: Db, ctx: Ctx, status: str = "open") -> dict[str, Any]:
    need_any(db, ctx.user_id, "stock.view", "po.create")
    if status not in ("open", "closed"):
        raise HTTPException(422, "status is open or closed")
    return {"cases": [_case(db, c) for c in supply_cases.listing(db, status=status)]}


@router.post("/api/supply-risks/{task_id}/close")
def close_risk(task_id: int, payload: dict[str, Any], db: Db, ctx: Ctx) -> dict[str, Any]:
    try:
        return supply_cases.close(db, ctx.user_id, task_id, str(payload.get("outcome") or ""), payload.get("note"))
    except supply_cases.CaseError as exc:
        raise HTTPException(exc.status, str(exc)) from None
