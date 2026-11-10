"""Purchase order versions, the history of revisions, a budget override mark, and what closing an order records

Revision ID: 0040
Revises: 0039
"""

from alembic import op

revision = "0040"
down_revision = "0039"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "alter table po add column version integer not null default 1, "
        "add column budget_accepted boolean not null default false, "
        "add column closed_reason text, add column closed_at timestamptz"
    )
    op.execute("alter table po_line add column short_closed_qty numeric(18,4) not null default 0")
    op.execute(
        """
        create table po_revision (
            id bigserial primary key,
            po_no text not null references po (no) on delete cascade,
            version integer not null,
            changed_by text,
            changed_at timestamptz not null default now(),
            reason text not null,
            changes jsonb not null,
            total_before numeric(18,2) not null,
            total_after numeric(18,2) not null,
            reapproval boolean not null default false,
            unique (po_no, version)
        )
        """
    )


def downgrade() -> None:
    op.execute("drop table po_revision")
    op.execute("alter table po_line drop column short_closed_qty")
    op.execute(
        "alter table po drop column version, drop column budget_accepted, drop column closed_reason, drop column closed_at"
    )
