"""Shared pieces of E66 and E67 (block 0c): the two database logins as the block 0c spec names them (4.1, 4.4, 5), built
from the environment by the evals themselves, so the evals never ask the platform which login it uses.

The owner is the user in CONTROL_DATABASE_URL (evals/env.py points it at the evals' own control database). The app login
is APP_DATABASE_USER, else `<control database name>_app`, with APP_DATABASE_PASSWORD.
"""

import contextlib
import os
from collections.abc import Callable, Iterator
from typing import Any

from sqlalchemy import create_engine, text
from sqlalchemy.engine import URL, Connection, make_url
from sqlalchemy.pool import NullPool


def owner_url(database: str | None = None) -> URL:
    url = make_url(os.environ["CONTROL_DATABASE_URL"])
    return url.set(database=database) if database else url


def control_database() -> str:
    return str(owner_url().database)


def app_user() -> str:
    return os.environ.get("APP_DATABASE_USER") or f"{control_database()}_app"


def app_url(database: str | None = None) -> URL:
    return owner_url(database).set(username=app_user(), password=os.environ.get("APP_DATABASE_PASSWORD") or "")


@contextlib.contextmanager
def connect(url: URL) -> Iterator[Connection]:
    """One connection of its own (no pool), closed afterwards."""
    engine = create_engine(url, poolclass=NullPool)
    try:
        with engine.connect() as conn:
            yield conn
    finally:
        engine.dispose()


def tenant_database(slug: str) -> str:
    """The database name of a company, read by the owner from the control database."""
    with connect(owner_url()) as conn:
        return str(
            conn.execute(
                text(
                    "select d.database_name from tenant_database d join tenant t on t.id = d.tenant_id where t.slug = :s"
                ),
                {"s": slug},
            ).scalar_one()
        )


def sqlstate(exc: BaseException) -> str | None:
    orig = getattr(exc, "orig", exc)
    return getattr(orig, "sqlstate", None) or getattr(orig, "pgcode", None)


def message(exc: BaseException) -> str:
    return str(getattr(exc, "orig", exc)).strip().splitlines()[0] if str(getattr(exc, "orig", exc)).strip() else ""


def checker(out: Any) -> Callable[..., bool]:
    """check(ok, text, severity="high"): a finding when not ok; returns ok."""

    def check(ok: Any, finding: str, severity: str = "high") -> bool:
        if not ok:
            out.find(severity, finding)
        return bool(ok)

    return check
