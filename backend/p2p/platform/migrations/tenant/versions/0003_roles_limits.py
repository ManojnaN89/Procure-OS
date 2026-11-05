"""Roles with scope, what each role may do, and approval limits per user

Revision ID: 0003
Revises: 0002
"""

import sqlalchemy as sa
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None

# Default starting point; a tenant administrator can change these rows.
DEFAULT_PERMISSIONS = {
    "Requester": ["request.create", "request.view_own", "request.cancel_own"],
    "Approver": ["request.view", "request.approve", "po.approve", "invoice.approve"],
    "Buyer": ["request.view", "rfq.create", "rfq.send", "po.create", "po.send", "supplier.view"],
    "Store": ["request.view", "grn.create", "stock.view"],
    "AP": ["invoice.view", "invoice.book", "invoice.match", "payment.view"],
    "Finance": ["invoice.view", "invoice.approve", "payment.propose", "payment.approve", "budget.view"],
    "Admin": ["admin.users", "admin.rules", "admin.settings", "audit.view"],
}


def upgrade() -> None:
    op.create_table(
        "user_role",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("user_id", sa.Integer, nullable=False),
        sa.Column("role", sa.String(40), nullable=False),
        # empty means all locations / all departments
        sa.Column("scope_location", sa.String(40)),
        sa.Column("scope_department", sa.String(40)),
    )
    op.create_index("ix_user_role_user", "user_role", ["user_id"])
    role_permission = op.create_table(
        "role_permission",
        sa.Column("role", sa.String(40), primary_key=True),
        sa.Column("action", sa.String(60), primary_key=True),
    )
    op.bulk_insert(
        role_permission,
        [{"role": r, "action": a} for r, actions in DEFAULT_PERMISSIONS.items() for a in actions],
    )
    op.create_table(
        "user_limit",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("user_id", sa.Integer, nullable=False),
        sa.Column("document", sa.String(40), nullable=False),
        sa.Column("category_code", sa.String(40)),
        sa.Column("location_code", sa.String(40)),
        sa.Column("max_value", sa.Numeric(16, 2), nullable=False),
    )
    op.create_index("ix_user_limit_user", "user_limit", ["user_id", "document"])


def downgrade() -> None:
    op.drop_table("user_limit")
    op.drop_table("role_permission")
    op.drop_table("user_role")
