"""Login, refresh and logout. Wrong passwords five times in a row lock the account for 15 minutes, and an address that
fails too often is refused for a while (ratelimit.py)."""

import hashlib
import secrets
from datetime import UTC, datetime, timedelta
from typing import Annotated

from argon2.exceptions import InvalidHashError, VerificationError
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import select, text, update
from sqlalchemy.orm import Session

from p2p.platform import company_state, permissions, ratelimit, routing, tenants
from p2p.platform.control_models import AppUser, RefreshToken, Tenant
from p2p.platform.tokens import create_access_token
from p2p.platform.users import hasher

MAX_FAILED = 5
LOCK_MINUTES = 15
REFRESH_DAYS = 7

router = APIRouter(prefix="/api/auth", tags=["auth"])
_DUMMY_HASH = hasher.hash("not-a-real-password")


class LoginIn(BaseModel):
    email: str
    password: str


class RefreshIn(BaseModel):
    refresh_token: str


class TokensOut(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"  # noqa: S105


class MeOut(BaseModel):
    user_id: int
    tenant: str
    tenant_name: str
    role: str
    display_name: str
    permissions: list[str]
    company_state: str


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _role_of(slug: str, user_id: int) -> str:
    with Session(routing.engine_for(slug)) as ts:
        role = ts.execute(text("select role from user_profile where user_id = :i"), {"i": user_id}).scalar()
    if role is None:
        raise HTTPException(403, "No profile in this tenant")
    return str(role)


def _issue(s: Session, user: AppUser, slug: str) -> TokensOut:
    raw = secrets.token_urlsafe(32)
    expires = datetime.now(UTC) + timedelta(days=REFRESH_DAYS)
    s.add(RefreshToken(user_id=user.id, token_hash=_hash(raw), expires_at=expires))
    s.commit()
    return TokensOut(access_token=create_access_token(user.id, slug, _role_of(slug, user.id)), refresh_token=raw)


@router.post("/login")
def login(body: LoginIn, request: Request) -> TokensOut:
    now = datetime.now(UTC)
    address = request.client.host if request.client else "unknown"
    ratelimit.check_login(address)
    with tenants.control_session() as s:
        user = s.scalar(select(AppUser).where(AppUser.email == body.email.strip().lower()))
        # a login whose profile was never written (DF-36) is refused exactly like a wrong password
        if user is None or not user.active or user.provisioned_at is None:
            try:  # same work as a real check, so timing does not reveal which emails exist
                hasher.verify(_DUMMY_HASH, body.password)
            except VerificationError:
                pass
            ratelimit.login_failed(address, body.email)
            raise HTTPException(401, "Wrong email or password")
        if user.locked_until and user.locked_until > now:
            ratelimit.login_failed(address, body.email)
            raise HTTPException(423, "Account locked after repeated failures; try again later")
        try:
            hasher.verify(user.password_hash, body.password)
        except (VerificationError, InvalidHashError):
            user.failed_attempts += 1
            if user.failed_attempts >= MAX_FAILED:
                user.locked_until = now + timedelta(minutes=LOCK_MINUTES)
                user.failed_attempts = 0
            s.commit()
            ratelimit.login_failed(address, body.email)
            raise HTTPException(401, "Wrong email or password") from None
        slug = s.scalar(select(Tenant.slug).where(Tenant.id == user.tenant_id, Tenant.active))
        if slug is None:
            raise HTTPException(403, "Tenant is not active")
        refused = company_state.allows_sign_in(slug, user.id)
        if refused:
            raise HTTPException(403, refused)
        user.failed_attempts = 0
        user.locked_until = None
        ratelimit.login_ok(address, body.email)
        return _issue(s, user, slug)


@router.post("/refresh")
def refresh(body: RefreshIn) -> TokensOut:
    now = datetime.now(UTC)
    with tenants.control_session() as s:
        row = s.scalar(select(RefreshToken).where(RefreshToken.token_hash == _hash(body.refresh_token)))
        if row is None or row.expires_at <= now:
            raise HTTPException(401, "Invalid or expired refresh token")
        if row.revoked_at is not None:
            # a used token came back: assume it leaked and end every session of this user
            s.execute(
                update(RefreshToken)
                .where(RefreshToken.user_id == row.user_id, RefreshToken.revoked_at.is_(None))
                .values(revoked_at=now)
            )
            s.commit()
            raise HTTPException(401, "Invalid or expired refresh token")
        user = s.get(AppUser, row.user_id)
        slug = s.scalar(select(Tenant.slug).where(Tenant.id == user.tenant_id, Tenant.active)) if user else None
        if user is None or not user.active or user.provisioned_at is None or slug is None:
            raise HTTPException(401, "Invalid or expired refresh token")
        refused = company_state.allows_sign_in(slug, user.id)
        if refused:
            raise HTTPException(403, refused)
        row.revoked_at = now
        return _issue(s, user, slug)


@router.post("/logout", status_code=204)
def logout(body: RefreshIn) -> None:
    with tenants.control_session() as s:
        s.execute(
            update(RefreshToken)
            .where(RefreshToken.token_hash == _hash(body.refresh_token), RefreshToken.revoked_at.is_(None))
            .values(revoked_at=datetime.now(UTC))
        )
        s.commit()


@router.get("/me")
def me(
    ctx: Annotated[routing.TenantContext, Depends(routing.tenant_context)],
    db: Annotated[Session, Depends(routing.tenant_session)],
) -> MeOut:
    name = db.execute(text("select display_name from user_profile where user_id = :i"), {"i": ctx.user_id}).scalar()
    if name is None:
        raise HTTPException(404, "No profile")
    actions = permissions.granted(db, ctx.user_id)
    with tenants.control_session() as s:
        found = s.execute(select(Tenant.name, Tenant.state).where(Tenant.slug == ctx.slug)).first()
    tenant_name, state = (str(found[0]), str(found[1])) if found else (ctx.slug, "live")
    return MeOut(
        user_id=ctx.user_id,
        tenant=ctx.slug,
        tenant_name=tenant_name,
        role=ctx.role,
        display_name=str(name),
        permissions=list(actions),
        company_state=state,
    )
