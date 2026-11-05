"""Creating users: the login row in the control database, the profile in the tenant database."""

from decimal import Decimal

from argon2 import PasswordHasher
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from p2p.platform import routing, tenants
from p2p.platform.control_models import AppUser, Tenant

hasher = PasswordHasher()
MIN_PASSWORD_LENGTH = 10
ROLES = ("Requester", "Approver", "Buyer", "Store", "AP", "Finance", "Admin")


def create_user(
    tenant_slug: str, email: str, password: str, display_name: str, role: str = "Requester"
) -> int:
    email = email.strip().lower()
    if "@" not in email:
        raise ValueError("not an email address")
    if len(password) < MIN_PASSWORD_LENGTH:
        raise ValueError(f"password must have at least {MIN_PASSWORD_LENGTH} characters")
    if role not in ROLES:
        raise ValueError(f"role must be one of {', '.join(ROLES)}")
    engine = tenants.control_engine()
    try:
        with Session(engine) as s:
            tenant = s.scalar(select(Tenant).where(Tenant.slug == tenant_slug, Tenant.active))
            if tenant is None:
                raise ValueError(f"unknown tenant '{tenant_slug}'")
            if s.scalar(select(AppUser.id).where(AppUser.email == email)):
                raise ValueError("a user with this email already exists")
            user = AppUser(tenant_id=tenant.id, email=email, password_hash=hasher.hash(password))
            s.add(user)
            s.flush()
            user_id = user.id
            with Session(routing.engine_for(tenant_slug)) as ts:
                ts.execute(
                    text("insert into user_profile (user_id, display_name, role) values (:i, :n, :r)"),
                    {"i": user_id, "n": display_name, "r": role},
                )
                ts.execute(
                    text("insert into user_role (user_id, role) values (:i, :r)"), {"i": user_id, "r": role}
                )
                ts.commit()
            s.commit()
            return user_id
    finally:
        engine.dispose()


def grant_role(
    tenant_slug: str, user_id: int, role: str, *, location: str | None = None, department: str | None = None
) -> None:
    """Give a user another role, optionally limited to one location and department."""
    if role not in ROLES:
        raise ValueError(f"role must be one of {', '.join(ROLES)}")
    with Session(routing.engine_for(tenant_slug)) as ts:
        ts.execute(
            text(
                "insert into user_role (user_id, role, scope_location, scope_department) "
                "values (:i, :r, :l, :d)"
            ),
            {"i": user_id, "r": role, "l": location, "d": department},
        )
        ts.commit()


def set_limit(
    tenant_slug: str,
    user_id: int,
    document: str,
    max_value: Decimal,
    *,
    category: str | None = None,
    location: str | None = None,
) -> None:
    """Set an approval limit for a user (FR42). Without a limit row the user cannot approve."""
    with Session(routing.engine_for(tenant_slug)) as ts:
        ts.execute(
            text(
                "insert into user_limit (user_id, document, category_code, location_code, max_value) "
                "values (:i, :d, :c, :l, :m)"
            ),
            {"i": user_id, "d": document, "c": category, "l": location, "m": max_value},
        )
        ts.commit()
