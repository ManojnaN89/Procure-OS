"""Period RFQs (task 12.1) and the value of their award (task 12.2): pure arithmetic, no database.

A period is calendar dates: it starts after today, ends on or after its start and is at most 12 months long (it may
cross 31 March). Its suggested quantity is the larger of the open requirements in the period and what was ordered in
the last 12 months for as many months as the period covers, rounded up to a whole purchase unit. The award's value is
worked out like an order's: quantity times the net price per part, GST at the quoted rate split by the states.
"""

import calendar
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import ROUND_CEILING, Decimal

from p2p.formulas.gst import Registration
from p2p.formulas.money import to_decimal
from p2p.formulas.orders import line_amounts

MAX_MONTHS = 12


def months_in(start: date, end: date) -> int:
    """The calendar months the period touches: 1 Jan to 31 Mar is 3, 15 Jan to 14 Feb is 2, one day is 1."""
    return (end.year * 12 + end.month) - (start.year * 12 + start.month) + 1


def add_months(day: date, months: int) -> date:
    """The same day `months` later (the month's last day when it is shorter)."""
    n = day.year * 12 + day.month - 1 + months
    year, month = divmod(n, 12)
    return date(year, month + 1, min(day.day, calendar.monthrange(year, month + 1)[1]))


def last_day(start: date) -> date:
    """The last day a period starting on `start` may end: 12 months on, less a day (1 Apr 2027 -> 31 Mar 2028)."""
    return add_months(start, MAX_MONTHS) - timedelta(days=1)


def check_period(start: date, end: date, today: date) -> list[str]:
    """What is wrong with the period, in words (empty when it is fine)."""
    problems = []
    if start <= today:
        problems.append("the period must start after today")
    if end < start:
        problems.append("the period ends before it starts")
    elif end > last_day(start):
        problems.append("a period is at most 12 months")
    return problems


@dataclass(frozen=True)
class Suggestion:
    qty: Decimal  # rounded up to a whole purchase unit
    history_part: Decimal  # ordered in 12 months x months / 12, before rounding


def suggested(open_need: Decimal, ordered_12m: Decimal, months: int) -> Suggestion:
    """The larger of the open need and the last 12 months' orders pro rata, rounded up to a whole unit."""
    part = to_decimal(ordered_12m) * months / 12
    best = max(to_decimal(open_need), part)
    return Suggestion(best.to_integral_value(rounding=ROUND_CEILING), part)


@dataclass(frozen=True)
class AwardPart:
    qty: Decimal
    net_price: Decimal
    gst_rate: Decimal


@dataclass(frozen=True)
class AwardValue:
    taxable: Decimal
    cgst: Decimal
    sgst: Decimal
    igst: Decimal

    @property
    def total(self) -> Decimal:
        return self.taxable + self.cgst + self.sgst + self.igst

    def plus(self, other: "AwardValue") -> "AwardValue":
        """Two suppliers' parts of one award together."""
        return AwardValue(
            self.taxable + other.taxable, self.cgst + other.cgst, self.sgst + other.sgst, self.igst + other.igst
        )


NOTHING = AwardValue(Decimal("0.00"), Decimal("0.00"), Decimal("0.00"), Decimal("0.00"))


def award_value(parts: Sequence[AwardPart], supplier_state: str, location_state: str) -> AwardValue:
    """Per part: taxable = qty x net price, half up to paise; GST split as an order's line (formulas/orders.py)."""
    zero = Decimal("0.00")
    taxable, cgst, sgst, igst = zero, zero, zero, zero
    for p in parts:
        line = line_amounts(p.qty, p.net_price, p.gst_rate, supplier_state, location_state, Registration.REGULAR)
        taxable, cgst, sgst, igst = taxable + line.taxable, cgst + line.cgst, sgst + line.sgst, igst + line.igst
    return AwardValue(taxable, cgst, sgst, igst)
