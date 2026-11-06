"""Purchase requests and their lines, the document number counters, and the permission to run replenishment

Revision ID: 0016
Revises: 0015
"""

from alembic import op

revision = "0016"
down_revision = "0015"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # the last number used per document type, location (for per-location series) and financial year
    op.execute(
        """
        create table number_counter (
            document_type text not null,
            scope_key text not null,
            fy text not null,
            last_seq integer not null,
            primary key (document_type, scope_key, fy)
        )
        """
    )
    op.execute(
        """
        create table request (
            no text primary key,
            requester text,
            on_behalf_of text,
            department_code text,
            ship_to_location text not null references location (code),
            type text not null check (type in ('goods', 'service', 'capex')),
            priority text not null default 'normal' check (priority in ('normal', 'urgent')),
            project_code text,
            created_at timestamptz not null default now(),
            need_by date,
            channel text not null check (channel in ('form', 'email', 'chat', 'planning')),
            source_ref text,
            justification text,
            preferred_supplier text,
            status text not null default 'draft'
                check (status in ('draft', 'submitted', 'approved', 'rejected', 'returned', 'cancelled', 'closed')),
            total_value numeric(18,2) not null default 0
        )
        """
    )
    op.execute("create index request_status on request (status, created_at desc)")
    # qty and uom are what is bought (the purchase unit); base_qty is the same quantity in the stock unit, so open
    # requests can be counted as stock on the way
    op.execute(
        """
        create table request_line (
            request_no text not null references request (no) on delete cascade,
            line_no integer not null,
            item_code text references item (code),
            free_text text,
            attributes jsonb,
            qty numeric(18,4) not null check (qty > 0),
            uom text,
            base_qty numeric(18,4),
            est_price numeric(18,4),
            need_by date,
            delivery_location text references location (code),
            cost_centre_code text,
            gl_code text,
            category_code text,
            project_code text,
            reason text,
            status text not null default 'open' check (status in ('open', 'sourcing', 'ordered', 'cancelled')),
            primary key (request_no, line_no),
            check (item_code is not null or free_text is not null)
        )
        """
    )
    op.execute(
        "create index request_line_item on request_line (item_code, delivery_location) where status in ('open', 'sourcing')"
    )
    op.execute(
        "insert into role_permission (role, action) values ('Buyer', 'replenishment.run'), ('Admin', 'replenishment.run') "
        "on conflict do nothing"
    )


def downgrade() -> None:
    op.execute("delete from role_permission where action = 'replenishment.run'")
    op.execute("drop table request_line")
    op.execute("drop table request")
    op.execute("drop table number_counter")
