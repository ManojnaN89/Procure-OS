"""FR20: goods receipt tolerance."""

from decimal import Decimal
from enum import StrEnum

from p2p.formulas.money import to_decimal


class ReceiptStatus(StrEnum):
    OVER_BLOCKED = "over_blocked"  # more than ordered plus the over-delivery tolerance
    OPEN = "open"  # below ordered minus the under-delivery tolerance: the line stays open
    COMPLETE = "complete"  # within tolerance, or closed short on purpose


def max_receivable(ordered: Decimal | int | str, over_pct: Decimal | int | str) -> Decimal:
    return to_decimal(ordered) * (1 + to_decimal(over_pct) / 100)


def receipt_status(
    ordered: Decimal | int | str,
    received_total: Decimal | int | str,
    over_pct: Decimal | int | str = 0,
    under_pct: Decimal | int | str = 0,
    *,
    closed_short: bool = False,
) -> ReceiptStatus:
    """Cumulative received must be <= ordered x (1 + over%); below ordered x (1 - under%) keeps the line open."""
    o, r = to_decimal(ordered), to_decimal(received_total)
    if r > max_receivable(o, over_pct):
        return ReceiptStatus.OVER_BLOCKED
    if r < o * (1 - to_decimal(under_pct) / 100) and not closed_short:
        return ReceiptStatus.OPEN
    return ReceiptStatus.COMPLETE
