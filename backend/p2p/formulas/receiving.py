"""Rules for taking goods in. Plain arithmetic on quantities that come from the order and the receipts."""

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

QTY = Decimal("0.0001")


def max_receivable(ordered: Decimal, over_pct: Decimal) -> Decimal:
    """The most that may be taken in against a line: the ordered quantity plus the over-delivery the company accepts."""
    return (ordered * (Decimal(100) + over_pct) / 100).quantize(QTY, rounding=ROUND_HALF_UP)


def room_left(ordered: Decimal, counted: Decimal, over_pct: Decimal) -> Decimal:
    """How much more can still be received. `counted` is what has been accepted, plus what waits for inspection.

    Rejected goods do not count: the supplier replaces them, and the replacement must fit too.
    """
    return max(max_receivable(ordered, over_pct) - counted, Decimal(0))


@dataclass(frozen=True)
class SplitResult:
    accepted: Decimal
    rejected: Decimal


def split(received: Decimal, rejected: Decimal | None) -> SplitResult:
    """Received goods are accepted except for what is rejected. A rejection cannot be more than was received."""
    rej = rejected or Decimal(0)
    if received <= 0:
        raise ValueError("the quantity received must be more than zero")
    if rej < 0 or rej > received:
        raise ValueError("the rejected quantity must be between zero and the quantity received")
    return SplitResult(received - rej, rej)


def line_status(ordered: Decimal, accepted_total: Decimal) -> str:
    """A line is received once the accepted quantity reaches the ordered quantity; some accepted is a part receipt."""
    if accepted_total >= ordered:
        return "received"
    return "part_received" if accepted_total > 0 else "open"


def order_status(line_statuses: list[str]) -> str | None:
    """The status the order should take from its lines, or None if no line has received anything yet."""
    live = [s for s in line_statuses if s != "cancelled"]
    if live and all(s in ("received", "closed") for s in live):
        return "received"
    if any(s in ("received", "part_received", "closed") for s in live):
        return "part_received"
    return None


def stock_unit_cost(price: Decimal, qty: Decimal, base_qty: Decimal | None) -> Decimal:
    """The cost of one stock unit: the price of one purchase unit divided by the stock units in it."""
    if not base_qty or base_qty <= 0 or qty <= 0:
        return price.quantize(QTY, rounding=ROUND_HALF_UP)
    return (price * qty / base_qty).quantize(QTY, rounding=ROUND_HALF_UP)
