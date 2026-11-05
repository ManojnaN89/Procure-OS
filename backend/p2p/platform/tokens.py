"""Short-lived access tokens.

The tenant and role travel inside the token, so every request knows whose data it may touch.
"""

from datetime import UTC, datetime, timedelta
from typing import Any

import jwt

from p2p.config import get_settings

ALGORITHM = "HS256"


def _secret() -> str:
    secret = get_settings().jwt_secret
    if len(secret) < 16:
        raise RuntimeError("JWT_SECRET is missing or too short")
    return secret


def create_access_token(user_id: int, tenant: str, role: str, *, minutes: int = 15) -> str:
    now = datetime.now(UTC)
    claims = {
        "sub": str(user_id),
        "tenant": tenant,
        "role": role,
        "iat": now,
        "exp": now + timedelta(minutes=minutes),
    }
    return jwt.encode(claims, _secret(), algorithm=ALGORITHM)


def decode_access_token(token: str) -> dict[str, Any]:
    """Raises jwt.PyJWTError for a bad signature, an expired token or missing claims."""
    claims: dict[str, Any] = jwt.decode(
        token, _secret(), algorithms=[ALGORITHM], options={"require": ["exp", "sub", "tenant", "role"]}
    )
    return claims
