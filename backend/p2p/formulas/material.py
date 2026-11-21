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


def releasable(need: Decimal, issued: Decimal, reserved: Decimal, requested: Decimal, open_cover: Decimal) -> Decimal:
    """Of what live purchase requests bring for a line, how much stock may take over: only cover on request lines not yet
    with the buyer (`open_cover`), never more than the line lacks once its uncovered part is counted."""
    lacking = max(need - issued - reserved, ZERO)
    return max(min(open_cover, lacking - still_short(need, issued, reserved, requested)), ZERO)


def allocate(wants: list[tuple[Decimal, Decimal]], free: Decimal) -> list[tuple[Decimal, Decimal]]:
    """Share the free stock of one item-location among lines in need-by order. Each want is (uncovered, releasable).

    First every line takes what no purchase request covers, earliest first; what is left then goes, earliest first, to
    the part that a request not yet with the buyer covers (that request is cut by as much). So a line is never both
    stocked and bought for, and the total set aside never exceeds what is free.
    Returns per line (taken for the uncovered part, taken over from a request).
    """
    left = max(free, ZERO)
    first: list[Decimal] = []
    for uncovered, _ in wants:
        take = to_reserve(uncovered, left)
        left -= take
        first.append(take)
    out: list[tuple[Decimal, Decimal]] = []
    for (_, covered), take in zip(wants, first, strict=True):
        over = to_reserve(covered, left)
        left -= over
        out.append((take, over))
    return out


def net_want(need: Decimal, issued: Decimal, reserved: Decimal, requested: Decimal, bought: Decimal) -> Decimal:
    """What a net line (the ERP already counted the stock) may set aside: only what arrived on what was bought for it
    and is not yet set aside or issued, never more than it still lacks."""
    arrived = max(bought - requested, ZERO)
    return max(min(arrived - reserved - issued, still_short(need, issued, reserved, requested)), ZERO)
