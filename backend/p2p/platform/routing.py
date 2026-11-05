"""Per-request tenant routing.

The tenant comes only from the signed login token. A request without a valid token gets no
database session at all, so there is no way to run a query without tenant context. Each tenant
has its own database, so a session for tenant A physically cannot see tenant B's rows.
"""

import threading
from collections import OrderedDict
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Annotated

import jwt
from fastapi import Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from p2p.platform import tenants
from p2p.platform.control_models import Tenant
from p2p.platform.tokens import decode_access_token

MAX_ENGINES = 20
_engines: OrderedDict[str, Engine] = OrderedDict()
_lock = threading.Lock()


@dataclass(frozen=True)
class TenantContext:
    slug: str
    user_id: int
    role: str


def clear_engine_cache() -> None:
    with _lock:
        for engine in _engines.values():
            engine.dispose()
        _engines.clear()


def engine_for(slug: str) -> Engine:
    with _lock:
        if slug in _engines:
            _engines.move_to_end(slug)
            return _engines[slug]
        engine = tenants.tenant_engine(slug)
        _engines[slug] = engine
        while len(_engines) > MAX_ENGINES:
            _, old = _engines.popitem(last=False)
            old.dispose()
        return engine


def _tenant_is_active(slug: str) -> bool:
    engine = tenants.control_engine()
    try:
        with Session(engine) as s:
            return bool(s.scalar(select(Tenant.active).where(Tenant.slug == slug)))
    finally:
        engine.dispose()


def tenant_context(request: Request) -> TenantContext:
    header = request.headers.get("authorization", "")
    scheme, _, token = header.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise HTTPException(401, "Login required")
    try:
        claims = decode_access_token(token)
    except jwt.PyJWTError:
        raise HTTPException(401, "Invalid or expired token") from None
    slug = str(claims["tenant"])
    if not _tenant_is_active(slug):
        raise HTTPException(403, "Unknown or inactive tenant")
    return TenantContext(slug=slug, user_id=int(claims["sub"]), role=str(claims["role"]))


def tenant_session(ctx: Annotated[TenantContext, Depends(tenant_context)]) -> Iterator[Session]:
    """A database session for the caller's own tenant, closed when the request ends."""
    session = Session(engine_for(ctx.slug))
    try:
        yield session
    finally:
        session.close()
