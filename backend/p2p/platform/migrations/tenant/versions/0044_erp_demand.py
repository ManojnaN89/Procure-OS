"""Demand from the client's ERP: production orders and the materials they need, MRP results, sales orders and the demand
plan, each with its ERP document number; the column mapping per input kind; one row per import run

Revision ID: 0044
Revises: 0043
"""

from alembic import op

revision = "0044"
down_revision = "0043"
branch_labels = None
depends_on = None

# who loads the ERP files: the store (it issues the material) and the administrator (setting up the link)
GRANTS = [("Store", "demand.import"), ("Admin", "demand.import")]


def upgrade() -> None:
    op.execute(
        """
        create table erp_column_map (
            kind text not null,
            version integer not null,
            columns jsonb not null,
            created_by text,
            created_at timestamptz not null default now(),
            primary key (kind, version)
        )
        """
    )
    op.execute(
        """
        create table demand_import (
            id bigserial primary key,
            source text not null,
            files jsonb not null,
            loaded integer not null,
            already_loaded integer not null,
            rejected integer not null,
            imported_by text,
            imported_at timestamptz not null default now()
        )
        """
    )
    # item and location codes are checked by the import, not by foreign keys, so a masters re-import is never blocked
    op.execute(
        """
        create table production_order (
            id bigserial primary key,
            order_no text not null unique,
            product_code text not null,
            qty numeric(18,4) not null check (qty > 0),
            need_by date not null,
            reason text,
            location_code text not null,
            status text not null default 'open',
            import_id bigint not null references demand_import (id),
            created_at timestamptz not null default now()
        )
        """
    )
    op.execute(
        """
        create table material_requirement (
            id bigserial primary key,
            source_kind text not null check (source_kind in ('production_order', 'mrp')),
            source_doc_no text not null,
            line_key text not null,
            production_order_id bigint references production_order (id),
            item_code text not null,
            qty numeric(18,4) not null check (qty > 0),
            unit text not null,
            base_qty numeric(18,4) not null check (base_qty > 0),
            need_by date not null,
            location_code text not null,
            status text not null default 'open',
            import_id bigint not null references demand_import (id),
            created_at timestamptz not null default now(),
            unique (source_kind, source_doc_no, line_key)
        )
        """
    )
    op.execute("create index material_requirement_open on material_requirement (status, need_by)")
    op.execute(
        """
        create table product_demand (
            id bigserial primary key,
            kind text not null check (kind in ('sales_order', 'demand_plan')),
            doc_no text not null,
            product_code text not null,
            customer text,
            qty numeric(18,4) not null check (qty > 0),
            need_by date,
            period text,
            status text,
            import_id bigint not null references demand_import (id),
            created_at timestamptz not null default now(),
            unique (kind, doc_no, product_code)
        )
        """
    )
    for role, action in GRANTS:
        op.execute(f"insert into role_permission (role, action) values ('{role}', '{action}') on conflict do nothing")  # noqa: S608


def downgrade() -> None:
    for role, action in GRANTS:
        op.execute(f"delete from role_permission where role = '{role}' and action = '{action}'")  # noqa: S608
    op.execute("drop table product_demand")
    op.execute("drop table material_requirement")
    op.execute("drop table production_order")
    op.execute("drop table demand_import")
    op.execute("drop table erp_column_map")
