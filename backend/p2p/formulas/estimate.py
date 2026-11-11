"""The price a purchase request is estimated at, before anyone has quoted. It chooses the approver and is checked against
the budget; the price paid comes later from the contract or the quotes."""

from decimal import Decimal

CONTRACT = "contract"
LAST_PAID = "last price paid"
STANDARD = "standard price"


def estimated_price(
    contract: Decimal | None, last_paid: Decimal | None, standard: Decimal | None
) -> tuple[Decimal, str] | None:
    """The contract price, else the last price paid, else the item's standard price, with where it came from. A price of
    zero or less is no price. None when there is none."""
    for price, basis in ((contract, CONTRACT), (last_paid, LAST_PAID), (standard, STANDARD)):
        if price is not None and price > 0:
            return Decimal(price), basis
    return None
