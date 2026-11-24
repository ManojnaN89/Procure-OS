"""The accounting export (2.6, R8): a generic CSV of journal vouchers for the client's ledger.

Nothing is written back and nothing is decided here. A month's export is frozen when the month closes: a final is
kept with its hash and every document in it recorded once, so a ledger import can drop a repeat and a late document
goes into the next open month with its own event month.
"""

import hashlib
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from p2p.approvals import budget_periods
from p2p.formulas import journal
from p2p.platform import audit, settings
from p2p.platform.sod import user

PAISE = Decimal("0.01")


class ExportError(Exception):
    def __init__(self, message: str, status: int = 409) -> None:
        super().__init__(message)
        self.status = status


def _ledgers(db: Session, on: date) -> dict[str, journal.Ledger]:
    out: dict[str, journal.Ledger] = {}
    for r in db.execute(
        text(
            "select distinct on (purpose) purpose, gl_code, ledger_name from ledger_map "
            "where effective_from <= :d and (effective_to is null or effective_to >= :d) "
            "order by purpose, version desc"
        ),
        {"d": on},
    ).all():
        out[str(r[0])] = journal.Ledger(str(r[1] or r[0]), str(r[2]))
    for r in db.execute(text("select code, name from gl_account")).all():
        out.setdefault(str(r[0]), journal.Ledger(str(r[0]), str(r[1])))
    return out


def _msme(db: Session, supplier: str) -> bool:
    return db.execute(text("select msme_category from supplier where code = :c"), {"c": supplier}).scalar() in (
        "micro",
        "small",
    )


def _period_of(on: date) -> str:
    return f"{on.year}-{on.month:02d}"


def _next_period(period: str) -> str:
    y, m = int(period[:4]), int(period[5:])
    return f"{y + 1}-01" if m == 12 else f"{y}-{m + 1:02d}"


def _previous_period(period: str) -> str:
    y, m = int(period[:4]), int(period[5:])
    return f"{y - 1}-12" if m == 1 else f"{y}-{m - 1:02d}"


def _in_period(event: date, period: str, locked: set[str]) -> bool:
    """The document belongs to this export when its own month is this one, or an earlier closed month whose later
    months up to this one are all closed (R7.4: late documents keep their event month)."""
    own = _period_of(event)
    if own == period:
        return True
    if own > period:
        return False
    y, m = int(own[:4]), int(own[5:])
    while f"{y}-{m:02d}" < period:
        if f"{y}-{m:02d}" not in locked:
            return False
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return True


def _already(db: Session, kind: str, no: str) -> bool:
    return (
        db.execute(
            text("select 1 from accounting_export_doc where doc_type = :t and doc_no = :n"), {"t": kind, "n": no}
        ).first()
        is not None
    )


def _exported_docs(db: Session) -> set[tuple[str, str]]:
    return {(str(r[0]), str(r[1])) for r in db.execute(text("select doc_type, doc_no from accounting_export_doc"))}


def _gl(value: Any) -> str | None:
    return str(value) if value not in (None, "") else None


def _month_end(period: str) -> date:
    y, m = int(period[:4]), int(period[5:])
    return (date(y + 1, 1, 1) if m == 12 else date(y, m + 1, 1)) - timedelta(days=1)


def _purchase_vouchers(db: Session, period: str, maps: dict[str, journal.Ledger]) -> list[journal.Voucher]:
    locked = set(budget_periods.locks(db))
    done = _exported_docs(db)
    out: list[journal.Voucher] = []
    rows = db.execute(
        text(
            "select i.no, i.type, i.supplier_code, coalesce(s.trade_name, s.legal_name) as supplier_name, "
            "coalesce(i.supplier_gstin, s.gstin) as gstin, coalesce(i.booked_at::date, i.received_on) as booked_on, "
            "i.taxable, i.cgst, i.sgst, i.igst, i.round_off, coalesce(i.tds_amount, 0) as tds, i.reverse_charge, "
            "coalesce(i.discount_taken, 0) as discount from invoice i join supplier s on s.code = i.supplier_code "
            "where i.status in ('approved', 'part_paid', 'paid') and i.type in ('invoice', 'credit_note', 'debit_note') "
            "and coalesce(i.booked_at::date, i.received_on) <= :end order by i.no"
        ),
        {"end": _month_end(period)},
    ).mappings()
    for r in rows:
        booked = r["booked_on"]
        key = ("purchase" if r["type"] == "invoice" else r["type"], r["no"])
        if not booked or not _in_period(booked, period, locked) or key in done:
            continue
        lines = db.execute(
            text(
                "select coalesce(rl.gl_code, pl.gl_code, it.default_gl::text) as gl, l.taxable, l.line_no, "
                "coalesce(pl.description, l.description, l.invoice_no) as what "
                "from invoice_line l left join po_line pl on pl.po_no = l.po_no and pl.line_no = l.po_line_no "
                "left join request_line rl on rl.request_no = pl.request_no and rl.line_no = pl.request_line_no "
                "left join item it on it.code = pl.item_code where l.invoice_no = :n order by l.line_no"
            ),
            {"n": r["no"]},
        ).all()
        gl_lines: list[tuple[str, Decimal, str | None]] = []
        for gl, amount, line_no, what in lines:
            if _gl(gl) is None:
                raise ExportError(f"invoice {r['no']} line {line_no} has no GL account to post {amount} to")
            gl_lines.append((str(gl), Decimal(amount), str(what)))
        freight = sum(
            (
                Decimal(x[0])
                for x in db.execute(
                    text("select amount from invoice_charge where invoice_no = :n and type = 'freight'"), {"n": r["no"]}
                )
            ),
            Decimal(0),
        )
        party = {"msme": _msme(db, r["supplier_code"])}
        if r["type"] == "invoice":
            out.append(
                journal.purchase(
                    no=str(r["no"]), on=booked, event_period=_period_of(booked), supplier=str(r["supplier_code"]),
                    supplier_name=str(r["supplier_name"]), gstin=r["gstin"], gl_lines=gl_lines, freight=freight,
                    tax={"cgst": Decimal(r["cgst"]), "sgst": Decimal(r["sgst"]), "igst": Decimal(r["igst"])},
                    round_off=Decimal(r["round_off"] or 0), tds=Decimal(r["tds"]),
                    msme=party["msme"], reverse_charge=bool(r["reverse_charge"]), maps=maps,
                )
            )  # fmt: skip
        else:
            out.append(
                journal.note(
                    kind=str(r["type"]), no=str(r["no"]), on=booked, event_period=_period_of(booked),
                    supplier=str(r["supplier_code"]), supplier_name=str(r["supplier_name"]), gstin=r["gstin"],
                    gl_lines=gl_lines,
                    tax={"cgst": Decimal(r["cgst"]), "sgst": Decimal(r["sgst"]), "igst": Decimal(r["igst"])},
                    msme=party["msme"], maps=maps,
                )
            )  # fmt: skip
    return out


def _payment_vouchers(db: Session, period: str, maps: dict[str, journal.Ledger]) -> list[journal.Voucher]:
    locked = set(budget_periods.locks(db))
    done = _exported_docs(db)
    out: list[journal.Voucher] = []
    for r in db.execute(
        text(
            "select p.no, p.type, p.supplier_code, coalesce(s.trade_name, s.legal_name) as supplier_name, "
            "coalesce(s.gstin, '') as gstin, p.amount, coalesce(p.msme_interest, 0) as interest, p.paid_on, "
            "coalesce(r.company_bank_account, 'CBA-01') as bank from payment p "
            "join supplier s on s.code = p.supplier_code left join payment_run r on r.no = p.run_no "
            "where p.paid_on is not null and p.amount > 0 order by p.no"
        )
    ).mappings():
        on = r["paid_on"]
        key = ("advance" if r["type"] == "advance" else "payment", r["no"])
        if not on or not _in_period(on, period, locked) or key in done:
            continue
        if r["type"] == "advance":
            out.append(
                journal.advance(
                    no=str(r["no"]), on=on, event_period=_period_of(on), supplier=str(r["supplier_code"]),
                    supplier_name=str(r["supplier_name"]), gstin=r["gstin"], amount=Decimal(r["amount"]),
                    bank_account=str(r["bank"]), maps=maps,
                )
            )  # fmt: skip
        else:
            out.append(
                journal.payment(
                    no=str(r["no"]), on=on, event_period=_period_of(on), supplier=str(r["supplier_code"]),
                    supplier_name=str(r["supplier_name"]), gstin=r["gstin"], amount=Decimal(r["amount"]),
                    interest=Decimal(r["interest"]), msme=_msme(db, str(r["supplier_code"])),
                    bank_account=str(r["bank"]), maps=maps,
                )
            )  # fmt: skip
    for r in db.execute(
        text(
            "select a.advance_payment_no, a.amount, coalesce(i.booked_at::date, i.received_on) as booked_on, "
            "p.supplier_code, coalesce(s.trade_name, s.legal_name) as supplier_name, coalesce(s.gstin, '') as gstin "
            "from advance_adjustment a join invoice i on i.no = a.invoice_no "
            "join payment p on p.no = a.advance_payment_no join supplier s on s.code = p.supplier_code "
            "order by a.id"
        )
    ).mappings():
        on = r["booked_on"]
        key = ("advance_adjustment", f"{r['advance_payment_no']}:{on}")
        if not on or not _in_period(on, period, locked) or key in done:
            continue
        out.append(
            journal.advance_adjustment(
                no=str(r["advance_payment_no"]), on=on, event_period=_period_of(on),
                supplier=str(r["supplier_code"]), supplier_name=str(r["supplier_name"]), gstin=r["gstin"],
                amount=Decimal(r["amount"]), msme=_msme(db, str(r["supplier_code"])), maps=maps,
            )
        )  # fmt: skip
    return out


def _accruals(
    db: Session, period: str, grni: list[dict[str, Any]], maps: dict[str, journal.Ledger]
) -> list[journal.Voucher]:
    """The month's accrual, and the reversal of the month before's (dated the 1st of this month)."""
    out: list[journal.Voucher] = []
    made, _ = journal.accrual(
        period=period,
        on=_month_end(period),
        event_period=period,
        gl_lines=[(str(r["gl_code"]), Decimal(r["value"]), f"{r['po_no']}/{r['line_no']}") for r in grni],
        maps=maps,
    )
    if grni and not _already(db, made.type, made.no):
        out.append(made)
    previous = _previous_period(period)
    prior = db.execute(text("select grni from period_close where period = :p"), {"p": previous}).scalar()
    if prior:
        _, back = journal.accrual(
            period=previous,
            on=date(int(period[:4]), int(period[5:]), 1),
            event_period=period,
            gl_lines=[(str(r["gl_code"]), Decimal(r["value"]), f"{r['po_no']}/{r['line_no']}") for r in prior],
            maps=maps,
        )
        if not _already(db, back.type, back.no):
            out.append(back)
    return out


def build(db: Session, period: str, *, grni: list[dict[str, Any]] | None = None) -> list[journal.Voucher]:
    """Every voucher of the month, each balanced."""
    end = _month_end(period)
    maps = _ledgers(db, end)
    vouchers = _purchase_vouchers(db, period, maps) + _payment_vouchers(db, period, maps)
    if grni is not None:
        vouchers += _accruals(db, period, grni, maps)
    for v in vouchers:
        if not v.balanced:
            raise ExportError(f"voucher {v.no} does not balance: debit {v.debit}, credit {v.credit}")
    return vouchers


def draft(db: Session, period: str, user_id: int) -> dict[str, Any]:
    """What the month holds now; nothing is kept but the audit row (R8.3)."""
    vouchers = build(db, period)
    content, totals = journal.render(vouchers)
    audit.record(
        db, user(user_id), "accounts.export_draft", "accounting_export", period,
        after={"vouchers": len(vouchers), "format": settings.value(db, "accounts_export_format", "generic_csv")},
    )  # fmt: skip
    db.commit()
    return {
        "period": period,
        "kind": "draft",
        "vouchers": len(vouchers),
        "debit": str(sum((v.debit for v in vouchers), Decimal(0))),
        "credit": str(sum((v.credit for v in vouchers), Decimal(0))),
        "totals": {k: str(v) for k, v in sorted(totals.items())},
        "content": content,
    }


def final(db: Session, period: str, user_id: int, *, grni: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """The month's final export: kept with its hash, every document recorded once (R8.3)."""
    if db.execute(text("select 1 from accounting_export where period = :p"), {"p": period}).first():
        raise ExportError(f"{period} already has a final export")
    vouchers = build(db, period, grni=grni or [])
    content, totals = journal.render(vouchers)
    digest = hashlib.sha256(content.encode()).hexdigest()
    debit = sum((v.debit for v in vouchers), Decimal(0))
    credit = sum((v.credit for v in vouchers), Decimal(0))
    export_id = db.execute(
        text(
            "insert into accounting_export (period, format, vouchers, debit, credit, content, file_hash, made_by) "
            "values (:p, :f, :n, :d, :c, :body, :h, :by) returning id"
        ),
        {
            "p": period, "f": settings.value(db, "accounts_export_format", "generic_csv"), "n": len(vouchers),
            "d": debit, "c": credit, "body": content, "h": digest, "by": str(user_id),
        },
    ).scalar_one()  # fmt: skip
    for v in vouchers:
        db.execute(
            text(
                "insert into accounting_export_doc (export_id, doc_type, doc_no, event_date) values (:e, :t, :n, :d) "
                "on conflict (doc_type, doc_no) do nothing"
            ),
            {"e": export_id, "t": v.type, "n": v.no, "d": v.on},
        )
    audit.record(
        db, user(user_id), "accounts.export_final", "accounting_export", str(export_id),
        after={"period": period, "vouchers": len(vouchers), "debit": str(debit), "credit": str(credit),
               "hash": digest},
    )  # fmt: skip
    return {
        "id": int(export_id),
        "period": period,
        "kind": "final",
        "vouchers": len(vouchers),
        "debit": str(debit),
        "credit": str(credit),
        "hash": digest,
        "content": content,
        "totals": {k: str(v) for k, v in sorted(totals.items())},
    }


def file(db: Session, period: str, kind: str, user_id: int) -> dict[str, Any]:
    """A final export's own bytes again, or a fresh draft."""
    if kind == "final":
        row = (
            db.execute(
                text(
                    "select id, content, file_hash, vouchers, debit, credit, made_at from accounting_export where period = :p"
                ),
                {"p": period},
            )
            .mappings()
            .first()
        )
        if row is None:
            raise ExportError(f"{period} has no final export", 404)
        return {**dict(row), "period": period, "kind": "final"}
    if kind != "draft":
        raise ExportError("kind is draft or final", 422)
    return draft(db, period, user_id)


def summary(db: Session, period: str) -> dict[str, Any] | None:
    row = (
        db.execute(
            text(
                "select id, period, format, vouchers, debit, credit, file_hash as hash, made_by, made_at "
                "from accounting_export where period = :p"
            ),
            {"p": period},
        )
        .mappings()
        .first()
    )
    return dict(row) if row else None


def made_at_words(value: datetime) -> str:
    return value.strftime("%d %b %Y %H:%M")
