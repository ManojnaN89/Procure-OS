"""The app's own database login on the control database (task 1.7, D-25, DF-37)

The app reads the companies and their database addresses but never writes them (only the owner creates and drops a
company); it reads and writes logins, refresh tokens, flags, settings and jobs. A new control table gets its grants in its
own migration (no default privileges here).

Revision ID: 0004
Revises: 0003
"""

from alembic import context, op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None

READ = ("tenant", "tenant_database", "alembic_version")
WRITE = ("app_user", "refresh_token", "feature_flag", "tenant_setting", "job")


def _app() -> str:
    role = context.config.attributes.get("app_role")
    if not role:
        raise RuntimeError("run migrations through p2p.cli: it names the app's database login")
    return '"' + str(role).replace('"', '""') + '"'


def upgrade() -> None:
    app = _app()
    op.execute("revoke create on schema public from public")
    op.execute(f"grant usage on schema public to {app}")
    op.execute(f"grant select on {', '.join(READ)} to {app}")
    op.execute(f"grant select, insert, update, delete on {', '.join(WRITE)} to {app}")
    op.execute(f"grant usage, select on all sequences in schema public to {app}")


def downgrade() -> None:
    app = _app()
    op.execute(f"revoke all on all sequences in schema public from {app}")
    op.execute(f"revoke all on {', '.join(READ + WRITE)} from {app}")
    op.execute(f"revoke usage on schema public from {app}")
    op.execute("grant create on schema public to public")
