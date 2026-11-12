"""Reconciliation (plan step 13, P1-39): a supplier's statement of account matched line by line against our invoices,
credit notes and payments, and every line of an imported bank statement kept so the ones that did not match can be
matched by a person

Revision ID: 0054
Revises: 0053
"""

from alembic import op

revision = "0054"
down_revision = "0053"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # one supplier statement of account as imported: the same file for the same supplier is imported once
    op.execute(
        """
        create table vendor_statement (
            id bigserial primary key,
            supplier_code text not null references supplier (code),
            period_from date not null,
            period_to date not null,
            file_name text,
            file_hash text not null,
            lines integer not null,
            matched integer not null default 0,
            amount_differs integer not null default 0,
            only_theirs integer not null default 0,
            only_ours integer not null default 0,
            imported_by text not null,
            imported_at timestamptz not null default now(),
            unique (supplier_code, file_hash),
            check (period_to >= period_from)
        )
        """
    )
    # the result of matching, one row per line of their statement and per item of ours that is not on it
    op.execute(
        """
        create table vendor_statement_line (
            id bigserial primary key,
            statement_id bigint not null references vendor_statement (id),
            row_no integer,
            doc_date date,
            doc_type text not null check (doc_type in ('invoice', 'credit_note', 'debit_note', 'payment')),
            doc_no text,
            their_amount numeric(18,2),
            our_ref text,
            our_amount numeric(18,2),
            difference numeric(18,2),
            outcome text not null check (outcome in ('matched', 'amount_differs', 'only_theirs', 'only_ours')),
            reason text not null
        )
        """
    )
    op.execute("create index vendor_statement_line_statement on vendor_statement_line (statement_id, id)")
    # every debit line of an imported bank statement; the same bank line (in this or an overlapping statement) is kept once
    op.execute(
        """
        create table bank_statement_line (
            id bigserial primary key,
            line_key text not null unique,
            row_no integer not null,
            value_date date,
            utr text,
            narration text,
            amount numeric(18,2) not null,
            status text not null check (status in ('matched', 'waiting', 'open', 'confirmed')),
            payment_no text references payment (no),
            why text,
            imported_by text not null,
            imported_at timestamptz not null default now(),
            confirmed_by text,
            confirmed_at timestamptz
        )
        """
    )
    op.execute("create index bank_statement_line_status on bank_statement_line (status, value_date)")


def downgrade() -> None:
    op.execute("drop table bank_statement_line")
    op.execute("drop table vendor_statement_line")
    op.execute("drop table vendor_statement")
