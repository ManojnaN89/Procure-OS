"""The buying route of a request line (D37): plain comparisons of facts from our own records with the rule's thresholds.

The tests run in order and the first that passes decides: a contract with money left, a single-source item, an
emergency, a safe reorder from the known supplier, and otherwise a request for quotation. Every test is written down
with what it found, so the decision can be explained and repeated against the rule version it used.
"""

from collections.abc import Mapping
from dataclasses import dataclass, fields, replace
from decimal import Decimal
from typing import Any

from p2p.formulas.money import format_inr

D = Decimal
ROUTES = ("contract", "single_source", "emergency", "reorder", "rfq")
# For each threshold, which way is stricter: a category row can only move a threshold that way.
_STRICTER_IS_LOWER = {
    "agree_above",  # agreement needed from a lower value
    "reorder_within_days",  # the last purchase must be more recent
    "reorder_below",  # reorders only for smaller values
    "reorder_price_pct",  # less room for a price change
    "two_quotes_from",  # more quotes from a lower value
    "three_quotes_above",
}
_STRICTER_IS_HIGHER = {"reorder_on_time_pct", "window_days"}


@dataclass(frozen=True)
class RouteRule:
    version: str  # e.g. "buying_route v1" or "buying_route v1 + RAW v2"
    agree_above: Decimal  # single source above this 30-day value needs the purchase manager
    reorder_within_days: int  # a safe reorder needs a purchase this recent
    reorder_on_time_pct: Decimal  # ... from a supplier at least this punctual
    reorder_below: Decimal  # ... for a 30-day value under this
    reorder_price_pct: Decimal  # ... at a price this close to the last one
    two_quotes_from: Decimal  # a quotation request needs 2 quotes from this value
    three_quotes_above: Decimal  # ... and 3 above this one
    window_days: int  # the value of an item is counted over this many days (split-order protection)


def stricter(rule: RouteRule, override: Mapping[str, Any], version: str) -> RouteRule:
    """The rule with a category's thresholds applied, each only where it is stricter than the company's."""
    changes: dict[str, Any] = {"version": version}
    for f in fields(RouteRule):
        value = override.get(f.name)
        if f.name == "version" or value is None:
            continue
        current = getattr(rule, f.name)
        value = type(current)(value)
        if (f.name in _STRICTER_IS_LOWER and value < current) or (f.name in _STRICTER_IS_HIGHER and value > current):
            changes[f.name] = value
    return replace(rule, **changes)


_PCTS = ("reorder_on_time_pct", "reorder_price_pct")
_DAYS = ("reorder_within_days", "window_days")


def check_rule(values: Mapping[str, Any]) -> list[str]:
    """What is wrong with a set of thresholds, in words (empty when they are fine). A category row names only some."""
    given = {k: D(str(v)) for k, v in values.items() if v is not None}
    found: list[str] = []
    if any(v < 0 for v in given.values()):
        found.append("a threshold cannot be negative")
    if any(given.get(k, D(0)) > 100 for k in _PCTS):
        found.append("a percentage cannot be above 100")
    if any(k in given and (given[k] < 1 or given[k] != given[k].to_integral_value()) for k in _DAYS):
        found.append("a number of days is a whole number of at least 1")
    two, three = given.get("two_quotes_from"), given.get("three_quotes_above")
    if two is not None and three is not None and three <= two:
        found.append("the value above which three quotes are needed must be higher than the value for two quotes")
    return found


def quotes_needed(value: Decimal | None, rule: RouteRule) -> int:
    """Quotes a quotation request needs for a 30-day value: 1 below the first band, 2 up to and including the second
    band's start, 3 above it. An unknown value is treated as the middle band."""
    if value is None:
        return 2
    if value < rule.two_quotes_from:
        return 1
    return 2 if value <= rule.three_quotes_above else 3


def on_time_pct(on_time: int, judged: int) -> Decimal | None:
    """The share of deliveries on or before the promised date, as a percentage; None with nothing to judge."""
    return D(100) * on_time / judged if judged else None


def change_pct(new: Decimal, old: Decimal) -> Decimal:
    """How far a price moved from the last one, in percent (always positive)."""
    return abs(new - old) * 100 / old


@dataclass(frozen=True)
class Facts:
    """What the records say about one line. None means the record has nothing to say."""

    contract: str | None  # why a contract covers the line, or None
    contract_note: str  # why not, when it does not
    single_source: str | None  # the supplier the item is single-sourced from
    emergency: bool
    window_value: Decimal | None  # this line and the same item's other lines in the window
    last_supplier: str | None = None
    last_supplier_problem: str | None = None  # why the last supplier cannot be used now
    days_since_last: int | None = None
    on_time: Decimal | None = None  # percent
    price_change: Decimal | None = None  # percent against the last purchase


@dataclass(frozen=True)
class Check:
    test: str
    passed: bool
    detail: str

    def as_json(self) -> dict[str, Any]:
        return {"test": self.test, "passed": self.passed, "detail": self.detail}


@dataclass(frozen=True)
class Choice:
    route: str
    checks: tuple[Check, ...]
    needs_agreement: bool  # the purchase manager must agree before the order is made
    min_quotes: int  # for a quotation request; 0 otherwise

    @property
    def reason(self) -> str:
        """The test that decided (always the last one), then those before it that did not pass."""
        failed = [c.detail for c in self.checks if not c.passed]
        return self.checks[-1].detail + (f" (failed: {'; '.join(failed)})" if failed else "")


def _value(v: Decimal | None) -> str:
    return "unknown" if v is None else format_inr(v)


def _pct(v: Decimal | None) -> str:
    return "unknown" if v is None else f"{v.quantize(D('0.1'))}%"


def _num(v: Decimal) -> str:
    """A threshold as people write it: 80, 2.5 (never 8E+1 or 80.00)."""
    return f"{v.normalize():f}"


def reorder_checks(facts: Facts, rule: RouteRule) -> list[Check]:
    """The four tests of a safe reorder, each written down whether it passed or not."""
    days, value = facts.days_since_last, facts.window_value
    when = "never bought" if days is None else f"last bought from {facts.last_supplier} {days} days ago"
    problem = f", but {facts.last_supplier_problem}" if facts.last_supplier_problem else ""
    return [
        Check(
            "reorder.recent",
            days is not None and days <= rule.reorder_within_days and not facts.last_supplier_problem,
            f"{when} (within {rule.reorder_within_days}){problem}",
        ),
        Check(
            "reorder.on_time",
            facts.on_time is not None and facts.on_time >= rule.reorder_on_time_pct,
            f"on time {_pct(facts.on_time)} (at least {_num(rule.reorder_on_time_pct)}%)",
        ),
        Check(
            "reorder.value",
            value is not None and value < rule.reorder_below,
            f"{_value(value)} in {rule.window_days} days (under {format_inr(rule.reorder_below)})",
        ),
        Check(
            "reorder.price",
            facts.price_change is not None and facts.price_change <= rule.reorder_price_pct,
            f"price {_pct(facts.price_change)} from the last (within {_num(rule.reorder_price_pct)}%)",
        ),
    ]


def choose(facts: Facts, rule: RouteRule) -> Choice:
    """The route: the first test that passes, in the plan's order."""
    checks: list[Check] = []
    if facts.contract:
        return Choice("contract", (Check("contract", True, facts.contract),), False, 0)
    checks.append(Check("contract", False, facts.contract_note))
    if facts.single_source:
        agree = facts.window_value is None or facts.window_value > rule.agree_above
        detail = f"single-source item from {facts.single_source}" + (
            f", {_value(facts.window_value)} in {rule.window_days} days is above {format_inr(rule.agree_above)}: "
            "the purchase manager agrees"
            if agree
            else ""
        )
        return Choice("single_source", (*checks, Check("single_source", True, detail)), agree, 0)
    checks.append(Check("single_source", False, "not a single-source item"))
    if facts.emergency:
        detail = "emergency request: direct buy, the purchase manager agrees"
        return Choice("emergency", (*checks, Check("emergency", True, detail)), True, 0)
    checks.append(Check("emergency", False, "not an emergency"))
    reorder = reorder_checks(facts, rule)
    if all(c.passed for c in reorder):
        detail = "safe reorder: " + ", ".join(c.detail for c in reorder)
        return Choice("reorder", (*checks, Check("reorder", True, detail)), False, 0)
    checks.extend(reorder)
    need = quotes_needed(facts.window_value, rule)
    detail = f"quotation: {_value(facts.window_value)} in {rule.window_days} days needs {need} quote{'s' if need > 1 else ''}"
    return Choice("rfq", (*checks, Check("rfq", True, detail)), False, need)


def is_tighter(old_route: str, new_route: str) -> bool:
    """A buyer may always tighten a route; loosening it needs the purchase manager. A quotation request is the tightest
    route, so moving any line to quotation tightens it and moving a line away from quotation loosens it."""
    return new_route == "rfq" and old_route != "rfq"
