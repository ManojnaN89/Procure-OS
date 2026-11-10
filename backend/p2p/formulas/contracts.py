"""How much of a contract's ceiling has been used. Plain arithmetic on numbers from the contract and the orders."""

from dataclasses import dataclass
from decimal import Decimal

PAISE = Decimal("0.01")


@dataclass(frozen=True)
class ContractUse:
    max_value: Decimal | None  # the ceiling; None means the contract has none
    used: Decimal  # ordered so far, before tax

    @property
    def remaining(self) -> Decimal | None:
        return None if self.max_value is None else max(self.max_value - self.used, Decimal(0)).quantize(PAISE)

    @property
    def pct_used(self) -> Decimal | None:
        if not self.max_value:
            return None
        return (self.used * 100 / self.max_value).quantize(Decimal("0.1"))


def ceiling_breach(max_value: Decimal | None, used: Decimal, new: Decimal) -> Decimal:
    """By how much new orders would take a contract over its ceiling (zero when they fit; reaching it exactly fits)."""
    if max_value is None:
        return Decimal("0.00")
    return max(used + new - max_value, Decimal(0)).quantize(PAISE)
