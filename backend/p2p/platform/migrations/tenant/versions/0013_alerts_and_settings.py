"""Notifications can be company-wide alerts with a severity that someone resolves; app settings

Revision ID: 0013
Revises: 0012
"""

from alembic import op

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # an alert with no user is for everyone in the company
    op.execute("alter table notification alter column user_id drop not null")
    op.execute(
        "alter table notification add column severity text not null default 'low' "
        "check (severity in ('low', 'medium', 'high'))"
    )
    op.execute("alter table notification add column related_type text")
    op.execute("alter table notification add column related_id text")
    op.execute("alter table notification add column resolved boolean not null default false")
    op.execute("alter table notification add column resolved_at timestamptz")
    op.execute(
        "create table app_setting (key text primary key, value text not null, "
        "updated_at timestamptz not null default now())"
    )


def downgrade() -> None:
    op.execute("drop table app_setting")
    for col in ("resolved_at", "resolved", "related_id", "related_type", "severity"):
        op.execute(f"alter table notification drop column {col}")
