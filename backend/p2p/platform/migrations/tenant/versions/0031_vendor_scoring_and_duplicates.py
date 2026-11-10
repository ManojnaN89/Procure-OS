"""The duplicate-request flags raised on a request, and the reason a person gave for going ahead anyway

Revision ID: 0031
Revises: 0030
"""

from alembic import op

revision = "0031"
down_revision = "0030"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # One row per other request a request looks like a repeat of. The scores of suppliers are worked out when asked for
    # from the orders and receipts, so nothing is stored for them.
    op.execute(
        """
        create table request_check (
            id bigserial primary key,
            request_no text not null references request (no) on delete cascade,
            kind text not null default 'duplicate' check (kind in ('duplicate')),
            other_request text not null,
            similarity numeric(4,3) not null,
            detail jsonb,
            flagged_at timestamptz not null default now(),
            override_reason text,
            overridden_by text,
            overridden_at timestamptz,
            unique (request_no, kind, other_request)
        )
        """
    )


def downgrade() -> None:
    op.execute("drop table request_check")
