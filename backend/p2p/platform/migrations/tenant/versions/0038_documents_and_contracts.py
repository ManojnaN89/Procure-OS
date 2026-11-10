"""Who added and who verified a supplier document, and who may store and activate a contract

Revision ID: 0038
Revises: 0037
"""

from alembic import op

revision = "0038"
down_revision = "0037"
branch_labels = None
depends_on = None

GRANTS = [
    ("Buyer", "supplier.documents"),
    ("Approver", "supplier.documents"),
    ("Approver", "supplier.verify_docs"),
    ("Buyer", "contract.manage"),
    ("Approver", "contract.manage"),
    ("Approver", "contract.activate"),
]


def upgrade() -> None:
    op.execute(
        "alter table supplier_document add column added_by text, add column verified_by text, add column verified_at timestamptz"
    )
    for role, action in GRANTS:
        op.execute(f"insert into role_permission (role, action) values ('{role}', '{action}') on conflict do nothing")  # noqa: S608


def downgrade() -> None:
    for role, action in GRANTS:
        op.execute(f"delete from role_permission where role = '{role}' and action = '{action}'")  # noqa: S608
    op.execute("alter table supplier_document drop column added_by, drop column verified_by, drop column verified_at")
