"""Stored email, linked to the RFQ, PO or invoice it is about

Revision ID: 0012
Revises: 0011
"""

from alembic import op

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        create table email_message (
            id serial primary key,
            message_id text not null unique,
            in_reply_to text,
            direction text not null check (direction in ('in', 'out')),
            from_addr text,
            to_addr text,
            subject text,
            body_text text,
            sent_at timestamptz,
            plus_tag text,
            linked_type text,
            linked_id text,
            case_id int references purchase_case (id),
            raw_document_id int references document (id),
            attachment_ids jsonb not null default '[]',
            created_at timestamptz not null default now()
        )
        """
    )
    op.execute("create index ix_email_link on email_message (linked_type, linked_id)")
    op.execute("create index ix_email_reply on email_message (in_reply_to)")


def downgrade() -> None:
    op.execute("drop table email_message")
