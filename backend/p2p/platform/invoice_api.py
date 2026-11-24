"""The invoice screens' API: what can be invoiced, entering an invoice, the exceptions, releasing a block, and the two
decisions on a bill without an order (authorise, approve, reject)."""

from collections.abc import Callable
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import text
from sqlalchemy.orm import Session

from p2p.agents import invoice_exceptions
from p2p.approvals import budget
from p2p.approvals.workflow import name_of, org_code_of
from p2p.formulas.calendar import business_today
from p2p.formulas.money import format_qty
from p2p.invoices import non_po
from p2p.invoices import service as invoices
from p2p.platform import routing
from p2p.platform.permissions import can_anywhere, need_any, require
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


SEE = ("invoice.view", "invoice.book", "invoice.match", "invoice.approve")


def _may_see(db: Session, user_id: int, no: str) -> bool:
    """A role that may see invoices, or the bill's own authoriser or a step approver (or their deputy), that bill only."""
    if any(can_anywhere(db, user_id, action) for action in SEE):
        return True
    inv = db.execute(text("select authoriser from invoice where no = :n"), {"n": no}).first()
    if inv is None:
        return False
    me = org_code_of(db, user_id)
    if me is None:
        return False
    if inv[0] and me in {inv[0], non_po.deputy_of(db, str(inv[0]), business_today())}:
        return True
    step = db.execute(
        text(
            "select 1 from invoice_approval_step where invoice_no = :n "
            "and (approver = :me or delegate = :me or exists (select 1 from delegation d where d.from_user = approver "
            "and d.to_user = :me and d.start_date <= :d and (d.end_date is null or d.end_date >= :d))) limit 1"
        ),
        {"n": no, "me": me, "d": business_today()},
    ).first()
    return step is not None


@router.get("/invoices")
def list_invoices(db: Db, ctx: Ctx, status: str | None = None, kind: str | None = None) -> list[dict[str, Any]]:
    need_any(db, ctx.user_id, *SEE)
    rows = _rows(
        db,
        "select i.no, i.type, i.kind, i.category_code, c.name as category, i.supplier_code, "
        "coalesce(s.trade_name, s.legal_name) as supplier, i.supplier_invoice_no, "
        "i.invoice_date, i.total, i.status, i.payment_block, i.block_reason, i.duplicate_of, i.due_date "
        "from invoice i join supplier s on s.code = i.supplier_code "
        "left join item_category c on c.code = i.category_code "
        "where (cast(:st as text) is null or i.status = :st) and (cast(:k as text) is null or i.kind = :k) "
        "order by i.created_at desc, i.no desc limit 500",
        st=status,
        k=kind,
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
            "kind": r["kind"],
            "category": r["category"],
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


@router.get("/invoices/non-po/options")
def non_po_options(
    db: Db,
    ctx: Annotated[routing.TenantContext, Depends(require("invoice.book"))],
    supplier: str | None = None,
) -> list[dict[str, Any]]:
    """The categories a supplier may bill without an order, with their limits, documents, GL and a proposed cost centre."""
    today = business_today()
    rules = non_po.approved_categories(db, supplier, today) if supplier else non_po.rules_in_force(db, today)
    return [
        {
            "category": r.category_code,
            "name": r.category_name,
            "max_value": _money(r.max_value),
            "supporting_doc": r.supporting_doc,
            "gl_code": r.gl_code,
            "version": r.version,
            "cost_centre_code": non_po.last_cost_centre(db, supplier, r.category_code) if supplier else None,
        }
        for r in rules
    ]


@router.get("/invoices/{no}")
def get_invoice(no: str, db: Db, ctx: Ctx) -> dict[str, Any]:
    if not _may_see(db, ctx.user_id, no):
        raise HTTPException(403, "Not allowed: invoice.view")
    rows = _rows(
        db,
        "select i.*, coalesce(s.trade_name, s.legal_name) as supplier, c.name as category_name, "
        "cc.name as cost_centre_name from invoice i join supplier s on s.code = i.supplier_code "
        "left join item_category c on c.code = i.category_code left join cost_centre cc on cc.code = i.cost_centre_code "
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
    documents = _rows(
        db,
        "select l.kind, l.document_id, d.filename, d.doc_type from invoice_document_link l "
        "join document d on d.id = l.document_id where l.invoice_no = :n order by l.kind, l.document_id",
        n=no,
    )
    can_work = can_anywhere(db, ctx.user_id, "invoice.match")
    can_authorise, can_approve = non_po.may(db, no, ctx.user_id) if i["kind"] == "non_po" else (False, False)
    checks = budget.check_invoice(db, no, business_today()) if i["kind"] == "non_po" else []
    return {
        "id": i["no"],
        "type": i["type"],
        "kind": i["kind"],
        "category": i["category_code"],
        "category_name": i["category_name"],
        "cost_centre": i["cost_centre_code"],
        "cost_centre_name": i["cost_centre_name"],
        "gl_code": lines[0]["gl_code"] if i["kind"] == "non_po" and lines else None,
        "supplier_code": i["supplier_code"],
        "supplier": i["supplier"],
        "number": i["supplier_invoice_no"],
        "date": i["invoice_date"].isoformat(),
        "fy": i["fy"],
        "status": i["status"],
        "block": i["payment_block"],
        "reason": i["block_reason"],
        "duplicate_of": i["duplicate_of"],
        "suspect_of": i["suspect_of"],
        "not_duplicate_reason": i["not_duplicate_reason"],
        "authoriser": i["authoriser"],
        "authoriser_name": name_of(db, i["authoriser"]) if i["authoriser"] else None,
        "authorised_by": i["authorised_by"],
        "authorised_on": i["authorised_on"].isoformat() if i["authorised_on"] else None,
        "approved_by": i["approved_by"],
        "approval_rule": {"code": i["approval_rule_code"], "version": i["approval_rule_version"]}
        if i["approval_rule_code"]
        else None,
        "over_budget_reason": i["over_budget_reason"],
        "steps": non_po.steps(db, no) if i["kind"] == "non_po" else [],
        "budget": [c.as_json() for c in checks],
        "documents": documents,
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
                "hsn_sac": ln["hsn_sac"],
                "qty": format_qty(ln["qty"]),
                "uom": ln["uom"],
                "unit_price": _money(ln["unit_price"]),
                "gst_rate": _money(ln["gst_rate"]),
                "taxable": _money(ln["taxable"]),
                "category": ln["category_code"],
                "cost_centre": ln["cost_centre_code"],
                "gl_code": ln["gl_code"],
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
        "can_authorise": can_authorise,
        "can_approve": can_approve,
    }


def _error(call: Callable[[], dict[str, Any]]) -> dict[str, Any]:
    try:
        return call()
    except invoices.InvoiceInputError as exc:
        raise HTTPException(422, str(exc)) from None
    except invoices.InvoiceError as exc:
        message = str(exc)
        if "does not exist" in message:
            raise HTTPException(404, message) from None
        first = ("segregation", "not your turn", "may not approve", "not linked", "this role")
        raise HTTPException(403 if message.startswith(first) else 409, message) from None


@router.post("/invoices")
def enter_invoice(
    payload: dict[str, Any], db: Db, ctx: Annotated[routing.TenantContext, Depends(require("invoice.book"))]
) -> dict[str, Any]:
    raw_lines = payload.get("lines")
    if not isinstance(raw_lines, list):
        raise HTTPException(422, "lines must be a list")
    kind = str(payload.get("kind") or "po")
    if kind not in ("po", "non_po"):
        raise HTTPException(422, "kind must be po or non_po")
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
            kind=kind,
            category_code=payload.get("category_code") or None,
            cost_centre_code=payload.get("cost_centre_code") or None,
            gl_code=payload.get("gl_code") or None,
            documents=list(payload.get("documents") or []),
            not_duplicate_reason=payload.get("not_duplicate_reason") or None,
            taxable=_num(payload["taxable"], "taxable") if payload.get("taxable") not in (None, "") else None,
            cgst=_num(payload["cgst"], "cgst") if payload.get("cgst") not in (None, "") else None,
            sgst=_num(payload["sgst"], "sgst") if payload.get("sgst") not in (None, "") else None,
            igst=_num(payload["igst"], "igst") if payload.get("igst") not in (None, "") else None,
            total=_num(payload["total"], "total") if payload.get("total") not in (None, "") else None,
            lines=[
                invoices.InvLine(
                    po_no=(str(raw["po_no"]) if raw.get("po_no") else None),
                    po_line_no=(int(raw["po_line_no"]) if raw.get("po_line_no") else None),
                    qty=_num(raw.get("qty"), "qty"),
                    unit_price=_num(raw.get("unit_price"), "unit_price"),
                    discount=_num(raw.get("discount"), "discount", Decimal(0)),
                    gst_rate=_num(raw["gst_rate"], "gst_rate") if raw.get("gst_rate") not in (None, "") else None,
                    description=raw.get("description") or None,
                    hsn_sac=raw.get("hsn_sac") or None,
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
            422,
            "an invoice needs supplier_code, number, date and lines with qty and unit_price "
            "(an invoice line names po_no and po_line_no; a bill line does not)",
        ) from None

    def enter() -> dict[str, Any]:
        done = invoices.enter_invoice(db, data, ctx.user_id, as_of=business_today())
        return {
            "invoice": done.no,
            "status": done.status,
            "result": done.result,
            "reasons": done.reasons,
            "duplicate_of": done.duplicate_of,
            "kind": done.kind,
            "authoriser": done.authoriser,
        }

    return _error(enter)


def _decision(call: Callable[[], dict[str, Any]]) -> dict[str, Any]:
    try:
        return call()
    except invoices.InvoiceError as exc:
        message = str(exc)
        if "does not exist" in message:
            raise HTTPException(404, message) from None
        if message.startswith(("segregation", "it is not your turn", "this role", "this login")):
            raise HTTPException(403, message) from None
        raise HTTPException(409, message) from None


@router.post("/invoices/{no}/authorise")
def authorise(no: str, payload: dict[str, Any], db: Db, ctx: Ctx) -> dict[str, Any]:
    def go() -> dict[str, Any]:
        return non_po.authorise(
            db,
            ctx.slug,
            no,
            ctx.user_id,
            note=payload.get("note"),
            over_budget_reason=payload.get("over_budget_reason"),
        )

    return _decision(go)


@router.post("/invoices/{no}/approve")
def approve(no: str, payload: dict[str, Any], db: Db, ctx: Ctx) -> dict[str, Any]:
    # NEEDS block 12: a clean credit or debit note waits `received` for a second person and is approved through this
    # same endpoint (block 10's interim: a clean note is approved at entry).
    def go() -> dict[str, Any]:
        return non_po.approve(
            db,
            ctx.slug,
            no,
            ctx.user_id,
            note=payload.get("note"),
            over_budget_reason=payload.get("over_budget_reason"),
        )

    return _decision(go)


@router.post("/invoices/{no}/reject")
def reject(no: str, payload: dict[str, Any], db: Db, ctx: Ctx) -> dict[str, str]:
    reason = str(payload.get("reason") or "").strip()
    if not reason:
        raise HTTPException(422, "a reason is needed")

    def go() -> dict[str, Any]:
        non_po.reject(db, ctx.slug, no, ctx.user_id, reason)
        return {"status": "cancelled"}

    return _decision(go)


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
