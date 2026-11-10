"""Goods receipts: what arrived against an order, what was accepted, what was rejected

Revision ID: 0026
Revises: 0025
"""

from alembic import op

revision = "0026"
down_revision = "0025"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        create table goods_receipt (
            no text primary key,
            po_no text not null references po (no),
            location_code text not null,
            gate_entry_at timestamptz not null default now(),
            received_by text not null,
            delivery_challan_no text,
            supplier_invoice_ref text,
            eway_bill_no text,
            vehicle_no text,
            inspection_status text not null default 'not_required'
                check (inspection_status in ('not_required', 'pending', 'passed', 'failed')),
            accepted_on date,
            remarks text,
            posted_by text not null,
            created_at timestamptz not null default now()
        )
        """
    )
    op.execute("create index goods_receipt_po on goods_receipt (po_no)")
    # qty_accepted and qty_rejected stay empty while a line waits for inspection
    op.execute(
        """
        create table gr_line (
            gr_no text not null references goods_receipt (no) on delete cascade,
            po_no text not null,
            po_line_no integer not null,
            qty_received numeric(18,4) not null check (qty_received > 0),
            qty_accepted numeric(18,4) check (qty_accepted >= 0),
            qty_rejected numeric(18,4) check (qty_rejected >= 0),
            reject_reason text,
            primary key (gr_no, po_line_no),
            foreign key (po_no, po_line_no) references po_line (po_no, line_no),
            check (qty_accepted is null or qty_accepted + qty_rejected = qty_received)
        )
        """
    )


def downgrade() -> None:
    op.execute("drop table gr_line")
    op.execute("drop table goods_receipt")
