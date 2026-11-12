"""Supplier selection (plan step 6, P1-34): how quotes are compared as versioned rows, and a line selected from more than
one supplier

Revision ID: 0048
Revises: 0047
"""

from alembic import op

revision = "0048"
down_revision = "0047"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # One row per version. The method is the cheapest total cost or a weighted score; the weights and the tie-break order
    # are data so a selection can be repeated against the version it used. A change closes the old row.
    op.execute(
        """
        create table supplier_selection_rule (
            id bigserial primary key,
            version integer not null unique,
            effective_from date not null,
            effective_to date,
            method text not null check (method in ('total_cost', 'weighted')),
            weight_price numeric(6,2) not null,
            weight_delivery numeric(6,2) not null,
            weight_quality numeric(6,2) not null,
            weight_payment_terms numeric(6,2) not null,
            tie_break text not null,
            input_credit boolean not null,
            note text,
            created_by text,
            created_at timestamptz not null default now()
        )
        """
    )
    # the company's earlier choice of comparison (a plain setting until now) becomes version 1
    op.execute(
        "insert into supplier_selection_rule (version, effective_from, method, weight_price, weight_delivery, "
        "weight_quality, weight_payment_terms, tie_break, input_credit, note, created_by) values (1, '2000-01-01', "
        "case when exists (select 1 from org_setting where key = 'comparison_mode' and value = 'weighted') "
        "then 'weighted' else 'total_cost' end, 40, 30, 20, 10, 'delivery,vendor_score', true, "
        "'Plan default (step 6)', 'system:migration')"
    )
    op.execute("delete from org_setting where key = 'comparison_mode'")
    # a line may now go to more than one supplier, each for part of the quantity
    op.execute("alter table award drop constraint award_pkey")
    op.execute("alter table award add primary key (rfq_no, rfq_line_no, supplier_code)")
    op.execute("alter table award add column split boolean not null default false, add column selection_rule text")
    # a request line is still ordered once per source; a line shared between suppliers has one order line per quote
    op.execute("drop index po_line_request_line")
    op.execute(
        "create unique index po_line_request_line on po_line (request_no, request_line_no, source_ref) "
        "where status <> 'cancelled'"
    )


def downgrade() -> None:
    op.execute("drop index po_line_request_line")
    op.execute(
        "create unique index po_line_request_line on po_line (request_no, request_line_no) where status <> 'cancelled'"
    )
    op.execute("alter table award drop column selection_rule, drop column split")
    op.execute("delete from award a where exists (select 1 from award b where b.rfq_no = a.rfq_no "
               "and b.rfq_line_no = a.rfq_line_no and b.supplier_code < a.supplier_code)")  # fmt: skip
    op.execute("alter table award drop constraint award_pkey")
    op.execute("alter table award add primary key (rfq_no, rfq_line_no)")
    op.execute(
        "insert into org_setting (key, value) select 'comparison_mode', 'weighted' from supplier_selection_rule "
        "where effective_to is null and method = 'weighted' on conflict (key) do nothing"
    )
    op.execute("drop table supplier_selection_rule")
