"""Tenant provisioning: one database per tenant, tracked in the control database."""

import os
import re
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, select, text
from sqlalchemy.engine import URL, Engine, make_url
from sqlalchemy.orm import Session

from p2p.config import get_settings
from p2p.platform import crypto
from p2p.platform.control_models import Tenant, TenantDatabase

MIGRATIONS = Path(__file__).parent / "migrations"
SLUG = re.compile(r"^[a-z][a-z0-9_]{1,30}$")


@dataclass
class MigrateResult:
    slug: str
    ok: bool
    revision: str | None = None
    error: str | None = None


def control_url() -> URL:
    return make_url(get_settings().control_database_url)


_control_engines: dict[str, Engine] = {}


def control_engine() -> Engine:
    """The one shared engine (with its connection pool) for the control database. Do not dispose it per call."""
    url = _render(control_url())
    if url not in _control_engines:
        _control_engines[url] = create_engine(url, pool_size=10, max_overflow=20, pool_pre_ping=True)
    return _control_engines[url]


def reset_control_engines() -> None:
    """Close every shared control connection (used when the database is dropped, as in tests)."""
    for engine in _control_engines.values():
        engine.dispose()
    _control_engines.clear()


@contextmanager
def control_session() -> Iterator[Session]:
    """A session on the shared control engine."""
    with Session(control_engine()) as session:
        yield session


def _config(kind: str, url: str, script_dir: Path | None = None) -> Config:
    cfg = Config()
    cfg.set_main_option("script_location", str(script_dir or MIGRATIONS / kind))
    cfg.attributes["url"] = url
    return cfg


def _render(url: URL) -> str:
    return url.render_as_string(hide_password=False)


def migrate_control() -> None:
    command.upgrade(_config("control", _render(control_url())), "head")


@contextmanager
def _admin_connection() -> Iterator["object"]:
    """Autocommit connection to the server's maintenance database (needed for CREATE DATABASE)."""
    engine = create_engine(control_url().set(database="postgres"), isolation_level="AUTOCOMMIT")
    try:
        with engine.connect() as conn:
            yield conn
    finally:
        engine.dispose()


def tenant_db_name(slug: str) -> str:
    # a second test or eval run on the same Postgres sets its own prefix, so the two never share a company database
    return f"{os.environ.get('P2P_TENANT_DB_PREFIX', 'p2p_t_')}{slug}"


def create_tenant(
    slug: str, name: str | None = None, *, script_dir: Path | None = None, load_reference: bool = True
) -> MigrateResult:
    if not SLUG.match(slug):
        raise ValueError("slug must be lower case letters, digits or underscore, starting with a letter (2-31 chars)")
    db_name = tenant_db_name(slug)
    engine = control_engine()
    with Session(engine) as s:
        if s.scalar(select(Tenant).where(Tenant.slug == slug)):
            raise ValueError(f"tenant '{slug}' already exists")
    with _admin_connection() as conn:
        conn.execute(text(f'create database "{db_name}"'))  # type: ignore[attr-defined]
    url = control_url().set(database=db_name)
    with Session(engine) as s:
        tenant = Tenant(slug=slug, name=name or slug)
        s.add(tenant)
        s.flush()
        s.add(
            TenantDatabase(
                tenant_id=tenant.id,
                database_name=db_name,
                connection_encrypted=crypto.encrypt(_render(url)),
            )
        )
        s.commit()
    result = migrate_tenant(slug, script_dir=script_dir)
    if result.ok and load_reference:
        from p2p.platform import reference

        engine_ = tenant_engine(slug)
        try:
            with Session(engine_) as ts:
                reference.load_reference(ts)
        finally:
            engine_.dispose()
    return result


def migrate_tenant(slug: str, *, script_dir: Path | None = None) -> MigrateResult:
    """Bring one tenant database to the latest revision. Never raises: failures come back in the result."""
    engine = control_engine()
    with Session(engine) as s:
        row = s.execute(
            select(TenantDatabase).join(Tenant, Tenant.id == TenantDatabase.tenant_id).where(Tenant.slug == slug)
        ).scalar_one()
        url = crypto.decrypt(row.connection_encrypted)
        try:
            command.upgrade(_config("tenant", url, script_dir), "head")
            tenant_engine = create_engine(url)
            try:
                with tenant_engine.connect() as c:
                    rev = MigrationContext.configure(c).get_current_revision()
            finally:
                tenant_engine.dispose()
        except Exception as exc:  # noqa: BLE001 - one tenant failing must not stop the others
            return MigrateResult(slug, False, error=f"{type(exc).__name__}: {str(exc).splitlines()[0][:200]}")
        row.schema_revision = rev
        row.migrated_at = datetime.now(UTC)
        s.commit()
        return MigrateResult(slug, True, revision=rev)


def migrate_all(*, script_dir: Path | None = None) -> list[MigrateResult]:
    engine = control_engine()
    with Session(engine) as s:
        slugs = list(s.scalars(select(Tenant.slug).where(Tenant.active).order_by(Tenant.slug)))
    return [migrate_tenant(slug, script_dir=script_dir) for slug in slugs]


def head_revision(script_dir: Path | None = None) -> str | None:
    return ScriptDirectory.from_config(_config("tenant", "", script_dir)).get_current_head()


def tenant_engine(slug: str) -> Engine:
    """Engine for one tenant's database (the caller disposes it or caches it)."""
    engine = control_engine()
    with Session(engine) as s:
        row = s.execute(
            select(TenantDatabase).join(Tenant, Tenant.id == TenantDatabase.tenant_id).where(Tenant.slug == slug)
        ).scalar_one()
        return create_engine(crypto.decrypt(row.connection_encrypted))


def drop_tenant(slug: str) -> bool:
    """Remove a company completely: its logins, jobs and flags in the control database, and its own database.

    Returns False when there was no such tenant. Only for a company that is made again from its data (the demo
    company); there is no undo.
    """
    engine = control_engine()
    with Session(engine) as s:
        tenant = s.scalar(select(Tenant).where(Tenant.slug == slug))
        if tenant is None:
            return False
        db_name = s.scalar(select(TenantDatabase.database_name).where(TenantDatabase.tenant_id == tenant.id))
        tid = tenant.id
        s.execute(
            text("delete from refresh_token where user_id in (select id from app_user where tenant_id = :t)"),
            {"t": tid},
        )
        for table in ("app_user", "feature_flag", "tenant_setting", "tenant_database"):
            s.execute(text(f"delete from {table} where tenant_id = :t"), {"t": tid})  # noqa: S608 - fixed table names
        s.execute(text("delete from job where tenant_slug = :s"), {"s": slug})
        s.execute(text("delete from tenant where id = :t"), {"t": tid})
        s.commit()
    from p2p.platform import routing

    routing.clear_engine_cache()
    if db_name:
        with _admin_connection() as conn:
            conn.execute(text(f'drop database if exists "{db_name}" with (force)'))  # type: ignore[attr-defined]
    return True
