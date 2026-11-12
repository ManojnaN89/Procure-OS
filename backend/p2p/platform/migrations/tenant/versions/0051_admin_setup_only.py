"""Admin does setup only: the ERP demand import stays with the store

Revision ID: 0051
Revises: 0050
"""

from alembic import op

revision = "0051"
down_revision = "0050"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("delete from role_permission where role = 'Admin' and action = 'demand.import'")


def downgrade() -> None:
    op.execute("insert into role_permission (role, action) values ('Admin', 'demand.import') on conflict do nothing")
