"""The bank statement: the bank's own lines settle payments, one payment at a time (2.1, Q18, OP-5).

A debit line is matched to a payment from a bank file by the payment number (or the reference sent in the file)
appearing in the line's narration or bank reference, and by the amount to the paisa. It settles that payment whatever
the rest of its run does; the run is `paid` when none of its payments waits and at least one settled. A credit line is
kept: one that names a payment of ours with the same amount is listed for a person as "may be the bank returning
<payment>" and never applied by itself. Anything else goes to a person: a line with no payment, an amount that differs,
a date that has not come, a payment already matched. Importing the same statement again changes nothing.
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
from p2p.formulas.words import count
from p2p.invoices import settlement
from p2p.invoices.service import InvoiceError
from p2p.platform import audit, tasks, users
from p2p.platform.sod import user

D = Decimal
DATE_COLUMNS = ("date", "value date", "txn date", "transaction date", "paid on", "posting date")
UTR_COLUMNS = ("utr", "utr no", "utr number", "bank reference", "bank ref", "txn id", "transaction id")
TEXT_COLUMNS = ("reference", "narration", "description", "remarks", "particulars")
DEBIT_COLUMNS = ("debit", "debit amount", "withdrawal", "withdrawal amt")
CREDIT_COLUMNS = ("credit", "credit amount", "deposit", "deposit amount")
AMOUNT_COLUMNS = ("amount",)
MAX_LINES = 5000


@dataclass
class Line:
    row: int
    paid_on: date | None
    utr: str
    narration: str
    amount: D
    direction: str = "debit"


@dataclass
class Outcome:
    settled: list[dict[str, Any]] = field(default_factory=list)  # payments a line settled
    runs_paid: list[str] = field(default_factory=list)
    already_paid: list[dict[str, Any]] = field(default_factory=list)
    for_a_person: list[dict[str, Any]] = field(default_factory=list)
    returns: list[dict[str, Any]] = field(default_factory=list)  # credit lines that may be a return
    skipped: int = 0
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
    """The statement's lines (debits and credits), the lines that could not be read, and how many were skipped."""
    reader = csv.reader(io.StringIO(csv_text.lstrip("\ufeff")))
    rows = [r for r in reader if any(c.strip() for c in r)]
    if not rows:
        raise InvoiceError("the statement is empty")
    header = [c.strip().lower() for c in rows[0]]
    c_date, c_utr = _column(header, DATE_COLUMNS), _column(header, UTR_COLUMNS)
    c_text = _column(header, TEXT_COLUMNS)
    c_debit, c_credit = _column(header, DEBIT_COLUMNS), _column(header, CREDIT_COLUMNS)
    c_amount = _column(header, AMOUNT_COLUMNS)
    if c_date is None or (c_text is None and c_utr is None):
        raise InvoiceError("the statement needs a date column and a reference (or narration) column")
    if c_debit is None and c_credit is None and c_amount is None:
        raise InvoiceError("the statement needs an amount (or debit and credit) column")
    if len(rows) - 1 > MAX_LINES:
        raise InvoiceError(f"a statement can have at most {MAX_LINES} lines")
    lines: list[Line] = []
    bad: list[dict[str, Any]] = []
    skipped = 0
    for n, r in enumerate(rows[1:], start=2):
        debit = read_amount(_cell(r, c_debit)) if c_debit is not None else None
        credit = read_amount(_cell(r, c_credit)) if c_credit is not None else None
        if c_debit is None and c_credit is None:
            amount = read_amount(_cell(r, c_amount))
            debit, credit = amount, None
        if debit and debit > 0 and (credit is None or credit <= 0):
            amount, direction = debit, "debit"
        elif credit and credit > 0 and (debit is None or debit <= 0):
            amount, direction = credit, "credit"
        else:
            skipped += 1
            continue
        paid_on = read_date(_cell(r, c_date))
        lines.append(Line(n, paid_on, _cell(r, c_utr), _cell(r, c_text), amount, direction))
        if paid_on is None:
            bad.append({"row": n, "amount": str(amount), "why": f"the date '{_cell(r, c_date)}' cannot be read"})
    return lines, bad, skipped


def _payments(db: Session) -> list[dict[str, Any]]:
    return [
        dict(r)
        for r in db.execute(
            text(
                "select p.no, p.reference, p.amount, p.run_no, p.status, p.utr, p.paid_on, r.run_date "
                "from payment p join payment_run r on r.no = p.run_no "
                "where p.status in ('in_file', 'recorded', 'settled') order by p.no"
            )
        ).mappings()
    ]


def _key(line: Line, seen: dict[str, int]) -> str:
    """The same bank line, in this statement or an overlapping one, has the same key (identical lines are counted)."""
    base = f"{line.direction}|{line.paid_on}|{line.utr.upper()}|{line.narration.upper()}|{line.amount}"
    seen[base] = seen.get(base, 0) + 1
    return hashlib.sha256(f"{base}|{seen[base]}".encode()).hexdigest()


def _keep(
    db: Session,
    key: str,
    line: Line,
    status: str,
    user_id: int,
    *,
    payment: str | None = None,
    why: str | None = None,
) -> int:
    """Keep the line; a line kept before changes only while nobody has settled it. Returns its id."""
    db.execute(
        text(
            "insert into bank_statement_line (line_key, row_no, value_date, utr, narration, amount, status, payment_no, "
            "why, imported_by, direction) values (:k, :r, :d, :u, :n, :a, :s, :p, :w, :by, :dir) "
            "on conflict (line_key) do update set status = excluded.status, payment_no = excluded.payment_no, "
            "why = excluded.why, row_no = excluded.row_no where bank_statement_line.status in ('open', 'waiting')"
        ),
        {
            "k": key, "r": line.row, "d": line.paid_on, "u": line.utr or None, "n": line.narration[:500] or None,
            "a": line.amount, "s": status, "p": payment, "w": why, "by": str(user_id), "dir": line.direction,
        },
    )  # fmt: skip
    return int(db.execute(text("select id from bank_statement_line where line_key = :k"), {"k": key}).scalar_one())


def _linked(db: Session) -> dict[str, str]:
    """Payments a kept debit line already stands for, and that line's key."""
    return {
        str(r[0]): str(r[1])
        for r in db.execute(
            text(
                "select payment_no, line_key from bank_statement_line where payment_no is not null "
                "and direction = 'debit' and status in ('waiting', 'confirmed', 'matched')"
            )
        )
    }


def import_statement(
    db: Session, csv_text: str, user_id: int, *, today: date, tenant: str | None = None
) -> dict[str, Any]:
    """Match the statement's lines to payments, settle each payment on its own line, and keep every line."""
    lines, unreadable, skipped = parse(csv_text)
    out = Outcome(skipped=skipped, for_a_person=list(unreadable))
    why_unreadable = {b["row"]: b["why"] for b in unreadable}
    pool = _payments(db)
    linked = _linked(db)
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
            "direction": line.direction,
        }
        haystack = f"{line.narration} {line.utr}".upper()
        named = [
            p
            for p in pool
            if p["no"].upper() in haystack or (p["reference"] and str(p["reference"]).upper() in haystack)
        ]
        same = [p for p in named if D(p["amount"]) == line.amount]
        if line.direction == "credit":
            possible = same[0] if same else (named[0] if named else None)
            if possible is not None:
                credit_why = f"may be the bank returning {possible['no']}"
                out.returns.append({**shown, "payment": possible["no"], "why": credit_why})
            else:
                credit_why = "a credit that names no payment of ours"
                out.for_a_person.append({**shown, "why": credit_why})
            _keep(db, key, line, "open", user_id, why=credit_why)
            continue
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
            if p["status"] == "settled":
                out.already_paid.append({**shown, "payment": p["no"]})
                _keep(db, key, line, "matched", user_id, payment=p["no"])
            elif p["no"] in linked and linked[p["no"]] != key:
                why = f"payment {p['no']} is already matched by an earlier line"
            elif line.paid_on is not None and line.paid_on > today:
                why = "the bank date has not come yet"
            elif p["utr"] and line.utr and str(p["utr"]).upper() != line.utr.upper():
                why = "UTR differs from the one recorded"
            else:
                line_id = _keep(db, key, line, "waiting", user_id, payment=p["no"])
                try:
                    done = settlement.settle_by_line(db, p["no"], line_id, user_id, tenant=tenant, how="line")
                except InvoiceError as exc:
                    why = str(exc)
                else:
                    out.settled.append({**shown, "payment": p["no"], "run": p["run_no"], "utr": line.utr or None})
                    if done:
                        out.runs_paid.append(str(p["run_no"]))
                    continue
        if why:
            out.for_a_person.append({**shown, "why": why})
            _keep(db, key, line, "open", user_id, why=why)
    out.runs_paid = sorted(set(out.runs_paid))
    open_items = len(out.for_a_person)
    if open_items and tenant:
        out.task_id = tasks.create_task(
            db,
            tenant,
            kind="bank_statement_lines",
            title=f"Bank statement: {count(open_items, 'line')} {'needs' if open_items == 1 else 'need'} a person",
            assignee=users.first_with_role(db, "Finance", "AP"),
            sla_hours=24,
            payload={"for_a_person": out.for_a_person[:50], "returns": out.returns[:50]},
            actor=user(user_id),
        )
    audit.record(
        db, user(user_id), "payment_run.statement", "payment_run", hashlib.sha256(csv_text.encode()).hexdigest()[:12],
        after={"lines": len(lines), "settled": len(out.settled), "runs_paid": out.runs_paid,
               "already_paid": len(out.already_paid), "for_a_person": len(out.for_a_person),
               "returns": len(out.returns)},
    )  # fmt: skip
    db.commit()
    return {
        "lines": len(lines),
        "skipped": out.skipped,
        "settled": out.settled,
        "matched": out.settled,  # kept for callers written before block 12
        "runs_paid": out.runs_paid,
        "already_paid": out.already_paid,
        "for_a_person": out.for_a_person,
        "returns": out.returns,
        "waiting": [],
        "task_id": out.task_id,
    }


# ---- lines no payment fitted: a person matches them --------------------------------------------


def _unsettled(db: Session) -> list[rules.Candidate]:
    """Payments the bank may have paid that no bank line stands for yet."""
    return [
        rules.Candidate(r[0], r[1], D(r[2]), r[3])
        for r in db.execute(
            text(
                "select p.no, p.reference, p.amount, r.run_date from payment p join payment_run r on r.no = p.run_no "
                "where p.status in ('in_file', 'recorded') and p.amount > 0 "
                "and not exists (select 1 from bank_statement_line b where b.payment_no = p.no "
                "and b.direction = 'debit' and b.status in ('waiting', 'confirmed', 'matched')) order by p.no"
            )
        )
    ]


def open_lines(db: Session) -> list[dict[str, Any]]:
    """Bank lines nobody has matched yet, each with the payments it could be (same amount; named ones first)."""
    candidates = _unsettled(db)
    out = []
    for r in db.execute(
        text(
            "select id, row_no, value_date, utr, narration, amount, why, imported_at, direction "
            "from bank_statement_line where status = 'open' order by value_date nulls last, id"
        )
    ).mappings():
        said = f"{r['narration'] or ''} {r['utr'] or ''}"
        out.append({**dict(r), "suggestions": rules.suggest(D(r["amount"]), said, r["value_date"], candidates)})
    return out


def confirm(
    db: Session, line_id: int, payment_no: str, user_id: int, *, today: date, tenant: str | None = None
) -> dict[str, Any]:
    """A person says an open debit line is a payment. The amounts must agree to the paisa; the person who approved the
    run cannot confirm it. The payment is settled; its run is paid when nothing else waits."""
    line = (
        db.execute(text("select * from bank_statement_line where id = :i for update"), {"i": line_id})
        .mappings()
        .first()
    )
    if line is None:
        raise InvoiceError(f"bank line {line_id} does not exist")
    if line["status"] != "open":
        raise InvoiceError(f"bank line {line_id} is already {line['status']}")
    if line["direction"] != "debit":
        raise InvoiceError("only a debit line is a payment; a credit may be a return")
    if line["value_date"] is None or line["value_date"] > today:
        raise InvoiceError("the line's date cannot be read or has not come yet")
    pay = db.execute(text("select amount from payment where no = :n"), {"n": payment_no}).first()
    if pay is None:
        raise InvoiceError(f"payment {payment_no} does not exist")
    if payment_no not in {c.no for c in _unsettled(db)}:
        raise InvoiceError(f"payment {payment_no} is not waiting for the bank (paid, matched already, or no bank file)")
    if D(pay[0]) != D(line["amount"]):
        raise InvoiceError(f"the amounts differ: the line is {line['amount']}, payment {payment_no} is {pay[0]}")
    audit.record(
        db, user(user_id), "bank_statement.confirm", "bank_statement_line", str(line_id),
        before={"status": "open", "why": line["why"]},
        after={"status": "confirmed", "payment": payment_no, "amount": line["amount"]},
    )  # fmt: skip
    settlement.settle_by_line(db, payment_no, line_id, user_id, tenant=tenant, how="confirmed line")
    done = db.execute(
        text("select r.status from payment p join payment_run r on r.no = p.run_no where p.no = :n"),
        {"n": payment_no},
    ).scalar()
    return {"line": line_id, "payment": payment_no, "run_paid": done == "paid"}
