"""A throw-away environment for the evals: its own control database and its own company databases, dropped afterwards.

Nothing here touches the development data. The evals import the p2p package from ../backend.
"""

import contextlib
import os
import sys
import uuid
from collections.abc import Iterator
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

# the same defaults the tests use, so the evals run on a machine without secrets of its own
os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")
os.environ.setdefault("JWT_SECRET", "eval-secret-for-the-evals-only-0123456789abcdef")
from cryptography.fernet import Fernet

os.environ.setdefault("TENANT_SECRET_KEY", Fernet.generate_key().decode())

from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

CONTROL_DB = os.environ.get("P2P_EVAL_CONTROL_DB", "p2p_control_eval")


@contextlib.contextmanager
def environment() -> Iterator[str]:
    """Create the evals' control database, point the platform at it, and drop everything at the end."""
    from p2p.config import get_settings
    from p2p.platform import tenants
    from p2p.platform.routing import clear_engine_cache

    real_url = get_settings().control_database_url
    admin = create_engine(make_url(real_url).set(database="postgres"), isolation_level="AUTOCOMMIT")
    with admin.connect() as c:
        if c.execute(text("select 1 from pg_database where datname = :n"), {"n": CONTROL_DB}).first():
            # a run that was killed leaves its companies behind: drop them before the list of them is lost
            try:
                with create_engine(make_url(real_url).set(database=CONTROL_DB)).connect() as old:
                    for (name,) in old.execute(text("select database_name from tenant_database")):
                        c.execute(text(f'drop database if exists "{name}" with (force)'))
            except Exception:  # noqa: BLE001 - never migrated: nothing to drop
                pass
        c.execute(text(f'drop database if exists "{CONTROL_DB}" with (force)'))
        c.execute(text(f'create database "{CONTROL_DB}"'))
    url = make_url(real_url).set(database=CONTROL_DB).render_as_string(hide_password=False)
    previous = os.environ.get("CONTROL_DATABASE_URL")
    os.environ["CONTROL_DATABASE_URL"] = url
    get_settings.cache_clear()
    tenants.migrate_control()
    from datetime import date

    from p2p.formulas.calendar import pin_today

    pin_today(date(2026, 11, 6))  # the evals are written around Friday 6 November 2026
    import logging

    logging.disable(logging.INFO)  # no request log lines in the report
    try:
        yield url
    finally:
        pin_today(None)
        clear_engine_cache()
        tenants.reset_control_engines()
        with create_engine(url).connect() as c:
            names = [r[0] for r in c.execute(text("select database_name from tenant_database"))]
        with admin.connect() as c:
            for n in names:
                c.execute(text(f'drop database if exists "{n}" with (force)'))
            c.execute(text(f'drop database if exists "{CONTROL_DB}" with (force)'))
        admin.dispose()
        if previous is None:
            os.environ.pop("CONTROL_DATABASE_URL", None)
        else:
            os.environ["CONTROL_DATABASE_URL"] = previous
        get_settings.cache_clear()


def slug(prefix: str = "ev") -> str:
    return prefix + uuid.uuid4().hex[:8]


def forget_tenants(prefix: str) -> None:
    """Remove the companies an eval made under this prefix. The demo company's logins (buyer@apexpumps.example and so on) are
    unique across the whole control database, so two evals that make it cannot leave their companies behind."""
    from p2p.platform import tenants

    with tenants.control_engine().connect() as conn:
        slugs = [r[0] for r in conn.execute(text("select slug from tenant where slug like :p"), {"p": prefix + "%"})]
    for name in slugs:
        tenants.drop_tenant(name)
