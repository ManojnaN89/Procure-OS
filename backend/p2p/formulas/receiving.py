"""Rules for taking goods in. Plain arithmetic on quantities that come from the order and the receipts."""

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

from p2p.formulas.words import count

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
    """The status the order should take from its lines, or None if no line has received anything yet.

    A short-closed line counts like a received one: nothing more is coming for it.
    """
    live = [s for s in line_statuses if s != "cancelled"]
    if live and all(s in ("received", "closed", "short_closed") for s in live):
        return "received"
    if any(s in ("received", "part_received", "closed", "short_closed") for s in live):
        return "part_received"
    return None


BASES = ("qty", "pct", "amount")


@dataclass(frozen=True)
class ServiceConfirmed:
    qty: Decimal  # what counts as received on the order line, in the line's unit
    amount: Decimal  # its value at the order price, in rupees


def confirm_service(
    basis: str, value: Decimal, ordered: Decimal, unit_price: Decimal, room: Decimal
) -> ServiceConfirmed:
    """A service confirmed by quantity, by a percentage of the ordered quantity, or by amount (a lump-sum line).

    `room` is the quantity that may still be confirmed (formulas `room_left`). More than that is refused; for an amount
    the limit is the room at the order price, to the paisa.
    """
    if basis not in BASES:
        raise ValueError("the basis must be qty, pct or amount")
    if value <= 0:
        raise ValueError("the quantity, percentage or amount confirmed must be more than zero")
    if basis == "amount":
        if unit_price <= 0:
            raise ValueError("a line without a price cannot be confirmed by amount")
        amount = value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        most = (room * unit_price).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        if amount > most:
            raise ValueError(f"{amount} is more than the {most} that may still be confirmed")
        return ServiceConfirmed((amount / unit_price).quantize(QTY, rounding=ROUND_HALF_UP), amount)
    qty = value if basis == "qty" else ordered * value / 100
    qty = qty.quantize(QTY, rounding=ROUND_HALF_UP)
    if qty > room:
        raise ValueError(f"{qty} is more than the {room} that may still be confirmed")
    return ServiceConfirmed(qty, (qty * unit_price).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def check_serials(serials: list[str], units: Decimal) -> list[str]:
    """One serial number per unit of a serialised item, none given twice. Returns them trimmed and in capitals."""
    clean = [s.strip().upper() for s in serials if s and s.strip()]
    if units != units.to_integral_value():
        raise ValueError("a serialised item is received in whole units")
    if len(clean) != int(units):
        raise ValueError(f"{count(int(units), 'serial number')} needed, one per unit; {len(clean)} given")
    twice = sorted({s for s in clean if clean.count(s) > 1})
    if twice:
        raise ValueError("given twice: " + ", ".join(twice))
    return clean


def stock_unit_cost(price: Decimal, qty: Decimal, base_qty: Decimal | None) -> Decimal:
    """The cost of one stock unit: the price of one purchase unit divided by the stock units in it."""
    if not base_qty or base_qty <= 0 or qty <= 0:
        return price.quantize(QTY, rounding=ROUND_HALF_UP)
    return (price * qty / base_qty).quantize(QTY, rounding=ROUND_HALF_UP)
