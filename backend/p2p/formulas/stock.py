"""Where a stocked item stands against its reorder data. Plain arithmetic on numbers from the item's planning record."""

from decimal import Decimal

QTY = Decimal("0.0001")

# Worst first: the first one that applies is the status.
NEGATIVE = "negative"  # the books say less than nothing is on the shelf: a posting error to look into
STOCKOUT = "stockout"
BELOW_SAFETY = "below_safety"
REORDER = "reorder"
COVERED = "covered"  # at or below the reorder point, but enough is already on order
OVER_MAX = "over_max"
OK = "ok"
NOT_PLANNED = "not_planned"  # no reorder policy for this item here


def stock_status(
    on_hand: Decimal,
    on_order: Decimal,
    *,
    policy: str | None,
    reorder_point: Decimal | None,
    safety_stock: Decimal | None,
    max_qty: Decimal | None,
) -> str:
    """The inventory position (on hand plus on order) is compared with the reorder point; physical stock with the rest."""
    if on_hand < 0:
        return NEGATIVE
    if on_hand == 0:
        return STOCKOUT
    if policy in (None, "none"):
        return NOT_PLANNED
    if safety_stock is not None and on_hand < safety_stock:
        return BELOW_SAFETY
    if reorder_point is not None and on_hand <= reorder_point:
        return COVERED if on_hand + on_order > reorder_point else REORDER
    if max_qty is not None and on_hand > max_qty:
        return OVER_MAX
    return OK


def count_difference(counted: Decimal, on_hand: Decimal) -> Decimal:
    """What a stock count adds to (positive) or takes from (negative) the books to make them match the shelf."""
    return (counted - on_hand).quantize(QTY)
