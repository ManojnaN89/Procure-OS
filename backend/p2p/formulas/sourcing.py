"""Choosing between contract prices. Plain comparisons on numbers that come from the price records."""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal


@dataclass(frozen=True)
class PriceOption:
    supplier_code: str
    contract_code: str
    price: Decimal  # per unit, in the unit the item is bought in
    min_qty: Decimal  # the quantity from which this price applies (a quantity break)
    lead_days: int | None
    valid_to: date | None


def pick_contract_price(options: Sequence[PriceOption], qty: Decimal) -> PriceOption | None:
    """The best contract price for a quantity, or None when no option applies.

    A price applies when the quantity reaches its break (a break at exactly the quantity counts). A supplier with
    several breaks is held to the highest one the quantity reaches, which is its best price for that quantity. Between
    suppliers the lowest price wins; a tie goes to the shorter lead time, then to the supplier code, so the same data
    always gives the same answer.
    """
    best_per_supplier: dict[str, PriceOption] = {}
    for option in options:
        if option.min_qty > qty:
            continue
        held = best_per_supplier.get(option.supplier_code)
        if held is None or (option.min_qty, -option.price) > (held.min_qty, -held.price):
            best_per_supplier[option.supplier_code] = option
    if not best_per_supplier:
        return None
    return min(
        best_per_supplier.values(),
        key=lambda o: (o.price, o.lead_days if o.lead_days is not None else 10_000, o.supplier_code),
    )
