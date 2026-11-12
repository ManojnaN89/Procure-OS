"""The bank statement: a CSV from the bank marks payments as paid when the reference and the amount agree.

A debit line is matched to a payment of a run whose bank file was made, by the payment number (or the payment reference
sent in the file) appearing in the line's narration or bank reference, and by the amount to the paisa. A run is marked
paid only when every payment in it is matched, so a statement never settles half a run. Anything else goes to a person:
a line with no payment, an amount that differs, a date that has not come, a payment matched twice.
Importing the same statement again changes nothing: its lines show up as already paid.
"""

import csv
import hashlib
import io
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from p2p.formulas.money import format_inr
from p2p.invoices import payment as payments
from p2p.invoices.service import InvoiceError
from p2p.platform import audit, tasks
from p2p.platform.sod import user

D = Decimal
DATE_COLUMNS = ("date", "value date", "txn date", "transaction date", "paid on", "posting date")
UTR_COLUMNS = ("utr", "utr no", "utr number", "bank reference", "bank ref", "txn id", "transaction id")
TEXT_COLUMNS = ("reference", "narration", "description", "remarks", "particulars")
DEBIT_COLUMNS = ("debit", "debit amount", "withdrawal", "withdrawal amt", "amount")
MAX_LINES = 5000


@dataclass
class Line:
    row: int
    paid_on: date | None
    utr: str
    narration: str
    amount: D


@dataclass
class Outcome:
    matched: list[dict[str, Any]] = field(default_factory=list)  # lines tied to a payment
    runs_paid: list[str] = field(default_factory=list)
    already_paid: list[dict[str, Any]] = field(default_factory=list)
    for_a_person: list[dict[str, Any]] = field(default_factory=list)
    waiting: list[dict[str, Any]] = field(
        default_factory=list
    )  # matched, but the rest of their run is not on the statement
    skipped: int = 0  # credits and lines with no amount
    task_id: int | None = None


def _column(header: list[str], names: tuple[str, ...]) -> int | None:
    for name in names:
        if name in header:
            return header.index(name)
    return None


def _cell(row: list[str], i: int | None) -> str:
    return row[i].strip() if i is not None and i < len(row) else ""


def _date(value: str) -> date | None:
    value = value.strip()
    for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y", "%d-%b-%Y", "%d %b %Y"):
        try:
            return datetime.strptime(value, fmt).date()  # noqa: DTZ007 - a calendar date, no time
        except ValueError:
            continue
    return None


def _amount(value: str) -> D | None:
    cleaned = re.sub(r"[,\s₹]|INR|Rs\.?", "", value.strip(), flags=re.I)
    if not cleaned:
        return None
    try:
        out = D(cleaned)
    except InvalidOperation:
        return None
    return out if out.is_finite() else None


def parse(csv_text: str) -> tuple[list[Line], list[dict[str, Any]], int]:
    """The debit lines of a statement, the lines that could not be read (with why), and how many lines were skipped."""
    reader = csv.reader(io.StringIO(csv_text.lstrip("﻿")))
    rows = [r for r in reader if any(c.strip() for c in r)]
    if not rows:
        raise InvoiceError("the statement is empty")
    header = [c.strip().lower() for c in rows[0]]
    c_date, c_utr = _column(header, DATE_COLUMNS), _column(header, UTR_COLUMNS)
    c_text, c_debit = _column(header, TEXT_COLUMNS), _column(header, DEBIT_COLUMNS)
    if c_date is None or c_debit is None or (c_text is None and c_utr is None):
        raise InvoiceError(
            "the statement needs a date column, an amount (or debit) column and a reference (or narration) column"
        )
    if len(rows) - 1 > MAX_LINES:
        raise InvoiceError(f"a statement can have at most {MAX_LINES} lines")
    lines: list[Line] = []
    bad: list[dict[str, Any]] = []
    skipped = 0
    for n, r in enumerate(rows[1:], start=2):
        amount = _amount(_cell(r, c_debit))
        if amount is None or amount <= 0:
            skipped += 1  # a credit or an empty debit
            continue
        paid_on = _date(_cell(r, c_date))
        lines.append(Line(n, paid_on, _cell(r, c_utr), _cell(r, c_text), amount))
        if paid_on is None:
            bad.append({"row": n, "amount": str(amount), "why": f"the date '{_cell(r, c_date)}' cannot be read"})
    return lines, bad, skipped


def _payments(db: Session) -> list[dict[str, Any]]:
    return [
        dict(r)
        for r in db.execute(
            text(
                "select p.no, p.reference, p.amount, p.run_no, r.status as run_status, p.paid_on from payment p "
                "join payment_run r on r.no = p.run_no where p.amount > 0 and r.status in ('file_generated', 'paid') "
                "order by p.no"
            )
        ).mappings()
    ]


def _first_user_with_role(db: Session, *roles: str) -> int:
    for role in roles:
        found = db.execute(
            text("select user_id from user_profile where role = :r order by user_id limit 1"), {"r": role}
        ).scalar()
        if found:
            return int(found)
    return int(db.execute(text("select user_id from user_profile order by user_id limit 1")).scalar() or 0)


def import_statement(
    db: Session, csv_text: str, user_id: int, *, today: date, tenant: str | None = None
) -> dict[str, Any]:
    lines, unreadable, skipped = parse(csv_text)
    out = Outcome(skipped=skipped, for_a_person=list(unreadable))
    unreadable_rows = {b["row"] for b in unreadable}
    pool = _payments(db)
    taken: set[str] = set()
    by_run: dict[str, list[tuple[dict[str, Any], Line]]] = {}
    for line in lines:
        if line.row in unreadable_rows:
            continue
        shown = {
            "row": line.row,
            "date": line.paid_on.isoformat() if line.paid_on else None,
            "amount": str(line.amount),
            "reference": (line.narration or line.utr)[:80],
        }
        haystack = f"{line.narration} {line.utr}".upper()
        named = [
            p
            for p in pool
            if p["no"].upper() in haystack or (p["reference"] and str(p["reference"]).upper() in haystack)
        ]
        same = [p for p in named if D(p["amount"]) == line.amount]
        if not named:
            out.for_a_person.append({**shown, "why": "no payment has this reference"})
        elif not same:
            out.for_a_person.append(
                {
                    **shown,
                    "why": "the amount differs: payment "
                    + ", ".join(f"{p['no']} is {format_inr(p['amount'])}" for p in named[:3]),
                }
            )
        elif len({p["no"] for p in same}) > 1:
            out.for_a_person.append(
                {**shown, "why": "more than one payment fits: " + ", ".join(p["no"] for p in same[:4])}
            )
        else:
            p = same[0]
            if p["run_status"] == "paid":
                out.already_paid.append({**shown, "payment": p["no"]})
            elif p["no"] in taken:
                out.for_a_person.append({**shown, "why": f"payment {p['no']} is already matched by an earlier line"})
            elif line.paid_on is not None and line.paid_on > today:
                out.for_a_person.append({**shown, "why": "the bank date has not come yet"})
            else:
                taken.add(p["no"])
                by_run.setdefault(p["run_no"], []).append((p, line))
    for run_no, pairs in sorted(by_run.items()):
        needed = {p["no"] for p in pool if p["run_no"] == run_no}
        got = {p["no"] for p, _ in pairs}
        if got != needed:
            out.waiting.extend(
                {
                    "row": ln.row,
                    "payment": p["no"],
                    "run": run_no,
                    "why": f"the other {len(needed - got)} payment(s) of {run_no} are not on the statement",
                }
                for p, ln in pairs
            )
            continue
        paid_on = max(ln.paid_on for _, ln in pairs if ln.paid_on is not None)
        utrs = {p["no"]: ln.utr for p, ln in pairs if ln.utr}
        payments.mark_paid(db, run_no, user_id, paid_on=paid_on, utrs=utrs, tenant=tenant)
        out.runs_paid.append(run_no)
        out.matched.extend({"row": ln.row, "payment": p["no"], "run": run_no, "utr": ln.utr or None} for p, ln in pairs)
    open_items = len(out.for_a_person) + len(out.waiting)
    if open_items and tenant:
        out.task_id = tasks.create_task(
            db,
            tenant,
            kind="bank_statement_lines",
            title=f"Bank statement: {open_items} line(s) need a person",
            assignee=_first_user_with_role(db, "Finance", "AP"),
            sla_hours=24,
            payload={"for_a_person": out.for_a_person[:50], "waiting": out.waiting[:50]},
            actor=user(user_id),
        )
    audit.record(
        db, user(user_id), "payment_run.statement", "payment_run", hashlib.sha256(csv_text.encode()).hexdigest()[:12],
        after={"lines": len(lines), "matched": len(out.matched), "runs_paid": out.runs_paid,
               "already_paid": len(out.already_paid), "for_a_person": len(out.for_a_person), "waiting": len(out.waiting)},
    )  # fmt: skip
    db.commit()
    return {
        "lines": len(lines),
        "skipped": out.skipped,
        "matched": out.matched,
        "runs_paid": out.runs_paid,
        "already_paid": out.already_paid,
        "for_a_person": out.for_a_person,
        "waiting": out.waiting,
        "task_id": out.task_id,
    }
