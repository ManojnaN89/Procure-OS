"""Proposals: sensitive actions that wait for a person

Revision ID: 0008
Revises: 0007
"""

from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        create table proposal (
            id serial primary key,
            action text not null,
            summary text not null,
            payload jsonb not null,
            proposed_by_type text not null,
            proposed_by_id text not null,
            proposed_for text,
            risk text not null default 'medium' check (risk in ('low', 'medium', 'high')),
            required_permission text not null,
            document text,
            value numeric(16, 2),
            case_id int references purchase_case (id),
            status text not null default 'pending'
                check (status in ('pending', 'approved', 'edited', 'rejected', 'expired')),
            execution text check (execution in ('ok', 'failed')),
            result jsonb,
            created_at timestamptz not null default now(),
            expires_at timestamptz,
            decided_by int,
            decided_at timestamptz,
            decision_note text
        )
        """
    )
    op.execute("create index ix_proposal_status on proposal (status, created_at)")


def downgrade() -> None:
    op.execute("drop table proposal")
