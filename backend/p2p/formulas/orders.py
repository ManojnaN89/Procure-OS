"""The amounts on a purchase order: net prices, line values, GST and totals. All Decimal, quantised to paise.

A model never computes any of this (rule 2). The prices come from a contract or from a quote a person entered; the
GST rate comes from the item record or from the quote; this module only does the arithmetic.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

from p2p.formulas.gst import GstSplit, Registration, gst_split
from p2p.formulas.money import round_money, to_decimal

PRICE_STEP = Decimal("0.0001")
HUNDRED = Decimal(100)


def net_unit_price(price: Decimal | int | str, discount_pct: Decimal | int | str = 0) -> Decimal:
    """The price per unit after the supplier's discount, to four decimals (what the purchase order carries)."""
    p, d = to_decimal(price), to_decimal(discount_pct)
    if p < 0:
        raise ValueError("a price must not be negative")
    if not 0 <= d <= 100:
        raise ValueError("a discount must be between 0 and 100 percent")
    return (p * (HUNDRED - d) / HUNDRED).quantize(PRICE_STEP, rounding=ROUND_HALF_UP)


def current_gst_rate(
    before: Decimal | int | str | None,
    after: Decimal | int | str | None,
    change_date: date | None,
    on_date: date,
) -> Decimal | None:
    """The GST rate of an item on a date, from the rate before and after a change. None when the item has no rate."""
    if after is not None and change_date is not None and on_date >= change_date:
        return to_decimal(after)
    if before is not None:
        return to_decimal(before)
    return None if after is None else to_decimal(after)


@dataclass(frozen=True)
class LineAmounts:
    taxable: Decimal
    cgst: Decimal
    sgst: Decimal
    igst: Decimal

    @property
    def tax(self) -> Decimal:
        return self.cgst + self.sgst + self.igst

    @property
    def total(self) -> Decimal:
        return self.taxable + self.tax


def line_amounts(
    qty: Decimal | int | str,
    unit_price: Decimal | int | str,
    rate_pct: Decimal | int | str,
    supplier_state: str,
    place_of_supply_state: str,
    registration: Registration = Registration.REGULAR,
) -> LineAmounts:
    """Taxable value is quantity times the net price, rounded to paise. The tax follows the states and the registration."""
    taxable = round_money(to_decimal(qty) * to_decimal(unit_price))
    split: GstSplit = gst_split(taxable, rate_pct, supplier_state, place_of_supply_state, registration)
    return LineAmounts(taxable, split.cgst, split.sgst, split.igst)


def charge_rate(line_rates: Sequence[Decimal]) -> Decimal:
    """Freight and packing charged with the goods take the highest rate among them (the tax rule for a mixed supply)."""
    return max(line_rates) if line_rates else Decimal(0)


@dataclass(frozen=True)
class OrderTotals:
    taxable: Decimal
    cgst: Decimal
    sgst: Decimal
    igst: Decimal

    @property
    def total(self) -> Decimal:
        return self.taxable + self.cgst + self.sgst + self.igst


def order_totals(parts: Sequence[LineAmounts]) -> OrderTotals:
    """Add up the lines and the charges of an order."""
    zero = Decimal("0.00")
    return OrderTotals(
        taxable=sum((p.taxable for p in parts), zero),
        cgst=sum((p.cgst for p in parts), zero),
        sgst=sum((p.sgst for p in parts), zero),
        igst=sum((p.igst for p in parts), zero),
    )


def variance_pct(price: Decimal | int | str, estimate: Decimal | int | str) -> Decimal | None:
    """How far a price is above the estimate, in percent (negative when below). None when there is no estimate."""
    est = to_decimal(estimate)
    if est <= 0:
        return None
    return ((to_decimal(price) - est) / est * HUNDRED).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
