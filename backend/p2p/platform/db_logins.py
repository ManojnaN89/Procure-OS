"""Which database login a connection uses (D-25, DF-37).

The owner (the user in CONTROL_DATABASE_URL) owns every database and table: it runs migrations and creates and drops
companies, nothing else. Everything that runs the business (the API, the worker, imports and the other commands) signs
in as the app login: select, insert, update and delete only, insert only on the audit log and the setup history, and it
owns nothing, so it cannot switch off the triggers that protect them.
"""

from sqlalchemy import Connection, text
from sqlalchemy.engine import URL, make_url

from p2p.config import get_settings

MISSING = (
    "APP_DATABASE_PASSWORD is not set: the app signs in to the database with its own restricted login. Add it to .env "
    "(see .env.example), then run python -m p2p.cli control migrate"
)


class MissingAppLogin(RuntimeError):
    pass


def app_role_name() -> str:
    settings = get_settings()
    return settings.app_database_user or f"{make_url(settings.control_database_url).database}_app"


def app_url(url: URL) -> URL:
    password = get_settings().app_database_password
    if not password:
        raise MissingAppLogin(MISSING)
    return url.set(username=app_role_name(), password=password)


def owner_url(url: URL) -> URL:
    owner = make_url(get_settings().control_database_url)
    return url.set(username=owner.username, password=owner.password)


def ensure_app_role(conn: Connection) -> None:
    """Make the app login, or set its password again so it stays in step with .env. Run as the owner."""
    password = get_settings().app_database_password
    if not password:
        raise MissingAppLogin(MISSING)
    statement = conn.execute(
        text(
            "select format(case when exists (select 1 from pg_roles where rolname = cast(:n as text)) "
            "then 'alter role %I with login password %L' "
            "else 'create role %I with login nosuperuser nocreatedb nocreaterole noinherit nobypassrls password %L' end, "
            "cast(:n as text), cast(:p as text))"
        ),
        {"n": app_role_name(), "p": password},
    ).scalar_one()
    # quoted by Postgres itself (%I, %L); sent without parameters so nothing in the password is read as a placeholder
    conn.execution_options(no_parameters=True).exec_driver_sql(statement)


def login_kind(conn: Connection) -> str:
    """'owner' when this connection is a superuser or owns a table, else 'restricted'."""
    owner = conn.execute(
        text(
            "select coalesce((select rolsuper from pg_roles where rolname = current_user), false) "
            "or exists (select 1 from pg_tables where schemaname = 'public' and tableowner = current_user)"
        )
    ).scalar_one()
    return "owner" if owner else "restricted"
