"""The three-way match of a supplier invoice against the order and the goods received.

Plain rules on numbers and flags. Money is Decimal throughout. Per line, the invoice price may not be above the order
price by more than the tolerance (a percentage of the price, or a rupee amount on the line, whichever lets it through),
and the quantity may not be more than has been accepted at goods receipt and not yet invoiced (plus the quantity
tolerance). Within tolerance an invoice is paid; outside it, it is blocked until a person decides.
"""

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from decimal import Decimal

MATCHED = "matched"
WITHIN = "within_tolerance"
BLOCKED = "blocked"

# the reason an invoice is blocked, and what kind of block it puts on payment
PRICE = "price_variance"
QTY = "qty_over_received"
IRN = "irn_missing"
GSTIN = "gstin_mismatch"
TAX = "tax_not_allowed"
ARITH = "arithmetic_mismatch"
BLOCK_KIND = {PRICE: "match", QTY: "match", ARITH: "match", IRN: "tax", GSTIN: "tax", TAX: "tax"}

TWO_PLACES = Decimal("0.01")


@dataclass(frozen=True)
class Tolerance:
    price_pct: Decimal = Decimal(0)
    price_abs: Decimal = Decimal(0)
    qty_pct: Decimal = Decimal(0)


def tolerance_for(rows: Sequence[Mapping[str, str]], category: str | None) -> Tolerance:
    """The company's tolerance for a category: the 'ANY' rows, overridden key by key by the category's own rows."""
    values: dict[str, Decimal] = {}
    for scope in ("ANY", category):
        if scope is None:
            continue
        for r in rows:
            if (
                r["category_code"] == scope
                and r["key"] in ("price_pct", "price_abs", "qty_pct")
                and r["value"] not in (None, "")
            ):
                values[r["key"]] = Decimal(str(r["value"]))
    return Tolerance(
        values.get("price_pct", Decimal(0)), values.get("price_abs", Decimal(0)), values.get("qty_pct", Decimal(0))
    )


def normalize_invoice_no(number: str) -> str:
    """The supplier's invoice number without case, spaces or punctuation, so 'PIS/2425/0036' and 'pis 2425 0036' are one."""
    return re.sub(r"[^A-Z0-9]", "", number.upper())


@dataclass(frozen=True)
class LineVerdict:
    result: str  # matched, within_tolerance or blocked
    reason: str | None = None
    price_var: Decimal = Decimal(0)  # invoice price less order price, per unit
    qty_var: Decimal = Decimal(0)  # invoiced quantity beyond what could be invoiced (never negative)


def judge_line(
    order_price: Decimal, invoice_price: Decimal, qty: Decimal, available: Decimal, tol: Tolerance
) -> LineVerdict:
    """One invoice line. The price is judged first; a price above the order price that is within neither tolerance blocks."""
    diff = invoice_price - order_price
    result = MATCHED
    if diff > 0:
        by_percent = diff <= order_price * tol.price_pct / 100
        by_amount = diff * qty <= tol.price_abs
        if not (by_percent or by_amount):
            return LineVerdict(BLOCKED, PRICE, diff, max(qty - available, Decimal(0)))
        result = WITHIN
    allowed = available + available * tol.qty_pct / 100
    if qty > allowed:
        return LineVerdict(BLOCKED, QTY, diff, qty - available)
    return LineVerdict(result, None, diff, max(qty - available, Decimal(0)))


def overall(verdicts: Sequence[LineVerdict]) -> tuple[str, str | None]:
    """The first line that blocks decides the invoice; otherwise one line within tolerance makes it within tolerance."""
    result = MATCHED
    for v in verdicts:
        if v.result == BLOCKED:
            return BLOCKED, v.reason
        if v.result == WITHIN:
            result = WITHIN
    return result, None


@dataclass(frozen=True)
class Header:
    """What the invoice says about itself and the supplier, against what the company holds."""

    supplier_gst_type: str | None  # regular, composition, unregistered
    e_invoice_applicable: bool
    invoice_type: str
    irn: str | None
    tax_charged: Decimal
    invoice_supplier_gstin: str | None
    valid_supplier_gstins: frozenset[
        str
    ]  # the registrations the supplier has on file, and the one the order was placed with
    invoice_buyer_gstin: str | None
    order_buyer_gstin: str | None


def header_problem(h: Header) -> str | None:
    """A problem with the invoice as a whole (tax or identity), which blocks payment before any line is judged."""
    if h.supplier_gst_type in ("composition", "unregistered") and h.tax_charged > 0:
        return TAX
    if (
        h.invoice_type == "invoice"
        and h.e_invoice_applicable
        and h.supplier_gst_type == "regular"
        and not (h.irn or "").strip()
    ):
        return IRN
    if h.order_buyer_gstin and h.invoice_buyer_gstin != h.order_buyer_gstin:
        return GSTIN
    if h.valid_supplier_gstins and h.invoice_supplier_gstin not in h.valid_supplier_gstins:
        return GSTIN
    return None


@dataclass
class LineSums:
    qty: Decimal
    unit_price: Decimal
    discount: Decimal
    taxable: Decimal
    cgst: Decimal = Decimal(0)
    sgst: Decimal = Decimal(0)
    igst: Decimal = Decimal(0)


@dataclass
class Arithmetic:
    problems: list[str] = field(default_factory=list)


def check_arithmetic(
    lines: Sequence[LineSums],
    charges: Sequence[Decimal],
    header_taxable: Decimal,
    header_tax: Decimal,
    round_off: Decimal,
    total: Decimal,
) -> Arithmetic:
    """The invoice adds up: each line is quantity times price, and the totals are the sum of the parts.

    A line may be out by two paise (rounding of the line); the total by one rupee (it is rounded to a rupee).
    """
    out = Arithmetic()
    for n, ln in enumerate(lines, start=1):
        expected = (ln.qty * (ln.unit_price - ln.discount)).quantize(TWO_PLACES)
        if abs(expected - ln.taxable) > Decimal("0.02"):
            out.problems.append(f"line {n}: quantity times price is {expected}, the invoice says {ln.taxable}")
    parts = sum((ln.taxable for ln in lines), Decimal(0)) + sum(charges, Decimal(0))
    if abs(parts - header_taxable) > Decimal("0.05"):
        out.problems.append(f"the lines and charges add up to {parts}, the invoice says {header_taxable}")
    if abs(header_taxable + header_tax + round_off - total) > Decimal("1.00"):
        out.problems.append(
            f"taxable amount, tax and rounding make {header_taxable + header_tax + round_off}, the invoice total is {total}"
        )
    return out
