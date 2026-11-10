"""Supplier invoices, their lines and charges, and what the three-way match found

Revision ID: 0027
Revises: 0026
"""

from alembic import op

revision = "0027"
down_revision = "0026"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # A credit note carries positive amounts and is linked to the invoice it reduces. A copy of an invoice already on
    # file is kept as a cancelled record that points at the original, so nothing is paid twice and the copy is not lost.
    op.execute(
        """
        create table invoice (
            no text primary key,
            type text not null default 'invoice' check (type in ('invoice', 'credit_note', 'debit_note')),
            supplier_code text not null references supplier (code) deferrable initially deferred,
            supplier_gstin text,
            buyer_gstin text,
            place_of_supply text,
            supplier_invoice_no text not null,
            dedupe_key text not null,
            invoice_date date not null,
            fy text not null,
            irn text,
            ack_no text,
            ack_date date,
            eway_bill_no text,
            reverse_charge boolean not null default false,
            received_at timestamptz not null default now(),
            received_on date not null,
            channel text not null default 'manual' check (channel in ('email', 'upload', 'portal', 'manual')),
            taxable numeric(18,2) not null,
            cgst numeric(18,2) not null default 0,
            sgst numeric(18,2) not null default 0,
            igst numeric(18,2) not null default 0,
            cess numeric(18,2) not null default 0,
            round_off numeric(18,2) not null default 0,
            total numeric(18,2) not null,
            ref_invoice_no text,
            due_date date,
            payment_block text not null default 'none' check (payment_block in ('none', 'match', 'manual', 'duplicate', 'tax')),
            status text not null default 'received'
                check (status in ('received', 'matched', 'blocked', 'approved', 'part_paid', 'paid', 'cancelled')),
            duplicate_of text,
            block_reason text,
            cancel_reason text,
            document_ref text,
            entered_by text,
            created_at timestamptz not null default now()
        )
        """
    )
    op.execute("create index invoice_dedupe on invoice (supplier_code, fy, dedupe_key)")
    op.execute("create index invoice_status on invoice (status, received_on desc)")
    op.execute(
        """
        create table invoice_line (
            invoice_no text not null references invoice (no) on delete cascade,
            line_no integer not null,
            po_no text not null,
            po_line_no integer not null,
            description text,
            hsn_sac text,
            qty numeric(18,4) not null,
            uom text,
            unit_price numeric(18,4) not null,
            discount numeric(18,4) not null default 0,
            taxable numeric(18,2) not null,
            gst_rate numeric(5,2) not null default 0,
            cgst numeric(18,2) not null default 0,
            sgst numeric(18,2) not null default 0,
            igst numeric(18,2) not null default 0,
            primary key (invoice_no, line_no),
            foreign key (po_no, po_line_no) references po_line (po_no, line_no)
        )
        """
    )
    op.execute(
        """
        create table invoice_charge (
            id bigserial primary key,
            invoice_no text not null references invoice (no) on delete cascade,
            type text not null,
            amount numeric(18,2) not null,
            gst_rate numeric(5,2) not null default 0,
            sac text
        )
        """
    )
    # one row per invoice line that was looked at, and one without a line for a problem with the invoice as a whole
    op.execute(
        """
        create table match_result (
            id bigserial primary key,
            invoice_no text not null references invoice (no) on delete cascade,
            invoice_line_no integer,
            result text not null check (result in ('matched', 'within_tolerance', 'blocked')),
            reason_codes text,
            price_var numeric(18,4),
            qty_var numeric(18,4),
            detail text,
            created_at timestamptz not null default now(),
            resolved_by text,
            resolved_at timestamptz,
            resolution text
        )
        """
    )
    op.execute("create index match_result_invoice on match_result (invoice_no)")
    op.execute(
        "insert into role_permission (role, action) values ('AP', 'invoice.view'), ('AP', 'invoice.book'), "
        "('AP', 'invoice.match'), ('Finance', 'invoice.view'), ('Finance', 'invoice.approve') on conflict do nothing"
    )


def downgrade() -> None:
    op.execute("drop table match_result")
    op.execute("drop table invoice_charge")
    op.execute("drop table invoice_line")
    op.execute("drop table invoice")
