"""Control tables: tenant, tenant_database, feature_flag, tenant_setting

Revision ID: 0001
Revises:
"""

import sqlalchemy as sa
from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "tenant",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("slug", sa.String(32), nullable=False, unique=True),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("active", sa.Boolean, nullable=False, server_default="true"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_table(
        "tenant_database",
        sa.Column("tenant_id", sa.Integer, sa.ForeignKey("tenant.id"), primary_key=True),
        sa.Column("database_name", sa.String(63), nullable=False, unique=True),
        sa.Column("connection_encrypted", sa.Text, nullable=False),
        sa.Column("schema_revision", sa.String(64)),
        sa.Column("migrated_at", sa.DateTime(timezone=True)),
    )
    op.create_table(
        "feature_flag",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("tenant_id", sa.Integer, sa.ForeignKey("tenant.id"), nullable=False),
        sa.Column("key", sa.String(100), nullable=False),
        sa.Column("enabled", sa.Boolean, nullable=False, server_default="false"),
        sa.UniqueConstraint("tenant_id", "key"),
    )
    op.create_table(
        "tenant_setting",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("tenant_id", sa.Integer, sa.ForeignKey("tenant.id"), nullable=False),
        sa.Column("key", sa.String(100), nullable=False),
        sa.Column("value", sa.JSON, nullable=False),
        sa.UniqueConstraint("tenant_id", "key"),
    )


def downgrade() -> None:
    for t in ("tenant_setting", "feature_flag", "tenant_database", "tenant"):
        op.drop_table(t)
