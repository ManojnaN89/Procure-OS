"""The budget check made when a request was submitted, kept with the request

Revision ID: 0023
Revises: 0022
"""

from alembic import op

revision = "0023"
down_revision = "0022"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("alter table request add column budget_check jsonb")


def downgrade() -> None:
    op.execute("alter table request drop column budget_check")
