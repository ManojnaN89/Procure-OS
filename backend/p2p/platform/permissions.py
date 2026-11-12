"""Who may do what: role permissions with scope (FR42 lives here too: approval limits per user)."""

from collections.abc import Callable, Mapping
from decimal import Decimal
from typing import Annotated

from fastapi import Depends, HTTPException
from sqlalchemy import text
from sqlalchemy.orm import Session

from p2p.platform import sod
from p2p.platform.routing import TenantContext, tenant_context, tenant_session


class PermissionDenied(Exception):
    pass


def can(db: Session, user_id: int, action: str, *, location: str | None = None, department: str | None = None) -> bool:
    """True if one of the user's roles grants the action for this location and department.

    A role limited to a location or department only counts when the caller says which one is meant.
    """
    rows = db.execute(
        text(
            "select ur.scope_location, ur.scope_department from user_role ur "
            "join role_permission rp on rp.role = ur.role "
            "where ur.user_id = :u and rp.action = :a"
        ),
        {"u": user_id, "a": action},
    )
    return any((loc is None or loc == location) and (dep is None or dep == department) for loc, dep in rows)


def can_anywhere(db: Session, user_id: int, action: str) -> bool:
    """True if any role of the user grants the action, whatever its scope (used to gate whole screens)."""
    return bool(
        db.execute(
            text(
                "select 1 from user_role ur join role_permission rp on rp.role = ur.role "
                "where ur.user_id = :u and rp.action = :a limit 1"
            ),
            {"u": user_id, "a": action},
        ).first()
    )


def approval_limit(
    db: Session, user_id: int, document: str, *, category: str | None = None, location: str | None = None
) -> Decimal | None:
    """The user's limit for this document type; the most specific row wins. None means no limit is set."""
    rows = db.execute(
        text(
            "select category_code, location_code, max_value from user_limit "
            "where user_id = :u and document = :d "
            "and (category_code is null or category_code = :c) "
            "and (location_code is null or location_code = :l)"
        ),
        {"u": user_id, "d": document, "c": category, "l": location},
    ).all()
    if not rows:
        return None
    best = max(rows, key=lambda r: ((r[0] is not None) + (r[1] is not None), r[2]))
    return Decimal(best[2])


def within_limit(
    db: Session,
    user_id: int,
    document: str,
    value: Decimal,
    *,
    category: str | None = None,
    location: str | None = None,
) -> bool:
    """No limit row means the user may not approve: a limit has to be given on purpose."""
    limit = approval_limit(db, user_id, document, category=category, location=location)
    return limit is not None and value <= limit


def authorize_approval(
    db: Session,
    user_id: int,
    *,
    action: str,
    document: str,
    value: Decimal,
    duty: str,
    case_actors: Mapping[str, sod.Actor],
    category: str | None = None,
    location: str | None = None,
    department: str | None = None,
) -> None:
    """All three checks for an approval: role, value limit, segregation of duties. Raises PermissionDenied."""
    if not can(db, user_id, action, location=location, department=department):
        raise PermissionDenied(f"role does not allow {action} here")
    if not within_limit(db, user_id, document, value, category=category, location=location):
        raise PermissionDenied(f"value {value} is above the user's limit for {document}")
    try:
        sod.check_segregation(case_actors, duty, sod.user(user_id))
    except sod.SegregationError as exc:
        raise PermissionDenied(str(exc)) from None


def need_any(db: Session, user_id: int, *actions: str) -> None:
    """Refuse (403) unless one of the user's roles grants one of the actions."""
    if not any(can_anywhere(db, user_id, a) for a in actions):
        raise HTTPException(403, f"Not allowed: needs one of {', '.join(actions)}")


def require(action: str) -> Callable[..., TenantContext]:
    """FastAPI dependency: the caller needs a role that grants this action (anywhere in the tenant)."""

    def dependency(
        ctx: Annotated[TenantContext, Depends(tenant_context)],
        db: Annotated[Session, Depends(tenant_session)],
    ) -> TenantContext:
        if not can_anywhere(db, ctx.user_id, action):
            raise HTTPException(403, f"Not allowed: {action}")
        return ctx

    return dependency
