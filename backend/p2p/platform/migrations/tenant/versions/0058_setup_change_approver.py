"""Who approved a setup change

A change to the company's controls is proposed by one administrator and applied when another approves it (D-22). The
setup history keeps the proposer in `by_user`; `approved_by` is the administrator who approved it (empty for a change
that applies at once, such as adding a person or stopping automation).

Revision ID: 0058
Revises: 0057
"""

from alembic import op

revision = "0058"
down_revision = "0057"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("alter table setup_change add column approved_by text")


def downgrade() -> None:
    op.execute("alter table setup_change drop column approved_by")
