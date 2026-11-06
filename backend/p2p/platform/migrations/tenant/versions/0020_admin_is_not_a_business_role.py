"""The Admin role administers the system; it does not take part in the purchase flow

Revision ID: 0020
Revises: 0019
"""

import sqlalchemy as sa
from alembic import op

revision = "0020"
down_revision = "0019"
branch_labels = None
depends_on = None

# Granted to Admin by earlier migrations for convenience. Submitting, quoting, ordering and approving are done by the
# business roles (Requester, Buyer, Approver), so that the person who administers the system cannot also approve its spend.
BUSINESS_ACTIONS = ["request.submit", "rfq.create", "rfq.send", "po.create", "po.approve"]


def upgrade() -> None:
    op.get_bind().execute(
        sa.text("delete from role_permission where role = 'Admin' and action = any(:actions)"),
        {"actions": BUSINESS_ACTIONS},
    )


def downgrade() -> None:
    op.get_bind().execute(
        sa.text(
            "insert into role_permission (role, action) select 'Admin', unnest(cast(:actions as text[])) on conflict do nothing"
        ),
        {"actions": BUSINESS_ACTIONS},
    )
