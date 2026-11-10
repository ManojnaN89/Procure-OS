"""Company setup: the history of every change an administrator makes (people, roles, approvers, rules, policies, automation)

Revision ID: 0030
Revises: 0029
"""

from alembic import op

revision = "0030"
down_revision = "0029"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # One row per change: who, when, what it was and what it became, and why. The audit log has the same facts; this
    # table is what the setup screens read, and what lets a past decision be explained against the setting in force then.
    op.execute(
        """
        create table setup_change (
            id bigserial primary key,
            at timestamptz not null default now(),
            by_user text not null,
            area text not null,
            key text not null,
            old_value jsonb,
            new_value jsonb,
            reason text not null
        )
        """
    )
    op.execute("create index setup_change_key on setup_change (area, key, at)")
    # Admin runs the setup and takes no part in the purchase flow. Migration 0020 took the purchase permissions away and
    # missed this one: running replenishment creates purchase requests.
    op.execute("delete from role_permission where role = 'Admin' and action = 'replenishment.run'")


def downgrade() -> None:
    op.execute(
        "insert into role_permission (role, action) values ('Admin', 'replenishment.run') on conflict do nothing"
    )
    op.execute("drop table setup_change")
