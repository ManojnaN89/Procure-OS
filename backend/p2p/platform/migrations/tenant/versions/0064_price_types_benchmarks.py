"""Price types and market benchmarks (task 13.1, D41 and D52)

A benchmark is evidence only: a market price for an item with its source, date, region and unit, entered or uploaded
by a buyer and verified (or rejected) by someone else. It never replaces a contract price, a quote, an estimate or an
order price. It counts toward a price review only when verified, fresh, for the region and in a convertible unit.

Revision ID: 0064
Revises: 0063
"""

from alembic import op

revision = "0064"
down_revision = "0063"
branch_labels = None
depends_on = None

GRANTS = (
    ("Buyer", "benchmark.enter"),
    ("Approver", "benchmark.enter"),
    ("Approver", "benchmark.verify"),
    ("Buyer", "price.view"),
    ("Approver", "price.view"),
    ("Finance", "price.view"),
)


def upgrade() -> None:
    op.execute(
        """
        create table market_benchmark (
            id bigserial primary key,
            item_code text not null references item (code) deferrable initially deferred,
            price numeric(18,4) not null check (price > 0),
            uom text not null,
            basis text not null default 'delivered' check (basis in ('delivered', 'ex_works')),
            effective_date date not null,
            region_code text references region (code) deferrable initially deferred,
            source_kind text not null check (source_kind in ('index', 'price_list', 'market_survey', 'other')),
            source_name text not null,
            source_ref text,
            document_id bigint references document (id),
            note text,
            status text not null default 'unverified' check (status in ('unverified', 'verified', 'rejected')),
            entered_by text,
            entered_at timestamptz not null default now(),
            verified_by text,
            verified_at timestamptz,
            verify_note text,
            rejected_reason text,
            replaces bigint references market_benchmark (id)
        )
        """
    )
    op.execute(
        "create unique index market_benchmark_key on market_benchmark "
        "(item_code, source_name, effective_date, region_code) nulls not distinct where status <> 'rejected'"
    )
    op.execute("create index market_benchmark_item on market_benchmark (item_code, status, effective_date desc)")
    for role, action in GRANTS:
        op.execute(f"insert into role_permission (role, action) values ('{role}', '{action}') on conflict do nothing")  # noqa: S608


def downgrade() -> None:
    for role, action in GRANTS:
        op.execute(f"delete from role_permission where role = '{role}' and action = '{action}'")  # noqa: S608
    op.execute("drop table market_benchmark")
