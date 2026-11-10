"""The control tower's numbers: working days, ageing buckets and the KPIs. Plain code on dates and Decimal values."""

from collections.abc import Sequence
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal

from p2p.formulas.calendar import BusinessCalendar

BUCKETS = ("0-2", "3-7", "8-14", "15+")
ONE = Decimal("0.1")


def working_days_between(start: date, end: date, calendar: BusinessCalendar) -> int:
    """Working days that have passed from `start` to `end`: the days after `start` up to and including `end`.

    The same day is 0. Nothing is counted for a date in the future.
    """
    if end <= start:
        return 0
    days = 0
    day = start
    while day < end:
        day += timedelta(days=1)
        if calendar.is_working_day(day):
            days += 1
    return days


def bucket_of(working_days: int) -> str:
    """The ageing bucket: 0-2, 3-7, 8-14 or 15 and more working days."""
    if working_days <= 2:
        return "0-2"
    if working_days <= 7:
        return "3-7"
    if working_days <= 14:
        return "8-14"
    return "15+"


def median(values: Sequence[Decimal | int]) -> Decimal | None:
    """The middle value (the mean of the two middle ones for an even count), to one decimal; None with nothing to count."""
    if not values:
        return None
    ordered = sorted(Decimal(v) for v in values)
    n = len(ordered)
    mid = ordered[n // 2] if n % 2 else (ordered[n // 2 - 1] + ordered[n // 2]) / 2
    return mid.quantize(ONE, rounding=ROUND_HALF_UP)


def percentile_90(values: Sequence[Decimal | int]) -> Decimal | None:
    """The 90th percentile by the nearest-rank rule: the value at position ceil(0.9 x n) of the sorted values."""
    if not values:
        return None
    ordered = sorted(Decimal(v) for v in values)
    rank = -(-len(ordered) * 9 // 10)  # ceil(n x 0.9) in whole numbers
    return ordered[rank - 1].quantize(ONE, rounding=ROUND_HALF_UP)


def share(part: int | Decimal, whole: int | Decimal) -> Decimal | None:
    """A percentage to one decimal; None when there is nothing to divide by."""
    if not whole:
        return None
    return (Decimal(part) * 100 / Decimal(whole)).quantize(ONE, rounding=ROUND_HALF_UP)


def spend_under_management(managed: Decimal, total: Decimal) -> Decimal | None:
    """Value of orders placed on a contract or through an RFQ, as a percentage of all order value."""
    return share(managed, total)


def saving(first_offer: Decimal, awarded: Decimal, qty: Decimal) -> Decimal:
    """What negotiating and comparing saved on a line: first offer less the awarded price, times the quantity."""
    return ((first_offer - awarded) * qty).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
