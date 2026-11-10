"""Alerts raised by rules: one open alert per subject until its cause goes away

Revision ID: 0037
Revises: 0036
"""

from alembic import op

revision = "0037"
down_revision = "0036"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # `key` is the rule and what it is about ("stale_requisition:REQ-1"): one row per key, so a problem is raised once, kept
    # while it lasts, resolved when it goes, and raised again if it comes back.
    op.execute(
        """
        create table alert (
            id bigserial primary key,
            key text not null unique,
            rule text not null,
            subject_type text not null,
            subject_id text not null,
            severity text not null check (severity in ('low', 'medium', 'high')),
            title text not null,
            detail text,
            link text,
            roles text not null,
            status text not null default 'open' check (status in ('open', 'resolved')),
            opened_at timestamptz not null default now(),
            resolved_at timestamptz,
            last_seen_at timestamptz not null default now(),
            times_opened integer not null default 1
        )
        """
    )
    op.execute("create index alert_open on alert (status, rule)")


def downgrade() -> None:
    op.execute("drop table alert")
