"""E67: the app runs without the database owner's rights (task 1.7, D-25, DF-37), written without the implementation.

Independence: written from the block 0c spec (sections 4.1-4.5, 5, 6) and the plan only; the platform's login code was
never read. The eval builds both logins itself from the environment (block0c.py): the owner is the user in
CONTROL_DATABASE_URL, the app is APP_DATABASE_USER (else `<control database>_app`) with APP_DATABASE_PASSWORD. It asks
Postgres, not the platform, who each connection is and what it may do (pg_roles, pg_class, has_table_privilege).

Scenario: the evals' own environment (evals/env.py); the demo company Apex Pumps; a short flow through the API as its
role logins (a request typed in, submitted, approved by the plant head, the job worker run, an order made). Then:
`/api/health` says the login is restricted; the audit chain verifies; every engine the runtime uses signs in as the app
login; connected straight as the app login to the control and the company database: not a superuser, cannot create
databases or roles, no row security bypass, owns nothing, member of no role; every refused statement of spec section 5
fails with SQLSTATE 42501 and its text and changes nothing; reading and writing business tables works; the grants are
exactly the spec's on every table; every stored connection decrypts to a URL without a password. As the owner:
`p2p.cli tenant migrate-all` succeeds and a table the owner makes afterwards is readable by the app (default
privileges).

Mutations (spec section 6), each made live in an owner transaction that is rolled back, must fail the same checks: the
runtime signs in as the owner (the probes run on an owner connection); the grant on audit_event includes update and
delete; the default privileges are missing (a new table the app cannot read).
"""

import os
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any

import block0c as z
import env
import lib
from block1 import cli
from lib import Outcome
from sqlalchemy import text
from sqlalchemy.engine import Connection, make_url
from sqlalchemy.exc import DBAPIError

ID = "E67"
TITLE = "The app runs without owner rights: its own login, insert-only audit by grant, nothing it could switch off"
REQUIREMENT = (
    "Task 1.7, D-25, DF-37 (block 0c spec 4.1-4.5, 5): every runtime path (API, worker, CLI, engines) signs in as the "
    "app login, which is no superuser, makes no database or role, bypasses no row security and owns nothing; it may "
    "select, insert, update and delete business tables, only select and insert audit_event and setup_change, only "
    "select alembic_version and the control tenant tables; every forbidden statement fails with 42501 and changes "
    "nothing; stored tenant URLs hold no password; health says restricted; the owner still migrates, and tables it "
    "makes later reach the app by default privileges"
)
PREFIX = "e67"
DOMAIN = "apexpumps.example"
ITEM, QTY = "RM-01", "20"  # a contract item; 20 of them is above the request auto-approval band, so a person approves
DML = {"SELECT", "INSERT", "UPDATE", "DELETE"}
PRIVS = ("SELECT", "INSERT", "UPDATE", "DELETE", "TRUNCATE", "REFERENCES", "TRIGGER")
TENANT_SPECIAL = {"audit_event": {"SELECT", "INSERT"}, "setup_change": {"SELECT", "INSERT"}, "alembic_version": {"SELECT"}}
CONTROL_GRANTS = {
    **{t: {"SELECT"} for t in ("tenant", "tenant_database", "alembic_version")},
    **{t: DML for t in ("app_user", "refresh_token", "feature_flag", "tenant_setting", "job")},
}
NEVER = {"TRUNCATE", "REFERENCES", "TRIGGER"}

# (what, statement, the text Postgres 17 gives; SQLSTATE 42501 for all), spec section 5
TENANT_PROBES = [
    ("update audit_event", "update audit_event set id = id where id = (select max(id) from audit_event)",
     "permission denied for table audit_event"),
    ("delete audit_event", "delete from audit_event where id = (select max(id) from audit_event)",
     "permission denied for table audit_event"),
    ("truncate audit_event", "truncate audit_event", "permission denied for table audit_event"),
    ("update setup_change", "update setup_change set id = id where id = (select max(id) from setup_change)",
     "permission denied for table setup_change"),
    ("delete setup_change", "delete from setup_change where id = (select max(id) from setup_change)",
     "permission denied for table setup_change"),
    ("truncate setup_change", "truncate setup_change", "permission denied for table setup_change"),
    ("disable the audit trigger", "alter table audit_event disable trigger all", "must be owner of table audit_event"),
    ("session_replication_role", "set session_replication_role = replica",
     'permission denied to set parameter "session_replication_role"'),
    ("create table", "create table e67_probe (x integer)", "permission denied for schema public"),
    ("create role", "create role e67_probe_role", "permission denied to create role"),
]  # fmt: skip
CONTROL_PROBES = [
    ("insert into tenant", "insert into tenant (slug, name) values ('e67probe', 'E67 probe')",
     "permission denied for table tenant"),
    ("update tenant_database", "update tenant_database set schema_revision = schema_revision",
     "permission denied for table tenant_database"),
]  # fmt: skip
# what must work as the app (in a transaction rolled back afterwards)
TENANT_WORKS = [
    "select count(*) from supplier",
    "insert into bank (code, name, ifsc_prefix) values ('E67X', 'E67 probe bank', 'E67X')",
    (
        "insert into audit_event (actor_type, actor_id, action, entity, entity_id) values "
        "('service', 'e67', 'e67.probe', 'probe', '1')"
    ),
]
CONTROL_WORKS = ["select count(*) from app_user", "select count(*) from tenant"]


# ---- what Postgres says about a connection ----------------------------------------------------------------------------


def identity(conn: Connection) -> dict[str, Any]:
    """Who the connection is and what the role may do, from the catalogue (in the caller's transaction)."""
    row = conn.execute(
        text(
            "select current_user, rolsuper, rolcreatedb, rolcreaterole, rolbypassrls, rolinherit from pg_roles "
            "where rolname = current_user"
        )
    ).one()
    owns = conn.execute(
        text(
            "select count(*) from pg_class c join pg_namespace n on n.oid = c.relnamespace where n.nspname = 'public' "
            "and c.relowner = (select oid from pg_roles where rolname = current_user)"
        )
    ).scalar_one()
    databases = conn.execute(
        text("select count(*) from pg_database where datdba = (select oid from pg_roles where rolname = current_user)")
    ).scalar_one()
    member_of = [
        r[0]
        for r in conn.execute(
            text(
                "select g.rolname from pg_auth_members m join pg_roles g on g.oid = m.roleid "
                "join pg_roles u on u.oid = m.member where u.rolname = current_user"
            )
        )
    ]
    user, superuser, createdb, createrole, bypassrls, inherit = row
    return {
        "user": user, "superuser": superuser, "createdb": createdb, "createrole": createrole, "bypassrls": bypassrls,
        "inherit": inherit, "owns tables": int(owns), "owns databases": int(databases), "member of": member_of,
        # the spec's login_kind: owner when a superuser or the owner of a table in public, else restricted
        "kind": "owner" if superuser or int(owns) else "restricted",
    }  # fmt: skip


def judge_identity(obs: dict[str, Any], app: str, where: str) -> list[tuple[str, str]]:
    wrong: list[tuple[str, str]] = []
    if obs["user"] != app:
        wrong.append(("high", f"{where}: signed in as {obs['user']}, not the app login {app}"))
    for flag in ("superuser", "createdb", "createrole", "bypassrls"):
        if obs[flag]:
            wrong.append(("high", f"{where}: the login has {flag}"))
    if obs["owns tables"] or obs["owns databases"]:
        owns = f"{obs['owns tables']} tables and {obs['owns databases']} databases"
        wrong.append(("high", f"{where}: the login owns {owns}"))
    if obs["member of"]:
        wrong.append(("medium", f"{where}: the login is a member of {obs['member of']} (it could act as them)"))
    if obs["inherit"]:
        wrong.append(("low", f"{where}: the login inherits (spec 4.1: noinherit)"))
    return wrong


def grants(conn: Connection, role: str) -> dict[str, set[str]]:
    """Table privileges of a role on every table and view in public."""
    rows = conn.execute(
        text(
            "select c.relname, array(select p from unnest(cast(:privs as text[])) p where has_table_privilege(:r, c.oid, p)) "
            "from pg_class c join pg_namespace n on n.oid = c.relnamespace "
            "where n.nspname = 'public' and c.relkind in ('r', 'v', 'm', 'p', 'f')"
        ),
        {"r": role, "privs": list(PRIVS)},
    )
    return {r[0]: set(r[1]) for r in rows}


def sequences_without_usage(conn: Connection, role: str) -> list[str]:
    return [
        r[0]
        for r in conn.execute(
            text(
                # eval bug, review of 22 Nov: Postgres may test the privilege before the kind, so guard it with a case
                "select c.relname from pg_class c join pg_namespace n on n.oid = c.relnamespace where n.nspname = "
                "'public' and case when c.relkind = 'S' then not has_sequence_privilege(:r, c.oid, 'USAGE') else false end"
            ),
            {"r": role},
        )
    ]


def judge_tenant_grants(found: dict[str, set[str]], where: str) -> list[tuple[str, str]]:
    wrong = []
    for table, privs in sorted(found.items()):
        want = TENANT_SPECIAL.get(table, DML)
        if privs != want:
            sev = "high" if (privs - want) & ({"UPDATE", "DELETE"} | NEVER) else "medium"
            wrong.append((sev, f"{where}: the app may {sorted(privs)} on {table}; the spec grants {sorted(want)}"))
    return wrong


def judge_control_grants(found: dict[str, set[str]], where: str) -> list[tuple[str, str]]:
    wrong = []
    for table, privs in sorted(found.items()):
        want = CONTROL_GRANTS.get(table)
        if want is not None and privs != want:
            sev = "high" if privs - want else "medium"
            wrong.append((sev, f"{where}: the app may {sorted(privs)} on {table}; the spec grants {sorted(want)}"))
        elif want is None and privs & NEVER:
            wrong.append(("high", f"{where}: the app may {sorted(privs & NEVER)} on {table}"))
    return wrong


def run_probes(conn: Connection, probes: list[tuple[str, str, str]], *, commit: bool) -> dict[str, tuple[Any, str]]:
    """Each statement in its own transaction (committed when it works, so 'changes nothing' is real), or, with commit
    False, in a savepoint of the caller's transaction (the caller rolls everything back)."""
    got: dict[str, tuple[Any, str]] = {}
    for name, sql, _ in probes:
        if commit:
            conn.rollback()
        tx = conn.begin() if commit else conn.begin_nested()
        try:
            conn.execute(text(sql))
        except DBAPIError as exc:
            tx.rollback()
            got[name] = (z.sqlstate(exc), z.message(exc))
            continue
        if commit:
            tx.commit()
        else:
            tx.rollback()
        got[name] = (None, "it worked")
    return got


def judge_probes(got: dict[str, tuple[Any, str]], probes: list[tuple[str, str, str]], where: str) -> list[tuple[str, str]]:
    wrong = []
    for name, _, words in probes:
        state, said = got[name]
        if state != "42501" or words not in said:
            wrong.append(("high", f"{where}: {name}: {state} {said!r}; expected 42501 {words!r}"))
    return wrong


def as_statement(conn: Connection, template: str, role: str) -> str:
    """A statement naming a role, quoted by Postgres itself (format %I), never by pasting text."""
    # eval bug, review of 22 Nov: untyped parameters of format() need a cast
    sql = "select format(cast(:t as text), cast(:r as text))"
    return str(conn.execute(text(sql), {"t": template, "r": role}).scalar_one())


# ---- the eval -----------------------------------------------------------------------------------------------------------


def run(ctx: lib.Ctx) -> Outcome:
    env.forget_tenants(PREFIX)  # left by an earlier run that stopped half way (the demo logins are unique)
    try:
        return _run(ctx)
    finally:
        env.forget_tenants(PREFIX)


def _run(ctx: lib.Ctx) -> Outcome:
    import tempfile

    from p2p.platform import demo

    out = Outcome(True)
    check = z.checker(out)
    lib.capture_mail()
    app_name, password = z.app_user(), os.environ.get("APP_DATABASE_PASSWORD") or ""
    out.metrics["app login / control database"] = f"{app_name} / {z.control_database()}"
    check(password, "APP_DATABASE_PASSWORD is not set in the eval environment (spec 4.4: evals/env.py sets one)")
    check(os.environ.get("APP_DATABASE_USER") == f"{z.control_database()}_app",
          f"evals/env.py should force APP_DATABASE_USER to {z.control_database()}_app (spec 4.4); it is "
          f"{os.environ.get('APP_DATABASE_USER')!r}", "medium")  # fmt: skip
    if not password:
        return out
    slug = env.slug(PREFIX)
    folder = Path(tempfile.mkdtemp(prefix="e67_"))
    demo.reset_company(demo.company("apex"), folder / "dataset", credentials=None, slug=slug)
    database = z.tenant_database(slug)
    _flow(out, check, slug)
    _runtime(out, check, slug, app_name)
    _as_the_app(out, check, database, app_name)
    _stored_urls(out, check, password)
    _owner(out, check, database, app_name)
    _missing_password(out, check)
    _owner_only_in_its_places(out, check)
    _mutations(out, check, database, app_name)
    return out


def _flow(out: Outcome, check: Callable[..., bool], slug: str) -> None:
    """A request typed in, submitted, approved, the worker run, an order made: all as the role logins and the app."""
    from fastapi.testclient import TestClient
    from p2p.main import app
    from p2p.platform import audit, routing, users, worker
    from p2p.platform.tokens import create_access_token
    from sqlalchemy.orm import Session

    client = TestClient(app)
    who = {"production": "Requester", "planthead": "Approver", "director": "Approver", "buyer": "Buyer"}
    uid = {k: int(users.user_id_by_email(slug, f"{k}@{DOMAIN}") or 0) for k in who}
    h = {k: {"Authorization": f"Bearer {create_access_token(uid[k], slug, r)}"} for k, r in who.items()}
    db = Session(routing.engine_for(slug))

    def one(sql: str, **p: Any) -> Any:
        db.rollback()
        return db.execute(text(sql), p).scalar()

    made = client.post("/api/requisitions", json={"lines": [{"item_code": ITEM, "qty": QTY}], "need_by": "2026-12-15",
                                                  "reason": "E67"}, headers=h["production"])  # fmt: skip
    if not check(made.status_code == 200, f"typing a request in answered {made.status_code} {made.text[:200]}"):
        return
    no = made.json()["id"]
    sub = client.post(f"/api/requisitions/{no}/submit", json={}, headers=h["production"])
    check(sub.status_code == 200, f"submitting {no} answered {sub.status_code} {sub.text[:200]}")
    approved_by = []
    for person in ("planthead", "director"):
        if not one("select count(*) from approval_step where request_no = :n and status = 'pending'", n=no):
            break
        r = client.post(f"/api/requisitions/{no}/approve", json={}, headers=h[person])
        approved_by.append(f"{person} {r.status_code}")
    taken = 0
    for _ in range(8):
        n = worker.run_once(tenant=slug)
        taken += n
        if n == 0:
            break
    pos = [r[0] for r in db.execute(text("select distinct po_no from po_line where request_no = :n"), {"n": no})]
    if not pos:  # the worker did not order it: the buyer does, as the screens and the CLI would
        client.post(f"/api/requisitions/{no}/source", json={}, headers=h["buyer"])
        cli("order", slug, "--request", no)
        db.rollback()
        pos = [r[0] for r in db.execute(text("select distinct po_no from po_line where request_no = :n"), {"n": no})]
    out.metrics["flow: request / approved by / jobs taken / orders"] = f"{no} / {approved_by} / {taken} / {pos}"
    status = one("select status from request where no = :n", n=no)
    check(pos, f"the flow as the app login did not reach an order for {no} (request status {status})")
    with z.connect(z.owner_url()) as conn:  # the owner reads the job table
        jobs = list(conn.execute(text("select kind, status, last_error from job where tenant_slug = :s"), {"s": slug}))
    refused = [j for j in jobs if j[2] and re.search(r"permission denied|must be owner", j[2])]
    failed = [j for j in jobs if j[1] == "failed" and j not in refused]
    out.metrics["jobs: total / refused by the database / failed otherwise"] = f"{len(jobs)} / {len(refused)} / {len(failed)}"
    check(not refused, f"jobs the database refused for the app login: {refused[:3]}")
    check(not failed, f"jobs that failed: {failed[:3]}", "medium")
    if not taken:
        out.notes.append("no job was due during the flow, so the worker's own login is checked only through its engine")
    db.rollback()
    for table in ("audit_event", "setup_change"):
        broken = audit.verify_chain(db, table)
        check(broken is None, f"the {table} chain does not verify after the flow: {broken}")
    db.close()


def _runtime(out: Outcome, check: Callable[..., bool], slug: str, app_name: str) -> None:
    """The engines the API, the worker and the CLI use, and what /api/health says."""
    from fastapi.testclient import TestClient
    from p2p.main import app
    from p2p.platform import routing, tenants

    engines = {
        "tenants.control_engine()": tenants.control_engine(),
        "routing.engine_for(slug)": routing.engine_for(slug),
        "tenants.tenant_engine(slug)": tenants.tenant_engine(slug),
    }
    users = {}
    for name, engine in engines.items():
        with engine.connect() as conn:
            users[name] = conn.execute(text("select current_user")).scalar_one()
    out.metrics["runtime engines sign in as"] = users
    for name, user in users.items():
        check(user == app_name, f"{name} signs in as {user}, not the app login {app_name}")
    health = TestClient(app).get("/api/health")
    body = health.json() if health.status_code == 200 else {}
    with z.connect(z.app_url()) as conn:
        oracle = identity(conn)["kind"]
    out.metrics["/api/health"] = body or health.status_code
    check(body.get("database") == "ok" and body.get("database_login") == "restricted" == oracle,
          f"/api/health says {body}; the app login is {oracle}; expected database ok and database_login restricted")  # fmt: skip


def _as_the_app(out: Outcome, check: Callable[..., bool], database: str, app_name: str) -> None:
    """Straight connections as the app login: who it is, what it may do, and what Postgres refuses it."""

    def owner_state() -> tuple[Any, ...]:
        with z.connect(z.owner_url(database)) as conn:
            tenant = (
                conn.execute(text("select count(*), max(id) from audit_event")).one(),
                conn.execute(text("select id, action, entity_id, reason from audit_event order by id desc limit 1")).first(),
                conn.execute(text("select count(*) from setup_change")).scalar(),
                conn.execute(text("select count(*) from pg_tables where schemaname = 'public'")).scalar(),
            )
        with z.connect(z.owner_url()) as conn:
            control = (
                conn.execute(text("select count(*) from tenant")).scalar(),
                tuple(tuple(r) for r in conn.execute(text("select * from tenant_database order by tenant_id"))),
                conn.execute(text("select count(*) from pg_roles where rolname = 'e67_probe_role'")).scalar(),
            )
        return tenant + control

    before = owner_state()
    found: list[tuple[str, str]] = []
    with z.connect(z.app_url(database)) as conn:
        me = identity(conn)
        found += judge_identity(me, app_name, "company database")
        tenant_grants = grants(conn, app_name)
        found += judge_tenant_grants(tenant_grants, "company database")
        found += [("medium", f"company database: no usage on sequence {s}") for s in sequences_without_usage(conn, app_name)]
        conn.rollback()
        got = run_probes(conn, TENANT_PROBES, commit=True)
        found += judge_probes(got, TENANT_PROBES, "company database")
        for sql in TENANT_WORKS:
            conn.rollback()
            tx = conn.begin()
            try:
                conn.execute(text(sql))
            except DBAPIError as exc:
                found.append(("high", f"company database: the app cannot {sql.split(' (')[0]!r}: {z.message(exc)}"))
            finally:
                tx.rollback()
    with z.connect(z.app_url()) as conn:
        found += judge_identity(identity(conn), app_name, "control database")
        found += judge_control_grants(grants(conn, app_name), "control database")
        conn.rollback()
        got_control = run_probes(conn, CONTROL_PROBES, commit=True)
        found += judge_probes(got_control, CONTROL_PROBES, "control database")
        for sql in CONTROL_WORKS:
            conn.rollback()
            try:
                conn.execute(text(sql))
            except DBAPIError as exc:
                found.append(("high", f"control database: the app cannot {sql!r}: {z.message(exc)}"))
        conn.rollback()
    after = owner_state()
    out.metrics["as the app: refused statements (42501 with the spec's text)"] = (
        f"{sum(1 for n, _, w in TENANT_PROBES if got[n][0] == '42501' and w in got[n][1])} of {len(TENANT_PROBES)} "
        f"(company), {sum(1 for n, _, w in CONTROL_PROBES if got_control[n][0] == '42501' and w in got_control[n][1])} "
        f"of {len(CONTROL_PROBES)} (control)"
    )
    out.metrics["as the app: tables checked for the spec's grants"] = len(tenant_grants)
    check(after == before, f"the refused statements changed something: before {before}, after {after}")
    for severity, finding in found:
        out.find(severity, finding)
    _clean_probes(database)


def _clean_probes(database: str) -> None:
    """What a probe left if Postgres let it through (a finding already): its table and its role."""
    with z.connect(z.owner_url(database)) as conn:
        conn.execute(text("drop table if exists e67_probe"))
        conn.execute(text("drop role if exists e67_probe_role"))
        conn.commit()
    with z.connect(z.owner_url()) as conn:
        conn.execute(text("delete from tenant where slug = 'e67probe'"))
        conn.commit()


def _stored_urls(out: Outcome, check: Callable[..., bool], password: str) -> None:
    from p2p.platform import crypto

    owner_password = z.owner_url().password or ""
    with z.connect(z.owner_url()) as conn:
        stored = list(conn.execute(text("select database_name, connection_encrypted from tenant_database")))
    bad = []
    for name, token in stored:
        plain = crypto.decrypt(token)
        try:
            url = make_url(plain)
            has_password, has_user = bool(url.password), bool(url.username)
        except Exception:  # noqa: BLE001 - not a URL: look for the passwords in the text itself
            has_password, has_user = False, False
        if has_password or (owner_password and owner_password in plain) or password in plain:
            bad.append((name, "password"))
        elif has_user:
            bad.append((name, "user name"))
    out.metrics["stored company connections / with a password or user"] = f"{len(stored)} / {len(bad)}"
    check(not [b for b in bad if b[1] == "password"], f"stored connections that still hold a password: {bad}")
    check(not [b for b in bad if b[1] == "user name"], f"stored connections that still name a user (spec 4.3: host, "
                                                      f"port, database only): {bad}", "medium")  # fmt: skip


def _owner(out: Outcome, check: Callable[..., bool], database: str, app_name: str) -> None:
    """The owner still migrates; a table it makes afterwards reaches the app by default privileges."""
    code, printed = cli("tenant", "migrate-all")
    out.metrics["tenant migrate-all as the owner: exit code"] = code
    check(code == 0, f"p2p.cli tenant migrate-all failed ({code}): {printed[-300:]}")
    code, printed = cli("control", "migrate")
    check(code == 0, f"p2p.cli control migrate failed ({code}): {printed[-300:]}")
    check(f"app login {app_name} is ready" in printed, f"control migrate printed {printed.strip()[-200:]!r}; spec 4.5: "
                                                       f"'control database is up to date; app login {app_name} is ready'",
          "low")  # fmt: skip
    with z.connect(z.owner_url(database)) as conn:
        conn.execute(text("create table if not exists e67_after_migrate (id integer)"))
        conn.commit()
    try:
        with z.connect(z.app_url(database)) as conn:
            privs = grants(conn, app_name).get("e67_after_migrate", set())
            try:
                conn.execute(text("select count(*) from e67_after_migrate"))
                readable = True
            except DBAPIError:
                readable = False
        out.metrics["a table the owner makes after migrating: the app may"] = sorted(privs)
        check(readable and privs == DML, f"a table the owner made after migrating: the app may {sorted(privs)}, "
                                         f"reads it {readable}; the spec's default privileges give {sorted(DML)}")  # fmt: skip
    finally:
        with z.connect(z.owner_url(database)) as conn:
            conn.execute(text("drop table if exists e67_after_migrate"))
            conn.commit()


def _missing_password(out: Outcome, check: Callable[..., bool]) -> None:
    """Without APP_DATABASE_PASSWORD the app login cannot be made, with the spec's words (4.3)."""
    from p2p.config import get_settings

    saved = os.environ.get("APP_DATABASE_PASSWORD")
    os.environ["APP_DATABASE_PASSWORD"] = ""  # empty, so a value in a .env file cannot stand in for it
    get_settings.cache_clear()
    try:
        from p2p.platform import db_logins

        db_logins.app_url(z.owner_url())
        got = "no error"
    except Exception as exc:  # noqa: BLE001 - the error is what is checked
        got = f"{type(exc).__name__}: {exc}"
    finally:
        if saved is None:
            os.environ.pop("APP_DATABASE_PASSWORD", None)
        else:
            os.environ["APP_DATABASE_PASSWORD"] = saved
        get_settings.cache_clear()
    out.metrics["no APP_DATABASE_PASSWORD"] = got[:160]
    want = (
        "MissingAppLogin: APP_DATABASE_PASSWORD is not set: the app signs in to the database with its own restricted "
        "login. Add it to .env (see .env.example), then run python -m p2p.cli control migrate"
    )
    check(got == want, f"without APP_DATABASE_PASSWORD: {got!r}; spec 4.3: {want!r}", "medium")


def _owner_only_in_its_places(out: Outcome, check: Callable[..., bool]) -> None:
    """Only tenants.py, demo.py and cli.py use the owner's engine or session (spec 4.3, 6)."""
    root = env.ROOT / "backend" / "p2p"
    allowed = {root / "platform" / "tenants.py", root / "platform" / "demo.py", root / "cli.py"}
    users = sorted(
        str(p.relative_to(root))
        for p in root.rglob("*.py")
        if p not in allowed and re.search(r"\b(owner_engine|_owner_control_session)\s*\(", p.read_text(encoding="utf-8"))
    )
    out.metrics["modules using the owner's login outside tenants, demo and cli"] = users or "none"
    check(not users, f"modules using the owner's login: {users}", "medium")


def _mutations(out: Outcome, check: Callable[..., bool], database: str, app_name: str) -> None:
    """Each broken rule made live in an owner transaction that is rolled back; the same checks must fail."""
    caught: dict[str, str] = {}

    def verdict(name: str, found: list[tuple[str, str]]) -> None:
        told = [f for f in found if f[0] in ("high", "medium")]
        caught[name] = f"caught ({len(told)} findings)" if told else "NOT caught"
        check(told, f"mutation '{name}' was not caught: the eval cannot tell this rule is broken", "medium")

    with z.connect(z.owner_url(database)) as conn:
        # 1. the runtime signs in as the owner: the same identity, grants and probes on an owner connection
        who = identity(conn)
        conn.rollback()
        found = judge_identity(who, app_name, "owner")
        if who["kind"] != "restricted":  # what /api/health would then say instead of restricted
            found.append(("high", f"owner: the login kind is {who['kind']}"))
        outer = conn.begin()
        try:
            found += judge_tenant_grants(grants(conn, str(who["user"])), "owner")
            found += judge_probes(run_probes(conn, TENANT_PROBES, commit=False), TENANT_PROBES, "owner")
        finally:
            outer.rollback()
        verdict("the runtime engines use the owner URL", found)

        # 2. the grant on audit_event includes update and delete
        outer = conn.begin()
        try:
            conn.execute(text(as_statement(conn, "grant update, delete on audit_event to %I", app_name)))
            found = judge_tenant_grants(grants(conn, app_name), "audit_event granted")
        finally:
            outer.rollback()
        verdict("the grant on audit_event includes update or delete", found)

        # 3. the default privileges are missing: a table the owner makes is not the app's
        outer = conn.begin()
        try:
            conn.execute(text(as_statement(
                conn, "alter default privileges in schema public revoke select, insert, update, delete on tables from %I",
                app_name)))  # fmt: skip
            conn.execute(text("create table e67_mutation_probe (id integer)"))
            privs = grants(conn, app_name).get("e67_mutation_probe", set())
            found = [] if privs == DML else [("high", f"a new table gives the app {sorted(privs)}")]
        finally:
            outer.rollback()
        verdict("default privileges missing", found)
    out.metrics["mutation checks (rule broken on purpose: caught?)"] = caught
