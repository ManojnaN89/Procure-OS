"""Regions and the scope of supplier approvals and contract prices, read from the company's records (D44).

The rules are in formulas/scope.py; this module reads the rows and gives the SQL conditions the price and supplier
readers share, so a regional approval or price is never used outside its scope anywhere. The delivery location of a line
decides; every line to buy has one (the line's, else its request's ship-to).
"""

from datetime import date
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from p2p.formulas.scope import Scope, contains


def price_in_scope(alias: str, param: str = "loc") -> str:
    """SQL: the price record `alias` applies at the location bound to :param (everywhere, the location or its region).
    A reader that names no location (a report, not a line to buy) sees every price. Never null, so `not` of it is the
    prices that apply somewhere else."""
    return (
        f"coalesce(cast(:{param} as text) is null or ({alias}.location_code is null and {alias}.region_code is null) "  # noqa: S608
        f"or {alias}.location_code = :{param} "
        f"or {alias}.region_code = (select l.region_code from location l where l.code = :{param}), false)"
    )


def approved_in_scope(alias: str, param: str = "loc") -> str:
    """SQL: the supplier_category row `alias` (approval) covers the location bound to :param."""
    rows = (
        f"from supplier_category_scope x where x.supplier_code = {alias}.supplier_code "
        f"and x.category_code = {alias}.category_code"
    )
    return (
        f"(not exists (select 1 {rows}) or exists (select 1 {rows} and (x.location_code = :{param} "  # noqa: S608
        f"or x.region_code = (select l.region_code from location l where l.code = :{param}))))"
    )


def region_of(db: Session) -> dict[str, str | None]:
    return {str(r[0]): r[1] for r in db.execute(text("select code, region_code from location"))}


def names(db: Session) -> dict[str, str]:
    """Region and location codes to names, for the words on screen."""
    out = {str(r[0]): str(r[1]) for r in db.execute(text("select code, name from region"))}
    out.update({str(r[0]): str(r[1]) for r in db.execute(text("select code, name from location"))})
    return out


def regions(db: Session) -> list[dict[str, Any]]:
    """Every region with its locations."""
    out = []
    for code, name in db.execute(text("select code, name from region order by code")):
        locs = [
            {"code": str(r[0]), "name": str(r[1])}
            for r in db.execute(
                text("select code, name from location where region_code = :r order by code"), {"r": code}
            )
        ]
        out.append({"code": str(code), "name": str(name), "locations": locs})
    return out


def approval_scope(db: Session, supplier: str, category: str) -> Scope:
    rows = db.execute(
        text(
            "select location_code, region_code from supplier_category_scope where supplier_code = :s and category_code = :c"
        ),
        {"s": supplier, "c": category},
    ).all()
    return Scope.of((r[0] for r in rows), (r[1] for r in rows))


def approved_at(db: Session, supplier: str, category: str, location: str | None, as_of: date) -> bool:
    """Approved for the category on the date, at the location (D44)."""
    found = db.execute(
        text(
            "select 1 from supplier_category where supplier_code = :s and category_code = :c "
            "and (approved_from is null or approved_from <= :d)"
        ),
        {"s": supplier, "c": category, "d": as_of},
    ).first()
    return found is not None and contains(approval_scope(db, supplier, category), location, region_of(db))


def price_scope(row: dict[str, Any]) -> Scope:
    return Scope.of([row.get("location_code")], [row.get("region_code")])
