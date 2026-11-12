"""Signed links that need no login: the token carries a few plain values and a signature.

Each kind of link has its own key, made from the server's secret and the link's purpose, so a token made for one purpose
(an approval) is never accepted for another (a supplier's answer to an order). The values are readable by anyone who has
the token; the signature only proves they were not made up or altered. Expiry and "works once" are the caller's rules.
"""

import base64
import hashlib
import hmac
import json
from typing import Any

from p2p.config import get_settings


class SignatureError(Exception):
    """The token was not made by us for this purpose, or it was altered."""


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def _unb64(text_: str) -> bytes:
    return base64.urlsafe_b64decode(text_ + "=" * (-len(text_) % 4))


class Signer:
    def __init__(self, purpose: str) -> None:
        self.purpose = purpose

    def _key(self) -> bytes:
        secret = get_settings().jwt_secret
        if not secret:
            raise SignatureError(f"signed links ({self.purpose}) are not available: no signing secret is set")
        return hashlib.sha256(f"{self.purpose}:".encode() + secret.encode()).digest()

    def sign(self, values: list[Any]) -> str:
        body = _b64(json.dumps(values, separators=(",", ":")).encode())
        return f"{body}.{_b64(hmac.new(self._key(), body.encode(), hashlib.sha256).digest())}"

    def read(self, token: str) -> list[Any]:
        """The values a token carries. Raises SignatureError for anything altered or malformed."""
        key = self._key()
        try:
            body, signature = token.split(".")
            good = hmac.compare_digest(_unb64(signature), hmac.new(key, body.encode(), hashlib.sha256).digest())
            values = json.loads(_unb64(body))
        except Exception:  # noqa: BLE001 - whatever is wrong with the text, it is not a token we made
            raise SignatureError("this link is not valid") from None
        if not good or not isinstance(values, list):
            raise SignatureError("this link is not valid")
        return values
