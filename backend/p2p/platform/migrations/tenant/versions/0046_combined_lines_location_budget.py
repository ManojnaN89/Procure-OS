"""Combined requisition lines and budgets per location: what a planning request line buys for stock (the rest of it buys
for the material request lines linked to it), and a budget row may be held by a location instead of a cost centre

Revision ID: 0046
Revises: 0045
"""

from alembic import op

revision = "0046"
down_revision = "0045"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # base unit; null on a line a person asked for (all of it is theirs)
    op.execute("alter table request_line add column stock_qty numeric(18,4) check (stock_qty >= 0)")
    # a location's own budget: location_code set and cost_centre_code empty
    op.execute(
        "alter table budget add column location_code text references location (code) deferrable initially deferred"
    )
    op.execute(
        "create index budget_location on budget (location_code, gl_code, period) where location_code is not null"
    )


def downgrade() -> None:
    op.execute("drop index budget_location")
    op.execute("alter table budget drop column location_code")
    op.execute("alter table request_line drop column stock_qty")
