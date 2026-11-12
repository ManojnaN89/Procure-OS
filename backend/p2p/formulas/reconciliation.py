"""Reconciliation arithmetic: what was received and not invoiced (and the reverse) on an order line, a supplier's
statement of account against our books, and which payment a bank line is likely to be. Plain rules on Decimal money."""

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from p2p.formulas.matching import normalize_invoice_no

ZERO = Decimal(0)
DOCUMENTS = ("invoice", "credit_note", "debit_note")


# ---- received not invoiced, invoiced not received -----------------------------------------------


@dataclass(frozen=True)
class Event:
    """A receipt (goods accepted or a service confirmed) or an invoice on an order line: its date, quantity and value."""

    on: date
    qty: Decimal
    value: Decimal


@dataclass(frozen=True)
class Position:
    received_qty: Decimal
    invoiced_qty: Decimal
    received_value: Decimal
    invoiced_value: Decimal
    not_invoiced: Decimal  # value received and not yet invoiced (GRNI)
    not_received: Decimal  # value invoiced beyond what was received
    since: date | None  # the oldest receipt (or invoice) of the open value
    age_days: int | None


def _oldest_open(events: Sequence[Event], covered: Decimal) -> date | None:
    """The first event, oldest first, that what the other side covers does not reach."""
    running = ZERO
    for e in sorted(events, key=lambda e: e.on):
        running += e.value
        if running > covered:
            return e.on
    return None


def position(receipts: Iterable[Event], invoices: Iterable[Event], as_of: date) -> Position:
    """An order line on a date: the value received against the value invoiced, each counting only what happened by then.

    The open value is aged from the oldest receipt (or invoice) it still includes, first in first out.
    """
    got = [e for e in receipts if e.on <= as_of]
    billed = [e for e in invoices if e.on <= as_of]
    received = sum((e.value for e in got), ZERO)
    invoiced = sum((e.value for e in billed), ZERO)
    gap = received - invoiced
    since = _oldest_open(got, invoiced) if gap > 0 else _oldest_open(billed, received) if gap < 0 else None
    return Position(
        sum((e.qty for e in got), ZERO), sum((e.qty for e in billed), ZERO), received, invoiced,
        max(gap, ZERO), max(-gap, ZERO), since, (as_of - since).days if since else None,
    )  # fmt: skip


# ---- a supplier's statement of account ----------------------------------------------------------


@dataclass(frozen=True)
class Theirs:
    """A line of the supplier's statement: an invoice, credit or debit note they raised, or a payment they received."""

    row: int
    on: date | None
    doc_type: str
    doc_no: str
    amount: Decimal
    reference: str = ""


@dataclass(frozen=True)
class Ours:
    """An item of ours for the supplier: a document (its number is the supplier's) or a payment (its number is ours)."""

    ref: str  # our number: PI-... or PAY-...
    doc_type: str
    doc_no: str  # the supplier's document number; for a payment, our payment number
    on: date
    amount: Decimal
    utr: str | None = None
    absent: str | None = None  # why it is not on our books for this statement: 'was cancelled', 'came on ...'


@dataclass(frozen=True)
class Result:
    row: int | None
    on: date | None
    doc_type: str
    doc_no: str
    their_amount: Decimal | None
    our_ref: str | None
    our_amount: Decimal | None
    outcome: str  # matched, amount_differs, only_theirs, only_ours
    reason: str

    @property
    def difference(self) -> Decimal | None:
        if self.their_amount is None or self.our_amount is None:
            return None
        return self.their_amount - self.our_amount


def _pair(line: Theirs, ours: Ours, how: str) -> Result:
    if line.amount == ours.amount:
        return Result(line.row, line.on, line.doc_type, line.doc_no, line.amount, ours.ref, ours.amount, "matched", how)
    return Result(
        line.row, line.on, line.doc_type, line.doc_no, line.amount, ours.ref, ours.amount, "amount_differs",
        f"{how}, but theirs is {line.amount:,.2f} and ours {ours.amount:,.2f} (difference {line.amount - ours.amount:,.2f})",
    )  # fmt: skip


def _theirs_only(line: Theirs, reason: str) -> Result:
    return Result(line.row, line.on, line.doc_type, line.doc_no, line.amount, None, None, "only_theirs", reason)


def _label(doc_type: str) -> str:
    return doc_type.replace("_", " ")


def match_statement(theirs: Sequence[Theirs], ours: Sequence[Ours], *, window_days: int = 3) -> list[Result]:
    """Every line of their statement matched, differing or theirs only, then every item of ours not on it.

    A document is found by its type and number (without case, spaces or punctuation). A payment is found by our payment
    number or the bank reference in its number or reference; failing that by the same amount within `window_days` of
    our payment date, when exactly one payment fits.
    """
    live = {(o.doc_type, normalize_invoice_no(o.doc_no)): o for o in ours if o.doc_type in DOCUMENTS and not o.absent}
    dead = {(o.doc_type, normalize_invoice_no(o.doc_no)): o for o in ours if o.doc_type in DOCUMENTS and o.absent}
    payments = [o for o in ours if o.doc_type == "payment" and not o.absent]
    used: set[str] = set()
    out: list[Result] = []
    for line in theirs:
        if line.doc_type in DOCUMENTS:
            key = (line.doc_type, normalize_invoice_no(line.doc_no))
            found = live.get(key)
            if found and found.ref in used:
                out.append(_theirs_only(line, f"{_label(line.doc_type)} {line.doc_no} is on their statement twice"))
            elif found:
                used.add(found.ref)
                out.append(_pair(line, found, f"our {found.ref} has the same number"))
            elif key in dead:
                out.append(_theirs_only(line, f"our {dead[key].ref} with this number {dead[key].absent}"))
            else:
                out.append(_theirs_only(line, f"no {_label(line.doc_type)} of ours has the number {line.doc_no}"))
            continue
        said = f"{line.doc_no} {line.reference}".upper()
        named = [
            p for p in payments if p.ref not in used and (p.ref.upper() in said or (p.utr and p.utr.upper() in said))
        ]
        if len(named) == 1:
            used.add(named[0].ref)
            out.append(_pair(line, named[0], f"our payment {named[0].ref} has this reference"))
            continue
        near = [
            p
            for p in payments
            if p.ref not in used and p.amount == line.amount and line.on and abs((p.on - line.on).days) <= window_days
        ]
        if not named and len(near) == 1:
            used.add(near[0].ref)
            out.append(_pair(line, near[0], f"our payment {near[0].ref} has the same amount within {window_days} days"))
        elif len(named) > 1 or len(near) > 1:
            fits = ", ".join(p.ref for p in (named or near)[:4])
            out.append(_theirs_only(line, f"more than one payment of ours fits ({fits}): match it by hand"))
        else:
            out.append(_theirs_only(line, "no payment of ours has this reference, or this amount near this date"))
    for o in ours:
        if o.absent or o.ref in used:
            continue
        what = f"our payment {o.ref}" if o.doc_type == "payment" else f"our {o.ref} ({_label(o.doc_type)} {o.doc_no})"
        out.append(
            Result(None, o.on, o.doc_type, o.doc_no, None, o.ref, o.amount, "only_ours",
                   f"{what} of {o.on:%d %b %Y} is not on their statement")
        )  # fmt: skip
    return out


# ---- the bank: which payment a line is likely to be ---------------------------------------------


@dataclass(frozen=True)
class Candidate:
    no: str
    reference: str | None
    amount: Decimal
    on: date  # the run date


def suggest(amount: Decimal, said: str, on: date | None, candidates: Sequence[Candidate], limit: int = 3) -> list[str]:
    """Payments a bank line could be: the same amount to the paisa, those it names first, then the nearest in date."""
    said = said.upper()

    def rank(c: Candidate) -> tuple[int, int, str]:
        named = c.no.upper() in said or bool(c.reference and c.reference.upper() in said)
        return (0 if named else 1, abs((c.on - on).days) if on else 0, c.no)

    return [c.no for c in sorted((c for c in candidates if c.amount == amount), key=rank)[:limit]]
