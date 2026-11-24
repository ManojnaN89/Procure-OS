"""GSTR-2B against our books (9b.1, D-19, OP-8 to OP-11).

Every amount is an exact Decimal. A document is keyed by the supplier's GSTIN, its type, the normalised number and the
financial year of its date; an amendment is read with its new values. The comparison writes nothing to an invoice, a
block, a due date or a run: D-19's "no holding".
"""

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from p2p.formulas.fy import financial_year
from p2p.formulas.matching import normalize_invoice_no
from p2p.formulas.money import format_inr

FIELDS = ("taxable", "igst", "cgst", "sgst", "cess")
HEADS = ("igst", "cgst", "sgst", "cess")
OUTCOMES = ("matched", "differs", "gstin_differs", "only_2b", "only_books", "duplicate")


@dataclass(frozen=True)
class Doc:
    gstin: str
    doc_type: str  # invoice, credit_note or debit_note
    doc_no: str
    on: date
    taxable: Decimal
    igst: Decimal = Decimal(0)
    cgst: Decimal = Decimal(0)
    sgst: Decimal = Decimal(0)
    cess: Decimal = Decimal(0)
    itc_available: bool = True
    itc_reason: str | None = None
    our_invoice_no: str | None = None
    reverse_charge: bool = False

    @property
    def key(self) -> tuple[str, str, str, str]:
        return (self.gstin.upper(), self.doc_type, normalize_invoice_no(self.doc_no), financial_year(self.on))

    @property
    def pan(self) -> str:
        """The PAN inside a GSTIN, characters 3 to 12."""
        return self.gstin.upper()[2:12]


@dataclass(frozen=True)
class Compared:
    doc: Doc
    outcome: str
    our_invoice_no: str | None = None
    differences: tuple[str, ...] = ()
    reason: str | None = None


@dataclass
class _Books:
    docs: list[Doc] = field(default_factory=list)
    used: set[int] = field(default_factory=set)


def _difference(name: str, ours: Decimal, theirs: Decimal) -> str:
    more = "more" if theirs > ours else "less"
    return f"{name}: ours {format_inr(ours)}, 2B {format_inr(theirs)} ({format_inr(abs(theirs - ours))} {more})"


def _compare_fields(ours: Doc, theirs: Doc, tolerance: Decimal) -> tuple[str, ...]:
    out = [
        _difference(name, getattr(ours, name), getattr(theirs, name))
        for name in FIELDS
        if abs(getattr(theirs, name) - getattr(ours, name)) > tolerance
    ]
    if not out and ours.on != theirs.on:
        out.append(f"date: ours {ours.on:%d %b %Y}, 2B {theirs.on:%d %b %Y}")
    return tuple(out)


def _month_index(on: date) -> int:
    return on.year * 12 + on.month


def months_since(on: date, period: str) -> int:
    """How many months from a document's month to the period compared (0 when it is the same month)."""
    return max(_month_index(date(int(period[:4]), int(period[5:]), 1)) - _month_index(on), 0)


def compare(ours: Sequence[Doc], theirs: Sequence[Doc], *, tolerance: Decimal, period: str) -> tuple[Compared, ...]:
    """Every 2B line's outcome (first that applies), and our invoices that no 2B holds (R4.4)."""
    books = _Books(list(ours))
    by_key: dict[tuple[str, str, str, str], list[int]] = {}
    for n, doc in enumerate(books.docs):
        by_key.setdefault(doc.key, []).append(n)
    out: list[Compared] = []
    seen: set[tuple[str, str, str, str]] = set()
    for doc in theirs:
        if doc.key in seen:
            out.append(Compared(doc, "duplicate", reason="the same document appears twice in the file"))
            continue
        seen.add(doc.key)
        reason = None if doc.itc_available else f"credit not available: {doc.itc_reason or 'no reason given'}"
        indexes = by_key.get(doc.key, [])
        if indexes:
            ours_doc = books.docs[indexes[0]]
            books.used.update(indexes)
            differences = _compare_fields(ours_doc, doc, tolerance)
            out.append(
                Compared(
                    doc,
                    "differs" if differences else "matched",
                    ours_doc.our_invoice_no,
                    differences,
                    reason,
                )
            )
            continue
        same_pan = [
            books.docs[n]
            for n in range(len(books.docs))
            if n not in books.used
            and books.docs[n].pan == doc.pan
            and books.docs[n].doc_type == doc.doc_type
            and normalize_invoice_no(books.docs[n].doc_no) == normalize_invoice_no(doc.doc_no)
            and financial_year(books.docs[n].on) == financial_year(doc.on)
        ]
        if same_pan:
            books.used.add(books.docs.index(same_pan[0]))
            out.append(
                Compared(
                    doc,
                    "gstin_differs",
                    same_pan[0].our_invoice_no,
                    reason=f"ours is on {same_pan[0].gstin}, the 2B on {doc.gstin}",
                )
            )
            continue
        out.append(Compared(doc, "only_2b", reason=reason))
    for n, doc in enumerate(books.docs):
        if n in books.used:
            continue
        out.append(
            Compared(
                doc,
                "only_books",
                doc.our_invoice_no,
                reason=f"not in any 2B for {months_since(doc.on, period)} months",
            )
        )
    return tuple(out)


def counts(results: Sequence[Compared]) -> dict[str, int]:
    return {outcome: sum(r.outcome == outcome for r in results) for outcome in OUTCOMES}
