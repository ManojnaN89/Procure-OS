"""The price a purchase request is estimated at, before anyone has quoted. It chooses the approver and is checked against
the budget; the price paid comes later from the contract or the quotes. Every request is estimated the same way, whoever
or whatever raised it (the form, the plain-words intake, replenishment, the store check)."""

from dataclasses import dataclass
from decimal import Decimal

CONTRACT = "contract"
LAST_PAID = "last price paid"
STANDARD = "standard price"


@dataclass(frozen=True)
class Estimate:
    price: Decimal
    basis: str  # CONTRACT, LAST_PAID or STANDARD
    ref: str | None = None  # the contract, or the order the last price was paid on

    @property
    def source(self) -> str:
        """Where the estimate came from, in words kept with the request line: 'contract CNT-0001',
        'last price paid on PO-...', 'standard price'."""
        if self.ref is None:
            return self.basis
        return f"{self.basis} on {self.ref}" if self.basis == LAST_PAID else f"{self.basis} {self.ref}"


def estimated_price(
    contract: tuple[Decimal, str | None] | None,
    last_paid: tuple[Decimal, str | None] | None,
    standard: Decimal | None,
) -> Estimate | None:
    """The contract price, else the last price paid, else the item's standard price, each with its reference (the
    contract, the order). A price of zero or less is no price. None when there is none."""
    for found, basis in ((contract, CONTRACT), (last_paid, LAST_PAID)):
        if found is not None and found[0] is not None and found[0] > 0:
            return Estimate(Decimal(found[0]), basis, found[1])
    if standard is not None and standard > 0:
        return Estimate(Decimal(standard), STANDARD)
    return None
