"""Setting stock aside for material requests and what is left to buy. Plain arithmetic in the item's base unit."""

from decimal import Decimal

ZERO = Decimal(0)


def to_reserve(want: Decimal, free: Decimal) -> Decimal:
    """How much of the free stock a line takes: what it still wants, never more than is free, never below zero.

    Free stock is on hand less what is already set aside; when the books fall below what is set aside it is negative,
    and then nothing more is set aside.
    """
    return max(min(want, free), ZERO)


def still_short(need: Decimal, issued: Decimal, reserved: Decimal, requested: Decimal) -> Decimal:
    """What a line still lacks that no purchase request asks for yet: the need less what was issued, what is set aside and
    what live purchase requests already buy for it."""
    return max(need - issued - reserved - requested, ZERO)


def earmarked(need: Decimal, issued: Decimal, reserved: Decimal, requested: Decimal) -> Decimal:
    """The part of a line's purchase requests still to arrive for it: what it lacks after issue and stock set aside, up to
    what was requested. Stock on order for it is not free for other uses."""
    return max(min(requested, need - issued - reserved), ZERO)
