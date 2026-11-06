"""Supplier replies read from email, waiting for a person to confirm them

Revision ID: 0021
Revises: 0020
"""

from alembic import op

revision = "0021"
down_revision = "0020"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # One draft per incoming email. A model reads the email; code matches it to the RFQ lines; a person (or, if an
    # administrator has set 'quote.read' to automatic, the autonomy gate) turns a clean draft into a quote.
    op.execute(
        """
        create table quote_draft (
            id bigserial primary key,
            rfq_no text not null references rfq (no),
            supplier_code text,
            email_id bigint not null unique,
            intent text,
            status text not null default 'pending'
                check (status in ('pending', 'needs_person', 'applied', 'dismissed')),
            reading jsonb,
            proposal jsonb,
            problems jsonb not null default '[]',
            assumptions jsonb not null default '[]',
            confidence numeric(4,3),
            model text,
            prompt_version text,
            quote_no text,
            created_at timestamptz not null default now(),
            decided_by text,
            decided_at timestamptz,
            decision_note text
        )
        """
    )
    op.execute("create index quote_draft_rfq on quote_draft (rfq_no, status)")


def downgrade() -> None:
    op.execute("drop table quote_draft")
