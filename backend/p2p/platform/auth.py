"""Login, refresh and logout. Wrong passwords five times in a row lock the account for 15 minutes."""

import hashlib
import secrets
from datetime import UTC, datetime, timedelta
from typing import Annotated

from argon2.exceptions import InvalidHashError, VerificationError
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select, text, update
from sqlalchemy.orm import Session

from p2p.platform import routing, tenants
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
    role: str
    display_name: str


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
def login(body: LoginIn) -> TokensOut:
    now = datetime.now(UTC)
    with Session(tenants.control_engine()) as s:
        user = s.scalar(select(AppUser).where(AppUser.email == body.email.strip().lower()))
        if user is None or not user.active:
            try:  # same work as a real check, so timing does not reveal which emails exist
                hasher.verify(_DUMMY_HASH, body.password)
            except VerificationError:
                pass
            raise HTTPException(401, "Wrong email or password")
        if user.locked_until and user.locked_until > now:
            raise HTTPException(423, "Account locked after repeated failures; try again later")
        try:
            hasher.verify(user.password_hash, body.password)
        except (VerificationError, InvalidHashError):
            user.failed_attempts += 1
            if user.failed_attempts >= MAX_FAILED:
                user.locked_until = now + timedelta(minutes=LOCK_MINUTES)
                user.failed_attempts = 0
            s.commit()
            raise HTTPException(401, "Wrong email or password") from None
        slug = s.scalar(select(Tenant.slug).where(Tenant.id == user.tenant_id, Tenant.active))
        if slug is None:
            raise HTTPException(403, "Tenant is not active")
        user.failed_attempts = 0
        user.locked_until = None
        return _issue(s, user, slug)


@router.post("/refresh")
def refresh(body: RefreshIn) -> TokensOut:
    now = datetime.now(UTC)
    with Session(tenants.control_engine()) as s:
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
        if user is None or not user.active or slug is None:
            raise HTTPException(401, "Invalid or expired refresh token")
        row.revoked_at = now
        return _issue(s, user, slug)


@router.post("/logout", status_code=204)
def logout(body: RefreshIn) -> None:
    with Session(tenants.control_engine()) as s:
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
    return MeOut(user_id=ctx.user_id, tenant=ctx.slug, role=ctx.role, display_name=str(name))
