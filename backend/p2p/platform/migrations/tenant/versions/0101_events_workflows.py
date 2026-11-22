"""Events recorded once by a key from their source, and one workflow per business key (tasks 11a.1, 11a.2; D39)

An event (`email.received`, `material_request.figures_changed`, ...) is kept once per type and key: a replay finds the
row already there and does nothing. A router sends it to the engine and at most one capability, which joins the open
workflow of its business key (at most one open or waiting per kind and key) instead of starting a second one.

Revision ID: 0101
Revises: 0100
"""

from alembic import op

revision = "0101"
down_revision = "0100"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        create table event (
            id bigserial primary key,
            type text not null,
            key text not null,
            source text not null,
            payload jsonb not null default '{}',
            occurred_at timestamptz not null,
            recorded_at timestamptz not null default now(),
            status text not null default 'new' check (status in ('new', 'routed', 'ignored', 'failed')),
            workflow_id int,
            routed_at timestamptz,
            note text,
            unique (type, key)
        )
        """
    )
    op.execute(
        """
        create table workflow (
            id serial primary key,
            kind text not null,
            business_key text not null,
            capability text not null,
            status text not null default 'open'
                check (status in ('open', 'waiting', 'done', 'cancelled', 'handed_over')),
            basis_hash text,
            first_event_id bigint references event (id),
            last_event_id bigint references event (id),
            run_id int references agent_run (id),
            task_id int references task (id),
            created_at timestamptz not null default now(),
            updated_at timestamptz not null default now(),
            closed_at timestamptz,
            close_reason text
        )
        """
    )
    op.execute(
        "create unique index ux_workflow_open on workflow (kind, business_key) where status in ('open', 'waiting')"
    )
    op.execute("alter table event add constraint fk_event_workflow foreign key (workflow_id) references workflow (id)")
    op.execute("create index ix_event_new on event (recorded_at) where status = 'new'")


def downgrade() -> None:
    op.execute("alter table event drop constraint fk_event_workflow")
    op.execute("drop table workflow")
    op.execute("drop table event")
