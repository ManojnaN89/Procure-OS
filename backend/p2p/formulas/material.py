"""Setting stock aside for material requests and what is left to buy. Plain arithmetic in the item's base unit."""

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from typing import Any

from p2p.formulas.calendar import BusinessCalendar

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


# ---- safety stock (C4) ------------------------------------------------------------------------------


def free_stock(on_hand: Decimal, reserved: Decimal, safety: Decimal | None) -> Decimal:
    """What stock is free to set aside or issue: usable on hand less what is set aside and less the safety stock. It may be
    negative (safety stock raised after stock was set aside); nothing is then taken from it."""
    return on_hand - reserved - (safety or ZERO)


# ---- time-phased cover (rule Q17) -------------------------------------------------------------------


@dataclass(frozen=True)
class Part:
    """Supply still to come on one document line, in the stock unit, dated as rule Q17 says."""

    qty: Decimal
    date: date
    ref: Any = None  # what the caller needs to name it (document, line, kind of date)


def latest_arrival(need_by: date, buffer_days: int, calendar: BusinessCalendar) -> date:
    """The last day supply may arrive for a need: the need-by moved back `buffer_days` working days on the place's
    calendar. A buffer of 0 is the need-by itself, whatever day it is."""
    day = need_by
    for _ in range(buffer_days):
        day -= timedelta(days=1)
        while not calendar.is_working_day(day):
            day -= timedelta(days=1)
    return day


def supply_date(promised: date | None, asked: date, today: date) -> date:
    """When a supply part is expected: the promise, else the date asked for; a date already passed with the goods not in
    counts as today."""
    return max(promised or asked, today)


def time_phase(parts: list[Part], wants: list[Decimal]) -> list[list[Part]]:
    """Share the parts still to come on one request line among the material request lines it buys for: earliest part
    first, to the lines in their need-by order, each up to what is still coming for it (`wants`). Returns the parts per
    line. A line wanting more than the parts hold gets nothing more here."""
    queue = sorted(parts, key=lambda p: p.date)
    left = [p.qty for p in queue]
    out: list[list[Part]] = []
    i = 0
    for want in wants:
        got: list[Part] = []
        need = max(want, ZERO)
        while need > 0 and i < len(queue):
            take = min(need, left[i])
            if take > 0:
                got.append(Part(take, queue[i].date, queue[i].ref))
                need -= take
                left[i] -= take
            if left[i] <= 0:
                i += 1
        out.append(got)
    return out


@dataclass(frozen=True)
class Cover:
    """A material request line's cover split by date (stock unit)."""

    lacking: Decimal  # need less issued less set aside
    timely: Decimal  # still coming by the latest arrival
    late: Decimal  # still coming after it
    at_risk: Decimal  # of what is lacking, what only late supply covers
    short: Decimal  # of what is lacking, what nothing covers


def cover(need: Decimal, issued: Decimal, reserved: Decimal, timely: Decimal, late: Decimal) -> Cover:
    """Rule Q17 for one line: supply counts for the need only when it comes by the latest arrival; what only late supply
    covers is at risk, and what nothing covers is short (it is bought; the late part is not bought again)."""
    lacking = max(need - issued - reserved, ZERO)
    at_risk = min(late, max(lacking - timely, ZERO))
    short = max(lacking - timely - late, ZERO)
    return Cover(lacking, timely, late, at_risk, short)


def releasable_timed(c: Cover, open_cover: Decimal) -> Decimal:
    """Of the cover requests not yet with the buyer bring, how much stock may take over once the line has what is short
    and at risk: never more than the line still lacks after that."""
    return max(min(open_cover, c.lacking - c.short - c.at_risk), ZERO)


# ---- why a line is short (the planning capability explains these, it never works them out) --------


CAUSES = ("covered", "held_for_earlier", "held_quality", "late_supply", "no_stock", "on_order", "safety_stock", "short")


def causes(
    c: Cover,
    *,
    need: Decimal,
    issued: Decimal,
    reserved: Decimal,
    usable: Decimal,
    set_aside_elsewhere: Decimal,
    safety: Decimal,
    held: Decimal,
) -> list[str]:
    """The engine's reasons for a line, sorted: covered (issued and set aside reach the need), on_order (timely supply),
    late_supply (something at risk), short (nothing covers part of it); and, for a line still lacking, no_stock (no usable
    stock at the place), held_for_earlier (usable stock there is set aside for other lines), safety_stock (stock is free
    before the safety stock but not after it), held_quality (quarantine or blocked stock there)."""
    found: set[str] = set()
    if issued + reserved >= need:
        found.add("covered")
    if c.timely > 0:
        found.add("on_order")
    if c.at_risk > 0:
        found.add("late_supply")
    if c.short > 0:
        found.add("short")
    if c.lacking > 0:
        free_before = usable - set_aside_elsewhere - reserved
        if usable <= 0:
            found.add("no_stock")
        if set_aside_elsewhere > 0:
            found.add("held_for_earlier")
        if free_before > 0 and free_before - safety <= 0:
            found.add("safety_stock")
        if held > 0:
            found.add("held_quality")
    return sorted(found)
