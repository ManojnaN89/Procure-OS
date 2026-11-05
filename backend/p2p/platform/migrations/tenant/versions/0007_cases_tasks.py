"""Cases, case events, human tasks and in-app notifications

Revision ID: 0007
Revises: 0006
"""

from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        create table purchase_case (
            id serial primary key,
            ref text not null unique,
            state text not null default 'request' check (state in
                ('request', 'approval', 'sourcing', 'order', 'supplier', 'receipt', 'invoice', 'payment', 'closed')),
            title text,
            data jsonb not null default '{}',
            created_at timestamptz not null default now(),
            updated_at timestamptz not null default now()
        )
        """
    )
    op.execute(
        """
        create table case_event (
            id bigserial primary key,
            case_id int not null references purchase_case (id),
            at timestamptz not null default now(),
            event text not null,
            from_state text,
            to_state text,
            actor_type text not null,
            actor_id text not null,
            detail jsonb
        )
        """
    )
    op.execute("create index ix_case_event_case on case_event (case_id, at)")
    op.execute(
        """
        create table task (
            id serial primary key,
            case_id int references purchase_case (id),
            kind text not null,
            title text not null,
            assignee int not null,
            status text not null default 'open' check (status in ('open', 'done', 'cancelled', 'overdue')),
            sla_hours numeric(6, 2) not null,
            due_at timestamptz not null,
            escalation_chain jsonb not null default '[]',
            escalation_level int not null default 0,
            payload jsonb,
            created_at timestamptz not null default now(),
            completed_at timestamptz,
            completed_by int
        )
        """
    )
    op.execute("create index ix_task_assignee on task (assignee, status)")
    op.execute(
        """
        create table notification (
            id bigserial primary key,
            user_id int not null,
            kind text not null,
            title text not null,
            body text,
            link text,
            created_at timestamptz not null default now(),
            read_at timestamptz
        )
        """
    )
    op.execute("create index ix_notification_user on notification (user_id, read_at)")


def downgrade() -> None:
    for t in ("notification", "task", "case_event", "purchase_case"):
        op.execute(f"drop table {t}")
