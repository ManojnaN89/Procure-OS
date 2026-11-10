"""Invoices read from documents: what was read, waiting for AP to confirm

Revision ID: 0035
Revises: 0034
"""

from alembic import op

revision = "0035"
down_revision = "0034"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # One draft per document. The document is read (the QR code by code, the rest by a model whose every value must be
    # found in the text); AP confirms the draft, which enters the invoice through the same checks as an invoice typed by
    # hand, or - only if an administrator has set 'invoice.read' to automatic and nothing is in doubt - the gate does.
    op.execute(
        """
        create table invoice_draft (
            id bigserial primary key,
            source text not null check (source in ('email', 'upload')),
            email_id bigint,
            document_id bigint,
            filename text,
            document_hash text,
            supplier_code text,
            status text not null default 'pending' check (status in ('pending', 'needs_person', 'applied', 'dismissed')),
            path text check (path in ('qr', 'model', 'none')),
            reading jsonb,
            qr jsonb,
            proposal jsonb,
            problems jsonb not null default '[]',
            assumptions jsonb not null default '[]',
            confidence numeric,
            model text,
            prompt_version text,
            invoice_no text,
            decided_by text,
            decided_at timestamptz,
            decision_note text,
            created_by text,
            created_at timestamptz not null default now()
        )
        """
    )
    op.execute("create index invoice_draft_status on invoice_draft (status, id desc)")
    # the same file twice is one document (a supplier that sends its invoice again, or two mailboxes that both got it)
    op.execute(
        "create unique index invoice_draft_hash on invoice_draft (document_hash) "
        "where document_hash is not null and status in ('pending', 'needs_person', 'applied')"
    )
    op.execute(
        "create unique index invoice_draft_email_doc on invoice_draft (email_id, document_id) where email_id is not null"
    )


def downgrade() -> None:
    op.execute("drop table invoice_draft")
