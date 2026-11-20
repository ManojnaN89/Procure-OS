"""Tolerance versions (task 3.2, D-17)

The tolerance set is versioned as a whole: version N is every (key, category) value in force from its start date. A
change makes the next version from a date (today or later); the version in force ends the day before. Version 1 is the
company's rows as they are. Each match, goods receipt and invoice keeps the version it was judged under, so re-running a
past decision gives the same result.

Revision ID: 0060
Revises: 0059
"""

from alembic import op

revision = "0060"
down_revision = "0059"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        create table tolerance_version (
            version integer primary key check (version >= 1),
            effective_from date not null,
            effective_to date,
            note text,
            created_by text,
            created_at timestamptz not null default now()
        )
        """
    )
    op.execute(
        "insert into tolerance_version (version, effective_from, note, created_by) "
        "values (1, '2000-01-01', 'Set up with the company', 'system:setup')"
    )
    op.execute(
        "alter table tolerance add column version integer not null default 1 "
        "references tolerance_version (version) deferrable initially deferred"
    )
    op.execute(
        "create unique index tolerance_version_key on tolerance (version, key, category_code) nulls not distinct"
    )
    op.execute("alter table match_result add column tolerance_version integer")
    op.execute("alter table invoice add column tolerance_version integer")
    op.execute("alter table goods_receipt add column tolerance_version integer")


def downgrade() -> None:
    op.execute("alter table goods_receipt drop column tolerance_version")
    op.execute("alter table invoice drop column tolerance_version")
    op.execute("alter table match_result drop column tolerance_version")
    op.execute("drop index tolerance_version_key")
    op.execute("delete from tolerance where version <> 1")
    op.execute("alter table tolerance drop column version")
    op.execute("drop table tolerance_version")
