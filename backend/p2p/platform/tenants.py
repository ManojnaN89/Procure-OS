"""Tenant provisioning: one database per tenant, tracked in the control database."""

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


def control_engine() -> Engine:
    return create_engine(control_url())


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
    return f"p2p_t_{slug}"


def create_tenant(slug: str, name: str | None = None, *, script_dir: Path | None = None) -> MigrateResult:
    if not SLUG.match(slug):
        raise ValueError("slug must be lower case letters, digits or underscore, starting with a letter (2-31 chars)")
    db_name = tenant_db_name(slug)
    engine = control_engine()
    try:
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
    finally:
        engine.dispose()
    return migrate_tenant(slug, script_dir=script_dir)


def migrate_tenant(slug: str, *, script_dir: Path | None = None) -> MigrateResult:
    """Bring one tenant database to the latest revision. Never raises: failures come back in the result."""
    engine = control_engine()
    try:
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
    finally:
        engine.dispose()


def migrate_all(*, script_dir: Path | None = None) -> list[MigrateResult]:
    engine = control_engine()
    try:
        with Session(engine) as s:
            slugs = list(s.scalars(select(Tenant.slug).where(Tenant.active).order_by(Tenant.slug)))
    finally:
        engine.dispose()
    return [migrate_tenant(slug, script_dir=script_dir) for slug in slugs]


def head_revision(script_dir: Path | None = None) -> str | None:
    return ScriptDirectory.from_config(_config("tenant", "", script_dir)).get_current_head()


def tenant_engine(slug: str) -> Engine:
    """Engine for one tenant's database (the caller disposes it or caches it)."""
    engine = control_engine()
    try:
        with Session(engine) as s:
            row = s.execute(
                select(TenantDatabase).join(Tenant, Tenant.id == TenantDatabase.tenant_id).where(Tenant.slug == slug)
            ).scalar_one()
            return create_engine(crypto.decrypt(row.connection_encrypted))
    finally:
        engine.dispose()
