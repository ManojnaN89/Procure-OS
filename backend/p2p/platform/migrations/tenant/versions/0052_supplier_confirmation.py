"""Supplier confirmation (plan step 8, P1-36): the supplier's signed links, the changes it asks for, dispatch notes and the
reminders about delivery

Revision ID: 0052
Revises: 0051
"""

from alembic import op

revision = "0052"
down_revision = "0051"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # a link in an email to a supplier: one order, one supplier, one purpose; it expires and works once
    op.execute(
        """
        create table supplier_link (
            id bigserial primary key,
            po_no text not null references po (no),
            supplier_code text not null,
            purpose text not null check (purpose in ('respond', 'dispatch')),
            po_version integer not null,
            issued_at timestamptz not null default now(),
            expires_at timestamptz not null,
            used_at timestamptz,
            used_for text
        )
        """
    )
    op.execute("create index supplier_link_po on supplier_link (po_no)")
    # a change the supplier asks for, old value to new, for the buyer to accept or reject
    op.execute(
        """
        create table po_change (
            id bigserial primary key,
            po_no text not null references po (no),
            po_line_no integer,
            kind text not null check (kind in ('date', 'qty', 'price', 'decline')),
            old_value text,
            new_value text,
            reason text,
            source text not null check (source in ('link', 'email')),
            email_id bigint,
            status text not null default 'proposed'
                check (status in ('proposed', 'waiting_approval', 'accepted', 'rejected', 'withdrawn')),
            within_tolerance boolean,
            task_id bigint,
            proposal_id bigint,
            decided_by text,
            decided_at timestamptz,
            decision_note text,
            version_made integer,
            created_at timestamptz not null default now()
        )
        """
    )
    op.execute("create index po_change_po on po_change (po_no, status)")
    # what the supplier says it has shipped; the store sees it as an expected arrival
    op.execute(
        """
        create table dispatch_note (
            id bigserial primary key,
            po_no text not null references po (no),
            shipped_on date not null,
            expected_on date,
            vehicle_no text,
            lr_no text,
            invoice_no text,
            source text not null check (source in ('link', 'email')),
            email_id bigint,
            recorded_by text not null,
            created_at timestamptz not null default now()
        )
        """
    )
    op.execute("create index dispatch_note_po on dispatch_note (po_no)")
    op.execute(
        """
        create table dispatch_note_line (
            note_id bigint not null references dispatch_note (id) on delete cascade,
            po_line_no integer not null,
            qty numeric(18,4) not null check (qty > 0),
            primary key (note_id, po_line_no)
        )
        """
    )
    # reminders about a delivery date, each sent and recorded once
    op.execute(
        """
        create table delivery_reminder (
            id bigserial primary key,
            po_no text not null references po (no),
            kind text not null check (kind in ('before', 'overdue')),
            promised_date date not null,
            number integer not null,
            sent_to text,
            sent_at timestamptz not null default now(),
            unique (po_no, kind, promised_date, number)
        )
        """
    )


def downgrade() -> None:
    for table in ("delivery_reminder", "dispatch_note_line", "dispatch_note", "po_change", "supplier_link"):
        op.execute(f"drop table {table}")
