"""Choosing between contract prices. Plain comparisons on numbers that come from the price records."""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from p2p.formulas.calendar import BusinessCalendar


@dataclass(frozen=True)
class PriceOption:
    supplier_code: str
    contract_code: str
    price: Decimal  # per unit, in the unit the item is bought in
    min_qty: Decimal  # the quantity from which this price applies (a quantity break)
    lead_days: int | None
    valid_to: date | None
    record_id: int | None = None  # the price record, so what is bought keeps the version it used


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


def reminder_due(
    *,
    last_contact: date,
    reminders_sent: int,
    max_reminders: int,
    after_days: int,
    today: date,
    due_date: date,
    calendar: BusinessCalendar,
) -> bool:
    """Whether a supplier that has not answered should be reminded today.

    `last_contact` is the day the request, or the previous reminder, went out. A reminder is due once `after_days`
    working days have passed since then, never after the reply date the supplier was given, and never more than
    `max_reminders` times. `after_days` of zero or less switches reminders off.
    """
    if after_days <= 0 or reminders_sent >= max_reminders or today > due_date:
        return False
    return today >= calendar.add_working_days(last_contact, after_days)
