"""Regional scope (task 12.4, D44)

A region is a named group of locations (a location is in at most one). Supplier approval for a category may be limited
to locations and/or regions; a contract price may carry one location or one region. A regional approval or price is
never used outside its scope: the delivery location of the line decides. RFQs are made per category and delivery
location.

Revision ID: 0062
Revises: 0061
"""

from alembic import op

revision = "0062"
down_revision = "0061"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("create table region (code text primary key, name text not null)")
    op.execute(
        "alter table location add column region_code text references region (code) deferrable initially deferred"
    )
    op.execute(
        """
        create table supplier_category_scope (
            id bigserial primary key,
            supplier_code text not null,
            category_code text not null,
            location_code text references location (code) deferrable initially deferred,
            region_code text references region (code) deferrable initially deferred,
            check ((location_code is null) <> (region_code is null)),
            foreign key (supplier_code, category_code) references supplier_category (supplier_code, category_code)
                on delete cascade deferrable initially deferred
        )
        """
    )
    op.execute(
        "alter table price_record "
        "add column location_code text references location (code) deferrable initially deferred, "
        "add column region_code text references region (code) deferrable initially deferred, "
        "add constraint price_record_one_scope check (location_code is null or region_code is null)"
    )
    op.execute("alter table rfq add column delivery_location text references location (code)")


def downgrade() -> None:
    op.execute("alter table rfq drop column delivery_location")
    op.execute(
        "alter table price_record drop constraint price_record_one_scope, drop column region_code, "
        "drop column location_code"
    )
    op.execute("drop table supplier_category_scope")
    op.execute("alter table location drop column region_code")
    op.execute("drop table region")
