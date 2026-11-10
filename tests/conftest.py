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

TEST_CONTROL_DB = os.environ.get("P2P_TEST_CONTROL_DB", "p2p_control_test")  # a parallel run sets its own

# Tests run with their own secrets so they work on a machine without a .env file.
os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")
# The document models are already on disk. Without this the model library asks its server whether they are up to date,
# and a connection that stalls half-open hangs the whole test run (it has, twice).
os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("HF_HUB_ETAG_TIMEOUT", "10")
os.environ.setdefault("HF_HUB_DOWNLOAD_TIMEOUT", "30")
os.environ.setdefault("JWT_SECRET", "test-secret-for-unit-tests-only-0123456789")
os.environ.setdefault("TENANT_SECRET_KEY", Fernet.generate_key().decode())


def _admin(url_text: str):  # type: ignore[no-untyped-def]
    return create_engine(make_url(url_text).set(database="postgres"), isolation_level="AUTOCOMMIT")


def _drop_leftover_tenants(admin_conn, real_url: str, control_db: str) -> None:  # type: ignore[no-untyped-def]
    """Drop the tenant databases a previous run left behind (its control database still lists them)."""
    exists = admin_conn.execute(text("select 1 from pg_database where datname = :n"), {"n": control_db}).first()
    if not exists:
        return
    with create_engine(make_url(real_url).set(database=control_db)).connect() as c:
        try:
            names = [r[0] for r in c.execute(text("select database_name from tenant_database"))]
        except Exception:  # noqa: BLE001 - the old control database was never migrated: nothing to drop
            names = []
    for n in names:
        admin_conn.execute(text(f'drop database if exists "{n}" with (force)'))


@pytest.fixture(scope="session")
def control_db() -> Iterator[str]:
    """A throw-away control database on the local Postgres; tenant databases made by tests are dropped too."""
    real_url = get_settings().control_database_url
    admin = _admin(real_url)
    try:
        with admin.connect() as c:
            _drop_leftover_tenants(c, real_url, TEST_CONTROL_DB)  # from a run that was killed before it could clean up
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


@pytest.fixture(autouse=True)
def _empty_job_queue(request: pytest.FixtureRequest) -> None:
    """Nothing runs the jobs that a test queues, so they would pile up and crowd the next test's worker run."""
    if "control_db" not in request.fixturenames:
        return
    request.getfixturevalue("control_db")
    with tenants.control_engine().begin() as c:
        c.execute(text("delete from job"))


@pytest.fixture(autouse=True)
def _fresh_rate_limits() -> None:
    """Every test starts with no failed logins or uploads counted against it."""
    from p2p.platform import ratelimit

    ratelimit.reset()


@pytest.fixture(autouse=True)
def _fixed_business_day() -> Iterator[None]:
    """The tests are written around Friday 6 November 2026. Pinning the business date keeps them from depending on the day
    they are run (a quote that was 6 days late yesterday must not be 7 days late today)."""
    from datetime import date

    from p2p.formulas.calendar import pin_today

    pin_today(date(2026, 11, 6))
    yield
    pin_today(None)
