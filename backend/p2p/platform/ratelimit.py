"""Rate limits kept in the process's own memory (one API process; a second process would need a shared store).

* Failed logins: counted per address and per address-and-account. An address that fails too often is refused for a while
  before any password is checked, so guessing across many accounts, and making the server do password hashing for nothing,
  both stop. (A wrong password five times also locks that one account for 15 minutes; see auth.py.)
* Calls that cost money or time (uploads, and what is answered by a language model): a number per minute per signed-in person.

The numbers are settings (`login_ip_max_failures`, `login_ip_window_seconds`, `upload_max_per_minute`,
`model_calls_max_per_minute`). The address is the one the connection comes from; behind a proxy that must be the proxy's job.
"""

import threading
import time
from collections import defaultdict, deque
from collections.abc import Callable
from typing import Annotated, Any

from fastapi import Depends, HTTPException

from p2p.config import get_settings
from p2p.platform import routing

_lock = threading.Lock()
_hits: dict[str, deque[float]] = defaultdict(deque)
clock: Callable[[], float] = time.monotonic  # replaced in tests


def reset() -> None:
    with _lock:
        _hits.clear()


def _recent(key: str, window: float, now: float) -> deque[float]:
    q = _hits[key]
    while q and q[0] <= now - window:
        q.popleft()
    return q


def count(key: str, window: float) -> int:
    with _lock:
        return len(_recent(key, window, clock()))


def add(key: str) -> None:
    with _lock:
        _hits[key].append(clock())


def retry_after(key: str, window: float) -> int:
    """Whole seconds until the oldest counted hit leaves the window."""
    with _lock:
        now = clock()
        q = _recent(key, window, now)
        return max(1, int(q[0] + window - now) + 1) if q else 1


def _refuse(key: str, window: float, what: str) -> HTTPException:
    return HTTPException(
        429, f"Too many {what}; try again in a little while", headers={"Retry-After": str(retry_after(key, window))}
    )


# ---- failed logins --------------------------------------------------------------------------------


def _login_window() -> float:
    return float(get_settings().login_ip_window_seconds)


def check_login(address: str) -> None:
    """Refuse a login attempt from an address that has already failed too often."""
    s = get_settings()
    key = f"login-fail:{address}"
    if count(key, _login_window()) >= s.login_ip_max_failures:
        raise _refuse(key, _login_window(), "failed sign-ins from this address")


def login_failed(address: str, email: str) -> None:
    add(f"login-fail:{address}")
    add(f"login-fail:{address}:{email.strip().lower()}")


def login_ok(address: str, email: str) -> None:
    """A good password ends the count for that account from that address (the address's own count stands)."""
    with _lock:
        _hits.pop(f"login-fail:{address}:{email.strip().lower()}", None)


# ---- per person per minute --------------------------------------------------------------------------

LIMITS = {"upload": "upload_max_per_minute", "model": "model_calls_max_per_minute"}
WHAT = {"upload": "uploads", "model": "requests to the assistant"}


def take(bucket: str, who: str) -> None:
    """Count one call by this person and refuse it when they have made too many in the last minute."""
    limit = int(getattr(get_settings(), LIMITS[bucket]))
    key = f"{bucket}:{who}"
    if count(key, 60) >= limit:
        raise _refuse(key, 60, WHAT[bucket])
    add(key)


def limited(bucket: str) -> Any:
    """A route dependency: `dependencies=[ratelimit.limited('upload')]`."""

    def dependency(ctx: Annotated[routing.TenantContext, Depends(routing.tenant_context)]) -> None:
        take(bucket, f"{ctx.slug}:{ctx.user_id}")

    return Depends(dependency)
