"""The price-gap test of a contract price against the market (D41, task 12.5).

Market price = the lowest counting benchmark in the contract's unit. Gap in rupees = contract - market per unit (4
places); gap % = gap / contract price x 100 (4 places, half up; the contract price is the denominator); saving = gap x
the relevant quantity (paise, half up). A review goes ahead only when all of: gap % above the rule's percent, gap above
its rupees per unit, saving above its minimum, and at least its number of counting benchmarks. 'Above' is strict.
The thresholds are a recommended default that needs validating with the company (O2).
"""

from dataclasses import dataclass, field
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

FOUR = Decimal("0.0001")
PAISE = Decimal("0.01")
HUNDRED = Decimal(100)


@dataclass(frozen=True)
class ThresholdRule:
    version: str  # e.g. 'price_threshold v1'
    gap_pct: Decimal
    gap_inr_per_unit: Decimal
    min_saving_inr: Decimal
    evidence_max_age_days: int
    min_sources: int
    expiry_review_days: int


@dataclass(frozen=True)
class Gap:
    market: Decimal | None  # per unit of the contract price
    gap_inr: Decimal | None  # per unit
    gap_pct: Decimal | None
    saving: Decimal | None
    sources: int
    goes_ahead: bool
    tests: dict[str, bool] = field(default_factory=dict)  # each test and whether it passed
    why: str = ""


def gap_pct(contract: Decimal, market: Decimal) -> Decimal:
    return ((contract - market) / contract * HUNDRED).quantize(FOUR, rounding=ROUND_HALF_UP)


def test(contract: Decimal, markets: list[Decimal], relevant_qty: Decimal, rule: ThresholdRule) -> Gap:
    """The test on counting benchmark prices (already in the contract's unit)."""
    if not markets or contract <= 0:
        return Gap(None, None, None, None, len(markets), False, {}, "no counting benchmark: no market claim")
    market = min(markets)
    gap = (contract - market).quantize(FOUR, rounding=ROUND_HALF_UP)
    pct = gap_pct(contract, market)
    saving = (gap * relevant_qty).quantize(PAISE, rounding=ROUND_HALF_UP)
    tests = {
        "gap_pct": pct > rule.gap_pct,
        "gap_inr_per_unit": gap > rule.gap_inr_per_unit,
        "min_saving_inr": saving > rule.min_saving_inr,
        "min_sources": len(markets) >= rule.min_sources,
    }
    failed = [k for k, ok in tests.items() if not ok]
    why = "above every threshold" if not failed else "not above: " + ", ".join(failed)
    return Gap(market, gap, pct, saving, len(markets), not failed, tests, why)


def stricter(base: ThresholdRule, row: dict[str, object], label: str) -> ThresholdRule:
    """A category row may only make the company rule stricter: higher limits never apply."""

    def pick(key: str, current: Decimal) -> Decimal:
        value = row.get(key)
        return min(current, Decimal(str(value))) if value is not None else current

    def pick_int(key: str, current: int, stricter_is_lower: bool) -> int:
        value = row.get(key)
        if value is None:
            return current
        return min(current, int(str(value))) if stricter_is_lower else max(current, int(str(value)))

    return ThresholdRule(
        label,
        pick("gap_pct", base.gap_pct),
        pick("gap_inr_per_unit", base.gap_inr_per_unit),
        pick("min_saving_inr", base.min_saving_inr),
        pick_int("evidence_max_age_days", base.evidence_max_age_days, True),
        pick_int("min_sources", base.min_sources, False),
        pick_int("expiry_review_days", base.expiry_review_days, False),
    )


def expiry_due(valid_to: date | None, on: date, days: int) -> bool:
    """A contract within `days` calendar days of its end (the end day counted) opens an expiry review."""
    return valid_to is not None and 0 <= (valid_to - on).days <= days


def urgent(priority: str | None, need_by: date | None, on: date, lead_days: int, rfq_days: int) -> bool:
    """An urgent need stays on a fitting contract whatever the gap (Rev line 231): an urgent or emergency line, or one
    needed sooner than the lead time plus the time an RFQ takes. Built now; used at the operational route (OP-16)."""
    if priority in ("urgent", "emergency"):
        return True
    return need_by is not None and (need_by - on).days < lead_days + rfq_days
