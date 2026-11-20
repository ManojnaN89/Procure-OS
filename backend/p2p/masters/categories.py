"""Approving a supplier for a category, and where (D44, task 12.4).

A supplier is approved for a category from a date, everywhere or only at some locations and regions. Changing the
approval or its scope is a proposal (`supplier.approve_category`): a person with `supplier.activate` approves it, never
the one who asked. Orders already made keep their price and supplier; the new scope applies to what is decided next.
"""

from datetime import date
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from p2p.formulas.scope import Scope
from p2p.formulas.scope import label as scope_label
from p2p.masters import regions
from p2p.platform import audit, proposals
from p2p.platform.sod import Actor

ACTION = "supplier.approve_category"


class CategoryError(Exception):
    def __init__(self, message: str, status: int = 422) -> None:
        super().__init__(message)
        self.status = status


def categories_of(db: Session, supplier: str) -> list[dict[str, Any]]:
    """The categories a supplier is approved for, from when and where."""
    words = regions.names(db)
    out = []
    for code, start in db.execute(
        text("select category_code, approved_from from supplier_category where supplier_code = :s order by 1"),
        {"s": supplier},
    ):
        scope = regions.approval_scope(db, supplier, str(code))
        out.append(
            {
                "code": str(code),
                "approved_from": start.isoformat() if start else None,
                "scope": {"locations": sorted(scope.locations), "regions": sorted(scope.regions)},
                "where": scope_label(scope, words),
            }
        )
    return out


def _check(db: Session, supplier: str, category: str, scope: Scope) -> str:
    status = db.execute(text("select status from supplier where code = :c"), {"c": supplier}).scalar()
    if status is None:
        raise CategoryError(f"supplier {supplier} does not exist", 404)
    if db.execute(text("select 1 from item_category where code = :c"), {"c": category}).first() is None:
        raise CategoryError(f"there is no category {category}")
    for table, codes in (("location", scope.locations), ("region", scope.regions)):
        known = {str(r[0]) for r in db.execute(text(f"select code from {table}"))}  # noqa: S608 - fixed names
        if unknown := sorted(codes - known):
            raise CategoryError(f"there is no {table} {', '.join(unknown)}")
    return str(status)


def _waiting(db: Session, supplier: str, category: str) -> int | None:
    found = db.execute(
        text(
            "select id from proposal where action = :a and status = 'pending' and payload->>'supplier' = :s "
            "and payload->>'category' = :c"
        ),
        {"a": ACTION, "s": supplier, "c": category},
    ).scalar()
    return None if found is None else int(found)


def propose(
    db: Session, actor: Actor, supplier: str, category: str, approved_from: date, scope: Scope, reason: str
) -> int:
    """Ask for the approval (or a new scope); nothing changes until another person approves it."""
    if not reason.strip():
        raise CategoryError("a reason is needed")
    _check(db, supplier, category, scope)
    if (waiting := _waiting(db, supplier, category)) is not None:
        raise CategoryError(f"a change to {supplier}'s approval for {category} is waiting (proposal {waiting})", 409)
    where = scope_label(scope, regions.names(db))
    return proposals.propose(
        db,
        actor,
        action=ACTION,
        summary=f"Approve {supplier} for {category} from {approved_from:%d %b %Y}, {where}. Reason: {reason.strip()}",
        payload={
            "supplier": supplier,
            "category": category,
            "approved_from": approved_from.isoformat(),
            "locations": sorted(scope.locations),
            "regions": sorted(scope.regions),
            "reason": reason.strip(),
        },
        required_permission="supplier.activate",
    )


@proposals.executor(ACTION)
def _apply(db: Session, payload: dict[str, Any], actor: Actor) -> dict[str, Any]:
    supplier, category = str(payload["supplier"]), str(payload["category"])
    scope = Scope.of(payload.get("locations") or [], payload.get("regions") or [])
    _check(db, supplier, category, scope)
    before = next((c for c in categories_of(db, supplier) if c["code"] == category), None)
    db.execute(
        text(
            "insert into supplier_category (supplier_code, category_code, approved_from) values (:s, :c, :f) "
            "on conflict (supplier_code, category_code) do update set approved_from = :f"
        ),
        {"s": supplier, "c": category, "f": date.fromisoformat(str(payload["approved_from"]))},
    )
    db.execute(
        text("delete from supplier_category_scope where supplier_code = :s and category_code = :c"),
        {"s": supplier, "c": category},
    )
    rows = [{"s": supplier, "c": category, "loc": loc, "reg": None} for loc in sorted(scope.locations)]
    rows += [{"s": supplier, "c": category, "loc": None, "reg": reg} for reg in sorted(scope.regions)]
    if rows:
        db.execute(
            text(
                "insert into supplier_category_scope (supplier_code, category_code, location_code, region_code) "
                "values (:s, :c, :loc, :reg)"
            ),
            rows,
        )
    after = next(c for c in categories_of(db, supplier) if c["code"] == category)
    audit.record(
        db,
        actor,
        "supplier.category_scope",
        "supplier",
        supplier,
        before=before,
        after=after,
        reason=payload.get("reason"),
    )
    db.commit()
    return {"supplier": supplier, "category": after}
