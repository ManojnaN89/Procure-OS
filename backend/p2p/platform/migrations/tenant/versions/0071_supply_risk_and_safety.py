"""Supply promised too late for a need (rule Q17, task 6.1) and the settings of the store check (task 6.5)

A `supply_risk` is the case for the buyer when an order brings what a material request needs after its latest arrival
date (the need-by less the receiving buffer in working days): one open case per order and material request, its lines
updated by each evaluation, closed by the buyer with what was done or by the system once nothing on it is at risk. It
keeps the buffer it was judged with.

Revision ID: 0071
Revises: 0070
"""

from alembic import op

revision = "0071"
down_revision = "0070"
branch_labels = None
depends_on = None

SETTINGS = [
    ("receiving_buffer_days", "0"),
    ("late_supply_case_days", "2"),
    ("auto_store_check", "true"),
    ("store_check_submit", "false"),
]


def upgrade() -> None:
    op.execute(
        """
        create table supply_risk (
            task_id integer primary key references task (id),
            po_no text not null,
            mr_no text not null,
            location_code text not null,
            lines jsonb not null,
            buffer_days integer not null,
            outcome text check (outcome in ('expedited', 'accepted_late', 'bought_elsewhere', 'resolved')),
            note text,
            closed_by text,
            closed_at timestamptz
        )
        """
    )
    op.execute("create unique index supply_risk_open on supply_risk (po_no, mr_no) where closed_at is null")
    for key, value in SETTINGS:
        op.execute(f"insert into app_setting (key, value) values ('{key}', '{value}') on conflict do nothing")  # noqa: S608


def downgrade() -> None:
    for key, _ in SETTINGS:
        op.execute(f"delete from app_setting where key = '{key}'")  # noqa: S608
    op.execute("drop table supply_risk")
