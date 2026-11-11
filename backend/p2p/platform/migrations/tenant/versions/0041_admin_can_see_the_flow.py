"""The administrator can see the whole flow (read only), so the screens are not empty for them

Revision ID: 0041
Revises: 0040
"""

from alembic import op

revision = "0041"
down_revision = "0040"
branch_labels = None
depends_on = None

# View only. Raising, approving, receiving or paying stay with the other roles (segregation of duties).
GRANTS = [
    ("Admin", "request.view"),
    ("Admin", "supplier.view"),
    ("Admin", "stock.view"),
    ("Admin", "invoice.view"),
    ("Admin", "payment.view"),
    ("Admin", "budget.view"),
]


def upgrade() -> None:
    for role, action in GRANTS:
        op.execute(f"insert into role_permission (role, action) values ('{role}', '{action}') on conflict do nothing")  # noqa: S608


def downgrade() -> None:
    for role, action in GRANTS:
        op.execute(f"delete from role_permission where role = '{role}' and action = '{action}'")  # noqa: S608
