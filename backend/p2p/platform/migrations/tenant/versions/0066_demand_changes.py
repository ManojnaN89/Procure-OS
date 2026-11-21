"""Changed and cancelled ERP demand, and MRP results as material requests (tasks 6.3 and 6.4)

A row the ERP sends again with other values is applied: each applied change is kept (`demand_change`: what changed,
before, after, and its effects on requests, reservations and cases). Where the buyer already has what the change made
too much, or the store issued more than is now needed, a case is opened for a person (`demand_change_case`, one per
change and document, with its task). An MRP result is gross (the platform nets stock) or net (the ERP already did), and
an MRP plan gets a material request per location, without a product. An issued line may now need less than was issued.

Revision ID: 0066
Revises: 0065
"""

from alembic import op

revision = "0066"
down_revision = "0065"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("alter table demand_import add column changed integer not null default 0")
    op.execute(
        """
        create table demand_change (
            id bigserial primary key,
            import_id bigint not null references demand_import (id),
            kind text not null,
            doc_no text not null,
            line_key text,
            requirement_id bigint references material_requirement (id),
            mr_no text,
            what text[] not null,
            before jsonb not null,
            after jsonb not null,
            effects jsonb not null default '{}',
            applied_at timestamptz not null default now()
        )
        """
    )
    op.execute("create index demand_change_doc on demand_change (doc_no)")
    op.execute("create index demand_change_import on demand_change (import_id)")
    op.execute(
        """
        create table demand_change_case (
            task_id integer primary key references task (id),
            change_id bigint not null references demand_change (id),
            owner_kind text not null check (owner_kind in ('buyer', 'store')),
            doc_type text not null check (doc_type in ('po', 'rfq', 'request', 'material_request')),
            doc_no text not null,
            lines jsonb not null,
            excess jsonb not null,
            amount numeric(18,2),
            outcome text check (outcome in ('reduced', 'cancelled', 'kept_for_stock', 'expedited', 'not_possible',
                                            'returned_to_stock', 'kept_for_production')),
            note text,
            closed_by text,
            closed_at timestamptz
        )
        """
    )
    op.execute(
        "alter table material_requirement add column basis text not null default 'gross' "
        "check (basis in ('gross', 'net'))"
    )
    op.execute(
        "alter table material_requirement add constraint material_requirement_status_check "
        "check (status in ('open', 'issued', 'cancelled'))"
    )
    op.execute(
        "alter table production_order add constraint production_order_status_check "
        "check (status in ('open', 'cancelled'))"
    )
    op.execute("alter table material_request drop constraint material_request_source_kind_check")
    op.execute(
        "alter table material_request add constraint material_request_source_kind_check "
        "check (source_kind in ('production_order', 'joiner', 'mrp'))"
    )
    op.execute("alter table material_request alter column product_code drop not null")
    # a line cut below what was already issued closes as issued: only an open line must hold no more than it needs
    op.execute("alter table material_request_line drop constraint material_request_line_check")
    op.execute(
        "alter table material_request_line add constraint material_request_line_check "
        "check (status <> 'open' or reserved_qty + issued_qty <= need_qty)"
    )
    op.execute(
        "insert into app_setting (key, value) values ('demand_change_case_days', '2') on conflict (key) do nothing"
    )


def downgrade() -> None:
    op.execute("delete from app_setting where key = 'demand_change_case_days'")
    op.execute("alter table material_request_line drop constraint material_request_line_check")
    op.execute(
        "alter table material_request_line add constraint material_request_line_check "
        "check (reserved_qty + issued_qty <= need_qty)"
    )
    op.execute("alter table material_request alter column product_code set not null")
    op.execute("alter table material_request drop constraint material_request_source_kind_check")
    op.execute(
        "alter table material_request add constraint material_request_source_kind_check "
        "check (source_kind in ('production_order', 'joiner'))"
    )
    op.execute("alter table production_order drop constraint production_order_status_check")
    op.execute("alter table material_requirement drop constraint material_requirement_status_check")
    op.execute("alter table material_requirement drop column basis")
    op.execute("drop table demand_change_case")
    op.execute("drop table demand_change")
    op.execute("alter table demand_import drop column changed")
