"""Profile of each user inside the tenant (the login itself lives in the control database)

Revision ID: 0002
Revises: 0001
"""

import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "user_profile",
        sa.Column("user_id", sa.Integer, primary_key=True),
        sa.Column("display_name", sa.String(200), nullable=False),
        sa.Column("role", sa.String(40), nullable=False, server_default="Requester"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )


def downgrade() -> None:
    op.drop_table("user_profile")
