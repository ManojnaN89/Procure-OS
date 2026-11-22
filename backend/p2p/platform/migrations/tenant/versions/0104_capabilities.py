"""One switch per AI capability (task 11a.5, D49, D54)

The seven capabilities of D49. Switching one on needs a second administrator and records the D54 checks given; switching
it off is immediate and hands its open work to people. The capabilities built before WP-11a run in propose or confirm
mode and start on (OP-6); monitoring is not built yet and starts off; `changed_by` says the migration set it. Planning
stays governed by its automation setting `planning.explain` as well (owner decision 8).

Revision ID: 0104
Revises: 0103
"""

from alembic import op

revision = "0104"
down_revision = "0103"
branch_labels = None
depends_on = None

ON = ("intake", "planning", "sourcing", "execution", "invoice_ap", "assistant")
OFF = ("monitoring",)
BASIS = '{"basis": "built before WP-11a; runs in propose or confirm mode"}'


def upgrade() -> None:
    op.execute(
        """
        create table ai_capability (
            code text primary key check (
                code in ('intake', 'planning', 'sourcing', 'execution', 'invoice_ap', 'monitoring', 'assistant')
            ),
            enabled boolean not null default false,
            checks jsonb,
            changed_at timestamptz not null default now(),
            changed_by text not null
        )
        """
    )
    for code in ON + OFF:
        on = "true" if code in ON else "false"
        checks = f"'{BASIS}'::jsonb" if code in ON else "null"
        op.execute(
            "insert into ai_capability (code, enabled, checks, changed_by) "  # noqa: S608 - fixed values
            f"values ('{code}', {on}, {checks}, 'migration')"
        )
    # The seed is the starting state, not a change: a new company's setup history and audit log start empty.


def downgrade() -> None:
    op.execute("drop table ai_capability")
