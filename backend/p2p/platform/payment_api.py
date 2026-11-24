"""The payment screens' API: what is due, planning and approving a run, recording and settling payments, TDS, advances
and notes (4.5). Every read needs a payment permission; a step person may read their own run."""

from collections.abc import Callable
from datetime import date
from decimal import Decimal
from typing import Annotated, Any, TypeVar

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from sqlalchemy import text
from sqlalchemy.orm import Session

from p2p.formulas.calendar import business_today
from p2p.invoices import advances, bank_statement, notes, runs, settlement, tds
from p2p.invoices.service import InvoiceError
from p2p.platform import otp, permissions, routing
from p2p.platform.permissions import require
from p2p.platform.rows import rows as _rows

router = APIRouter(prefix="/api", tags=["payments"])
Db = Annotated[Session, Depends(routing.tenant_session)]
Ctx = Annotated[routing.TenantContext, Depends(routing.tenant_context)]
T = TypeVar("T")

TWO_HUNDRED_TWO = (
    "the bank is closed", "a run cannot be dated", "the value date", "give the bank reference",
    "a bank reference is", "the refund must", "say which rows", "a reason is needed to change",
    "the advance must", "the terms allow", "the amounts differ", "the statement needs",
)  # fmt: skip
FORBIDDEN = (
    "segregation of duties",
    "this run waits",
    "this advance waits",
    "this person approved",
    "only that person",
    "not your step",
)


def _money(value: Any) -> str | None:
    return None if value is None else str(value)


def _date(value: Any, field: str) -> date:
    try:
        return date.fromisoformat(str(value))
    except ValueError:
        raise HTTPException(422, f"{field} must be a date like 2026-11-20") from None


def _call[T](call: Callable[[], T]) -> T:
    try:
        return call()
    except InvoiceError as exc:
        message = str(exc)
        if "does not exist" in message:
            raise HTTPException(404, message) from None
        if message.startswith(FORBIDDEN) or any(message.startswith(w) for w in FORBIDDEN):
            raise HTTPException(403, message) from None
        if message.startswith(TWO_HUNDRED_TWO):
            raise HTTPException(422, message) from None
        raise HTTPException(409, message) from None


def _may_read(db: Session, ctx: routing.TenantContext, run_no: str | None = None) -> None:
    if permissions.can_anywhere(db, ctx.user_id, "payment.view") or permissions.can_anywhere(
        db, ctx.user_id, "payment.approve"
    ):
        return
    if run_no:
        from p2p.approvals.workflow import org_code_of  # noqa: PLC0415 - a small lookup

        me = org_code_of(db, ctx.user_id)
        if (
            me
            and db.execute(
                text(
                    "select 1 from payment_run_approval where run_no = :r and (approver = :m or delegate = :m) limit 1"
                ),
                {"r": run_no, "m": me},
            ).first()
        ):
            return
    raise HTTPException(403, "Not allowed: payment.view")


# ---- what is due -------------------------------------------------------------------------------


@router.get("/payment-runs/due")
def due(db: Db, ctx: Ctx, on: str | None = None, window: int | None = None) -> dict[str, Any]:
    """What a run on a date would pay (small suppliers first), and what is held back and why."""
    _may_read(db, ctx)
    run_date = _date(on, "on") if on else business_today()
    rows: list[dict[str, Any]] = []
    for adv in runs.due_advances(db):
        supplier = _rows(
            db, "select coalesce(trade_name, legal_name) as name from supplier where code = :c", c=adv["supplier_code"]
        )
        hold = tds.preview_advance(db, str(adv["po_no"]), Decimal(str(adv["taxable"])), run_date)
        rows.append(
            {
                "kind": "advance", "invoice": None, "po_no": adv["po_no"],
                "supplier_code": adv["supplier_code"], "supplier": supplier[0]["name"] if supplier else adv["supplier_code"],
                "due_date": run_date.isoformat(), "pay_by": run_date.isoformat(), "msme": False,
                "msme_deadline": None, "total": _money(adv["gross"]), "tds": str(sum((r.amount for r in hold), Decimal(0))),
                "tds_status": None, "credit": None, "advance": None, "discount": None, "interest": None,
                "net": _money(Decimal(str(adv["gross"])) - sum((r.amount for r in hold), Decimal(0))),
                "taxable": _money(adv["taxable"]),
            }
        )  # fmt: skip
    for row in runs.due_invoices(db, run_date, window):
        p = payment_payable(db, row["no"], run_date)
        supplier = _rows(
            db, "select coalesce(trade_name, legal_name) as name from supplier where code = :c", c=row["supplier_code"]
        )
        rows.append(
            {
                "kind": "invoice", "invoice": row["no"], "po_no": None,
                "supplier_code": row["supplier_code"], "supplier": supplier[0]["name"] if supplier else row["supplier_code"],
                "due_date": row["due_date"].isoformat() if row["due_date"] else None,
                "pay_by": (row["pay_by"] or row["due_date"]).isoformat() if row["pay_by"] or row["due_date"] else None,
                "msme": bool(row["msme"]),
                "msme_deadline": row["msme_deadline"].isoformat() if row.get("msme_deadline") else None,
                "tds_status": tds.status_of(db, row["no"]) or "confirmed",
                "total": _money(p.total), "tds": _money(p.tds), "credit": _money(p.credit),
                "advance": _money(p.advance), "discount": _money(p.discount),
                "discount_until": p.window_end.isoformat() if p.window_end else None,
                "interest": _money(p.interest), "net": _money(p.net + p.interest),
            }
        )  # fmt: skip
    held = [
        {
            "item": h["no"],
            "supplier_code": h["supplier_code"],
            "due_date": h["due_date"].isoformat() if h["due_date"] else None,
            "reason": h["reason"],
            "amount": _money(h["total"]),
        }
        for h in runs.held_back(db, run_date, window)
    ]
    return {"due": rows, "held": held}


def payment_payable(db: Session, invoice_no: str, on: date) -> Any:
    from p2p.invoices import payment  # noqa: PLC0415 - kept local so the API's imports stay small

    return payment.payable(db, invoice_no, on)


# ---- runs -------------------------------------------------------------------------------------


@router.get("/payment-runs")
def list_runs(db: Db, ctx: Ctx) -> list[dict[str, Any]]:
    _may_read(db, ctx)
    rows = _rows(
        db,
        "select r.no, r.run_date, r.status, r.total, r.proposed_by, r.approved_by, r.cancel_reason, "
        "(select count(*) from payment p where p.run_no = r.no) as payments from payment_run r "
        "order by r.created_at desc, r.no desc",
    )
    return [
        {
            "id": r["no"],
            "run_date": r["run_date"].isoformat(),
            "status": r["status"],
            "total": _money(r["total"]),
            "payments": r["payments"],
            "cancel_reason": r["cancel_reason"],
        }
        for r in rows
    ]


@router.get("/payment-runs/{no}")
def get_run(no: str, db: Db, ctx: Ctx) -> dict[str, Any]:
    _may_read(db, ctx, no)
    found = _rows(db, "select * from payment_run where no = :n", n=no)
    if not found:
        raise HTTPException(404, "Not found")
    r = found[0]
    payments = []
    for p in _rows(
        db,
        "select p.*, coalesce(s.trade_name, s.legal_name) as supplier from payment p "
        "join supplier s on s.code = p.supplier_code where p.run_no = :n order by p.no",
        n=no,
    ):
        allocations = _rows(
            db,
            "select a.invoice_no, a.amount, a.discount, i.supplier_invoice_no, i.type from payment_allocation a "
            "join invoice i on i.no = a.invoice_no where a.payment_no = :p order by a.invoice_no",
            p=p["no"],
        )
        payments.append(
            {
                "id": p["no"],
                "kind": p["type"],
                "po_no": p["po_no"],
                "supplier": p["supplier"],
                "supplier_code": p["supplier_code"],
                "amount": _money(p["amount"]),
                "tds": _money(p["tds_amount"]),
                "interest": _money(p["msme_interest"]),
                "mode": p["mode"],
                "utr": p["utr"],
                "status": p["status"],
                "value_date": p["value_date"].isoformat() if p["value_date"] else None,
                "recorded_by": p["recorded_by"],
                "evidence": p["evidence_document_id"],
                "fail_reason": p["fail_reason"],
                "settled_at": p["settled_at"].isoformat() if p["settled_at"] else None,
                "invoices": [
                    {
                        "id": a["invoice_no"],
                        "number": a["supplier_invoice_no"],
                        "type": a["type"],
                        "amount": _money(a["amount"]),
                        "discount": _money(a["discount"]),
                    }
                    for a in allocations
                ],
            }
        )
    page = runs.run_page(db, no)
    current = page["current"]
    can_approve = False
    if r["status"] == "proposed" and current is not None:
        from p2p.approvals.workflow import org_code_of  # noqa: PLC0415

        me = org_code_of(db, ctx.user_id)
        can_approve = me is not None and me in {current["approver"], current["delegate"]}
    removed = [
        {
            "item": (a["after"] or {}).get("item"),
            "reason": (a["after"] or {}).get("reason"),
        }
        for a in _rows(
            db,
            "select after from audit_event where action = 'payment_run.item_removed' and entity_id = :n order by id",
            n=no,
        )
    ]
    return {
        "id": r["no"],
        "run_date": r["run_date"].isoformat(),
        "status": r["status"],
        "total": _money(r["total"]),
        "proposed_by": r["proposed_by"],
        "approved_by": r["approved_by"],
        "rule": f"{r['rule_code']} v{r['rule_version']}" if r["rule_code"] else None,
        "cancel_reason": r["cancel_reason"],
        "has_file": bool(r["file_text"]),
        "payments": payments,
        "steps": page["steps"],
        "can_approve": can_approve,
        "needs_code": bool(
            Decimal(str(r["total"])) > 0 and otp.needed(db, Decimal(str(r["total"])), "payment_run_otp_above")
        ),
        "removed": removed,
        "can_file": r["status"] == "approved",
        "can_record": permissions.can_anywhere(db, ctx.user_id, "payment.record"),
        "can_import_statement": permissions.can_anywhere(db, ctx.user_id, "payment.propose"),
        "can_cancel": r["status"] in ("proposed", "approved"),
    }


@router.post("/payment-runs")
def propose(
    payload: dict[str, Any], db: Db, ctx: Annotated[routing.TenantContext, Depends(require("payment.propose"))]
) -> dict[str, Any]:
    run_date = _date(payload.get("run_date") or business_today().isoformat(), "run_date")
    window = payload.get("window_days")
    include = payload.get("include_advances", True) not in (False, "false", "0", 0)
    return _call(lambda: runs.propose_run(
        db, run_date, ctx.user_id,
        window_days=int(window) if window not in (None, "") else None,
        include_advances=include, tenant=ctx.slug,
    ))  # fmt: skip


@router.post("/payment-runs/{no}/code")
def send_code(no: str, db: Db, ctx: Ctx) -> dict[str, Any]:
    return {"sent_to": _call(lambda: runs.send_code(db, no, ctx.user_id))}


@router.post("/payment-runs/{no}/approve")
def approve(no: str, payload: dict[str, Any], db: Db, ctx: Ctx) -> dict[str, Any]:
    return _call(lambda: runs.approve_step(db, no, ctx.user_id, payload.get("code"), payload.get("note")))


@router.post("/payment-runs/{no}/reject")
def reject(no: str, payload: dict[str, Any], db: Db, ctx: Ctx) -> dict[str, Any]:
    reason = str(payload.get("reason") or "")
    if not reason.strip():
        raise HTTPException(422, "a reason is required to reject a run")
    _call(lambda: runs.reject(db, no, ctx.user_id, reason))
    return {"status": "cancelled"}


@router.post("/payment-runs/{no}/cancel")
def cancel(
    no: str, payload: dict[str, Any], db: Db, ctx: Annotated[routing.TenantContext, Depends(require("payment.propose"))]
) -> dict[str, Any]:
    reason = str(payload.get("reason") or "cancelled").strip()

    def work() -> dict[str, Any]:
        run = _rows(db, "select status, proposed_by from payment_run where no = :n", n=no)
        if not run:
            raise InvoiceError(f"{no} does not exist")
        runs.cancel(db, no, ctx.user_id, reason)
        db.commit()
        return {"status": "cancelled"}

    return _call(work)


@router.post("/payment-runs/{no}/file")
def make_file(
    no: str, db: Db, ctx: Annotated[routing.TenantContext, Depends(require("payment.propose"))]
) -> dict[str, Any]:
    return _call(lambda: {"run": no, "status": "file_generated", "file": runs.generate_file(db, no, ctx.user_id)})


@router.get("/payment-runs/{no}/file")
def get_file(
    no: str, db: Db, ctx: Annotated[routing.TenantContext, Depends(require("payment.view"))]
) -> dict[str, Any]:
    rows = _rows(db, "select file_text, status from payment_run where no = :n", n=no)
    if not rows or not rows[0]["file_text"]:
        raise HTTPException(404, "No bank file has been made for this run")
    return {"run": no, "file": rows[0]["file_text"]}


@router.post("/payment-runs/{no}/paid")
def mark_paid(no: str, db: Db, ctx: Ctx) -> dict[str, Any]:
    raise HTTPException(
        410,
        "Marking a run paid by hand is gone: record each payment with its bank reference, or import the bank statement",
    )


# ---- record, fail, reverse, statement ---------------------------------------------------------


def _proof(proof: UploadFile | None, need: bool) -> settlement.Evidence | None:
    if proof is None or not proof.filename:
        if need:
            raise HTTPException(422, "give the bank reference or a proof of payment")
        return None
    kind = proof.content_type or ""
    if not (kind == "application/pdf" or kind.startswith("image/")):
        raise HTTPException(422, "the proof is a PDF or an image")
    data = proof.file.read()
    return settlement.Evidence(data, proof.filename, kind)


@router.post("/payments/{no}/record")
def record(
    no: str,
    db: Db,
    ctx: Annotated[routing.TenantContext, Depends(require("payment.record"))],
    utr: Annotated[str | None, Form()] = None,
    value_date: Annotated[str | None, Form()] = None,
    proof: Annotated[UploadFile | None, File()] = None,
) -> dict[str, Any]:
    on = _date(value_date or business_today().isoformat(), "value_date")
    evidence = _proof(proof, need=not (utr or "").strip())
    return _call(lambda: settlement.record(
        db, no, ctx.user_id, utr=utr, value_date=on, evidence=evidence, tenant=ctx.slug,
    ))  # fmt: skip


@router.post("/payments/{no}/failed")
def failed(
    no: str,
    db: Db,
    ctx: Annotated[routing.TenantContext, Depends(require("payment.record"))],
    reason: Annotated[str, Form()] = "",
    value_date: Annotated[str | None, Form()] = None,
    bank_line_id: Annotated[int | None, Form()] = None,
    proof: Annotated[UploadFile | None, File()] = None,
) -> dict[str, Any]:
    on = _date(value_date or business_today().isoformat(), "value_date")
    evidence = _proof(proof, need=bank_line_id is None)
    if not reason.strip():
        raise HTTPException(422, "a reason is needed")
    if bank_line_id is None and evidence is None:
        raise HTTPException(422, "give the bank's line or a proof")
    return _call(lambda: settlement.failed(
        db, no, ctx.user_id, reason=reason, value_date=on, bank_line_id=bank_line_id,
        evidence=evidence, tenant=ctx.slug,
    ))  # fmt: skip


@router.post("/payments/{no}/reverse")
def reverse(
    no: str,
    db: Db,
    ctx: Annotated[routing.TenantContext, Depends(require("payment.record"))],
    reason: Annotated[str, Form()] = "",
    bank_line_id: Annotated[int | None, Form()] = None,
    proof: Annotated[UploadFile | None, File()] = None,
) -> dict[str, Any]:
    if not reason.strip():
        raise HTTPException(422, "a reason is needed")
    evidence = _proof(proof, need=bank_line_id is None)
    return _call(lambda: settlement.reversed(
        db, no, ctx.user_id, reason=reason, bank_line_id=bank_line_id, evidence=evidence,
    ))  # fmt: skip


@router.post("/payment-runs/statement")
def import_bank_statement(
    payload: dict[str, Any], db: Db, ctx: Annotated[routing.TenantContext, Depends(require("payment.propose"))]
) -> dict[str, Any]:
    """The bank's statement as CSV text. A debit line settles its own payment; credits that may be returns are listed."""
    csv_text = payload.get("csv")
    if not isinstance(csv_text, str) or not csv_text.strip():
        raise HTTPException(422, "send the statement as CSV text in 'csv'")
    if len(csv_text) > 2_000_000:
        raise HTTPException(422, "the statement is too large")
    try:
        return bank_statement.import_statement(db, csv_text, ctx.user_id, today=business_today(), tenant=ctx.slug)
    except InvoiceError as exc:
        raise HTTPException(422, str(exc)) from None


# ---- TDS --------------------------------------------------------------------------------------


@router.get("/tds")
def tds_rows(
    db: Db, ctx: Annotated[routing.TenantContext, Depends(require("tds.confirm"))], status: str = "proposed"
) -> list[dict[str, Any]]:
    if status != "proposed":
        raise HTTPException(422, "only the rows waiting for confirmation are listed here")
    return tds.pending(db)


@router.post("/tds/confirm")
def tds_confirm(
    payload: dict[str, Any], db: Db, ctx: Annotated[routing.TenantContext, Depends(require("tds.confirm"))]
) -> list[dict[str, Any]]:
    ids = payload.get("ids") or []
    if not isinstance(ids, list) or not ids:
        raise HTTPException(422, "say which rows to confirm")
    section = payload.get("section_code")
    reason = payload.get("reason")
    rows = _call(lambda: tds.confirm(db, [int(i) for i in ids], ctx.user_id, section_code=section, reason=reason))
    db.commit()
    return [
        {"id": r["id"], "status": r["status"], "section": r["section_code"], "amount": _money(r["amount"])}
        for r in rows
    ]


# ---- advances ---------------------------------------------------------------------------------


@router.post("/purchase-orders/{no}/advance")
def request_advance(
    no: str,
    payload: dict[str, Any],
    db: Db,
    ctx: Annotated[routing.TenantContext, Depends(require("advance.request"))],
) -> dict[str, Any]:
    pct = payload.get("pct")
    return _call(lambda: advances.request(
        db, no, ctx.user_id, pct=Decimal(str(pct)) if pct not in (None, "") else None,
        reason=payload.get("reason"), tenant=ctx.slug,
    ))  # fmt: skip


@router.get("/advances")
def list_advances(db: Db, ctx: Ctx) -> list[dict[str, Any]]:
    _may_read(db, ctx)
    return [
        {
            **{
                k: (v.isoformat() if isinstance(v, date) else str(v) if isinstance(v, Decimal) else v)
                for k, v in r.items()
            }
        }
        for r in advances.listing(db)
    ]


@router.post("/advances/{request_id}/recover")
def recover(
    request_id: int,
    payload: dict[str, Any],
    db: Db,
    ctx: Annotated[routing.TenantContext, Depends(require("advance.request"))],
) -> dict[str, Any]:
    return _call(lambda: advances.recover(db, request_id, ctx.user_id, str(payload.get("reason") or "")))


@router.post("/advances/{request_id}/reject")
def reject_advance(
    request_id: int,
    payload: dict[str, Any],
    db: Db,
    ctx: Annotated[routing.TenantContext, Depends(require("advance.approve"))],
) -> dict[str, Any]:
    _call(lambda: advances.reject(db, request_id, ctx.user_id, str(payload.get("reason") or "")))
    return {"id": request_id, "status": "rejected"}


# ---- notes ------------------------------------------------------------------------------------


@router.post("/debit-notes")
def debit_note(
    payload: dict[str, Any], db: Db, ctx: Annotated[routing.TenantContext, Depends(require("invoice.book"))]
) -> dict[str, Any]:
    supplier = payload.get("supplier")
    if not supplier:
        raise HTTPException(422, "say which supplier the debit note is for")
    taxable = Decimal(str(payload.get("taxable") or "0"))
    if taxable <= 0:
        raise HTTPException(422, "the debit note needs a taxable value")
    gst = Decimal(str(payload.get("gst_pct") or "0"))
    reason = str(payload.get("reason") or "").strip()
    if not reason:
        raise HTTPException(422, "a reason is needed for a debit note")
    note = _call(lambda: notes.raise_debit_note(
        db=db, supplier=str(supplier), taxable=taxable, gst_pct=gst, reason=reason,
        source_ref=str(payload.get("source_ref") or ""), user_id=ctx.user_id,
        ref_invoice_no=payload.get("ref_invoice_no"),
    ))  # fmt: skip
    db.commit()
    return {"invoice": note, "status": "received"}


@router.post("/invoices/{no}/approve")
def approve_note(
    no: str, db: Db, ctx: Annotated[routing.TenantContext, Depends(require("invoice.approve"))]
) -> dict[str, Any]:
    """The note branch of block 10's invoice approval endpoint (12.7.1).

    # NEEDS block 10: merge this branch into block 10's POST /api/invoices/{no}/approve (0170); the note enters
    # `received` and another person with invoice.approve approves it (audit `invoice.note_approve`).
    """
    return _call(lambda: notes.approve(db, no, ctx.user_id))


@router.post("/invoices/{no}/refund")
def refund(
    no: str,
    payload: dict[str, Any],
    db: Db,
    ctx: Annotated[routing.TenantContext, Depends(require("payment.record"))],
) -> dict[str, Any]:
    line = payload.get("bank_line_id")
    amount = payload.get("amount")
    if line is None or amount is None:
        raise HTTPException(422, "give the bank line and the amount")
    return _call(lambda: notes.refund(db, no, int(line), Decimal(str(amount)), ctx.user_id))
