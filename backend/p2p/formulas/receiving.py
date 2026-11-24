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


def needs_inspection(item_flag: bool, category_flag: bool) -> bool:
    """A line needs inspection when its item says so or its category does (plan 3.8, OP-3: either one)."""
    return bool(item_flag) or bool(category_flag)


@dataclass(frozen=True)
class ExcessSplit:
    counted: Decimal  # what may be received against the order
    excess: Decimal  # the rest of the goods, held for the buyer


def split_excess(good: Decimal, room: Decimal) -> ExcessSplit:
    """The good units a receipt brought split into what the order may still take and what is above it (C3, 4.2).

    Units rejected at the gate are not `good`: they went back. Nothing or less than the room is all counted.
    """
    if good < 0:
        raise ValueError("the quantity kept must not be negative")
    if room < 0:
        raise ValueError("the room left must not be negative")
    counted = min(good, room)
    return ExcessSplit(counted, good - counted)


def check_rejected_serials(
    rejected: Decimal, serials: list[str], received_serials: list[str], line_no: int
) -> list[str]:
    """The serial numbers of the units rejected, one for each, each among those received on the line (DF-31).

    Returns them trimmed and in capitals. Raises ValueError with the words the screen shows.
    """
    clean = [s.strip().upper() for s in serials if s and s.strip()]
    received = {s.strip().upper() for s in received_serials if s and s.strip()}
    if rejected != rejected.to_integral_value():
        raise ValueError(f"line {line_no}: a serialised item is rejected in whole units")
    units = int(rejected)
    if len(clean) != units:
        raise ValueError(f"line {line_no}: name the {count(units, 'serial number')} rejected, from those received")
    twice = sorted({s for s in clean if clean.count(s) > 1})
    if twice:
        raise ValueError(f"line {line_no}: given twice: {', '.join(twice)}")
    unknown = sorted(set(clean) - received)
    if unknown:
        raise ValueError(f"line {line_no}: not received on this line: {', '.join(unknown)}")
    return clean


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
