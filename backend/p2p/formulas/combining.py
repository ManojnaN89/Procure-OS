"""When planned request lines are bought on one requisition line. Dates and quantities only."""

from datetime import date
from decimal import Decimal

from p2p.formulas.replenishment import purchase_quantity


def within_window(need_by: list[date], days: int) -> bool:
    """Lines combine when the earliest and the latest need-by are at most `days` apart: 7 days apart do with a 7-day
    window, 8 do not."""
    return (max(need_by) - min(need_by)).days <= days


def combined_quantity(sources: list[Decimal], *, base_per_purchase_unit: Decimal, moq: Decimal) -> Decimal:
    """The combined line's quantity in the purchase unit: what all its sources need (stock unit), rounded up once to the
    pack and the minimum order, so two small needs are not rounded up twice."""
    return purchase_quantity(sum(sources, Decimal(0)), base_per_purchase_unit=base_per_purchase_unit, moq=moq)
