"""The store issues stock for use and corrects it after a count

Revision ID: 0039
Revises: 0038
"""

from alembic import op

revision = "0039"
down_revision = "0038"
branch_labels = None
depends_on = None

GRANTS = [("Store", "stock.issue"), ("Store", "stock.adjust")]


def upgrade() -> None:
    for role, action in GRANTS:
        op.execute(f"insert into role_permission (role, action) values ('{role}', '{action}') on conflict do nothing")  # noqa: S608


def downgrade() -> None:
    for role, action in GRANTS:
        op.execute(f"delete from role_permission where role = '{role}' and action = '{action}'")  # noqa: S608
