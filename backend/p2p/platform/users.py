"""Creating users: the login row in the control database, the profile in the tenant database."""

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
                ts.commit()
            s.commit()
            return user_id
    finally:
        engine.dispose()
