"""Purchase order tiers and touchless orders (plan step 7, P1-35, D35): the tier on each order, whether it was issued
without a buyer and the tests that decided it, and the versioned touchless rule

Revision ID: 0052
Revises: 0051
"""

from alembic import op

revision = "0052"
down_revision = "0051"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # One row per version. An empty value_limit means the top of the buyer's tier (the automatic band of the 'po'
    # approval rules); price_pct is how far above the requester's estimate a price may be. A change closes the old row.
    op.execute(
        """
        create table touchless_rule (
            id bigserial primary key,
            version integer not null unique,
            effective_from date not null,
            effective_to date,
            value_limit numeric(18,2),
            price_pct numeric(5,2) not null,
            note text,
            created_by text,
            created_at timestamptz not null default now()
        )
        """
    )
    op.execute(
        "insert into touchless_rule (version, effective_from, value_limit, price_pct, note, created_by) "
        "values (1, '2000-01-01', null, 2, 'Plan default (D35): the buyer tier, 2%', 'system:migration')"
    )
    op.execute(
        "alter table po add column approval_tier text, add column touchless boolean not null default false, "
        "add column touchless_checks jsonb, add column touchless_note text, add column touchless_rule text"
    )


def downgrade() -> None:
    for col in ("touchless_rule", "touchless_note", "touchless_checks", "touchless", "approval_tier"):
        op.execute(f"alter table po drop column {col}")
    op.execute("drop table touchless_rule")
