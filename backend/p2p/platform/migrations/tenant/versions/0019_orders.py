"""Quotes, awards and purchase orders

Revision ID: 0019
Revises: 0018
"""

from alembic import op

revision = "0019"
down_revision = "0018"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        create table quote (
            no text primary key,
            rfq_no text not null references rfq (no),
            supplier_code text not null references supplier (code) deferrable initially deferred,
            revision integer not null default 1,
            received_at timestamptz not null default now(),
            valid_until date,
            payment_terms_code text,
            delivery_terms text,
            packing_forwarding numeric(18,2) not null default 0 check (packing_forwarding >= 0),
            freight numeric(18,2) not null default 0 check (freight >= 0),
            warranty_months integer,
            deviations text,
            document_ref text,
            entered_by text,
            unique (rfq_no, supplier_code, revision)
        )
        """
    )
    op.execute(
        """
        create table quote_line (
            quote_no text not null references quote (no) on delete cascade,
            rfq_line_no integer not null,
            item_code text,
            offered_brand text,
            qty numeric(18,4),
            moq numeric(18,4),
            unit_price numeric(18,4) not null check (unit_price > 0),
            discount_pct numeric(7,4) not null default 0 check (discount_pct >= 0 and discount_pct <= 100),
            gst_rate numeric(5,2) not null check (gst_rate >= 0 and gst_rate <= 100),
            delivery_days integer check (delivery_days >= 0),
            deviation text,
            primary key (quote_no, rfq_line_no)
        )
        """
    )
    op.execute(
        """
        create table award (
            rfq_no text not null references rfq (no),
            rfq_line_no integer not null,
            supplier_code text not null,
            quote_no text not null references quote (no),
            qty numeric(18,4) not null,
            price numeric(18,4) not null,
            reason text,
            overridden boolean not null default false,
            by_user text,
            at timestamptz not null default now(),
            primary key (rfq_no, rfq_line_no)
        )
        """
    )
    op.execute(
        """
        create table po (
            no text primary key,
            po_type text not null default 'standard'
                check (po_type in ('standard', 'service', 'capex', 'contract_release')),
            supplier_code text not null references supplier (code) deferrable initially deferred,
            supplier_gstin text,
            buyer_gstin text,
            ship_to_location text not null references location (code) deferrable initially deferred,
            buyer text,
            request_no text references request (no),
            rfq_no text references rfq (no),
            contract_code text,
            created_at timestamptz not null default now(),
            approved_at timestamptz,
            sent_at timestamptz,
            payment_terms_code text,
            delivery_terms text,
            taxable numeric(18,2) not null default 0,
            cgst numeric(18,2) not null default 0,
            sgst numeric(18,2) not null default 0,
            igst numeric(18,2) not null default 0,
            total numeric(18,2) not null default 0,
            status text not null default 'draft' check (status in
                ('draft', 'submitted', 'approved', 'sent', 'acknowledged', 'part_received', 'received', 'closed', 'cancelled')),
            cancel_reason text,
            rule_code text,
            rule_version integer,
            approval_round integer not null default 0,
            decision_reason text,
            hold_reason text,
            hold_proposal_id bigint
        )
        """
    )
    op.execute("create index po_status on po (status, created_at desc)")
    op.execute(
        """
        create table po_line (
            po_no text not null references po (no) on delete cascade,
            line_no integer not null,
            request_no text not null,
            request_line_no integer not null,
            item_code text references item (code) deferrable initially deferred,
            description text,
            qty numeric(18,4) not null check (qty > 0),
            uom text,
            base_qty numeric(18,4),
            unit_price numeric(18,4) not null check (unit_price > 0),
            est_price numeric(18,4),
            gst_rate numeric(5,2) not null,
            taxable numeric(18,2) not null,
            cgst numeric(18,2) not null default 0,
            sgst numeric(18,2) not null default 0,
            igst numeric(18,2) not null default 0,
            need_by date,
            promised_date date,
            received_qty numeric(18,4) not null default 0,
            invoiced_qty numeric(18,4) not null default 0,
            gl_code text,
            source text not null check (source in ('contract', 'award')),
            source_ref text,
            status text not null default 'open'
                check (status in ('open', 'part_received', 'received', 'closed', 'cancelled')),
            primary key (po_no, line_no)
        )
        """
    )
    # a request line is ordered once; a cancelled order line frees it to be ordered again
    op.execute(
        "create unique index po_line_request_line on po_line (request_no, request_line_no) where status <> 'cancelled'"
    )
    op.execute(
        """
        create table po_charge (
            id bigserial primary key,
            po_no text not null references po (no) on delete cascade,
            type text not null check (type in ('freight', 'packing_forwarding', 'installation', 'other')),
            amount numeric(18,2) not null,
            gst_rate numeric(5,2) not null default 0,
            cgst numeric(18,2) not null default 0,
            sgst numeric(18,2) not null default 0,
            igst numeric(18,2) not null default 0
        )
        """
    )
    op.execute(
        """
        create table po_approval_step (
            id bigserial primary key,
            po_no text not null references po (no) on delete cascade,
            round integer not null,
            step integer not null,
            role text not null,
            approver text not null,
            delegate text,
            status text not null default 'waiting'
                check (status in ('waiting', 'pending', 'approved', 'rejected', 'cancelled')),
            acted_by text,
            acted_at timestamptz,
            comment text,
            task_id bigint,
            unique (po_no, round, step)
        )
        """
    )


def downgrade() -> None:
    for table in ("po_approval_step", "po_charge", "po_line", "po", "award", "quote_line", "quote"):
        op.execute(f"drop table {table}")
