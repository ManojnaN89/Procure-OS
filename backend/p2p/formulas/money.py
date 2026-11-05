"""Exact money and quantity arithmetic. Floats are refused: a float anywhere near money is a bug."""

from decimal import ROUND_CEILING, ROUND_HALF_UP, Decimal
from typing import Any

PAISE = Decimal("0.01")
QTY_STEP = Decimal("0.001")


def to_decimal(value: Any) -> Decimal:
    """Convert int, str or Decimal to Decimal. A float raises TypeError."""
    if isinstance(value, float):
        raise TypeError("float is not allowed for money or quantity; use str or Decimal")
    if isinstance(value, bool):
        raise TypeError("bool is not a number here")
    return value if isinstance(value, Decimal) else Decimal(value)


def round_money(value: Any) -> Decimal:
    """Round to paise, halves up (the way invoices round)."""
    return to_decimal(value).quantize(PAISE, rounding=ROUND_HALF_UP)


def round_qty(value: Any) -> Decimal:
    return to_decimal(value).quantize(QTY_STEP, rounding=ROUND_HALF_UP)


def round_rupee(value: Any) -> Decimal:
    """Round to a whole rupee, halves up (invoice round-off)."""
    return to_decimal(value).quantize(Decimal("1"), rounding=ROUND_HALF_UP)


def ceil_to_multiple(value: Any, multiple: Any) -> Decimal:
    v, m = to_decimal(value), to_decimal(multiple)
    if m <= 0:
        raise ValueError("multiple must be positive")
    return (v / m).to_integral_value(rounding=ROUND_CEILING) * m


def format_inr(value: Any, *, symbol: bool = True) -> str:
    """Indian digit grouping: 12345678.9 becomes '₹ 1,23,45,678.90'."""
    v = round_money(value)
    sign = "-" if v < 0 else ""
    whole, _, frac = f"{abs(v):.2f}".partition(".")
    head, tail = whole[:-3], whole[-3:]
    groups: list[str] = []
    while len(head) > 2:
        groups.insert(0, head[-2:])
        head = head[:-2]
    if head:
        groups.insert(0, head)
    grouped = ",".join([*groups, tail]) if groups else tail
    return f"{sign}{'₹ ' if symbol else ''}{grouped}.{frac}"
