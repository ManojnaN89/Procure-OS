"""What the invoice exceptions agent found out about a blocked invoice

Revision ID: 0033
Revises: 0032
"""

from alembic import op

revision = "0033"
down_revision = "0032"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # One row per time an invoice was looked into. The block itself is never changed by this: it is advice for a person.
    op.execute(
        """
        create table invoice_diagnosis (
            id bigserial primary key,
            invoice_no text not null references invoice (no) on delete cascade,
            cause text not null,
            fix text not null check (fix in ('credit_note', 'corrected_invoice', 'wait_for_receipt', 'request_release')),
            explanation text not null,
            vendor_subject text,
            vendor_body text,
            source text not null check (source in ('agent', 'rules')),
            run_id bigint,
            created_by integer,
            created_at timestamptz not null default now()
        )
        """
    )
    op.execute("create index invoice_diagnosis_invoice on invoice_diagnosis (invoice_no, created_at desc)")


def downgrade() -> None:
    op.execute("drop table invoice_diagnosis")
