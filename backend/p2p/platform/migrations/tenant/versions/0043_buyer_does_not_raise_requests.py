"""Only a requester raises a request by hand (the plan's role table); the buyer runs the stock check instead

Revision ID: 0043
Revises: 0042
"""

from alembic import op

revision = "0043"
down_revision = "0042"
branch_labels = None
depends_on = None

GRANTS = [("Buyer", "request.create"), ("Buyer", "request.view_own"), ("Buyer", "request.cancel_own")]


def upgrade() -> None:
    for role, action in GRANTS:
        op.execute(f"delete from role_permission where role = '{role}' and action = '{action}'")  # noqa: S608


def downgrade() -> None:
    for role, action in GRANTS:
        op.execute(f"insert into role_permission (role, action) values ('{role}', '{action}') on conflict do nothing")  # noqa: S608
