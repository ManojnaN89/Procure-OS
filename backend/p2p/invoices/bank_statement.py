"""The bank statement: a CSV from the bank marks payments as paid when the reference and the amount agree.

A debit line is matched to a payment of a run whose bank file was made, by the payment number (or the payment reference
sent in the file) appearing in the line's narration or bank reference, and by the amount to the paisa. A run is marked
paid only when every payment in it is matched, so a statement never settles half a run. Anything else goes to a person:
a line with no payment, an amount that differs, a date that has not come, a payment matched twice.
Importing the same statement again changes nothing: its lines show up as already paid.

Every debit line is kept (bank_statement_line), once however often it is imported. A payment matched on one statement
counts towards its run on the next, so a run can be settled across statements. A line no payment fits stays open: it is
listed with the payments of the same amount it could be (named ones first), and a person confirms which it is.
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

from p2p.formulas import reconciliation as rules
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


def read_date(value: str) -> date | None:
    """A date as banks and suppliers write it (2026-11-05, 05-11-2026, 05/11/2026, 05-Nov-2026, 05 Nov 2026)."""
    value = value.strip()
    for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y", "%d-%b-%Y", "%d %b %Y", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(value, fmt).date()  # noqa: DTZ007 - a calendar date, no time
        except ValueError:
            continue
    return None


def read_amount(value: str) -> D | None:
    """An amount with or without commas, spaces, a rupee sign, INR or Rs."""
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
        amount = read_amount(_cell(r, c_debit))
        if amount is None or amount <= 0:
            skipped += 1  # a credit or an empty debit
            continue
        paid_on = read_date(_cell(r, c_date))
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


def _key(line: Line, seen: dict[str, int]) -> str:
    """The same bank line, in this statement or an overlapping one, has the same key (identical lines are counted)."""
    base = f"{line.paid_on}|{line.utr.upper()}|{line.narration.upper()}|{line.amount}"
    seen[base] = seen.get(base, 0) + 1
    return hashlib.sha256(f"{base}|{seen[base]}".encode()).hexdigest()


def _keep(
    db: Session, key: str, line: Line, status: str, user_id: int, *, payment: str | None = None, why: str | None = None
) -> None:
    """Keep the line; a line kept before changes only while nobody has settled it."""
    db.execute(
        text(
            "insert into bank_statement_line (line_key, row_no, value_date, utr, narration, amount, status, payment_no, "
            "why, imported_by) values (:k, :r, :d, :u, :n, :a, :s, :p, :w, :by) on conflict (line_key) do update set "
            "status = excluded.status, payment_no = excluded.payment_no, why = excluded.why, row_no = excluded.row_no "
            "where bank_statement_line.status in ('open', 'waiting')"
        ),
        {
            "k": key, "r": line.row, "d": line.paid_on, "u": line.utr or None, "n": line.narration[:500] or None,
            "a": line.amount, "s": status, "p": payment, "w": why, "by": str(user_id),
        },
    )  # fmt: skip


def _linked(db: Session) -> dict[str, str]:
    """Payments a kept bank line already stands for, and that line's key."""
    return {
        str(r[0]): str(r[1])
        for r in db.execute(
            text(
                "select payment_no, line_key from bank_statement_line where payment_no is not null "
                "and status in ('waiting', 'confirmed', 'matched')"
            )
        )
    }


def _settle(db: Session, run_no: str, user_id: int, tenant: str | None) -> int:
    """Mark the run paid when every payment in it is on a bank line (from any statement). Returns how many are missing."""
    needed = {
        str(r[0]) for r in db.execute(text("select no from payment where run_no = :r and amount > 0"), {"r": run_no})
    }
    have = {
        str(r[0]): (r[1], r[2])
        for r in db.execute(
            text(
                "select payment_no, value_date, utr from bank_statement_line where payment_no = any(:p) "
                "and status in ('waiting', 'confirmed')"
            ),
            {"p": list(needed)},
        )
    }
    missing = len(needed - set(have))
    if missing or not needed:
        return missing
    db.execute(
        text(
            "update bank_statement_line set status = 'matched', why = null "
            "where payment_no = any(:p) and status = 'waiting'"
        ),
        {"p": list(needed)},
    )
    payments.mark_paid(
        db, run_no, user_id, paid_on=max(d for d, _ in have.values()), utrs={p: u for p, (_, u) in have.items() if u},
        tenant=tenant,
    )  # fmt: skip
    return 0


def import_statement(
    db: Session, csv_text: str, user_id: int, *, today: date, tenant: str | None = None
) -> dict[str, Any]:
    """Match the statement's debit lines to payments, settle the runs that are complete, and keep every line: the ones
    no payment fits stay open for a person (open_lines, confirm)."""
    lines, unreadable, skipped = parse(csv_text)
    out = Outcome(skipped=skipped, for_a_person=list(unreadable))
    why_unreadable = {b["row"]: b["why"] for b in unreadable}
    pool = _payments(db)
    linked = _linked(db)
    taken: set[str] = set()
    by_run: dict[str, list[tuple[str, Line, str]]] = {}
    seen: dict[str, int] = {}
    for line in lines:
        key = _key(line, seen)
        if line.row in why_unreadable:
            _keep(db, key, line, "open", user_id, why=why_unreadable[line.row])
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
        why: str | None = None
        if not named:
            why = "no payment has this reference"
        elif not same:
            why = "the amount differs: payment " + ", ".join(
                f"{p['no']} is {format_inr(p['amount'])}" for p in named[:3]
            )
        elif len({p["no"] for p in same}) > 1:
            why = "more than one payment fits: " + ", ".join(p["no"] for p in same[:4])
        else:
            p = same[0]
            if p["run_status"] == "paid":
                out.already_paid.append({**shown, "payment": p["no"]})
                _keep(db, key, line, "matched", user_id, payment=p["no"])
            elif p["no"] in taken or linked.get(p["no"], key) != key:
                why = f"payment {p['no']} is already matched by an earlier line"
            elif line.paid_on is not None and line.paid_on > today:
                why = "the bank date has not come yet"
            else:
                taken.add(p["no"])
                _keep(db, key, line, "waiting", user_id, payment=p["no"])
                by_run.setdefault(p["run_no"], []).append((p["no"], line, key))
        if why:
            out.for_a_person.append({**shown, "why": why})
            _keep(db, key, line, "open", user_id, why=why)
    for run_no, pairs in sorted(by_run.items()):
        missing = _settle(db, run_no, user_id, tenant)
        if missing:
            why = f"the other {missing} payment(s) of {run_no} are not on the statement"
            out.waiting.extend({"row": ln.row, "payment": p, "run": run_no, "why": why} for p, ln, _ in pairs)
            db.execute(
                text("update bank_statement_line set why = :w where line_key = any(:k) and status = 'waiting'"),
                {"w": why, "k": [k for _, _, k in pairs]},
            )
            continue
        out.runs_paid.append(run_no)
        out.matched.extend({"row": ln.row, "payment": p, "run": run_no, "utr": ln.utr or None} for p, ln, _ in pairs)
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


# ---- lines no payment fitted: a person matches them --------------------------------------------


def _unsettled(db: Session) -> list[rules.Candidate]:
    """Payments the bank may have paid that no bank line stands for yet: those of runs whose bank file was made."""
    return [
        rules.Candidate(r[0], r[1], D(r[2]), r[3])
        for r in db.execute(
            text(
                "select p.no, p.reference, p.amount, r.run_date from payment p join payment_run r on r.no = p.run_no "
                "where r.status = 'file_generated' and p.amount > 0 and not exists (select 1 from bank_statement_line b "
                "where b.payment_no = p.no and b.status in ('waiting', 'confirmed', 'matched')) order by p.no"
            )
        )
    ]


def open_lines(db: Session) -> list[dict[str, Any]]:
    """Bank lines nobody has matched yet, each with the payments it could be (same amount; named ones first)."""
    candidates = _unsettled(db)
    out = []
    for r in db.execute(
        text(
            "select id, row_no, value_date, utr, narration, amount, why, imported_at from bank_statement_line "
            "where status = 'open' order by value_date nulls last, id"
        )
    ).mappings():
        said = f"{r['narration'] or ''} {r['utr'] or ''}"
        out.append({**dict(r), "suggestions": rules.suggest(D(r["amount"]), said, r["value_date"], candidates)})
    return out


def confirm(
    db: Session, line_id: int, payment_no: str, user_id: int, *, today: date, tenant: str | None = None
) -> dict[str, Any]:
    """A person says an open bank line is a payment. The amounts must agree to the paisa; the person who approved the
    run cannot confirm that it was paid. When every payment of the run is then on the bank, the run is marked paid."""
    line = (
        db.execute(text("select * from bank_statement_line where id = :i for update"), {"i": line_id})
        .mappings()
        .first()
    )
    if line is None:
        raise InvoiceError(f"bank line {line_id} does not exist")
    if line["status"] != "open":
        raise InvoiceError(f"bank line {line_id} is already {line['status']}")
    if line["value_date"] is None or line["value_date"] > today:
        raise InvoiceError("the line's date cannot be read or has not come yet")
    pay = (
        db.execute(
            text(
                "select p.no, p.amount, p.run_no, r.approved_by from payment p join payment_run r on r.no = p.run_no "
                "where p.no = :n"
            ),
            {"n": payment_no},
        )
        .mappings()
        .first()
    )
    if pay is None:
        raise InvoiceError(f"payment {payment_no} does not exist")
    if payment_no not in {c.no for c in _unsettled(db)}:
        raise InvoiceError(f"payment {payment_no} is not waiting for the bank (paid, matched already, or no bank file)")
    if D(pay["amount"]) != D(line["amount"]):
        raise InvoiceError(f"the amounts differ: the line is {line['amount']}, payment {payment_no} is {pay['amount']}")
    if pay["approved_by"] == str(user_id):
        raise InvoiceError("segregation of duties: the person who approved the run cannot confirm its bank match")
    db.execute(
        text(
            "update bank_statement_line set status = 'confirmed', payment_no = :p, confirmed_by = :u, "
            "confirmed_at = now() where id = :i"
        ),
        {"p": payment_no, "u": str(user_id), "i": line_id},
    )
    audit.record(
        db, user(user_id), "bank_statement.confirm", "bank_statement_line", str(line_id),
        before={"status": "open", "why": line["why"]},
        after={"status": "confirmed", "payment": payment_no, "amount": line["amount"]},
    )  # fmt: skip
    missing = _settle(db, pay["run_no"], user_id, tenant)
    db.commit()
    return {"line": line_id, "payment": payment_no, "run": pay["run_no"], "run_paid": missing == 0}
