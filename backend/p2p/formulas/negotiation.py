"""The negotiation brief of a quoted line (task 11b.2, block 6): its figures, all worked out here.

The lowest counting offer for the whole quantity (L1) is compared with each reference: the last price paid for the
item at the place (with the threshold rule's minimum sources set to 1) and the market, the lowest counting benchmark.
Each is tested with the price-gap test (formulas/price_gap.py) with L1 in the place of the contract price: the gap is
per unit (4 places), the gap percent is on L1's price (4 places), the saving on the line's quantity (paise, half up).
A reference that passes every threshold triggers the brief. The target price is the lowest triggering reference (half
up to paise) and the saving at target = (L1 - target) x quantity (paise, half up). No trigger, no brief.
"""

from dataclasses import dataclass, replace
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

from p2p.formulas import price_gap

PAISE = Decimal("0.01")


@dataclass(frozen=True)
class Reference:
    kind: str  # last_paid or market
    price: Decimal
    on: date | None
    ref: str  # the order number, or the benchmark's source name
    ref_id: int | None = None  # the benchmark id


@dataclass(frozen=True)
class Trigger:
    reference: Reference
    gap: price_gap.Gap


@dataclass(frozen=True)
class Brief:
    triggers: list[Trigger]
    target: Decimal
    saving_at_target: Decimal


def _paise(value: Decimal) -> Decimal:
    return value.quantize(PAISE, rounding=ROUND_HALF_UP)


def brief(
    l1: Decimal,
    qty: Decimal,
    rule: price_gap.ThresholdRule,
    *,
    last_paid: Reference | None,
    markets: list[Reference],
) -> Brief | None:
    """The brief, or None when no reference triggers it."""
    triggers: list[Trigger] = []
    if last_paid is not None:
        gap = price_gap.test(l1, [last_paid.price], qty, replace(rule, min_sources=1))
        if gap.goes_ahead:
            triggers.append(Trigger(last_paid, gap))
    if markets:
        lowest = min(markets, key=lambda m: (m.price, m.ref_id or 0))
        gap = price_gap.test(l1, [m.price for m in markets], qty, rule)
        if gap.goes_ahead:
            triggers.append(Trigger(lowest, gap))
    if not triggers:
        return None
    target = _paise(min(t.reference.price for t in triggers))
    return Brief(triggers, target, _paise((l1 - target) * qty))
