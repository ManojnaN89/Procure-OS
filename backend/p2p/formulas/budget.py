"""The budget test for a request. Plain arithmetic on numbers that come from the budget and request tables."""

from dataclasses import dataclass
from decimal import Decimal

PAISE = Decimal("0.01")


@dataclass(frozen=True)
class BudgetCheck:
    status: str  # ok, over, or unchecked (no budget is set for these lines)
    budget: Decimal | None
    committed: Decimal
    requested: Decimal

    @property
    def available(self) -> Decimal | None:
        return None if self.budget is None else self.budget - self.committed

    @property
    def over_by(self) -> Decimal:
        if self.available is None or self.requested <= self.available:
            return Decimal("0.00")
        return (self.requested - self.available).quantize(PAISE)


def check_budget(budget: Decimal | None, committed: Decimal, requested: Decimal) -> BudgetCheck:
    """A request fits when its value is no more than what is left of the budget (equal is fine, one paisa more is over).

    The budget is what has been planned from the start of the financial year to the current month, so what was not spent
    in an earlier month is still there. `committed` is the value of the requests already approved or waiting. With no
    budget set the request is not checked, and says so.
    """
    if budget is None:
        return BudgetCheck("unchecked", None, committed, requested)
    status = "ok" if requested <= max(budget - committed, Decimal(0)) else "over"  # asking for nothing is never over
    return BudgetCheck(status, budget, committed, requested)
