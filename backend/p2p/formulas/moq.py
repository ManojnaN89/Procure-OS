"""FR06: minimum order quantity and pack rounding."""

from decimal import Decimal

from p2p.formulas.money import ceil_to_multiple, to_decimal


def moq_round(qty: Decimal | int | str, moq: Decimal | int | str = 0, multiple: Decimal | int | str = 1) -> Decimal:
    """q = max(MOQ, ceil(q / order multiple) x order multiple)."""
    q, m = to_decimal(qty), to_decimal(moq)
    if q < 0 or m < 0:
        raise ValueError("quantities must not be negative")
    return max(m, ceil_to_multiple(q, multiple))
