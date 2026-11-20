"""Contract price versions (D48)

A contract price is a version: a draft until someone other than who entered it activates it, then active. Prices on file
are version 1: active, or a draft on a contract not yet activated. A contract keeps who stored it, so that person never activates it. The sourcing decision and
the order line keep the exact price record (and so the version) they were bought at.

Revision ID: 0056
Revises: 0055
"""

from alembic import op

revision = "0056"
down_revision = "0055"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("alter table contract add column created_by text")
    op.execute(
        "alter table price_record add column version integer not null default 1, "
        "add column status text not null default 'active' check (status in ('draft', 'active', 'withdrawn')), "
        "add column created_by text, add column activated_by text, add column activated_at timestamptz"
    )
    # the price list of a contract not yet activated is its draft version 1
    op.execute(
        "update price_record p set status = 'draft' from contract c where c.code = p.contract_code and c.status = 'draft'"
    )
    op.execute("create index price_record_lookup on price_record (item_code, uom, status)")
    op.execute("alter table sourcing_decision add column price_record_id bigint references price_record (id)")
    op.execute("alter table po_line add column price_record_id bigint references price_record (id)")


def downgrade() -> None:
    op.execute("alter table po_line drop column price_record_id")
    op.execute("alter table sourcing_decision drop column price_record_id")
    op.execute("drop index price_record_lookup")
    op.execute(
        "alter table price_record drop column activated_at, drop column activated_by, drop column created_by, "
        "drop column status, drop column version"
    )
    op.execute("alter table contract drop column created_by")
