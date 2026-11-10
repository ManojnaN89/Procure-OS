"""The payment screens' API: what is due, planning a payment run, its approval status, the bank file, and recording payment."""

from collections.abc import Callable
from datetime import date
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import text
from sqlalchemy.orm import Session

from p2p.formulas import payment as rules
from p2p.formulas.calendar import business_today
from p2p.invoices import bank_statement, payment
from p2p.invoices.service import InvoiceError
from p2p.platform import routing
from p2p.platform.permissions import can_anywhere, require

router = APIRouter(prefix="/api", tags=["payments"])
Db = Annotated[Session, Depends(routing.tenant_session)]
Ctx = Annotated[routing.TenantContext, Depends(routing.tenant_context)]


def _money(value: Any) -> str | None:
    return None if value is None else str(value)


def _date(value: Any, field: str) -> date:
    try:
        return date.fromisoformat(str(value))
    except ValueError:
        raise HTTPException(422, f"{field} must be a date like 2026-11-20") from None


def _rows(db: Session, sql: str, **params: Any) -> list[dict[str, Any]]:
    return [dict(r._mapping) for r in db.execute(text(sql), params)]


def _error(call: Callable[[], dict[str, Any]]) -> dict[str, Any]:
    try:
        return call()
    except InvoiceError as exc:
        message = str(exc)
        if "does not exist" in message:
            raise HTTPException(404, message) from None
        raise HTTPException(403 if message.startswith("segregation") else 409, message) from None


@router.get("/payment-runs/due")
def due(db: Db, ctx: Ctx, on: str | None = None, window: int | None = None) -> list[dict[str, Any]]:
    """What a run on a date would pay: advances to be paid, then approved invoices due within the window (and those whose
    early-payment discount would be lost by waiting), small suppliers first."""
    run_date = _date(on, "on") if on else business_today()
    out: list[dict[str, Any]] = []
    for adv in payment.due_advances(db):
        gross, taxable = rules.advance_amounts(adv["total"], adv["taxable"], adv["advance_pct"])
        sup = _rows(
            db, "select coalesce(trade_name, legal_name) as name from supplier where code = :c", c=adv["supplier_code"]
        )
        out.append(
            {
                "kind": "advance",
                "invoice": None,
                "po_no": adv["no"],
                "supplier_code": adv["supplier_code"],
                "supplier": sup[0]["name"] if sup else adv["supplier_code"],
                "due_date": run_date.isoformat(),
                "msme": False,
                "total": _money(gross),
                "tds": None,
                "credit": None,
                "advance": None,
                "discount": None,
                "interest": None,
                "net": _money(gross),
                "taxable": _money(taxable),
            }
        )
    for row in payment.due_invoices(db, run_date, window):
        p = payment.payable(db, row["no"], run_date)
        sup = _rows(
            db, "select coalesce(trade_name, legal_name) as name from supplier where code = :c", c=row["supplier_code"]
        )
        out.append(
            {
                "kind": "invoice",
                "invoice": row["no"],
                "po_no": None,
                "supplier_code": row["supplier_code"],
                "supplier": sup[0]["name"] if sup else row["supplier_code"],
                "due_date": row["due_date"].isoformat(),
                "msme": bool(row["msme"]),
                "total": _money(p.total),
                "tds": _money(p.tds),
                "credit": _money(p.credit),
                "advance": _money(p.advance),
                "discount": _money(p.discount),
                "discount_until": p.window_end.isoformat() if p.window_end else None,
                "interest": _money(p.interest),
                "net": _money(p.net + p.interest),
            }
        )
    return out


@router.get("/payment-runs")
def list_runs(db: Db, ctx: Ctx) -> list[dict[str, Any]]:
    rows = _rows(
        db,
        "select r.no, r.run_date, r.status, r.total, r.proposed_by, r.approved_by, "
        "(select count(*) from payment p where p.run_no = r.no) as payments from payment_run r order by r.created_at desc, r.no desc",
    )
    return [
        {
            "id": r["no"],
            "run_date": r["run_date"].isoformat(),
            "status": r["status"],
            "total": _money(r["total"]),
            "payments": r["payments"],
        }
        for r in rows
    ]


@router.get("/payment-runs/{no}")
def get_run(no: str, db: Db, ctx: Ctx) -> dict[str, Any]:
    rows = _rows(db, "select * from payment_run where no = :n", n=no)
    if not rows:
        raise HTTPException(404, "Not found")
    r = rows[0]
    payments = _rows(
        db,
        "select p.*, coalesce(s.trade_name, s.legal_name) as supplier from payment p join supplier s on s.code = p.supplier_code "
        "where p.run_no = :n order by p.no",
        n=no,
    )
    out = []
    for p in payments:
        allocations = _rows(
            db,
            "select a.invoice_no, a.amount, a.discount, i.supplier_invoice_no, i.type from payment_allocation a join invoice i on i.no = a.invoice_no "
            "where a.payment_no = :p order by a.invoice_no",
            p=p["no"],
        )
        out.append(
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
                "paid_on": p["paid_on"].isoformat() if p["paid_on"] else None,
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
    proposal = (
        _rows(db, "select id, status, summary from proposal where id = :p", p=r["proposal_id"])
        if r["proposal_id"]
        else []
    )
    can_work = can_anywhere(db, ctx.user_id, "payment.propose")
    return {
        "id": r["no"],
        "run_date": r["run_date"].isoformat(),
        "status": r["status"],
        "total": _money(r["total"]),
        "proposed_by": r["proposed_by"],
        "approved_by": r["approved_by"],
        "approval": proposal[0] if proposal else None,
        "has_file": bool(r["file_text"]),
        "payments": out,
        "can_file": can_work and r["status"] == "approved",
        "can_mark_paid": can_work and r["status"] == "file_generated",
    }


@router.post("/payment-runs")
def propose(
    payload: dict[str, Any], db: Db, ctx: Annotated[routing.TenantContext, Depends(require("payment.propose"))]
) -> dict[str, Any]:
    run_date = _date(payload.get("run_date") or business_today().isoformat(), "run_date")
    window = payload.get("window_days")
    advances = payload.get("include_advances", True) not in (False, "false", "0", 0)
    return _error(
        lambda: {
            "run": payment.propose_run(
                db,
                run_date,
                ctx.user_id,
                window_days=int(window) if window not in (None, "") else None,
                include_advances=advances,
            ),
            "status": "waiting for approval",
        }
    )


@router.post("/payment-runs/{no}/file")
def make_file(
    no: str, db: Db, ctx: Annotated[routing.TenantContext, Depends(require("payment.propose"))]
) -> dict[str, Any]:
    return _error(lambda: {"run": no, "status": "file_generated", "file": payment.generate_file(db, no, ctx.user_id)})


@router.get("/payment-runs/{no}/file")
def get_file(
    no: str, db: Db, ctx: Annotated[routing.TenantContext, Depends(require("payment.view"))]
) -> dict[str, Any]:
    rows = _rows(db, "select file_text, status from payment_run where no = :n", n=no)
    if not rows or not rows[0]["file_text"]:
        raise HTTPException(404, "No bank file has been made for this run")
    return {"run": no, "file": rows[0]["file_text"]}


@router.post("/payment-runs/statement")
def import_bank_statement(
    payload: dict[str, Any], db: Db, ctx: Annotated[routing.TenantContext, Depends(require("payment.propose"))]
) -> dict[str, Any]:
    """The bank's statement as CSV text. Payments whose reference and amount agree are settled (a run only when every
    payment in it is on the statement); every other line is listed for a person and one task is made for it."""
    csv_text = payload.get("csv")
    if not isinstance(csv_text, str) or not csv_text.strip():
        raise HTTPException(422, "send the statement as CSV text in 'csv'")
    if len(csv_text) > 2_000_000:
        raise HTTPException(422, "the statement is too large")
    try:
        return bank_statement.import_statement(db, csv_text, ctx.user_id, today=business_today(), tenant=ctx.slug)
    except InvoiceError as exc:
        raise HTTPException(422, str(exc)) from None


@router.post("/payment-runs/{no}/paid")
def mark_paid(
    no: str, payload: dict[str, Any], db: Db, ctx: Annotated[routing.TenantContext, Depends(require("payment.propose"))]
) -> dict[str, Any]:
    paid_on = _date(payload.get("paid_on") or business_today().isoformat(), "paid_on")
    if paid_on > business_today():
        raise HTTPException(422, "the bank cannot have paid on a date that has not come yet")
    utrs = payload.get("utrs") if isinstance(payload.get("utrs"), dict) else None
    return _error(
        lambda: {
            "run": no,
            "status": "paid",
            "closed_orders": payment.mark_paid(db, no, ctx.user_id, paid_on=paid_on, utrs=utrs, tenant=ctx.slug),
        }
    )
