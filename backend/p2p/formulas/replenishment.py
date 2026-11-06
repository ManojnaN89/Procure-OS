"""How much to order when stock has fallen to its reorder point. Quantities only; prices are applied by the caller."""

from decimal import Decimal

from p2p.formulas.money import ceil_to_multiple
from p2p.formulas.moq import moq_round

QTY = Decimal("0.001")


def shortfall(policy: str | None, position: Decimal, *, max_qty: Decimal | None, order_qty: Decimal | None) -> Decimal:
    """What is needed, in the stock unit, to bring the inventory position (on hand plus on order) back to plan.

    min/max planning orders up to the maximum. Reorder-point planning orders its fixed lot. Zero means nothing to order.
    """
    if policy == "minmax":
        need = (max_qty or Decimal(0)) - position
    elif policy == "rop":
        need = order_qty or Decimal(0)
    else:
        need = Decimal(0)
    return max(need, Decimal(0))


def purchase_quantity(need_base: Decimal, *, base_per_purchase_unit: Decimal, moq: Decimal) -> Decimal:
    """The quantity to put on the request, in the unit the item is bought in.

    The need (stock unit) is turned into purchase units and rounded up: to whole packs when the item is bought in a pack
    (you cannot buy 1.4 cartons), to 0.001 when it is bought in its stock unit (weights and lengths). The result is then
    lifted to the supplier's minimum order. Nothing needed means nothing ordered, whatever the minimum.
    """
    if need_base <= 0:
        return Decimal(0)
    if base_per_purchase_unit <= 0:
        raise ValueError("a purchase unit must contain a positive number of stock units")
    step = QTY if base_per_purchase_unit == 1 else Decimal(1)
    units = ceil_to_multiple(need_base / base_per_purchase_unit, step)
    return moq_round(units, moq, step)
