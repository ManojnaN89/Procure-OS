"""What the supplier communication agent read and did, what suppliers promised, and when they were chased

Revision ID: 0036
Revises: 0035
"""

from alembic import op

revision = "0036"
down_revision = "0035"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # one row per email the agent looked at: what it was taken to be and what was done about it
    op.execute(
        """
        create table supplier_message (
            id bigserial primary key,
            email_id bigint not null unique,
            linked_type text not null,
            linked_ref text not null,
            supplier_code text,
            kind text,
            status text not null check (status in ('applied', 'task', 'noted', 'ignored', 'needs_person', 'handled_elsewhere')),
            reading jsonb,
            action text,
            task_id bigint,
            reply_draft text,
            confidence numeric(4,3),
            model text,
            prompt_version text,
            created_at timestamptz not null default now()
        )
        """
    )
    op.execute("create index supplier_message_ref on supplier_message (linked_ref)")
    # what a supplier has promised, which is not the same as what was ordered: the order itself never changes here
    op.execute(
        """
        create table po_commitment (
            id bigserial primary key,
            po_no text not null references po (no),
            po_line_no integer,
            promised_qty numeric(18,4),
            promised_date date,
            email_id bigint,
            note text,
            created_at timestamptz not null default now()
        )
        """
    )
    op.execute("create index po_commitment_po on po_commitment (po_no)")
    # reminders and chasers sent (or, when the autonomy gate said no, handed to a buyer as a task)
    op.execute(
        """
        create table supplier_chase (
            id bigserial primary key,
            po_no text not null references po (no),
            kind text not null check (kind in ('ack', 'balance')),
            how text not null check (how in ('email', 'task')),
            sent_at timestamptz not null default now(),
            task_id bigint
        )
        """
    )
    op.execute("create index supplier_chase_po on supplier_chase (po_no, kind, sent_at)")


def downgrade() -> None:
    op.execute("drop table supplier_chase")
    op.execute("drop table po_commitment")
    op.execute("drop table supplier_message")
