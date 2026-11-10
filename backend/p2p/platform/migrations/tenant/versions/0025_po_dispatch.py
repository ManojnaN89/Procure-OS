"""Sending an order to the supplier and the supplier's acknowledgement

Revision ID: 0025
Revises: 0024
"""

from alembic import op

revision = "0025"
down_revision = "0024"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "alter table po add column sent_to text, add column acknowledged_at timestamptz, add column ack_note text"
    )


def downgrade() -> None:
    op.execute("alter table po drop column sent_to, drop column acknowledged_at, drop column ack_note")
