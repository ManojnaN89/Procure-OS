"""Market benchmarks as evidence (D52): when one counts, and its price in another unit.

A benchmark counts only when it is verified, its effective date is on or before the evaluation date and no more than
the freshness limit before it (calendar days), its region is empty (national) or contains the scope it is compared for,
and its unit is the one compared or converts to it through a conversion row of the item. Anything else is shown with
the reason and triggers nothing. A benchmark is evidence only: no estimate, route, quote or order price reads it.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

from p2p.formulas.scope import Scope

FOUR = Decimal("0.0001")


@dataclass(frozen=True)
class Benchmark:
    id: int
    price: Decimal  # per uom, rupees, before GST
    uom: str
    effective_date: date
    region: str | None  # None: national
    status: str  # unverified, verified, rejected


@dataclass(frozen=True)
class Standing:
    counts: bool
    reason: str  # 'fresh', or why not: 'unverified', 'rejected', 'stale: 51 days old', 'other region', ...
    age_days: int


def age(b: Benchmark, on: date) -> int:
    return (on - b.effective_date).days


def covers(region: str | None, scope: Scope, region_of: Mapping[str, str | None]) -> bool:
    """A national benchmark covers everywhere; a regional one covers a scope that lies inside its region."""
    if region is None:
        return True
    if scope.everywhere:
        return False
    return all(r == region for r in scope.regions) and all(region_of.get(loc) == region for loc in scope.locations)


def factor_to(uom_from: str, uom_to: str, conversions: Mapping[tuple[str, str], Decimal]) -> Decimal | None:
    """How many `uom_to` one `uom_from` is, from the item's conversion rows (either direction); None if unknown."""
    if uom_from == uom_to:
        return Decimal(1)
    if (uom_from, uom_to) in conversions:
        return conversions[(uom_from, uom_to)]
    back = conversions.get((uom_to, uom_from))
    return Decimal(1) / back if back else None


def price_in(b: Benchmark, uom: str, conversions: Mapping[tuple[str, str], Decimal]) -> Decimal | None:
    """The benchmark's price per `uom` (4 places, half up), or None when the unit does not convert."""
    f = factor_to(uom, b.uom, conversions)  # one `uom` is f of the benchmark's unit
    if f is None:
        return None
    return (b.price * f).quantize(FOUR, rounding=ROUND_HALF_UP)


def standing(
    b: Benchmark,
    *,
    on: date,
    max_age_days: int,
    scope: Scope,
    region_of: Mapping[str, str | None],
    uom: str,
    conversions: Mapping[tuple[str, str], Decimal],
) -> Standing:
    """Whether the benchmark counts on the date for a price of `uom` applying in `scope`, and why not."""
    days = age(b, on)
    if b.status != "verified":
        return Standing(False, b.status, days)
    if days < 0:
        return Standing(False, "not in force yet", days)
    if days > max_age_days:
        return Standing(False, f"stale: {days} days old", days)
    if not covers(b.region, scope, region_of):
        return Standing(False, "other region", days)
    if factor_to(uom, b.uom, conversions) is None:
        return Standing(False, "unit not convertible", days)
    return Standing(True, "fresh", days)
