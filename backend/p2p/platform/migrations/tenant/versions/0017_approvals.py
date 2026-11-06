"""Approval of requests: who must approve, each step, and the rule version a decision used

Revision ID: 0017
Revises: 0016
"""

import sqlalchemy as sa
from alembic import op

revision = "0017"
down_revision = "0016"
branch_labels = None
depends_on = None

# How a role named in an approval chain becomes a person. A company edits these rows to fit its own organisation.
DEFAULT_ROLES = [
    ("manager", "requester_manager", None),  # the requester's own manager
    ("dept_head", "department_head", None),  # head of the request's department
    ("procurement_head", "department_head_of", "PUR"),  # head of a named department
    ("director", "designation", "Managing Director"),
    ("cfo", "designation", "Chief Financial Officer"),
    ("finance_manager", "designation", "Finance Manager"),
]


def upgrade() -> None:
    op.execute("alter table request add column rule_code text")
    op.execute("alter table request add column rule_version integer")
    op.execute("alter table request add column decision_reason text")
    op.execute("alter table request add column submitted_at timestamptz")
    op.execute("alter table request add column decided_at timestamptz")
    op.execute("alter table request add column approval_round integer not null default 0")
    op.execute(
        """
        create table approver_role (
            role text primary key,
            source text not null check (source in ('requester_manager', 'department_head', 'department_head_of', 'designation')),
            value text
        )
        """
    )
    op.get_bind().execute(
        sa.text("insert into approver_role (role, source, value) values (:r, :s, :v)"),
        [{"r": r, "s": src, "v": v} for r, src, v in DEFAULT_ROLES],
    )
    # one row per person who has to act; a round is one submission (a returned request is submitted again as a new round)
    op.execute(
        """
        create table approval_step (
            id bigserial primary key,
            request_no text not null references request (no) on delete cascade,
            round integer not null,
            step integer not null,
            role text not null,
            approver text not null,
            delegate text,
            status text not null default 'waiting'
                check (status in ('waiting', 'pending', 'approved', 'rejected', 'returned', 'cancelled')),
            acted_by text,
            acted_at timestamptz,
            comment text,
            task_id bigint,
            unique (request_no, round, step)
        )
        """
    )
    op.execute("create index approval_step_open on approval_step (approver, status) where status = 'pending'")
    op.execute(
        "insert into role_permission (role, action) values "
        "('Requester', 'request.submit'), ('Buyer', 'request.submit'), ('Admin', 'request.submit') "
        "on conflict do nothing"
    )


def downgrade() -> None:
    op.execute("delete from role_permission where action = 'request.submit'")
    op.execute("drop table approval_step")
    op.execute("drop table approver_role")
    for col in ("approval_round", "decided_at", "submitted_at", "decision_reason", "rule_version", "rule_code"):
        op.execute(f"alter table request drop column {col}")
