"""Price thresholds (task 12.5, D40 and D41)

A versioned rule per company (plus optional category rows that may only be stricter) for when a contract price above
the market opens a strategic review: the gap in percent and in rupees per unit, the saving at the relevant quantity,
how fresh the evidence must be and from how many sources, and how near the end of a contract opens an expiry review.
The values are a recommended default that needs validating with each company (O2).

Revision ID: 0063
Revises: 0062
"""

from alembic import op

revision = "0063"
down_revision = "0062"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        create table price_threshold_rule (
            id bigserial primary key,
            category_code text,
            version integer not null,
            effective_from date not null,
            effective_to date,
            gap_pct numeric(7,4) not null check (gap_pct >= 0 and gap_pct <= 100),
            gap_inr_per_unit numeric(18,2) not null check (gap_inr_per_unit >= 0),
            min_saving_inr numeric(18,2) not null check (min_saving_inr >= 0),
            evidence_max_age_days integer not null check (evidence_max_age_days >= 0),
            min_sources integer not null check (min_sources >= 1),
            expiry_review_days integer not null check (expiry_review_days >= 0),
            note text,
            created_by text,
            created_at timestamptz not null default now(),
            unique nulls not distinct (category_code, version)
        )
        """
    )
    op.execute(
        "insert into price_threshold_rule (category_code, version, effective_from, gap_pct, gap_inr_per_unit, "
        "min_saving_inr, evidence_max_age_days, min_sources, expiry_review_days, note, created_by) values "
        "(null, 1, '2000-01-01', 5.0000, 10.00, 25000.00, 45, 1, 60, "
        "'Recommended default, needs validation (O2)', 'system:setup')"
    )


def downgrade() -> None:
    op.execute("drop table price_threshold_rule")
