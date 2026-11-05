import shutil
import uuid
from pathlib import Path

from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from p2p.platform import tenants


def _slug() -> str:
    return "t" + uuid.uuid4().hex[:8]


def _tables(slug: str) -> set[str]:
    engine = tenants.tenant_engine(slug)
    try:
        with engine.connect() as c:
            return {
                r[0] for r in c.execute(text("select tablename from pg_tables where schemaname='public'"))
            }
    finally:
        engine.dispose()


def _script_dir_with_extra(tmp_path: Path, revision: str, down: str, table: str) -> Path:
    d = tmp_path / f"tenant_{revision}"
    shutil.copytree(tenants.MIGRATIONS / "tenant", d, ignore=shutil.ignore_patterns("__pycache__"))
    (d / "versions" / f"{revision}_{table}.py").write_text(
        f'revision = "{revision}"\ndown_revision = "{down}"\nbranch_labels = None\ndepends_on = None\n\n\n'
        f'def upgrade():\n    from alembic import op\n    op.execute("create table {table} (id int)")\n\n\n'
        "def downgrade():\n    pass\n"
    )
    return d


def test_two_tenants_each_get_their_own_database(control_db: str) -> None:
    a, b = _slug(), _slug()
    ra, rb = tenants.create_tenant(a), tenants.create_tenant(b)
    assert ra.ok and rb.ok
    assert ra.revision == rb.revision == tenants.head_revision()
    assert tenants.tenant_db_name(a) != tenants.tenant_db_name(b)

    # the stored connection string is encrypted, not readable text
    engine = create_engine(control_db)
    with engine.connect() as c:
        stored = c.execute(
            text("select connection_encrypted from tenant_database where database_name = :n"),
            {"n": tenants.tenant_db_name(a)},
        ).scalar_one()
    engine.dispose()
    password = make_url(control_db).password or ""
    assert password and password not in stored and "postgresql" not in stored


def test_new_migration_reaches_every_tenant_with_one_command(control_db: str, tmp_path: Path) -> None:
    a, b = _slug(), _slug()
    tenants.create_tenant(a)
    tenants.create_tenant(b)
    script_dir = _script_dir_with_extra(tmp_path, "0002", "0001", "probe_one")

    results = {r.slug: r for r in tenants.migrate_all(script_dir=script_dir)}

    for slug in (a, b):
        assert results[slug].ok and results[slug].revision == "0002"
        assert "probe_one" in _tables(slug)


def test_failure_on_one_tenant_is_reported_and_does_not_block_the_other(
    control_db: str, tmp_path: Path
) -> None:
    good, broken = _slug(), _slug()
    tenants.create_tenant(good)
    tenants.create_tenant(broken)
    # simulate a tenant whose database has gone missing
    admin = create_engine(make_url(control_db).set(database="postgres"), isolation_level="AUTOCOMMIT")
    with admin.connect() as c:
        c.execute(text(f'drop database "{tenants.tenant_db_name(broken)}" with (force)'))
    admin.dispose()
    script_dir = _script_dir_with_extra(tmp_path, "0003", "0001", "probe_two")

    results = {r.slug: r for r in tenants.migrate_all(script_dir=script_dir)}

    assert results[good].ok and results[good].revision == "0003"
    assert not results[broken].ok and results[broken].error
