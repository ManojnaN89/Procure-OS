"""Who may ask for a supplier's status to change, and who may approve it

Revision ID: 0024
Revises: 0023
"""

import sqlalchemy as sa
from alembic import op

revision = "0024"
down_revision = "0023"
branch_labels = None
depends_on = None

GRANTS = [("Buyer", "supplier.propose"), ("Approver", "supplier.propose"), ("Approver", "supplier.activate")]


def upgrade() -> None:
    for role, action in GRANTS:
        op.get_bind().execute(
            sa.text("insert into role_permission (role, action) values (:r, :a) on conflict do nothing"),
            {"r": role, "a": action},
        )


def downgrade() -> None:
    for role, action in GRANTS:
        op.get_bind().execute(
            sa.text("delete from role_permission where role = :r and action = :a"), {"r": role, "a": action}
        )
