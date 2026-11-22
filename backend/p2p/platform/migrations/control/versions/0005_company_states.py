"""Company states and safe user creation (block 0d, task 7.1, D-04, D-05, DF-36)

A company is in setup, cutover, live or suspended (`tenant.state`; a suspended one remembers the state it was in). Every
move is a row of `tenant_state_change`, insert only to the app. A cutover keeps the setup database as an archive
(`tenant_archive`). A login is usable only once its profile is written in the company's database (`provisioned_at`).
A queued job of a company moved to cutover is cancelled. Companies that exist when this runs are running companies:
they become live.

Revision ID: 0005
Revises: 0004
"""

from alembic import context, op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def _app() -> str:
    role = context.config.attributes.get("app_role")
    if not role:
        raise RuntimeError("run migrations through p2p.cli: it names the app's database login")
    return '"' + str(role).replace('"', '""') + '"'


def upgrade() -> None:
    app = _app()
    op.execute("alter table tenant add column state text not null default 'live'")
    op.execute("alter table tenant alter column state set default 'setup'")
    op.execute(
        "alter table tenant add constraint tenant_state_check check (state in ('setup', 'cutover', 'live', 'suspended'))"
    )
    op.execute(
        "alter table tenant add column state_before_suspension text "
        "check (state_before_suspension in ('setup', 'cutover', 'live'))"
    )
    op.execute("alter table tenant add column state_changed_at timestamptz not null default now()")
    op.execute("alter table app_user add column provisioned_at timestamptz")
    op.execute("update app_user set provisioned_at = created_at")
    op.execute("alter table job drop constraint job_status_check")
    op.execute(
        "alter table job add constraint job_status_check "
        "check (status in ('queued', 'running', 'done', 'failed', 'cancelled'))"
    )
    op.execute(
        """
        create table tenant_state_change (
            id serial primary key,
            tenant_id integer not null references tenant (id),
            from_state text not null,
            to_state text not null,
            at timestamptz not null default now(),
            by_kind text not null check (by_kind in ('user', 'operator', 'system')),
            by_id text not null,
            approved_by text,
            kind text check (kind in ('commercial', 'security')),
            reason text not null check (length(trim(reason)) > 0)
        )
        """
    )
    op.execute(
        """
        create table tenant_archive (
            id serial primary key,
            tenant_id integer not null references tenant (id),
            database_name text not null unique,
            archived_at timestamptz not null default now(),
            operator text not null,
            reason text not null,
            audit_rows integer not null,
            audit_last_hash text,
            setup_change_last_hash text
        )
        """
    )
    op.execute(f"grant update (state, state_before_suspension, state_changed_at) on tenant to {app}")
    op.execute(f"grant select, insert on tenant_state_change to {app}")
    op.execute(f"grant select on tenant_archive to {app}")
    op.execute(f"grant usage, select on sequence tenant_state_change_id_seq to {app}")


def downgrade() -> None:
    app = _app()
    op.execute(f"revoke all on tenant_state_change, tenant_archive from {app}")
    op.execute(f"revoke update (state, state_before_suspension, state_changed_at) on tenant from {app}")
    op.execute("drop table tenant_archive")
    op.execute("drop table tenant_state_change")
    op.execute("update job set status = 'failed' where status = 'cancelled'")
    op.execute("alter table job drop constraint job_status_check")
    op.execute(
        "alter table job add constraint job_status_check check (status in ('queued', 'running', 'done', 'failed'))"
    )
    op.execute("alter table app_user drop column provisioned_at")
    op.execute("alter table tenant drop column state_changed_at")
    op.execute("alter table tenant drop column state_before_suspension")
    op.execute("alter table tenant drop column state")
