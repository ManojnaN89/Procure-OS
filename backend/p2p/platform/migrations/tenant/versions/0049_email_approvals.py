"""Email approvals (plan step 4, P1-32): approval rows by location and rows that add an approver, the rule row behind each
approval step, reminders and escalation on the business calendar, and one-time codes

Revision ID: 0049
Revises: 0048
"""

from alembic import op

revision = "0049"
down_revision = "0048"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # A rule row may name a location (ANY when it does not); a chain written '+role' adds to the chain chosen by value
    op.execute("alter table approval_rule add column location_code text default 'ANY'")
    op.execute("update approval_rule set location_code = 'ANY' where location_code is null")
    # the head of a location (a plant head), for the approver role source location_head
    op.execute(
        "alter table location add column head_user text references org_user (code) deferrable initially deferred"
    )
    op.execute("alter table approver_role drop constraint approver_role_source_check")
    op.execute(
        "alter table approver_role add constraint approver_role_source_check check (source in "
        "('requester_manager', 'department_head', 'department_head_of', 'designation', 'location_head'))"
    )
    # each step keeps the rule row it came from and why it came to this person; when it opened and when it was reminded
    op.execute(
        "alter table approval_step add column rule_id bigint, add column rule_code text, add column rule_version integer, "
        "add column route_reason text, add column opened_at timestamptz, add column reminded_at timestamptz"
    )
    # one-time codes: only a keyed hash is kept; a code expires, allows a few attempts and works once
    op.execute(
        """
        create table one_time_code (
            id bigserial primary key,
            purpose text not null,
            ref text not null,
            user_id bigint not null,
            code_hash text not null,
            created_at timestamptz not null default now(),
            expires_at timestamptz not null,
            attempts integer not null default 0,
            status text not null default 'open' check (status in ('open', 'used', 'replaced', 'expired', 'locked')),
            used_at timestamptz
        )
        """
    )
    op.execute("create index one_time_code_open on one_time_code (purpose, ref, user_id) where status = 'open'")


def downgrade() -> None:
    op.execute("drop table one_time_code")
    for col in ("reminded_at", "opened_at", "route_reason", "rule_version", "rule_code", "rule_id"):
        op.execute(f"alter table approval_step drop column {col}")
    op.execute("delete from approver_role where source = 'location_head'")
    op.execute("alter table approver_role drop constraint approver_role_source_check")
    op.execute(
        "alter table approver_role add constraint approver_role_source_check check (source in "
        "('requester_manager', 'department_head', 'department_head_of', 'designation'))"
    )
    op.execute("alter table location drop column head_user")
    op.execute("alter table approval_rule drop column location_code")
