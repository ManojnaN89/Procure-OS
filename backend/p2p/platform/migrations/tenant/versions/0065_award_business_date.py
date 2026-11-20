"""The business date of an award (D-03 R10)

A conditional award's qualification deadline is counted in working days from the business date it was made on, not
from the moment's calendar date in another time zone.

Revision ID: 0065
Revises: 0064
"""

from alembic import op

revision = "0065"
down_revision = "0064"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("alter table award add column awarded_on date")
    op.execute("update award set awarded_on = (at at time zone 'Asia/Kolkata')::date")


def downgrade() -> None:
    op.execute("alter table award drop column awarded_on")
