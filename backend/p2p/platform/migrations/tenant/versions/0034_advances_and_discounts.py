"""Advance payments against orders, their adjustment against invoices, and early-payment discounts

Revision ID: 0034
Revises: 0033
"""

from alembic import op

revision = "0034"
down_revision = "0033"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # An advance is a payment of type 'advance' against an order. What is left of it (gross and the taxable part of it) is
    # taken off the invoices of that order: `used_*` grows as invoices are booked, and each use is an advance_adjustment.
    op.execute(
        """
        create table advance (
            payment_no text primary key references payment (no),
            po_no text not null references po (no),
            gross numeric(18,2) not null,
            taxable numeric(18,2) not null,
            used_gross numeric(18,2) not null default 0,
            used_taxable numeric(18,2) not null default 0,
            paid_on date
        )
        """
    )
    op.execute("create index advance_po on advance (po_no)")
    op.execute(
        """
        create table advance_adjustment (
            id bigserial primary key,
            advance_payment_no text not null references payment (no),
            invoice_no text not null references invoice (no),
            amount numeric(18,2) not null,
            taxable numeric(18,2) not null default 0,
            unique (advance_payment_no, invoice_no)
        )
        """
    )
    op.execute(
        "alter table invoice add column advance_used numeric(18,2) not null default 0, "
        "add column advance_taxable_used numeric(18,2) not null default 0, "
        "add column discount_taken numeric(18,2) not null default 0"
    )
    op.execute("alter table payment_allocation add column discount numeric(18,2) not null default 0")


def downgrade() -> None:
    op.execute("alter table payment_allocation drop column discount")
    op.execute(
        "alter table invoice drop column advance_used, drop column advance_taxable_used, drop column discount_taken"
    )
    op.execute("drop table advance_adjustment")
    op.execute("drop table advance")
