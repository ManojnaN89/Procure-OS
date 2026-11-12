"""A supplier's statement of account, imported and matched against our books for that supplier.

The file is CSV or Excel in our template (`TEMPLATE`): date, type (invoice, credit note, debit note or payment), the
document number (their invoice or note number; for a payment our payment number or the bank reference), the amount and a
reference. Each line is matched against our invoices, credit and debit notes (by type and number) and our payments (by
reference, else by amount near the date) in the statement's period: matched, the amount differs, only on their side, or
only on ours, each with its reason (formulas/reconciliation.py). The import and its result are kept; the same file for
the same supplier is imported once (a second import shows the first result).
"""

import hashlib
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from p2p.formulas import reconciliation as rules
from p2p.formulas.matching import normalize_invoice_no
from p2p.invoices.bank_statement import read_amount, read_date
from p2p.invoices.service import InvoiceError
from p2p.platform import audit, parsing
from p2p.platform.sod import user

D = Decimal
TEMPLATE = "date,type,document_no,amount,reference\n"
MAX_LINES = 5000
COLUMNS = {
    "date": ("date", "doc date", "document date", "invoice_date", "invoice date", "txn date"),
    "type": ("type", "document type", "doc type"),
    "document_no": ("document_no", "document no", "doc no", "number", "supplier_invoice_no", "invoice no", "bill no"),
    "amount": ("amount", "value", "debit/credit"),
    "reference": ("reference", "narration", "remarks", "utr"),
}
TYPES = {
    "invoice": "invoice", "bill": "invoice", "tax invoice": "invoice",
    "credit note": "credit_note", "credit_note": "credit_note", "credit": "credit_note", "cn": "credit_note",
    "debit note": "debit_note", "debit_note": "debit_note", "dn": "debit_note",
    "payment": "payment", "receipt": "payment", "payment received": "payment",
}  # fmt: skip
OUTCOMES = ("matched", "amount_differs", "only_theirs", "only_ours")
WINDOW_DAYS = 3  # a payment without a reference is matched on its amount within this many days of ours


def _rows(data: bytes, filename: str) -> list[list[str]]:
    suffix = Path(filename).suffix.lower()
    if suffix not in (".csv", ".xlsx"):
        raise InvoiceError("a statement must be a .csv or .xlsx file")
    doc = parsing.parse_document(data, filename)
    if not doc.tables:
        raise InvoiceError("the statement is empty")
    return doc.tables[0].rows


def parse(data: bytes, filename: str) -> tuple[list[rules.Theirs], list[dict[str, Any]]]:
    """Their lines, and the rows that could not be read (with the row and why)."""
    rows = _rows(data, filename)
    header = [c.strip().lower().lstrip("﻿") for c in rows[0]]
    col = {k: next((header.index(n) for n in names if n in header), None) for k, names in COLUMNS.items()}
    if col["date"] is None or col["amount"] is None or col["document_no"] is None:
        raise InvoiceError(f"the statement needs the columns of the template: {TEMPLATE.strip()}")
    if len(rows) - 1 > MAX_LINES:
        raise InvoiceError(f"a statement can have at most {MAX_LINES} lines")

    def cell(r: list[str], k: str) -> str:
        i = col[k]
        return r[i].strip() if i is not None and i < len(r) else ""

    lines: list[rules.Theirs] = []
    bad: list[dict[str, Any]] = []
    for n, r in enumerate(rows[1:], start=2):
        on, amount = read_date(cell(r, "date")), read_amount(cell(r, "amount"))
        kind = TYPES.get(cell(r, "type").lower().replace("-", " ") or "invoice")
        number = cell(r, "document_no")
        why = (
            "the date cannot be read" if on is None
            else "the amount cannot be read" if amount is None or amount == 0
            else f"the type '{cell(r, 'type')}' is not invoice, credit note, debit note or payment" if kind is None
            else "the document number is missing" if not number
            else None
        )  # fmt: skip
        if why or on is None or amount is None or kind is None:
            bad.append({"row": n, "why": why})
            continue
        lines.append(rules.Theirs(n, on, kind, number, abs(amount), cell(r, "reference")))
    return lines, bad


def _ours(db: Session, supplier: str, start: date, end: date, theirs: list[rules.Theirs]) -> list[rules.Ours]:
    """Our books for the supplier as they stood at the end of the period: documents dated in it and received by then,
    payments made in it, and any document of ours their lines name (one cancelled, or received later, is not on them)."""
    keys = [normalize_invoice_no(t.doc_no) for t in theirs if t.doc_type != "payment"]

    def absent(status: str, received_on: date) -> str | None:
        if status == "cancelled":
            return "was cancelled"
        return f"came on {received_on:%d %b %Y}, after the statement" if received_on > end else None

    out = [
        rules.Ours(r[0], r[1], r[2], r[3], D(r[4]), None, absent(r[5], r[6]))
        for r in db.execute(
            text(
                "select no, type, supplier_invoice_no, invoice_date, total, status, received_on from invoice "
                "where supplier_code = :s and ((invoice_date between :a and :b and status <> 'cancelled' "
                "and received_on <= :b) or dedupe_key = any(:k)) order by no"
            ),
            {"s": supplier, "a": start, "b": end, "k": keys},
        )
    ]
    out += [
        rules.Ours(r[0], "payment", r[0], r[1], D(r[2]), r[3])
        for r in db.execute(
            text(
                "select no, paid_on, amount, utr from payment where supplier_code = :s and paid_on between :a and :b "
                "and amount > 0 order by no"
            ),
            {"s": supplier, "a": start, "b": end},
        )
    ]
    return out


def _summary(db: Session, statement_id: int) -> dict[str, Any]:
    head = dict(db.execute(text("select * from vendor_statement where id = :i"), {"i": statement_id}).mappings().one())
    lines = [
        dict(r)
        for r in db.execute(
            text(
                "select row_no, doc_date, doc_type, doc_no, their_amount, our_ref, our_amount, difference, outcome, reason "
                "from vendor_statement_line where statement_id = :i order by row_no nulls last, id"
            ),
            {"i": statement_id},
        ).mappings()
    ]
    return {**head, "results": lines}


def import_statement(
    db: Session,
    supplier: str,
    data: bytes,
    filename: str,
    user_id: int,
    *,
    period_from: date | None = None,
    period_to: date | None = None,
) -> dict[str, Any]:
    """Read, match, keep and audit a supplier's statement; the period defaults to the dates on it."""
    if db.execute(text("select 1 from supplier where code = :s"), {"s": supplier}).first() is None:
        raise InvoiceError(f"supplier {supplier} does not exist")
    digest = hashlib.sha256(data).hexdigest()
    earlier = db.execute(
        text("select id from vendor_statement where supplier_code = :s and file_hash = :h"),
        {"s": supplier, "h": digest},
    ).scalar()
    if earlier:
        return {**_summary(db, int(earlier)), "already_imported": True, "unreadable": []}
    theirs, bad = parse(data, filename)
    if not theirs:
        raise InvoiceError("no line of the statement could be read")
    start = period_from or min(t.on for t in theirs if t.on)
    end = period_to or max(t.on for t in theirs if t.on)
    if end < start:
        raise InvoiceError("the period ends before it starts")
    results = rules.match_statement(theirs, _ours(db, supplier, start, end, theirs), window_days=WINDOW_DAYS)
    counts = {o: sum(r.outcome == o for r in results) for o in OUTCOMES}
    statement_id = db.execute(
        text(
            "insert into vendor_statement (supplier_code, period_from, period_to, file_name, file_hash, lines, matched, "
            "amount_differs, only_theirs, only_ours, imported_by) values (:s, :a, :b, :f, :h, :n, :m, :d, :t, :o, :u) "
            "returning id"
        ),
        {
            "s": supplier, "a": start, "b": end, "f": filename[:200], "h": digest, "n": len(theirs),
            "m": counts["matched"], "d": counts["amount_differs"], "t": counts["only_theirs"], "o": counts["only_ours"],
            "u": str(user_id),
        },
    ).scalar_one()  # fmt: skip
    for r in results:
        db.execute(
            text(
                "insert into vendor_statement_line (statement_id, row_no, doc_date, doc_type, doc_no, their_amount, our_ref, "
                "our_amount, difference, outcome, reason) values (:i, :r, :d, :t, :n, :ta, :o, :oa, :x, :c, :w)"
            ),
            {
                "i": statement_id, "r": r.row, "d": r.on, "t": r.doc_type, "n": r.doc_no, "ta": r.their_amount,
                "o": r.our_ref, "oa": r.our_amount, "x": r.difference, "c": r.outcome, "w": r.reason,
            },
        )  # fmt: skip
    audit.record(
        db, user(user_id), "vendor_statement.import", "vendor_statement", str(statement_id),
        after={"supplier": supplier, "period": f"{start}..{end}", "lines": len(theirs), "unreadable": len(bad), **counts},
    )  # fmt: skip
    db.commit()
    return {**_summary(db, int(statement_id)), "already_imported": False, "unreadable": bad}


def statements(db: Session, supplier: str | None = None) -> list[dict[str, Any]]:
    return [
        dict(r)
        for r in db.execute(
            text(
                "select v.*, coalesce(s.trade_name, s.legal_name) as supplier from vendor_statement v "
                "join supplier s on s.code = v.supplier_code where cast(:s as text) is null or v.supplier_code = :s "
                "order by v.imported_at desc, v.id desc limit 100"
            ),
            {"s": supplier},
        ).mappings()
    ]


def statement(db: Session, statement_id: int) -> dict[str, Any] | None:
    found = db.execute(text("select 1 from vendor_statement where id = :i"), {"i": statement_id}).first()
    return _summary(db, statement_id) if found else None
