"""Sourcing: how each approved request line is bought (a contract price or a request for quotation), and the quotation requests

Revision ID: 0018
Revises: 0017
"""

from alembic import op

revision = "0018"
down_revision = "0017"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # The masters are reloaded in one transaction by deleting and inserting them again, so every reference to a master
    # row has to be checked at commit, not at each statement (the stock tables already are).
    for table, constraint in (
        ("request", "request_ship_to_location_fkey"),
        ("request_line", "request_line_item_code_fkey"),
        ("request_line", "request_line_delivery_location_fkey"),
    ):
        op.execute(f"alter table {table} alter constraint {constraint} deferrable initially deferred")

    # the decision is kept with what it was based on, so it can be explained later
    op.execute(
        """
        create table rfq (
            no text primary key,
            buyer text,
            request_no text references request (no),
            category_code text,
            created_at timestamptz not null default now(),
            due_date date not null,
            type text not null default 'commercial' check (type in ('commercial', 'combined')),
            delivery_terms text,
            payment_terms_proposed text,
            status text not null default 'draft' check (status in ('draft', 'sent', 'closed', 'awarded', 'cancelled')),
            sent_at timestamptz,
            proposal_id bigint,
            note text
        )
        """
    )
    op.execute(
        """
        create table rfq_line (
            rfq_no text not null references rfq (no) on delete cascade,
            line_no integer not null,
            request_no text not null,
            request_line_no integer not null,
            item_code text references item (code) deferrable initially deferred,
            description text,
            qty numeric(18,4) not null check (qty > 0),
            uom text,
            need_by date,
            primary key (rfq_no, line_no)
        )
        """
    )
    op.execute(
        """
        create table rfq_supplier (
            rfq_no text not null references rfq (no) on delete cascade,
            supplier_code text not null references supplier (code) deferrable initially deferred,
            email text,
            invited_at timestamptz,
            send_error text,
            reminders_sent integer not null default 0,
            responded_at timestamptz,
            declined boolean not null default false,
            decline_reason text,
            primary key (rfq_no, supplier_code)
        )
        """
    )
    op.execute(
        """
        create table sourcing_decision (
            id bigserial primary key,
            request_no text not null,
            line_no integer not null,
            method text not null check (method in ('contract', 'rfq', 'manual')),
            supplier_code text,
            contract_code text,
            price numeric(18,4),
            uom text,
            lead_days integer,
            price_valid_to date,
            rfq_no text references rfq (no),
            reason text not null,
            decided_at timestamptz not null default now(),
            foreign key (request_no, line_no) references request_line (request_no, line_no) on delete cascade,
            unique (request_no, line_no)
        )
        """
    )
    op.execute("create index sourcing_decision_rfq on sourcing_decision (rfq_no)")
    op.execute(
        "insert into role_permission (role, action) values ('Admin', 'rfq.create'), ('Admin', 'rfq.send') "
        "on conflict do nothing"
    )


def downgrade() -> None:
    op.execute("delete from role_permission where role = 'Admin' and action in ('rfq.create', 'rfq.send')")
    for table in ("sourcing_decision", "rfq_supplier", "rfq_line", "rfq"):
        op.execute(f"drop table {table}")
    for table, constraint in (
        ("request", "request_ship_to_location_fkey"),
        ("request_line", "request_line_item_code_fkey"),
        ("request_line", "request_line_delivery_location_fkey"),
    ):
        op.execute(f"alter table {table} alter constraint {constraint} not deferrable")
