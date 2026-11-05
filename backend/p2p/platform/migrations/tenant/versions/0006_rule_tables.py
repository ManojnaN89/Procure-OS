"""Rules engine: versioned decision tables and a record of every evaluation

Revision ID: 0006
Revises: 0005
"""

from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        create table rule_table (
            id serial primary key,
            key text not null,
            version int not null,
            status text not null check (status in ('draft', 'published', 'archived')),
            jdm jsonb not null,
            note text,
            created_by text not null,
            created_at timestamptz not null default now(),
            published_at timestamptz,
            unique (key, version)
        )
        """
    )
    # at most one published version and one draft per key
    op.execute("create unique index ux_rule_published on rule_table (key) where status = 'published'")
    op.execute("create unique index ux_rule_draft on rule_table (key) where status = 'draft'")
    op.execute(
        """
        create table rule_evaluation (
            id bigserial primary key,
            at timestamptz not null default now(),
            key text not null,
            version int not null,
            input jsonb not null,
            output jsonb,
            trace jsonb,
            actor text not null,
            case_ref text
        )
        """
    )
    op.execute("create index ix_rule_evaluation_key on rule_evaluation (key, at)")


def downgrade() -> None:
    op.execute("drop table rule_evaluation")
    op.execute("drop table rule_table")
