"""Path A or B for a line a contract fits (D40 steps 3-5, block 6).

A fitting contract price is tested against the market at sourcing, on the line's own quantity (OP-18), with the
category's versioned threshold rule (formulas/price_gap.py). The line stays on the contract (route T or path A, as
before) when there is no counting benchmark, the gap is not above every threshold, or the need is urgent (OP-19: an
urgent need does not wait for an RFQ). Otherwise it is an A/B case: the line waits for the buyer, path B (ask for
quotes) recommended.
"""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from p2p.formulas import price_gap


@dataclass(frozen=True)
class PathTest:
    case: bool  # an A/B case: the line waits for the buyer
    gap: price_gap.Gap
    urgent: bool
    why: str


def test(
    contract_price: Decimal,
    markets: list[Decimal],
    qty: Decimal,
    rule: price_gap.ThresholdRule,
    *,
    priority: str | None,
    need_by: date | None,
    on: date,
    lead_days: int,
    rfq_days: int,
) -> PathTest:
    gap = price_gap.test(contract_price, markets, qty, rule)
    urgent = price_gap.urgent(priority, need_by, on, lead_days, rfq_days)
    if not gap.goes_ahead:
        return PathTest(False, gap, urgent, f"stays on the contract: {gap.why}")
    if urgent:
        return PathTest(False, gap, urgent, "stays on the contract: the need is urgent")
    return PathTest(True, gap, urgent, "the contract price is above the market beyond every threshold")
