"""Comparing quotes: what an offer really costs, and whether a price looks too good to be true.

Plain arithmetic on the numbers in the quotes. Nothing here is a model's opinion (CLAUDE.md rule 2).
"""

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import ROUND_DOWN, ROUND_HALF_UP, Decimal
from statistics import median

from p2p.formulas.money import format_inr, round_money

PAISA = Decimal("0.01")


def allocate(total: Decimal, shares: Sequence[Decimal]) -> list[Decimal]:
    """Split an amount over lines in proportion to their values, to the paisa.

    The parts always add up to the amount exactly: what is left over after rounding down goes, a paisa at a time, to the
    lines with the biggest remainders (the earlier line first when two are equal). With no values to go by the amount is
    split evenly.
    """
    n = len(shares)
    if n == 0:
        return []
    total = round_money(total)
    weight = sum(shares, Decimal(0))
    raw = [total / n] * n if weight <= 0 else [total * s / weight for s in shares]
    parts = [r.quantize(PAISA, rounding=ROUND_DOWN) for r in raw]
    left = int((total - sum(parts, Decimal(0))) / PAISA)
    order = sorted(range(n), key=lambda i: (-(raw[i] - parts[i]), i))
    for i in order[:left]:
        parts[i] += PAISA
    return parts


@dataclass(frozen=True)
class Landed:
    value: Decimal  # quantity times the net price
    charges: Decimal  # this line's share of the quote's freight and packing
    tax: Decimal
    total: Decimal


def landed_cost(value: Decimal, charges: Decimal, gst_rate: Decimal) -> Landed:
    """What a line costs to get to our door: its price, its share of the freight and packing, and the GST on both.

    The charges carry the rate of the goods they travel with (an approximation of the order's rule, which takes the
    highest rate among the goods).
    """
    tax = round_money((value + charges) * gst_rate / 100)
    return Landed(value, charges, tax, value + charges + tax)


def gap_pct(price: Decimal, reference: Decimal) -> Decimal | None:
    """How far above the reference a price is, in percent to two places (negative when it is below)."""
    if reference <= 0:
        return None
    return ((price - reference) / reference * 100).quantize(PAISA, rounding=ROUND_HALF_UP)


def price_questions(
    price: Decimal, last_price: Decimal | None, other_prices: Sequence[Decimal], threshold_pct: Decimal
) -> list[str]:
    """Reasons to ask the supplier before trusting a price: it is far below what the others offer or what we paid last.

    A price far below the rest is more often a mistake (a wrong unit, a missing item) than a bargain, so it is a question
    for a person and is never awarded without one. A threshold of zero switches the check off.
    """
    if threshold_pct <= 0:
        return []
    out: list[str] = []
    if other_prices:
        mid = Decimal(median(other_prices))
        gap = gap_pct(price, mid)
        if gap is not None and gap <= -threshold_pct:
            out.append(f"{-gap}% below the other quotes (median {format_inr(mid)})")
    if last_price is not None:
        gap = gap_pct(price, last_price)
        if gap is not None and gap <= -threshold_pct:
            out.append(f"{-gap}% below the last price paid ({format_inr(last_price)})")
    return out
