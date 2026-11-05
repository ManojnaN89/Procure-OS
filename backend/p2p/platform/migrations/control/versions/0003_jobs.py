"""Durable jobs for the worker: timers, escalations, emails, mailbox polling

Revision ID: 0003
Revises: 0002
"""

from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        create table job (
            id bigserial primary key,
            tenant_slug text not null,
            kind text not null,
            payload jsonb not null default '{}',
            run_at timestamptz not null default now(),
            status text not null default 'queued' check (status in ('queued', 'running', 'done', 'failed')),
            attempts int not null default 0,
            locked_at timestamptz,
            last_error text,
            created_at timestamptz not null default now(),
            finished_at timestamptz
        )
        """
    )
    op.execute("create index ix_job_due on job (run_at) where status in ('queued', 'running')")


def downgrade() -> None:
    op.execute("drop table job")
