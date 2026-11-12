"""Service entry and receipt without an order (plan step 9, P1-37): the requester's confirmation that a service was done,
goods taken in without an order as a flagged exception, and serial numbers for serialised items

Revision ID: 0053
Revises: 0051
"""

import sqlalchemy as sa
from alembic import op

revision = "0053"
down_revision = "0051"
branch_labels = None
depends_on = None

SERIES = "{prefix}-{loc}-{fy4}-{seq:05d}"


def upgrade() -> None:
    # an item that needs one serial number per unit on receipt (laptops, for example)
    op.execute("alter table item add column serialised boolean default false")
    # the requester's confirmation that a service on an order line was done (the service's receipt); what it confirms
    # counts as received on the line, so the invoice is matched against it (two-way: order and confirmation)
    op.execute(
        """
        create table service_entry (
            no text primary key,
            po_no text not null,
            po_line_no integer not null,
            period_from date,
            period_to date,
            basis text not null check (basis in ('qty', 'pct', 'amount')),
            value numeric(18,4) not null check (value > 0),
            qty_accepted numeric(18,4) not null check (qty_accepted > 0),
            amount_accepted numeric(18,2) not null,
            accepted_by text not null,
            accepted_on date not null,
            via text not null default 'app' check (via in ('app', 'emailed link')),
            remarks text,
            posted_by text not null,
            created_at timestamptz not null default now(),
            foreign key (po_no, po_line_no) references po_line (po_no, line_no),
            check (period_to is null or period_from is null or period_to >= period_from)
        )
        """
    )
    op.execute("create index service_entry_line on service_entry (po_no, po_line_no)")
    # goods taken in without an order: a flagged exception. Nothing enters stock until the purchase manager approves,
    # the buyer raises the order afterwards and the store posts the goods receipt on it (or the goods go back)
    op.execute(
        """
        create table unordered_receipt (
            no text primary key,
            supplier_code text not null references supplier (code),
            item_code text not null references item (code),
            qty numeric(18,4) not null check (qty > 0),
            uom text,
            location_code text not null,
            reason text not null,
            challan text,
            serials jsonb not null default '[]',
            received_by text not null,
            posted_by text not null,
            received_on date not null,
            status text not null default 'pending'
                check (status in ('pending', 'approved', 'rejected', 'ordered', 'received', 'returned')),
            proposal_id bigint,
            decided_by text,
            decided_at timestamptz,
            decision_note text,
            po_no text references po (no),
            gr_no text references goods_receipt (no),
            returned_on date,
            return_ref text,
            created_at timestamptz not null default now()
        )
        """
    )
    op.execute("create index unordered_receipt_status on unordered_receipt (status, created_at desc)")
    # one serial number per unit received of a serialised item; a serial is unique per item
    op.execute(
        """
        create table serial_number (
            item_code text not null references item (code),
            serial text not null,
            gr_no text not null references goods_receipt (no),
            po_no text not null,
            po_line_no integer not null,
            created_at timestamptz not null default now(),
            primary key (item_code, serial)
        )
        """
    )
    # the request behind an order raised after the goods came (it was approved as the exception, not by the chain)
    op.execute("alter table request drop constraint request_channel_check")
    op.execute(
        "alter table request add constraint request_channel_check "
        "check (channel in ('form', 'email', 'chat', 'planning', 'unordered_receipt'))"
    )
    op.execute(
        "insert into role_permission (role, action) values ('Approver', 'grn.unordered_approve') on conflict do nothing"
    )
    # a company already set up gets the number series; a new one loads it with its master data
    for doc, prefix in (("unordered_receipt", "URN"), ("service_receipt", "SRN")):
        op.get_bind().execute(
            sa.text(
                "insert into number_series (document_type, prefix, scope, format, reset) "
                "select :d, :p, 'location', :f, 'fy' where exists (select 1 from number_series) "
                "and not exists (select 1 from number_series where document_type = :d)"
            ),
            {"d": doc, "p": prefix, "f": SERIES},
        )


def downgrade() -> None:
    op.execute("delete from role_permission where action = 'grn.unordered_approve'")
    op.execute("update request set channel = 'planning' where channel = 'unordered_receipt'")
    op.execute("alter table request drop constraint request_channel_check")
    op.execute(
        "alter table request add constraint request_channel_check check (channel in ('form', 'email', 'chat', 'planning'))"
    )
    op.execute("drop table serial_number")
    op.execute("drop table unordered_receipt")
    op.execute("drop table service_entry")
    op.execute("alter table item drop column serialised")
