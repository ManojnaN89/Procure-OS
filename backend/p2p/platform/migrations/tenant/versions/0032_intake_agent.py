"""The intake agent: a request that is waiting for the requester's answer to one question

Revision ID: 0032
Revises: 0031
"""

from alembic import op

revision = "0032"
down_revision = "0031"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # The question asked, and everything said so far, so the answer can be read together with the first message.
    op.execute(
        """
        create table intake_pending (
            id bigserial primary key,
            user_id integer not null,
            channel text not null check (channel in ('chat', 'email')),
            source_text text not null,
            question text not null,
            asked text not null check (asked in ('item', 'qty', 'need_by', 'location')),
            attempts integer not null default 1,
            status text not null default 'waiting' check (status in ('waiting', 'done', 'dropped')),
            created_at timestamptz not null default now(),
            updated_at timestamptz not null default now()
        )
        """
    )
    op.execute("create index intake_pending_user on intake_pending (user_id, status, created_at desc)")


def downgrade() -> None:
    op.execute("drop table intake_pending")
