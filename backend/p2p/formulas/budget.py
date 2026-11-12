"""The budget test for a request. Plain arithmetic on numbers that come from the budget and request tables."""

from dataclasses import dataclass
from decimal import Decimal

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
