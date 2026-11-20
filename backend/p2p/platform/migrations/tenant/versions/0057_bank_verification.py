"""Who entered a supplier bank account, and who verified it, how and against what proof

A bank account is paid into only once a person other than the one who entered it (or asked for the change) has checked
it: the method (one of the setting `bank_verification_methods`), the stored proof, the person and the time. Finance
verifies.

Revision ID: 0057
Revises: 0055
"""

from alembic import op

revision = "0057"
down_revision = "0056"
branch_labels = None
depends_on = None

GRANTS = [("Finance", "supplier.verify_bank")]


def upgrade() -> None:
    op.execute(
        "alter table supplier_bank_account add column submitted_by text, add column verification_method text, "
        "add column evidence_document_id bigint references document (id), add column verified_by text, "
        "add column verified_at timestamptz"
    )
    for role, action in GRANTS:
        op.execute(f"insert into role_permission (role, action) values ('{role}', '{action}') on conflict do nothing")  # noqa: S608


def downgrade() -> None:
    for role, action in GRANTS:
        op.execute(f"delete from role_permission where role = '{role}' and action = '{action}'")  # noqa: S608
    op.execute(
        "alter table supplier_bank_account drop column submitted_by, drop column verification_method, "
        "drop column evidence_document_id, drop column verified_by, drop column verified_at"
    )
