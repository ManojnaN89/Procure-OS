"""The three-way match of a supplier invoice against the order and the goods received.

Plain rules on numbers and flags. Money is Decimal throughout. Per line, the invoice price may not be above the order
price by more than both the tolerance percentage and the rupee amount on the line (D-17, DF-12), and the quantity may
not be more than has been accepted at goods receipt and not yet invoiced (plus the quantity tolerance). Within
tolerance an invoice is paid; outside it, it is blocked until a person decides. A service line has no goods receipt:
what the requester confirmed in a service entry stands for it (a two-way match), and a service line invoiced beyond
what was confirmed is blocked as not confirmed. The GST rate and the tax on each line are compared with the order's.
"""

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal

MATCHED = "matched"
WITHIN = "within_tolerance"
BLOCKED = "blocked"

# the reason an invoice is blocked, and what kind of block it puts on payment
PRICE = "price_variance"
QTY = "qty_over_received"
IRN = "irn_missing"
GSTIN = "gstin_mismatch"
TAX = "tax_not_allowed"
TAX_HEAD = "tax_head"  # block 10's header check: IGST where CGST and SGST are due, or the reverse
GST = "gst_mismatch"
ARITH = "arithmetic_mismatch"
SERVICE = "service_not_confirmed"
BLOCK_KIND = {
    PRICE: "match",
    QTY: "match",
    SERVICE: "match",
    ARITH: "match",
    IRN: "tax",
    GSTIN: "tax",
    TAX: "tax",
    TAX_HEAD: "tax",
    GST: "tax",
}

TWO_PLACES = Decimal("0.01")
TAX_LINE_ABS_DEFAULT = Decimal("1.00")


@dataclass(frozen=True)
class Tolerance:
    price_pct: Decimal = Decimal(0)
    price_abs: Decimal = Decimal(0)
    qty_pct: Decimal = Decimal(0)
    tax_line_abs: Decimal = TAX_LINE_ABS_DEFAULT


def tolerance_for(rows: Sequence[Mapping[str, str]], category: str | None) -> Tolerance:
    """The company's tolerance for a category: the 'ANY' rows, overridden key by key by the category's own rows."""
    values: dict[str, Decimal] = {}
    for scope in ("ANY", category):
        if scope is None:
            continue
        for r in rows:
            if (
                r["category_code"] == scope
                and r["key"] in ("price_pct", "price_abs", "qty_pct", "tax_line_abs")
                and r["value"] not in (None, "")
            ):
                values[r["key"]] = Decimal(str(r["value"]))
    return Tolerance(
        values.get("price_pct", Decimal(0)),
        values.get("price_abs", Decimal(0)),
        values.get("qty_pct", Decimal(0)),
        values.get("tax_line_abs", TAX_LINE_ABS_DEFAULT),
    )


def normalize_invoice_no(number: str) -> str:
    """The supplier's invoice number without case, spaces or punctuation, so 'PIS/2425/0036' and 'pis 2425 0036' are one."""
    return re.sub(r"[^A-Z0-9]", "", number.upper())


@dataclass(frozen=True)
class LineVerdict:
    result: str  # matched, within_tolerance or blocked
    reason: str | None = None  # the first reason (existing readers)
    price_var: Decimal = Decimal(0)  # invoice price less order price, per unit
    qty_var: Decimal = Decimal(0)  # invoiced quantity beyond what could be invoiced (never negative)
    reasons: tuple[str, ...] = ()  # every reason of the line, in the order price, quantity, service
    variance_value: Decimal = Decimal("0.00")  # the rupee variance on the line, halves up to paise


def judge_line(
    order_price: Decimal,
    invoice_price: Decimal,
    qty: Decimal,
    available: Decimal,
    tol: Tolerance,
    *,
    service: bool = False,
) -> LineVerdict:
    """One invoice line. The price passes only within both the percentage and the rupee limit; the quantity must be
    within what was accepted. Every reason is kept, so a price block no longer hides a quantity problem.
    For a service line `available` is what was confirmed and not yet invoiced, and too much is 'not confirmed'."""
    diff = invoice_price - order_price
    reasons: list[str] = []
    within = False
    if diff > 0:
        by_percent = diff <= order_price * tol.price_pct / 100
        by_amount = diff * qty <= tol.price_abs
        if by_percent and by_amount:
            within = True
        else:
            reasons.append(PRICE)
    variance = (diff * qty).quantize(TWO_PLACES, rounding=ROUND_HALF_UP) if diff > 0 else Decimal("0.00")
    allowed = available + available * tol.qty_pct / 100
    if qty > allowed:
        reasons.append(SERVICE if service else QTY)
    if reasons:
        return LineVerdict(BLOCKED, reasons[0], diff, max(qty - available, Decimal(0)), tuple(reasons), variance)
    return LineVerdict(WITHIN if within else MATCHED, None, diff, max(qty - available, Decimal(0)), (), variance)


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


@dataclass(frozen=True)
class TaxParts:
    """The three tax heads of one amount (the invoice's or the order's expected tax)."""

    cgst: Decimal = Decimal("0.00")
    sgst: Decimal = Decimal("0.00")
    igst: Decimal = Decimal("0.00")


HEADS = ("CGST", "SGST", "IGST")


def _parts(value: TaxParts | Mapping[str, Decimal]) -> tuple[Decimal, Decimal, Decimal]:
    if isinstance(value, Mapping):
        return Decimal(value.get("cgst", 0)), Decimal(value.get("sgst", 0)), Decimal(value.get("igst", 0))
    return value.cgst, value.sgst, value.igst


def _amount(value: Decimal) -> str:
    return f"{value.quantize(TWO_PLACES, rounding=ROUND_HALF_UP):f}"


def _rate(value: Decimal) -> str:
    text = f"{value:f}"
    return text.rstrip("0").rstrip(".") if "." in text else text


def judge_gst(
    order_rate: Decimal,
    invoice_rate: Decimal,
    expected: TaxParts | Mapping[str, Decimal],
    charged: TaxParts | Mapping[str, Decimal],
    tol: Decimal,
    *,
    compare_amounts: bool = True,
) -> list[str]:
    """The GST on one line against the order's: the rate always, the amounts within the rupee tolerance per head.

    A reverse-charge line compares the rate only (the tax is ours to pay, not the supplier's to charge). At exactly
    the tolerance apart the line is within.
    """
    problems: list[str] = []
    if invoice_rate != order_rate:
        problems.append(f"GST {_rate(invoice_rate)}% on the invoice, {_rate(order_rate)}% on the order")
    if not compare_amounts:
        return problems
    for head, want, got in zip(HEADS, _parts(expected), _parts(charged), strict=True):
        apart = abs(got - want)
        if apart > tol:
            problems.append(
                f"{head} {_amount(got)} against {_amount(want)} expected ({_amount(apart)} apart, more than {_amount(tol)})"
            )
    return problems


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
