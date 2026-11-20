"""Regional scope (D44): where a supplier approval or a contract price applies.

A scope is 'everywhere' (no locations and no regions) or a set of locations and regions. A location is inside it when
it is listed or its region is listed. Two scopes intersect when some location could be inside both: everywhere meets
every scope, a region meets its own locations. Pure functions on plain values.
"""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass


@dataclass(frozen=True)
class Scope:
    locations: frozenset[str] = frozenset()
    regions: frozenset[str] = frozenset()

    @property
    def everywhere(self) -> bool:
        return not self.locations and not self.regions

    @classmethod
    def of(cls, locations: Iterable[str | None] = (), regions: Iterable[str | None] = ()) -> "Scope":
        return cls(frozenset(x for x in locations if x), frozenset(x for x in regions if x))


EVERYWHERE = Scope()


def contains(scope: Scope, location: str | None, region_of: Mapping[str, str | None]) -> bool:
    """Is the location inside the scope? An unknown location is inside only 'everywhere'."""
    if scope.everywhere:
        return True
    if location is None:
        return False
    return location in scope.locations or (region_of.get(location) or "") in scope.regions


def intersects(a: Scope, b: Scope, region_of: Mapping[str, str | None]) -> bool:
    """Could one location be inside both scopes?"""
    if a.everywhere or b.everywhere:
        return True
    if a.locations & b.locations or a.regions & b.regions:
        return True
    return any(region_of.get(loc) in b.regions for loc in a.locations) or any(
        region_of.get(loc) in a.regions for loc in b.locations
    )


def label(scope: Scope, names: Mapping[str, str] | None = None) -> str:
    """'everywhere', or the regions and locations in words."""
    if scope.everywhere:
        return "everywhere"
    names = names or {}
    return ", ".join(names.get(c, c) for c in sorted(scope.regions) + sorted(scope.locations))
