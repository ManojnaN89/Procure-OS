import os
import secrets
from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager, contextmanager

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from p2p.config import get_settings
from p2p.platform import tenants
from p2p.platform.routing import clear_engine_cache
from p2p.platform.scratch_db import ScratchControlDb, ScratchDatabaseInUse

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
# The run's own app login, named after its control database: a .env value never makes a test run change the dev login.
os.environ["APP_DATABASE_USER"] = f"{TEST_CONTROL_DB}_app"
os.environ.setdefault("APP_DATABASE_PASSWORD", secrets.token_urlsafe(24))


_scratch: ScratchControlDb | None = None


@pytest.fixture(scope="session")
def control_db() -> Iterator[str]:
    """A throw-away control database on the local Postgres; tenant databases made by tests are dropped too."""
    global _scratch
    scratch = ScratchControlDb(get_settings().control_database_url, TEST_CONTROL_DB)
    try:
        scratch.create()
    except OperationalError:
        pytest.skip("local Postgres is not running (docker compose up -d)")
    except ScratchDatabaseInUse as exc:
        pytest.exit(str(exc), returncode=3)
    _scratch = scratch
    previous = os.environ.get("CONTROL_DATABASE_URL")
    os.environ["CONTROL_DATABASE_URL"] = scratch.url
    get_settings.cache_clear()
    tenants.migrate_control()
    try:
        yield scratch.url
    finally:
        clear_engine_cache()
        tenants.reset_control_engines()  # the shared pool points at a database that is about to be dropped
        scratch.drop()
        _scratch = None
        if previous is None:
            os.environ.pop("CONTROL_DATABASE_URL", None)
        else:
            os.environ["CONTROL_DATABASE_URL"] = previous
        get_settings.cache_clear()


@contextmanager
def _owner_session(slug: str) -> Iterator[Session]:
    engine = tenants.owner_engine(slug)
    try:
        with Session(engine) as session:
            yield session
    finally:
        engine.dispose()


@pytest.fixture
def owner_session(control_db: str) -> Callable[[str], AbstractContextManager[Session]]:
    """A session on a company's database as its owner: for the few tests that do what only the owner can (switch the
    insert-only triggers off, create a table). The app itself never signs in as the owner."""
    return _owner_session


@pytest.fixture(autouse=True)
def _empty_job_queue(request: pytest.FixtureRequest) -> None:
    """Nothing runs the jobs that a test queues, so they would pile up and crowd the next test's worker run."""
    if "control_db" not in request.fixturenames:
        return
    request.getfixturevalue("control_db")
    try:
        with tenants.control_engine().begin() as c:
            c.execute(text("delete from job"))
    except OperationalError:
        if _scratch is not None and not _scratch.exists():  # stop once, not with an error in every test that follows
            pytest.exit(
                f"the test database {TEST_CONTROL_DB} was dropped by another process during the run", returncode=3
            )
        raise


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
