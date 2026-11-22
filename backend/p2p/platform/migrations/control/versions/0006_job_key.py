"""A real key on jobs: one waiting or running job per company, kind and key (task 11a.1, DF-23)

`worker.enqueue(..., key=)` inserts against this index, so two callers at once can never both queue the same work (the
old check-then-insert ran in two transactions). Once a job is done or failed its key is free again.

Revision ID: 0006
Revises: 0005
"""

from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("alter table job add column dedupe_key text")
    op.execute(
        "create unique index ux_job_key on job (tenant_slug, kind, dedupe_key) "
        "where dedupe_key is not null and status in ('queued', 'running')"
    )


def downgrade() -> None:
    op.execute("drop index ux_job_key")
    op.execute("alter table job drop column dedupe_key")
