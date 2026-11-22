"""D-25, DF-37: the app signs in to the database with its own login, which owns nothing and may only select, insert,
update and delete; the audit log and the setup history are insert only by grant; the owner login is for migrations and
for creating and dropping companies only."""

import re
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Connection, create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError
from test_tenant_provisioning import _script_dir_with_extra

from p2p.config import get_settings
from p2p.main import app
from p2p.platform import crypto, db_logins, routing, tenants

BACKEND = Path(__file__).resolve().parents[1] / "backend" / "p2p"
FULL = {"SELECT", "INSERT", "UPDATE", "DELETE"}
NEVER = ("TRUNCATE", "REFERENCES", "TRIGGER")


def _company() -> str:
    slug = "t" + uuid.uuid4().hex[:8]
    assert tenants.create_tenant(slug, load_reference=False).ok
    return slug


def _user(conn: Connection) -> str:
    return str(conn.execute(text("select current_user")).scalar_one())


def _refused(conn: Connection, statement: str, message: str) -> None:
    with pytest.raises(DBAPIError, match=re.escape(message)) as caught:
        conn.execute(text(statement))
    assert caught.value.orig is not None and getattr(caught.value.orig, "sqlstate", None) == "42501"
    conn.rollback()


def test_every_runtime_engine_signs_in_as_the_restricted_app_login(control_db: str) -> None:
    # given a company
    slug = _company()
    app_login = db_logins.app_role_name()
    assert app_login == f"{make_url(control_db).database}_app"
    # when the control engine, a tenant engine and the request routing connect
    engine = tenants.tenant_engine(slug)
    try:
        for e in (tenants.control_engine(), engine, routing.engine_for(slug)):
            with e.connect() as conn:
                # then each is the app login, which counts as restricted
                assert _user(conn) == app_login
                assert db_logins.login_kind(conn) == "restricted"
    finally:
        engine.dispose()
    owner = tenants.owner_engine(slug)
    try:
        with owner.connect() as conn:
            assert _user(conn) != app_login and db_logins.login_kind(conn) == "owner"
    finally:
        owner.dispose()


def test_the_app_login_has_no_special_rights(control_db: str) -> None:
    with tenants.control_engine().connect() as conn:
        row = conn.execute(
            text(
                "select rolsuper, rolcreatedb, rolcreaterole, rolbypassrls, rolinherit, rolcanlogin from pg_roles "
                "where rolname = current_user"
            )
        ).one()
        owned = conn.execute(text("select count(*) from pg_tables where tableowner = current_user")).scalar_one()
    assert tuple(row) == (False, False, False, False, False, True)
    assert owned == 0


def test_every_company_table_grants_the_app_exactly_its_rights(control_db: str) -> None:
    slug = _company()
    with routing.engine_for(slug).connect() as conn:
        tables = conn.execute(text("select tablename from pg_tables where schemaname = 'public'")).scalars().all()
        assert len(tables) > 100
        wrong = []
        for table in tables:
            rights = {
                p
                for p in (*FULL, *NEVER)
                if conn.execute(text(f"select has_table_privilege('{table}', '{p}')")).scalar()  # noqa: S608 - names from the catalogue
            }
            expected = {"SELECT", "INSERT"} if table in ("audit_event", "setup_change") else FULL
            expected = {"SELECT"} if table == "alembic_version" else expected
            if rights != expected:
                wrong.append((table, sorted(rights)))
    assert wrong == []


def test_what_only_the_owner_may_do_is_refused_to_the_app(control_db: str) -> None:
    # given a company with a row in its audit log
    slug = _company()
    with routing.engine_for(slug).connect() as conn:
        conn.execute(
            text(
                "insert into audit_event (actor_type, actor_id, action, entity, entity_id) values ('service', 's', 'x', 'x', '1')"
            )
        )
        conn.commit()
        # when the app tries what only the owner could, then Postgres refuses it (42501) and nothing changes
        for table in ("audit_event", "setup_change"):
            for statement in (f"update {table} set reason = 'x'", f"delete from {table}", f"truncate {table}"):
                _refused(conn, statement, f"permission denied for table {table}")
        _refused(
            conn, "alter table audit_event disable trigger audit_event_no_change", "must be owner of table audit_event"
        )
        _refused(
            conn,
            "set session_replication_role = replica",
            'permission denied to set parameter "session_replication_role"',
        )
        _refused(conn, "create table probe (id int)", "permission denied for schema public")
        _refused(conn, "create role probe_role", "permission denied to create role")
        assert conn.execute(text("select count(*) from audit_event")).scalar_one() == 1
        # while select and insert on business tables work
        conn.execute(text("insert into region (code, name) values ('R1', 'North')"))
        assert conn.execute(text("select name from region where code = 'R1'")).scalar_one() == "North"
        conn.rollback()
    with tenants.control_engine().connect() as conn:
        assert conn.execute(text("select count(*) from app_user")).scalar_one() >= 0
        _refused(conn, "insert into tenant (slug, name) values ('x', 'x')", "permission denied for table tenant")
        _refused(conn, "update tenant_database set database_name = 'x'", "permission denied for table tenant_database")


def test_a_table_a_later_migration_makes_is_open_to_the_app(control_db: str, tmp_path: Path) -> None:
    slug = _company()
    script_dir = _script_dir_with_extra(tmp_path, "x003", tenants.head_revision() or "", "probe_three")
    assert tenants.migrate_tenant(slug, script_dir=script_dir).ok
    engine = tenants.tenant_engine(slug)
    try:
        with engine.begin() as conn:
            conn.execute(text("insert into probe_three (id) values (1)"))
            assert conn.execute(text("select count(*) from probe_three")).scalar_one() == 1
    finally:
        engine.dispose()


def test_no_stored_company_address_keeps_a_user_or_a_password(control_db: str) -> None:
    # given a company made before the app login: its stored address still holds the owner's user and password
    slug = _company()
    owner = create_engine(control_db)
    with owner.begin() as conn:
        conn.execute(
            text(
                "update tenant_database set connection_encrypted = :c where tenant_id = (select id from tenant where slug = :s)"
            ),
            {
                "c": crypto.encrypt(
                    make_url(control_db).set(database=tenants.tenant_db_name(slug)).render_as_string(False)
                ),
                "s": slug,
            },
        )
    # when the stored credentials are forgotten, twice
    first, second = tenants.forget_stored_credentials(), tenants.forget_stored_credentials()
    # then one address was rewritten, none the second time, and none keeps a user or a password
    assert (first, second) == (1, 0)
    with owner.connect() as conn:
        stored = conn.execute(text("select connection_encrypted from tenant_database")).scalars().all()
    owner.dispose()
    for value in stored:
        url = make_url(crypto.decrypt(value))
        assert url.username is None and url.password is None
    with routing.engine_for(slug).connect() as conn:  # and the company still opens, as the app
        assert _user(conn) == db_logins.app_role_name()


def test_without_the_app_password_no_app_engine_is_made(control_db: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(get_settings(), "app_database_password", "")
    with pytest.raises(db_logins.MissingAppLogin) as caught:
        db_logins.app_url(make_url(control_db))
    assert str(caught.value) == (
        "APP_DATABASE_PASSWORD is not set: the app signs in to the database with its own restricted login. Add it to "
        ".env (see .env.example), then run python -m p2p.cli control migrate"
    )


def test_only_provisioning_code_uses_the_owner_login() -> None:
    users = {
        str(path.relative_to(BACKEND)).replace("\\", "/")
        for path in BACKEND.rglob("*.py")
        if re.search(r"\b(owner_engine|_owner_control_session)\b", path.read_text(encoding="utf-8"))
    }
    # block 0d: the platform team's moves of a company's state, its cutover and its archives also run as the owner
    owner_steps = {"platform/company_state.py", "platform/cutover.py", "platform/company_cli.py"}
    assert users <= {"platform/tenants.py", "platform/demo.py", "cli.py"} | owner_steps


def test_the_health_check_says_the_app_is_restricted(control_db: str) -> None:
    assert TestClient(app).get("/api/health").json() == {
        "status": "ok",
        "database": "ok",
        "database_login": "restricted",
    }
