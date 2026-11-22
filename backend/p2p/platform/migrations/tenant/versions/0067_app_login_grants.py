"""The app's own database login: select, insert, update and delete only (task 1.7, D-25, DF-37)

The owner (who runs this migration) keeps owning every table. The app login owns nothing: it can neither alter a table
nor switch off a trigger, the audit log and the setup history are insert only by grant (their triggers stay as a second
wall against the owner), and the migration record is read only. Default privileges give the app the same rights on every
table and sequence a later migration creates; a later insert-only table revokes update and delete in its own migration.

Revision ID: 0067
Revises: 0066
"""

from alembic import context, op

revision = "0067"
down_revision = "0066"
branch_labels = None
depends_on = None

INSERT_ONLY = ("audit_event", "setup_change")


def _app() -> str:
    role = context.config.attributes.get("app_role")
    if not role:
        raise RuntimeError("run migrations through p2p.cli: it names the app's database login")
    return '"' + str(role).replace('"', '""') + '"'


def upgrade() -> None:
    app = _app()
    op.execute("revoke create on schema public from public")
    op.execute(f"grant usage on schema public to {app}")
    op.execute(f"grant select, insert, update, delete on all tables in schema public to {app}")
    op.execute(f"grant usage, select on all sequences in schema public to {app}")
    op.execute(f"revoke update, delete on {', '.join(INSERT_ONLY)} from {app}")
    op.execute(f"revoke insert, update, delete on alembic_version from {app}")
    op.execute(f"alter default privileges in schema public grant select, insert, update, delete on tables to {app}")
    op.execute(f"alter default privileges in schema public grant usage, select on sequences to {app}")


def downgrade() -> None:
    app = _app()
    op.execute(f"alter default privileges in schema public revoke usage, select on sequences from {app}")  # noqa: S608 - a quoted role name
    op.execute(f"alter default privileges in schema public revoke select, insert, update, delete on tables from {app}")
    op.execute(f"revoke all on all sequences in schema public from {app}")
    op.execute(f"revoke all on all tables in schema public from {app}")
    op.execute(f"revoke usage on schema public from {app}")
    op.execute("grant create on schema public to public")
