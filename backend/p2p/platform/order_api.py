"""The order's document, its versions, and changing it after it was made: revise, cancel, close the balance; the changes a
supplier asked for and its dispatch notes.

Every version of an order is listed with its document as it was mailed; any two versions can be compared. The current
version's document is the stored one once issued, else drawn live and marked as a draft not issued."""

from collections.abc import Callable
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy import text
from sqlalchemy.orm import Session

from p2p.approvals.workflow import ApprovalError
from p2p.orders import changes, confirmation, dispatch_notes, document, versions
from p2p.platform import routing
from p2p.platform.permissions import PermissionDenied, can_anywhere, require

router = APIRouter(prefix="/api", tags=["orders"])
Db = Annotated[Session, Depends(routing.tenant_session)]
Ctx = Annotated[routing.TenantContext, Depends(routing.tenant_context)]
Buyer = Annotated[routing.TenantContext, Depends(require("po.create"))]


def _guard(call: Callable[[], dict[str, Any]]) -> dict[str, Any]:
    try:
        return call()
    except changes.InputError as exc:
        raise HTTPException(422, str(exc)) from None
    except changes.ChangeError as exc:
        message = str(exc)
        raise HTTPException(404 if "does not exist" in message else 409, message) from None
    except ApprovalError as exc:
        raise HTTPException(409, str(exc)) from None


def _pdf(data: bytes, filename: str) -> Response:
    return Response(
        data, media_type="application/pdf", headers={"Content-Disposition": f'inline; filename="{filename}"'}
    )


@router.get("/purchase-orders/{no}/pdf")
def order_pdf(no: str, db: Db, ctx: Ctx) -> Response:
    """The current version: as it was issued to the supplier, else drawn now and marked as a draft not issued."""
    try:
        return _pdf(versions.current_pdf(db, ctx.slug, no), f"{no}.pdf")
    except document.DocumentError:
        raise HTTPException(404, "Not found") from None


@router.get("/purchase-orders/{no}/versions")
def order_versions(no: str, db: Db, ctx: Ctx) -> list[dict[str, Any]]:
    try:
        return versions.listing(db, no)
    except versions.VersionError:
        raise HTTPException(404, "Not found") from None


def _whole(value: str | None, name: str) -> int:
    if value is None or not value.isdigit():
        raise HTTPException(422, f"{name} must be a version number")
    return int(value)


@router.get("/purchase-orders/{no}/versions/compare")
def compare_versions(
    no: str,
    db: Db,
    ctx: Ctx,
    first: Annotated[str | None, Query(alias="from")] = None,
    second: Annotated[str | None, Query(alias="to")] = None,
) -> dict[str, Any]:
    """What changed from one version to another, changed values only."""
    a, b = _whole(first, "from"), _whole(second, "to")
    try:
        return versions.compare(db, no, a, b)
    except versions.VersionError as exc:
        raise HTTPException(exc.status, "Not found" if "does not exist" in str(exc) else str(exc)) from None


@router.get("/purchase-orders/{no}/versions/{version}/pdf")
def version_pdf(no: str, version: int, db: Db, ctx: Ctx) -> Response:
    """The document exactly as it was mailed for that version (never drawn again)."""
    if db.execute(text("select 1 from po where no = :n"), {"n": no}).first() is None:
        raise HTTPException(404, "Not found")
    data = versions.stored_pdf(db, ctx.slug, no, version)
    if data is None:
        raise HTTPException(404, f"version {version} was not issued")
    return _pdf(data, f"{no}-v{version}.pdf")


@router.get("/purchase-orders/{no}/controls")
def order_controls(no: str, db: Db, ctx: Ctx) -> dict[str, Any]:
    """The order's version and history, what the caller may do to it now, and the choices for a header change."""
    po = db.execute(text("select status, version from po where no = :n"), {"n": no}).first()
    if po is None:
        raise HTTPException(404, "Not found")
    may = can_anywhere(db, ctx.user_id, "po.create")
    received = db.execute(
        text("select coalesce(sum(received_qty), 0) from po_line where po_no = :n and status <> 'cancelled'"), {"n": no}
    ).scalar_one()
    return {
        "version": po[1],
        "history": changes.history(db, no),
        "can_revise": may and po[0] in changes.REVISABLE,
        "can_change_header": may and po[0] in changes.REVISABLE,
        "can_cancel": may and po[0] in changes.CANCELLABLE and received == 0,
        "can_close_balance": may and po[0] in changes.CLOSABLE and received > 0,
        "options": {
            "locations": [
                {"code": r[0], "name": r[1], "state": r[2]}
                for r in db.execute(
                    text(
                        "select code, name, coalesce(state_code, left(gstin, 2)) from location "
                        "where coalesce(state_code, gstin) is not null order by code"
                    )
                )
            ],
            "payment_terms": [
                {"code": r[0], "name": r[1], "advance_pct": r[2] or 0}
                for r in db.execute(text("select code, name, advance_pct from payment_terms order by days, code"))
            ],
        },
    }


def _date(value: Any, field: str) -> date | None:
    if value in (None, ""):
        return None
    try:
        return date.fromisoformat(str(value))
    except ValueError:
        raise HTTPException(422, f"{field} must be a date like 2026-11-20") from None


def _qty(value: Any, field: str) -> Decimal | None:
    if value in (None, ""):
        return None
    try:
        number = Decimal(str(value))
    except InvalidOperation:
        raise HTTPException(422, f"{field} is not a number") from None
    if not number.is_finite():
        raise HTTPException(422, f"{field} is not a number")
    return number


_NEVER = {
    "supplier": "the supplier cannot be changed on an order: cancel it and order the lines again",
    "item": "the item cannot be changed on an order: cancel it and order the lines again",
}
_HEADER = ("ship_to_location", "payment_terms_code", "delivery_terms")


def _never(keys: Any) -> None:
    """A supplier or an item is never changed on an order (OP-3): cancel it and order again."""
    for key in keys:
        for word, message in _NEVER.items():
            if str(key).startswith(word):
                raise HTTPException(422, message)


def _header(raw: Any) -> changes.HeaderChange | None:
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise HTTPException(422, "header must be an object of the fields to change")
    _never(raw)
    unknown = sorted(set(raw) - set(_HEADER))
    if unknown:
        raise HTTPException(422, f"{', '.join(unknown)} cannot be changed on an order")
    values = {k: raw[k] for k in _HEADER if raw.get(k) is not None}
    if any(not isinstance(v, str) for v in values.values()):
        raise HTTPException(422, "the header fields are text")
    return changes.HeaderChange(**values)


def _seen(payload: dict[str, Any]) -> int | None:
    """The version the person saw (OP-9): optional; a later version is refused."""
    raw = payload.get("version")
    if raw in (None, ""):
        return None
    if isinstance(raw, bool) or not str(raw).isdigit():
        raise HTTPException(422, "version must be a whole number")
    return int(raw)


@router.post("/purchase-orders/{no}/revise")
def revise_order(no: str, payload: dict[str, Any], db: Db, ctx: Buyer) -> dict[str, Any]:
    _never(payload)
    header = _header(payload.get("header"))
    raw = payload.get("lines") or []
    if not isinstance(raw, list) or (not raw and header is None):
        raise HTTPException(
            422, "lines is required (a list of {line_no, qty, need_by or reprice}) unless header is given"
        )
    edits: list[changes.LineChange] = []
    for item in raw:
        if not isinstance(item, dict) or not str(item.get("line_no", "")).isdigit():
            raise HTTPException(422, "each line needs a line_no")
        _never(item)
        if item.get("unit_price") not in (None, ""):
            raise HTTPException(422, "a price is not typed: it comes from the contract or the award")
        edits.append(
            changes.LineChange(
                int(item["line_no"]),
                _qty(item.get("qty"), "qty"),
                _date(item.get("need_by"), "need_by"),
                reprice=item.get("reprice") is True,
            )
        )
    seen = _seen(payload)
    return _guard(
        lambda: changes.revise(
            db, ctx.slug, no, ctx.user_id, payload.get("reason"), edits, header=header, seen_version=seen
        )
    )


@router.post("/purchase-orders/{no}/cancel")
def cancel_order(no: str, payload: dict[str, Any], db: Db, ctx: Buyer) -> dict[str, Any]:
    return _guard(
        lambda: changes.cancel(
            db, ctx.slug, no, ctx.user_id, payload.get("reason"), reorder=bool(payload.get("reorder"))
        )
    )


@router.post("/purchase-orders/{no}/close-balance")
def close_balance(no: str, payload: dict[str, Any], db: Db, ctx: Buyer) -> dict[str, Any]:
    return _guard(lambda: changes.close_balance(db, ctx.slug, no, ctx.user_id, payload.get("reason")))


# ---- the supplier's answer: proposed changes and dispatch notes -------------------------------------


@router.get("/purchase-orders/{no}/supplier-answer")
def supplier_answer(no: str, db: Db, ctx: Ctx) -> dict[str, Any]:
    """The changes the supplier asked for (and who may decide them) and its dispatch notes."""
    if db.execute(text("select 1 from po where no = :n"), {"n": no}).first() is None:
        raise HTTPException(404, "Not found")
    return {
        "changes": confirmation.list_changes(db, no, ctx.user_id),
        "dispatch_notes": dispatch_notes.notes(db, no),
    }


@router.post("/supplier-changes/{change_id}/decide")
def decide_supplier_change(change_id: int, payload: dict[str, Any], db: Db, ctx: Ctx) -> dict[str, Any]:
    """A buyer accepts or rejects a change; a price above the tolerance then waits for the purchase manager."""
    accept = payload.get("accept")
    if not isinstance(accept, bool):
        raise HTTPException(422, "accept must be true or false")
    try:
        return confirmation.decide(db, ctx.slug, change_id, ctx.user_id, accept=accept, note=payload.get("note"))
    except confirmation.ConfirmationError as exc:
        raise HTTPException(404 if "does not exist" in str(exc) else 409, str(exc)) from None
    except PermissionDenied as exc:
        raise HTTPException(403, str(exc)) from None
