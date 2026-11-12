"""Confirming services and taking in goods without an order: the main app's screens, and the page behind the link in the
email that asks a requester to confirm a service (no sign-in: the signed link is the key, as for approval links)."""

from collections.abc import Callable
from datetime import date
from decimal import Decimal, InvalidOperation
from html import escape
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from p2p.approvals.email_links import LinkError
from p2p.approvals.workflow import org_code_of
from p2p.formulas.calendar import business_today
from p2p.formulas.money import format_inr, format_qty
from p2p.platform import permissions, proposals, ratelimit, routing
from p2p.platform.link_pages import page
from p2p.platform.permissions import can_anywhere, require
from p2p.receiving import goods, service_links, services, unordered

router = APIRouter(tags=["services and unordered receipts"])
Db = Annotated[Session, Depends(routing.tenant_session)]
Ctx = Annotated[routing.TenantContext, Depends(routing.tenant_context)]
Store = Annotated[routing.TenantContext, Depends(require("grn.create"))]
Buyer = Annotated[routing.TenantContext, Depends(require("po.create"))]
PurchaseManager = Annotated[routing.TenantContext, Depends(require(unordered.APPROVE))]
WINDOW, MAX_BAD = 600.0, 30


def _number(value: Any, name: str) -> Decimal:
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise HTTPException(422, f"{name} must be a number") from None
    if not number.is_finite():
        raise HTTPException(422, f"{name} must be a number")
    return number


def _day(value: Any, name: str) -> date | None:
    if value in (None, ""):
        return None
    try:
        return date.fromisoformat(str(value))
    except ValueError:
        raise HTTPException(422, f"{name} must be a date (YYYY-MM-DD)") from None


def _guard(call: Callable[[], Any]) -> Any:
    try:
        return call()
    except (services.ServiceEntryError, unordered.UnorderedError, goods.ReceiptError) as exc:
        message = str(exc)
        if "does not exist" in message:
            raise HTTPException(404, message) from None
        raise HTTPException(403 if message.startswith(("segregation", "only the person")) else 409, message) from None
    except (permissions.PermissionDenied, proposals.ProposalError) as exc:
        raise HTTPException(403 if isinstance(exc, permissions.PermissionDenied) else 409, str(exc)) from None


def _line_json(r: dict[str, Any]) -> dict[str, Any]:
    return {
        "po_no": r["po_no"],
        "line_no": r["line_no"],
        "name": r["description"],
        "supplier": r["supplier"],
        "ordered": format_qty(r["qty"]),
        "uom": r["uom"],
        "unit_price": str(r["unit_price"]),
        "amount": str(r["amount"]),
        "confirmed": format_qty(r["received_qty"]),
        "confirmed_amount": str(r["confirmed_amount"]),
        "left": format_qty(r["left"]),
        "need_by": r["need_by"].isoformat() if r["need_by"] else None,
    }


# ---- confirming services (the requester) ------------------------------------------------------


@router.get("/api/services/to-confirm")
def services_to_confirm(db: Db, ctx: Ctx) -> dict[str, Any]:
    """The services this person asked for that still wait for their confirmation, and the confirmations made lately."""
    me = org_code_of(db, ctx.user_id)
    lines = services.open_lines(db, for_person=me) if me else []
    made = [e for e in services.entries(db, limit=200) if me and e["accepted_by"] == me]
    return {
        "lines": [_line_json(r) for r in lines],
        "entries": [
            {
                "id": e["no"], "po_no": e["po_no"], "line_no": e["po_line_no"], "name": e["description"],
                "supplier": e["supplier"], "qty": format_qty(e["qty_accepted"]), "uom": e["uom"],
                "amount": str(e["amount_accepted"]), "on": e["accepted_on"].isoformat(), "via": e["via"],
                "period_from": e["period_from"].isoformat() if e["period_from"] else None,
                "period_to": e["period_to"].isoformat() if e["period_to"] else None,
            }
            for e in made
        ],
    }  # fmt: skip


@router.post("/api/services/confirm")
def confirm_service(payload: dict[str, Any], db: Db, ctx: Ctx) -> dict[str, Any]:
    try:
        c = services.Confirmation(
            str(payload["po_no"]),
            int(payload["line_no"]),
            str(payload.get("basis") or "pct"),
            _number(payload.get("value"), "value"),
            _day(payload.get("period_from"), "period_from"),
            _day(payload.get("period_to"), "period_to"),
            payload.get("remarks"),
        )
    except (KeyError, TypeError, ValueError):
        raise HTTPException(422, "po_no, line_no, basis and value are needed") from None

    def run() -> dict[str, Any]:
        done = services.confirm(db, c, ctx.user_id, as_of=business_today())
        return {
            "entry": done.no, "qty": format_qty(done.qty), "amount": str(done.amount), "po_status": done.po_status,
            "rematched": done.rematched,
        }  # fmt: skip

    result: dict[str, Any] = _guard(run)
    return result


# ---- the emailed link -------------------------------------------------------------------------


def _bad_key(request: Request) -> str:
    key = f"service-link-bad:{request.client.host if request.client else 'unknown'}"
    if ratelimit.count(key, WINDOW) >= MAX_BAD:
        raise LinkError("too many wrong links from this address; try again in a little while")
    return key


def _link(token: str, request: Request) -> service_links.ServiceLink:
    key = _bad_key(request)
    try:
        return service_links.read(token)
    except LinkError:
        ratelimit.add(key)
        raise


def _refused(message: str, status: int = 409) -> HTMLResponse:
    return page("This link cannot be used", f"<p>{escape(message)}</p>", status)


def _form(token: str, line: dict[str, Any], note: str = "") -> HTMLResponse:
    body = (
        f"<p>{escape(line['description'])} from {escape(line['supplier'])} on order {escape(line['po_no'])}.</p>"
        "<table><tr><th>Ordered</th><th>Confirmed so far</th><th>Order value</th></tr>"
        f"<tr><td>{escape(format_qty(line['qty']))} {escape(line['uom'] or '')}</td>"
        f"<td>{escape(format_qty(line['received_qty']))}</td><td>{escape(format_inr(line['amount']))}</td></tr></table>"
        + (f"<p class=note>{escape(note)}</p>" if note else "")
        + f"<form method=post action='/confirm-service/{escape(token)}'>"
        "<p class=muted>How much of it was done? Give a percentage of the order, a quantity, or an amount in rupees.</p>"
        "<p><select name=basis><option value=pct>Percent of the order</option><option value=qty>Quantity</option>"
        "<option value=amount>Amount (Rs)</option></select></p><input name=value inputmode=decimal required "
        "placeholder='For example 100'><p class=muted>Remarks (optional)</p><textarea name=remarks rows=2 maxlength=500>"
        "</textarea><p><button class=ok>Confirm</button></p></form>"
        "<p class=muted>This link works once. Opening it confirms nothing.</p>"
    )
    return page(f"Confirm a service ({line['po_no']})", body)


@router.get("/confirm-service/{token}", response_class=HTMLResponse)
def show_service_link(token: str, request: Request) -> HTMLResponse:
    try:
        link = _link(token, request)
        with Session(routing.engine_for(link.tenant)) as db:
            return _form(token, service_links.summary(db, link))
    except LinkError as exc:
        return _refused(str(exc), 400)


@router.post("/confirm-service/{token}", response_class=HTMLResponse)
def confirm_by_link(
    token: str,
    request: Request,
    basis: Annotated[str, Form()],
    value: Annotated[str, Form()],
    remarks: Annotated[str, Form()] = "",
) -> HTMLResponse:
    try:
        link = _link(token, request)
        amount = Decimal(value.strip().replace(",", ""))
        with Session(routing.engine_for(link.tenant)) as db:
            no = service_links.confirm(db, link, basis, amount, remarks.strip()[:500] or None, as_of=business_today())
    except (InvalidOperation, ValueError):
        return _refused("the value must be a number", 422)
    except LinkError as exc:
        return _refused(str(exc))
    return page(
        "Service confirmed", f"<p>Recorded as {escape(no)}. Thank you.</p><p class=muted>You can close this page.</p>"
    )


# ---- goods received without an order (the store, the purchase manager, the buyer) -------------


def _unordered_json(r: dict[str, Any], db: Session, user_id: int) -> dict[str, Any]:
    can_decide = r["status"] == "pending" and can_anywhere(db, user_id, unordered.APPROVE)
    return {
        "id": r["no"], "status": r["status"], "supplier": r["supplier"], "supplier_code": r["supplier_code"],
        "item": r["item_name"], "item_code": r["item_code"], "qty": format_qty(r["qty"]), "uom": r["uom"],
        "location": r["location_code"], "reason": r["reason"], "challan": r["challan"], "serials": r["serials"] or [],
        "received_on": r["received_on"].isoformat(), "received_by": r["received_by"], "proposal_id": r["proposal_id"],
        "decision_note": r["decision_note"], "po_no": r["po_no"], "po_status": r["po_status"], "gr_no": r["gr_no"],
        "returned_on": r["returned_on"].isoformat() if r["returned_on"] else None, "return_ref": r["return_ref"],
        "can_decide": can_decide,
        "can_order": r["status"] == "approved" and can_anywhere(db, user_id, "po.create"),
        "can_receive": r["status"] == "ordered" and r["po_status"] in goods.RECEIVABLE
        and can_anywhere(db, user_id, "grn.create"),
        "can_return": r["status"] == "rejected" and can_anywhere(db, user_id, "grn.create"),
    }  # fmt: skip


@router.get("/api/unordered-receipts")
def list_unordered(db: Db, ctx: Ctx, status: str | None = None, open_only: bool = False) -> list[dict[str, Any]]:
    return [_unordered_json(r, db, ctx.user_id) for r in unordered.listing(db, status=status, open_only=open_only)]


@router.post("/api/unordered-receipts")
def record_unordered(payload: dict[str, Any], db: Db, ctx: Store) -> dict[str, Any]:
    serials = payload.get("serials") or []
    if isinstance(serials, str):
        serials = [s for s in serials.replace(",", "\n").splitlines()]
    try:
        data = unordered.UnorderedIn(
            str(payload["supplier_code"]),
            str(payload["item_code"]),
            _number(payload.get("qty"), "qty"),
            str(payload["location_code"]),
            str(payload.get("reason") or ""),
            payload.get("challan"),
            [str(s) for s in serials],
        )
    except (KeyError, TypeError):
        raise HTTPException(422, "supplier_code, item_code, qty, location_code and reason are needed") from None
    no: str = _guard(lambda: unordered.record(db, data, ctx.user_id, as_of=business_today()))
    return {"receipt": no, "status": "pending"}


@router.post("/api/unordered-receipts/{no}/approve")
def approve_unordered(no: str, payload: dict[str, Any], db: Db, ctx: PurchaseManager) -> dict[str, Any]:
    row = next((r for r in unordered.listing(db) if r["no"] == no), None)
    if row is None or row["proposal_id"] is None:
        raise HTTPException(404, "Not found")
    done = _guard(lambda: proposals.approve(db, int(row["proposal_id"]), ctx.user_id, note=payload.get("note")))
    now = next(r for r in unordered.listing(db) if r["no"] == no)
    if now["status"] != "approved":
        raise HTTPException(409, f"{no} could not be approved: it is {now['status']}")
    return {"receipt": no, "proposal": done.status}


@router.post("/api/unordered-receipts/{no}/reject")
def reject_unordered(no: str, payload: dict[str, Any], db: Db, ctx: PurchaseManager) -> dict[str, Any]:
    reason = str(payload.get("reason") or "").strip()
    if not reason:
        raise HTTPException(422, "a reason is needed")
    _guard(lambda: unordered.reject(db, no, ctx.user_id, reason))
    return {"receipt": no, "status": "rejected"}


@router.post("/api/unordered-receipts/{no}/order")
def order_unordered(no: str, payload: dict[str, Any], db: Db, ctx: Buyer) -> dict[str, Any]:
    price = _number(payload.get("unit_price"), "unit_price")
    po_no: str = _guard(lambda: unordered.raise_order(db, ctx.slug, no, ctx.user_id, price, as_of=business_today()))
    return {"receipt": no, "po_no": po_no}


@router.post("/api/unordered-receipts/{no}/receive")
def receive_unordered(no: str, db: Db, ctx: Store) -> dict[str, Any]:
    gr_no: str = _guard(lambda: unordered.receive_on_order(db, no, ctx.user_id, as_of=business_today()))
    goods.tell_requesters(db, ctx.slug, gr_no)
    return {"receipt": no, "goods_receipt": gr_no}


@router.post("/api/unordered-receipts/{no}/returned")
def returned_unordered(no: str, payload: dict[str, Any], db: Db, ctx: Store) -> dict[str, Any]:
    _guard(lambda: unordered.mark_returned(db, no, ctx.user_id, payload.get("ref"), as_of=business_today()))
    return {"receipt": no, "status": "returned"}
