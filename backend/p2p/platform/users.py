"""Creating users: the login row in the control database, the profile in the tenant database."""

import contextlib
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from argon2 import PasswordHasher
from sqlalchemy import delete, func, select, text, update
from sqlalchemy.orm import Session

from p2p.platform import audit, routing, tenants
from p2p.platform.control_models import AppUser, Tenant
from p2p.platform.sod import Actor

hasher = PasswordHasher()
MIN_PASSWORD_LENGTH = 10
ROLES = ("Requester", "Approver", "Buyer", "Store", "AP", "Finance", "Admin")


ADMIN_BY_SYSTEM_ONLY = "add the person with another role, then give the Admin role: another administrator approves it"
REPAIR_AFTER = timedelta(minutes=10)  # a login without a profile younger than this may still be being made


def create_user(
    tenant_slug: str,
    email: str,
    password: str,
    display_name: str,
    role: str = "Requester",
    *,
    actor: Actor = audit.SYSTEM,
) -> int:
    """A login (control database) and its profile (the company's), written so that a failure never leaves a usable login
    without a profile (DF-36): the login is claimed unusable, the profile written, then the login made usable. If the
    profile cannot be written the login is removed again; if the process dies in between, `repair` finishes the job."""
    email = email.strip().lower()
    if "@" not in email:
        raise ValueError("not an email address")
    if len(password) < MIN_PASSWORD_LENGTH:
        raise ValueError(f"password must have at least {MIN_PASSWORD_LENGTH} characters")
    if role not in ROLES:
        raise ValueError(f"role must be one of {', '.join(ROLES)}")
    if role == "Admin" and actor != audit.SYSTEM:
        raise ValueError(ADMIN_BY_SYSTEM_ONLY)
    with Session(tenants.control_engine()) as s:
        tenant = s.scalar(select(Tenant).where(Tenant.slug == tenant_slug, Tenant.active))
        if tenant is None:
            raise ValueError(f"unknown tenant '{tenant_slug}'")
        if s.scalar(select(AppUser.id).where(AppUser.email == email)):
            raise ValueError("a user with this email already exists")
        user = AppUser(tenant_id=tenant.id, email=email, password_hash=hasher.hash(password), provisioned_at=None)
        s.add(user)
        s.commit()
        user_id = user.id
    try:
        with Session(routing.engine_for(tenant_slug)) as ts:
            ts.execute(
                text("insert into user_profile (user_id, display_name, role) values (:i, :n, :r)"),
                {"i": user_id, "n": display_name, "r": role},
            )
            ts.execute(text("insert into user_role (user_id, role) values (:i, :r)"), {"i": user_id, "r": role})
            audit.record(
                ts,
                actor,
                "user.create",
                "user",
                user_id,
                after={"email": email, "display_name": display_name, "role": role},
            )
            ts.commit()
    except BaseException:
        with (
            contextlib.suppress(Exception),
            Session(tenants.control_engine()) as s,
        ):  # best effort: repair does the rest
            s.execute(delete(AppUser).where(AppUser.id == user_id))
            s.commit()
        raise
    with Session(tenants.control_engine()) as s:
        s.execute(update(AppUser).where(AppUser.id == user_id).values(provisioned_at=func.now()))
        s.commit()
    return user_id


@dataclass(frozen=True)
class Fix:
    user_id: int
    email: str
    fixed: str  # provisioned, login_removed, or profile_without_login (reported, never deleted)


def repair(tenant_slug: str, *, now: datetime | None = None) -> list[Fix]:
    """Finish what a user creation that stopped half way left (DF-36): a login with a profile becomes usable; a login
    without a profile older than ten minutes is removed; a profile without a login is reported (the audit log points at
    it, so it stays). Each fix is a `user.repair` audit row."""
    now = now or datetime.now(UTC)
    with Session(tenants.control_engine()) as s:
        logins = {
            int(r[0]): (str(r[1]), r[2], r[3])
            for r in s.execute(
                select(AppUser.id, AppUser.email, AppUser.provisioned_at, AppUser.created_at)
                .join(Tenant, Tenant.id == AppUser.tenant_id)
                .where(Tenant.slug == tenant_slug)
            )
        }
    fixes: list[Fix] = []
    with Session(routing.engine_for(tenant_slug)) as ts:
        profiles = {int(r[0]) for r in ts.execute(text("select user_id from user_profile"))}
        for uid, (email, provisioned, created) in sorted(logins.items()):
            if provisioned is not None:
                continue
            if uid in profiles:
                fixes.append(Fix(uid, email, "provisioned"))
            elif created < now - REPAIR_AFTER:
                fixes.append(Fix(uid, email, "login_removed"))
        fixes += [Fix(uid, "", "profile_without_login") for uid in sorted(profiles - set(logins))]
        with Session(tenants.control_engine()) as s:
            for f in fixes:
                if f.fixed == "provisioned":
                    s.execute(update(AppUser).where(AppUser.id == f.user_id).values(provisioned_at=func.now()))
                elif f.fixed == "login_removed":
                    s.execute(text("delete from refresh_token where user_id = :u"), {"u": f.user_id})
                    s.execute(delete(AppUser).where(AppUser.id == f.user_id))
            s.commit()
        for f in fixes:
            if f.fixed != "profile_without_login":
                audit.record(
                    ts, audit.SYSTEM, "user.repair", "user", f.user_id, after={"fixed": f.fixed, "email": f.email}
                )
        ts.commit()
    return fixes


def user_id_by_email(tenant_slug: str, email: str) -> int | None:
    """The login of a company's user with this email, if there is one (used to link people in the organisation to logins)."""
    with Session(tenants.control_engine()) as s:
        return s.scalar(
            select(AppUser.id)
            .join(Tenant, Tenant.id == AppUser.tenant_id)
            .where(Tenant.slug == tenant_slug, AppUser.email == email.strip().lower())
        )


def grant_role(
    tenant_slug: str,
    user_id: int,
    role: str,
    *,
    location: str | None = None,
    department: str | None = None,
    actor: Actor = audit.SYSTEM,
) -> None:
    """Give a user another role, optionally limited to one location and department."""
    if role not in ROLES:
        raise ValueError(f"role must be one of {', '.join(ROLES)}")
    with Session(routing.engine_for(tenant_slug)) as ts:
        ts.execute(
            text("insert into user_role (user_id, role, scope_location, scope_department) values (:i, :r, :l, :d)"),
            {"i": user_id, "r": role, "l": location, "d": department},
        )
        audit.record(
            ts,
            actor,
            "role.grant",
            "user",
            user_id,
            after={"role": role, "location": location, "department": department},
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
    actor: Actor = audit.SYSTEM,
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
        audit.record(
            ts,
            actor,
            "limit.set",
            "user",
            user_id,
            after={"document": document, "max_value": max_value, "category": category, "location": location},
        )
        ts.commit()
