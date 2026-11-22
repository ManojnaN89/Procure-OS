"""Tenant provisioning: one database per tenant, tracked in the control database.

Two database logins (`db_logins`): the owner creates, migrates and drops; every runtime engine here signs in as the app.
"""

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
from sqlalchemy import Connection, create_engine, select, text
from sqlalchemy.engine import URL, Engine, make_url
from sqlalchemy.orm import Session

from p2p.config import get_settings
from p2p.platform import crypto, db_logins
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
    """The one shared engine (with its connection pool) for the control database, as the app login. Do not dispose it per
    call."""
    url = _render(db_logins.app_url(control_url()))
    if url not in _control_engines:
        _control_engines[url] = create_engine(url, pool_size=4, max_overflow=16, pool_pre_ping=True)
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


@contextmanager
def _owner_control_session() -> Iterator[Session]:
    """A session on the control database as the owner: only for creating, migrating and dropping companies."""
    engine = create_engine(control_url())
    try:
        with Session(engine) as session:
            yield session
    finally:
        engine.dispose()


def _config(kind: str, url: str, script_dir: Path | None = None) -> Config:
    cfg = Config()
    cfg.set_main_option("script_location", str(script_dir or MIGRATIONS / kind))
    cfg.attributes["url"] = url
    cfg.attributes["app_role"] = db_logins.app_role_name()  # the grants name the app's login
    return cfg


def _render(url: URL) -> str:
    return url.render_as_string(hide_password=False)


def migrate_control() -> None:
    """The app login (made or its password set again), the control tables and their grants, as the owner; then no stored
    company address keeps a user or a password."""
    with _admin_connection() as conn:
        db_logins.ensure_app_role(conn)
    command.upgrade(_config("control", _render(control_url())), "head")
    forget_stored_credentials()


@contextmanager
def _admin_connection() -> Iterator[Connection]:
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
    with _owner_control_session() as s:
        if s.scalar(select(Tenant).where(Tenant.slug == slug)):
            raise ValueError(f"tenant '{slug}' already exists")
    with _admin_connection() as conn:
        conn.execute(text(f'create database "{db_name}"'))
    url = _address(control_url().set(database=db_name))
    with _owner_control_session() as s:
        tenant = Tenant(slug=slug, name=name or slug)
        s.add(tenant)
        s.flush()
        s.add(
            TenantDatabase(
                tenant_id=tenant.id,
                database_name=db_name,
                connection_encrypted=crypto.encrypt(url),
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
    with _owner_control_session() as s:
        row = s.execute(
            select(TenantDatabase).join(Tenant, Tenant.id == TenantDatabase.tenant_id).where(Tenant.slug == slug)
        ).scalar_one()
        url = _render(db_logins.owner_url(make_url(crypto.decrypt(row.connection_encrypted))))
        try:
            command.upgrade(_config("tenant", url, script_dir), "head")
            from p2p.agents.runtime import setup_checkpoints

            setup_checkpoints(url)  # the agents' checkpoint tables: made by the owner, used by the app
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
    with _owner_control_session() as s:
        slugs = list(s.scalars(select(Tenant.slug).where(Tenant.active).order_by(Tenant.slug)))
    return [migrate_tenant(slug, script_dir=script_dir) for slug in slugs]


def head_revision(script_dir: Path | None = None) -> str | None:
    return ScriptDirectory.from_config(_config("tenant", "", script_dir)).get_current_head()


def _stored_url(slug: str) -> URL:
    with Session(control_engine()) as s:
        row = s.execute(
            select(TenantDatabase).join(Tenant, Tenant.id == TenantDatabase.tenant_id).where(Tenant.slug == slug)
        ).scalar_one()
        return make_url(crypto.decrypt(row.connection_encrypted))


def tenant_engine(slug: str) -> Engine:
    """Engine for one tenant's database, as the app login (the caller disposes it or caches it)."""
    # Postgres allows 100 connections in all: an engine keeps only two open while idle (a cache holds up to 20)
    return create_engine(db_logins.app_url(_stored_url(slug)), pool_size=2, max_overflow=8, pool_pre_ping=True)


def owner_engine(slug: str) -> Engine:
    """Engine for one tenant's database as the owner: for migrations, and for tests and evals that act as the owner.
    Never for the API, the worker or a service (the caller disposes it)."""
    return create_engine(db_logins.owner_url(_stored_url(slug)))


def _address(url: URL) -> str:
    """What a company's database row keeps: host, port and database, never a user or a password."""
    return _render(URL.create(url.drivername, host=url.host, port=url.port, database=url.database, query=url.query))


def forget_stored_credentials() -> int:
    """Rewrite any stored company address that still carries a user or a password (made before the app login). Returns
    how many were rewritten; running it again rewrites none."""
    done = 0
    with _owner_control_session() as s:
        for row in s.scalars(select(TenantDatabase)):
            url = make_url(crypto.decrypt(row.connection_encrypted))
            if url.username or url.password:
                row.connection_encrypted = crypto.encrypt(_address(url))
                done += 1
        s.commit()
    return done


def drop_tenant(slug: str) -> bool:
    """Remove a company completely: its logins, jobs and flags in the control database, and its own database.

    Returns False when there was no such tenant. Only for a company that is made again from its data (the demo
    company); there is no undo. Its state history and the setup archives a cutover kept go with it.
    """
    with _owner_control_session() as s:
        tenant = s.scalar(select(Tenant).where(Tenant.slug == slug))
        if tenant is None:
            return False
        db_name = s.scalar(select(TenantDatabase.database_name).where(TenantDatabase.tenant_id == tenant.id))
        tid = tenant.id
        archives = [
            str(r[0])
            for r in s.execute(text("select database_name from tenant_archive where tenant_id = :t"), {"t": tid})
        ]
        s.execute(
            text("delete from refresh_token where user_id in (select id from app_user where tenant_id = :t)"),
            {"t": tid},
        )
        for table in (
            "app_user",
            "feature_flag",
            "tenant_setting",
            "tenant_database",
            "tenant_state_change",
            "tenant_archive",
        ):
            s.execute(text(f"delete from {table} where tenant_id = :t"), {"t": tid})  # noqa: S608 - fixed table names
        s.execute(text("delete from job where tenant_slug = :s"), {"s": slug})
        s.execute(text("delete from tenant where id = :t"), {"t": tid})
        s.commit()
    from p2p.platform import routing

    routing.clear_engine_cache()
    with _admin_connection() as conn:
        for name in ([db_name] if db_name else []) + archives:  # the company's database and its setup archives
            conn.execute(text(f'drop database if exists "{name}" with (force)'))
    return True


def slug_of(db_name: str) -> str | None:
    """The company whose database this is (the reverse of `tenant_db_name`), or None for another database."""
    prefix = os.environ.get("P2P_TENANT_DB_PREFIX", "p2p_t_")
    return db_name[len(prefix) :] if db_name.startswith(prefix) else None
