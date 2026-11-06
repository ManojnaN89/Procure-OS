import os
from collections.abc import Iterator

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import OperationalError

from p2p.config import get_settings
from p2p.platform import tenants
from p2p.platform.routing import clear_engine_cache

TEST_CONTROL_DB = "p2p_control_test"

# Tests run with their own secrets so they work on a machine without a .env file.
os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")
os.environ.setdefault("JWT_SECRET", "test-secret-for-unit-tests-only-0123456789")
os.environ.setdefault("TENANT_SECRET_KEY", Fernet.generate_key().decode())


def _admin(url_text: str):  # type: ignore[no-untyped-def]
    return create_engine(make_url(url_text).set(database="postgres"), isolation_level="AUTOCOMMIT")


@pytest.fixture(scope="session")
def control_db() -> Iterator[str]:
    """A throw-away control database on the local Postgres; tenant databases made by tests are dropped too."""
    real_url = get_settings().control_database_url
    admin = _admin(real_url)
    try:
        with admin.connect() as c:
            c.execute(text(f'drop database if exists "{TEST_CONTROL_DB}" with (force)'))
            c.execute(text(f'create database "{TEST_CONTROL_DB}"'))
    except OperationalError:
        pytest.skip("local Postgres is not running (docker compose up -d)")
    test_url = make_url(real_url).set(database=TEST_CONTROL_DB).render_as_string(hide_password=False)
    previous = os.environ.get("CONTROL_DATABASE_URL")
    os.environ["CONTROL_DATABASE_URL"] = test_url
    get_settings.cache_clear()
    tenants.migrate_control()
    try:
        yield test_url
    finally:
        clear_engine_cache()
        tenants.reset_control_engines()  # the shared pool points at a database that is about to be dropped
        with create_engine(test_url).connect() as c:
            names = [r[0] for r in c.execute(text("select database_name from tenant_database"))]
        with admin.connect() as c:
            for n in names:
                c.execute(text(f'drop database if exists "{n}" with (force)'))
            c.execute(text(f'drop database if exists "{TEST_CONTROL_DB}" with (force)'))
        admin.dispose()
        if previous is None:
            os.environ.pop("CONTROL_DATABASE_URL", None)
        else:
            os.environ["CONTROL_DATABASE_URL"] = previous
        get_settings.cache_clear()
