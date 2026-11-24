"""Month-end and year-end close (R7, D-09): the budget lock with the month-end work added.

One action: Finance closes an ended month, in order. The close records every price measure to the month's end, freezes
what was received and not invoiced, keeps a final accounting export, and locks the budget month. Nothing else blocks
it: the other findings are warnings with a count and a link (OP-18).
"""

from datetime import date, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from p2p.approvals import budget_periods
from p2p.formulas import budget as budget_rules
from p2p.formulas import payment as payment_rules
from p2p.formulas.calendar import business_today
from p2p.invoices import accounting_export, price_measures, reconciliation
from p2p.platform import audit, settings
from p2p.platform.sod import user

D = Decimal


class PeriodCloseError(Exception):
    def __init__(self, message: str, status: int = 422) -> None:
        super().__init__(message)
        self.status = status


def month_end(period: str) -> date:
    y, m = int(period[:4]), int(period[5:])
    return (date(y + 1, 1, 1) if m == 12 else date(y, m + 1, 1)) - timedelta(days=1)


def _first_next(period: str) -> date:
    return month_end(period) + timedelta(days=1)


def _closed(db: Session, period: str) -> dict[str, Any] | None:
    row = db.execute(text("select * from period_close where period = :p"), {"p": period}).mappings().first()
    return dict(row) if row else None


def _warnings(db: Session, period: str, end: date) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    missing = [
        str(r[0])
        for r in db.execute(
            text(
                "select g.gstin from gst_registration g where not exists "
                "(select 1 from gstr2b_import i where i.gstin = g.gstin and i.period = :p and i.current) order by g.gstin"
            ),
            {"p": period},
        )
    ]
    if missing:
        out.append({"code": "gstr2b", "count": len(missing), "words": "GSTR-2B not compared for " + ", ".join(missing),
                    "link": "#/close"})  # fmt: skip
    bank = db.execute(
        text("select count(*), coalesce(sum(amount), 0) from bank_statement_line where status = 'open' "
             "and value_date <= :d"), {"d": end})  # fmt: skip
    bank_count, bank_total = bank.one()
    if bank_count:
        out.append({"code": "bank_open", "count": int(bank_count),
                    "words": f"{int(bank_count)} bank lines open", "amount": str(bank_total),
                    "link": "#/reconciliation"})  # fmt: skip
    late = db.execute(
        text("select count(*) from invoice where type = 'invoice' and status in ('received', 'blocked') "
             "and received_on between :a and :b"), {"a": date(int(period[:4]), int(period[5:]), 1), "b": end}).scalar_one()  # fmt: skip
    if late:
        out.append({"code": "not_booked", "count": int(late),
                    "words": f"{int(late)} invoices received in the month not booked; their vouchers go to the next month",
                    "link": "#/invoices"})  # fmt: skip
    waiting = db.execute(text("select count(*) from po_short_close where status = 'waiting'")).scalar_one()
    if waiting:
        out.append({"code": "short_close", "count": int(waiting), "words": f"{int(waiting)} short-closes waiting",
                    "link": "#/workspace"})  # fmt: skip
    msme = reconciliation.msme_due(db, end, 0)
    if msme:
        principal = sum((D(r["total"]) for r in msme), D(0))
        out.append({"code": "msme", "count": len(msme), "amount": str(principal),
                    "words": f"{len(msme)} small-supplier invoices past their deadline at the month end",
                    "link": "#/reconciliation"})  # fmt: skip
    open_orders = db.execute(text("select count(*) from po where status = 'received'")).scalar_one()
    if open_orders:
        out.append({"code": "money_open", "count": int(open_orders),
                    "words": f"{int(open_orders)} orders have all their supply and money still open",
                    "link": "#/purchase-orders"})  # fmt: skip
    return out


def preview(db: Session, period: str, today: date | None = None) -> dict[str, Any]:
    """What the month holds, and what blocks or warns its close; nothing is written (R7.2)."""
    if not budget_rules.valid_period(period):
        raise PeriodCloseError(f"'{period}' is not a month like 2026-11")
    today = today or business_today()
    refused = budget_rules.lockable(period, today, set(budget_periods.locks(db)))
    end = month_end(period)
    closed = _closed(db, period)
    lock = budget_periods.locks(db).get(period)
    blocking: list[str] = []
    if closed:
        blocking.append(f"{period} is already closed")
    elif lock:
        blocking.append(
            f"{period} was locked before month-end close existed"
            if refused is None
            else f"{period} cannot be closed: {refused}"
        )
    elif refused == "not ended":
        blocking.append(f"{period} has not ended")
    elif refused:
        blocking.append(f"{period} cannot be closed: {refused}")
    return {
        "period": period,
        "label": budget_periods.month_words(period),
        "status": "closed" if closed else "locked" if lock else "future" if today <= end else "open",
        "blocking": blocking,
        "warnings": [] if blocking else _warnings(db, period, end),
        "grni_total": str(closed["grni_total"]) if closed else None,
        "closed_by": closed["closed_by"] if closed else None,
        "closed_at": closed["closed_at"].isoformat() if closed else None,
    }


def _grni(db: Session, end: date) -> tuple[list[dict[str, Any]], Decimal]:
    """Received and not invoiced at the month end, with each line's GL account (R7.3)."""
    found = reconciliation.received_vs_invoiced(db, end)
    # NEEDS block 9: `received_vs_invoiced` already applies block 9's reversal (up to `reversed_on`) and counted-return
    # filters (H9-4); the owner connects them in that reader, so this snapshot freezes the same figures the screen shows.
    fallback = db.execute(
        text("select gl_code from ledger_map where purpose = 'grni' order by version desc limit 1")
    ).scalar()
    lines: list[dict[str, Any]] = []
    for line in found["lines"]:
        if not line["not_invoiced"]:
            continue
        gl = db.execute(
            text(
                "select coalesce(rl.gl_code, pl.gl_code, it.default_gl::text) from po_line pl "
                "left join request_line rl on rl.request_no = pl.request_no and rl.line_no = pl.request_line_no "
                "left join item it on it.code = pl.item_code where pl.po_no = :p and pl.line_no = :l"
            ),
            {"p": line["po_no"], "l": line["line_no"]},
        ).scalar()
        lines.append(
            {
                "po_no": line["po_no"], "line_no": line["line_no"], "item_code": line["item_code"],
                "supplier": line["supplier"], "value": str(line["not_invoiced"]),
                "gl_code": str(gl or fallback or ""),
            }
        )  # fmt: skip
    total = sum((D(line["value"]) for line in lines), D(0)).quantize(D("0.01"))
    return lines, total


def close(db: Session, period: str, user_id: int, reason: str, today: date | None = None) -> dict[str, Any]:
    """Close the month: measures, frozen GRNI, final export, the lock row and the checklist (R7.3)."""
    today = today or business_today()
    if not budget_rules.valid_period(period):
        raise PeriodCloseError(f"'{period}' is not a month like 2026-11")
    why = (reason or "").strip()
    if not why:
        raise PeriodCloseError("a reason is needed to close a month")
    held = set(budget_periods.locks(db))
    refused = budget_rules.lockable(period, today, held)
    if refused == "not ended":
        raise PeriodCloseError(f"{period} has not ended")
    if refused:
        raise PeriodCloseError(f"{period} cannot be closed: {refused}", 409)
    if _closed(db, period):
        raise PeriodCloseError(f"{period} is already closed", 409)
    held_rows = budget_periods.locks(db)
    if period in held_rows:
        raise PeriodCloseError(f"{period} was locked before month-end close existed", 409)
    end = month_end(period)
    warnings = _warnings(db, period, end)
    # R7.3: the close is one transaction. The measures, the frozen snapshot, the final export, the period_close row
    # and the lock commit together; a failure in any of them leaves the month open.
    price_measures.record(db, end, user(user_id), commit=False)
    price_measures.market_gap(db, end, user(user_id), commit=False)
    grni, grni_total = _grni(db, end)
    made = accounting_export.final(db, period, user_id, grni=grni)
    who = budget_periods._who(db, user_id)  # noqa: SLF001 - the lock's writer is the same person
    db.execute(
        text("insert into budget_period_lock (period, locked_by, reason) values (:p, :b, :r)"),
        {"p": period, "b": who, "r": why},
    )
    checklist = {"warnings": warnings, "grni_total": str(grni_total), "vouchers": made["vouchers"]}
    if period.endswith("-03"):  # the March snapshot keeps the year-end MSME figures (R7.5)
        checklist["msme_year_end"] = msme_year_end(db, f"{int(period[:4]) - 1}-{int(period[:4]) % 100:02d}", end)
    closed_at = db.execute(
        text(
            "insert into period_close (period, closed_by, reason, checklist, grni, grni_total, export_id) "
            "values (:p, :b, :r, cast(:c as jsonb), cast(:g as jsonb), :t, :e) returning closed_at"
        ),
        {"p": period, "b": who, "r": why, "c": _json(checklist), "g": _json(grni), "t": grni_total,
         "e": made["id"]},
    ).scalar_one()  # fmt: skip
    audit.record(db, user(user_id), "budget.period_lock", "budget_period", period, after={"locked_by": who},
                 reason=why)  # fmt: skip
    audit.record(
        db, user(user_id), "period.close", "period_close", period,
        after={"grni_total": str(grni_total), "vouchers": made["vouchers"], "debit": made["debit"],
               "credit": made["credit"], "warnings": len(warnings)},
        reason=why,
    )  # fmt: skip
    db.commit()
    return {
        "period": period,
        "status": "locked",
        "locked_by": who,
        "locked_at": closed_at.isoformat(),
        "closed_at": closed_at.isoformat(),
        "reason": why,
        "grni_total": str(grni_total),
        "export": {"id": made["id"], "vouchers": made["vouchers"], "debit": made["debit"],
                   "credit": made["credit"]},
        "warnings": warnings,
    }  # fmt: skip


def _json(value: Any) -> str:
    import json  # noqa: PLC0415 - a small use, kept local

    return json.dumps(value, default=str)


def grni(db: Session, period: str) -> dict[str, Any] | None:
    row = db.execute(text("select grni, grni_total, closed_at, export_id from period_close where period = :p"),
                     {"p": period}).mappings().first()  # fmt: skip
    if row is None:
        return None
    return {"period": period, "lines": row["grni"], "total": str(row["grni_total"]),
            "closed_at": row["closed_at"].isoformat(), "export_id": row["export_id"]}  # fmt: skip


def msme_year_end(db: Session, fy: str, as_of: date | None = None) -> dict[str, Any]:
    """At 31 March: each micro or small supplier's principal unpaid, how much is past the deadline, and the interest
    due and paid in the year (R3.4, OP-26)."""
    if not budget_rules.valid_fy(fy):
        raise PeriodCloseError(f"'{fy}' is not a financial year like 2026-27")
    on = as_of or date(int(fy[:4]) + 1, 3, 31)
    limits = payment_rules.MsmeLimits(
        max_days=int(settings.number(db, "msme_max_days", "45")),
        no_agreement_days=int(settings.number(db, "msme_no_agreement_days", "15")),
    )
    rates = [
        (r[0], D(r[1]))
        for r in db.execute(text("select effective_from, rate_pct from ref_rbi_bank_rate order by effective_from"))
    ]
    suppliers: dict[str, dict[str, Any]] = {}
    for r in db.execute(
        text(
            "select i.no, i.total, i.received_on, i.supplier_code, coalesce(s.trade_name, s.legal_name) as supplier, "
            "s.msme_category, coalesce(s.msme_agreement, false) as agreement, s.agreed_payment_days from invoice i "
            "join supplier s on s.code = i.supplier_code where i.type = 'invoice' "
            "and i.status <> 'cancelled' and i.received_on <= :d and (i.paid_on is null or i.paid_on > :d) "
            "and s.msme_category = any(:c) order by i.supplier_code, i.no"
        ),
        {"d": on, "c": list(payment_rules.MSME_CATEGORIES)},
    ).mappings():
        accepted = date.fromisoformat(str(r["received_on"]))
        deadline = payment_rules.msme_deadline(accepted, bool(r["agreement"]), r["agreed_payment_days"], limits)
        late = payment_rules.days_late(deadline, on)
        interest = payment_rules.msme_interest(D(r["total"]), deadline, on, rates) if late > 0 else D(0)
        entry = suppliers.setdefault(
            str(r["supplier_code"]),
            {"supplier_code": r["supplier_code"], "supplier": r["supplier"],
             "msme_category": r["msme_category"], "principal": D(0), "past_deadline": D(0),
             "interest_due": D(0), "invoices": 0},
        )  # fmt: skip
        entry["principal"] += D(r["total"])
        entry["invoices"] += 1
        if late > 0:
            entry["past_deadline"] += D(r["total"])
            entry["interest_due"] += interest
    paid_interest = db.execute(
        text(
            "select coalesce(sum(p.msme_interest), 0) from payment p join supplier s on s.code = p.supplier_code "
            "where p.paid_on between :a and :b and s.msme_category = any(:c)"
        ),
        {"a": date(int(fy[:4]), 4, 1), "b": on, "c": list(payment_rules.MSME_CATEGORIES)},
    ).scalar_one()
    rows = [
        {**v, "principal": str(v["principal"]), "past_deadline": str(v["past_deadline"]),
         "interest_due": str(v["interest_due"])}
        for v in sorted(suppliers.values(), key=lambda x: str(x["supplier_code"]))
    ]  # fmt: skip
    return {
        "fy": fy, "as_of": on,
        "suppliers": rows,
        "total_principal": str(sum((D(r["principal"]) for r in rows), D(0))),
        "total_past_deadline": str(sum((D(r["past_deadline"]) for r in rows), D(0))),
        "total_interest_due": str(sum((D(r["interest_due"]) for r in rows), D(0))),
        "interest_paid": str(D(paid_interest)),
        "note": "section 43B(h) amount is the principal past its deadline; words to be reviewed by the tax adviser",
    }  # fmt: skip


def banner(db: Session, today: date | None = None) -> dict[str, Any]:
    """From 1 March, how many small-supplier invoices will be past their deadline at 31 March."""
    today = today or business_today()
    if today.month != 3:
        return {"show": False, "count": 0}
    end = date(today.year, 3, 31)
    due = reconciliation.msme_due(db, end, 0)
    return {"show": bool(due), "count": len(due), "as_of": end.isoformat(),
            "words": f"Year end 31 March: {len(due)} invoices of small suppliers will be past their deadline unless paid by then"
            if due else ""}  # fmt: skip
