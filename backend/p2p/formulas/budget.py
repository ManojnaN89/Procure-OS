"""The budget test for a request. Plain arithmetic on numbers that come from the budget and request tables."""

import re
from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

PAISE = Decimal("0.01")
HUNDRED = Decimal(100)
WARN_PCT = Decimal(90)  # decision D36: warn at 90% used
BLOCK_PCT = Decimal(100)  # and over budget above 100%


@dataclass(frozen=True)
class BudgetCheck:
    status: str  # ok, warn (nearly used up), over, or unchecked (no budget is set for these lines)
    budget: Decimal | None
    committed: Decimal
    requested: Decimal
    block_pct: Decimal = BLOCK_PCT

    @property
    def limit(self) -> Decimal | None:
        """What may be committed in all before the budget is over (the budget itself at 100%)."""
        return None if self.budget is None else self.budget * self.block_pct / HUNDRED

    @property
    def available(self) -> Decimal | None:
        return None if self.limit is None else self.limit - self.committed

    @property
    def over_by(self) -> Decimal:
        if self.available is None or self.requested <= self.available:
            return Decimal("0.00")
        return (self.requested - self.available).quantize(PAISE)

    @property
    def used_pct(self) -> Decimal | None:
        """Share of the budget used once this request counts, unrounded (None without a budget, or a zero one)."""
        if not self.budget:
            return None
        return (self.committed + self.requested) * HUNDRED / self.budget


def check_budget(
    budget: Decimal | None,
    committed: Decimal,
    requested: Decimal,
    *,
    warn_pct: Decimal = WARN_PCT,
    block_pct: Decimal = BLOCK_PCT,
) -> BudgetCheck:
    """A request is over when the budget used with it goes above `block_pct` of the budget (exactly 100% is fine, one
    paisa more is over), and carries a warning when the budget used reaches `warn_pct` (exactly 90% warns, 89.99% does
    not).

    The budget is what has been planned from the start of the financial year to the current month, so what was not spent
    in an earlier month is still there. `committed` is the value of the requests already approved or waiting. With no
    budget set the request is not checked, and says so. Asking for nothing is never over and never warns.
    """
    if budget is None:
        return BudgetCheck("unchecked", None, committed, requested, block_pct)
    limit = budget * block_pct / HUNDRED
    if requested > max(limit - committed, Decimal(0)):
        status = "over"
    elif requested > 0 and (committed + requested) * HUNDRED >= budget * warn_pct:
        status = "warn"
    else:
        status = "ok"
    return BudgetCheck(status, budget, committed, requested, block_pct)


# ---- the budget engine (task 3.7): what is used, when spend is actual, locked months and the year ---------------------

PERIOD = re.compile(r"^(\d{4})-(0[1-9]|1[0-2])$")
FY = re.compile(r"^(\d{4})-(\d{2})$")


def open_order_value(qty: Decimal, short_closed: Decimal, unit_price: Decimal, actual: Decimal) -> Decimal:
    """What an open order line still holds of a budget: its value at the order price for what may still come (less what
    was short closed), less what is already actual on it; never below nothing. Before GST, in paise half up."""
    value = ((qty - short_closed) * unit_price).quantize(PAISE, rounding=ROUND_HALF_UP)
    return max(Decimal("0.00"), value - actual)


def receipt_actual(accepted: Decimal, unit_price: Decimal) -> Decimal | None:
    """What a stocked line spends when goods are accepted (D-10): the accepted quantity at the order price, in paise half
    up. Nothing accepted spends nothing (None); a tiny quantity that rounds to Rs 0.00 is still a row."""
    if accepted <= 0:
        return None
    return (accepted * unit_price).quantize(PAISE, rounding=ROUND_HALF_UP)


def receipt_reversal_actual(qty: Decimal, unit_price: Decimal) -> Decimal | None:
    """What a receipt reversal or a counted return takes back: minus what the accepted quantity spent (task 3.6)."""
    amount = receipt_actual(qty, unit_price)
    return None if amount is None else -amount


def next_period(period: str) -> str:
    y, m = int(period[:4]), int(period[5:])
    return f"{y + m // 12}-{m % 12 + 1:02d}"


def post_period(event: str, locked: set[str], current: str) -> str:
    """The month an actual is booked in: the month it happened, unless that month is locked; then the first month after
    it that is not locked, never later than the current month (the current month cannot have been locked, as it has not
    ended)."""
    period = event
    while period in locked and period < current:
        period = next_period(period)
    return period


def fy_of(period: str) -> str:
    """'2026-27' for any month from 2026-04 to 2027-03."""
    y, m = int(period[:4]), int(period[5:])
    start = y if m >= 4 else y - 1
    return f"{start}-{(start + 1) % 100:02d}"


def fy_periods(fy: str) -> list[str]:
    """The twelve months of a financial year ('2026-27': 2026-04 .. 2027-03)."""
    start = int(fy[:4])
    return [f"{start}-{m:02d}" for m in range(4, 13)] + [f"{start + 1}-{m:02d}" for m in range(1, 4)]


def valid_period(period: str) -> bool:
    return bool(PERIOD.match(period))


def valid_fy(fy: str) -> bool:
    found = FY.match(fy)
    return bool(found) and (int(found.group(1)) + 1) % 100 == int(found.group(2))  # type: ignore[union-attr]


def lockable(period: str, today: date, locked: set[str]) -> str | None:
    """Why a month cannot be locked today, or None when it can: it must have ended, not be locked yet, and every earlier
    month of its financial year must be locked first."""
    if period >= f"{today.year}-{today.month:02d}":
        return "not ended"
    if period in locked:
        return "already locked"
    for earlier in fy_periods(fy_of(period)):
        if earlier >= period:
            break
        if earlier not in locked:
            return f"lock {earlier} first"
    return None
