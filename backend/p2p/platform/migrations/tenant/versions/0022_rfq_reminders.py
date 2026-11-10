"""When a silent supplier was last reminded

Revision ID: 0022
Revises: 0021
"""

from alembic import op

revision = "0022"
down_revision = "0021"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("alter table rfq_supplier add column last_reminded_at timestamptz")


def downgrade() -> None:
    op.execute("alter table rfq_supplier drop column last_reminded_at")
