"""Agent run log and the autonomy gate settings

Revision ID: 0011
Revises: 0010
"""

from alembic import op

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        create table agent_run (
            id serial primary key,
            thread_id text not null unique,
            agent text not null,
            status text not null default 'running' check (status in ('running', 'waiting', 'done', 'failed')),
            input text,
            result text,
            waiting_for text,
            started_at timestamptz not null default now(),
            finished_at timestamptz
        )
        """
    )
    op.execute(
        """
        create table agent_step (
            id bigserial primary key,
            run_id int not null references agent_run (id),
            at timestamptz not null default now(),
            kind text not null check (kind in ('model', 'tool', 'gate', 'wait', 'resume', 'error')),
            name text,
            detail jsonb
        )
        """
    )
    op.execute("create index ix_agent_step_run on agent_step (run_id, id)")
    op.execute(
        """
        create table autonomy_setting (
            decision_type text primary key,
            level text not null default 'propose' check (level in ('off', 'propose', 'auto')),
            value_cap numeric(16, 2),
            min_confidence numeric(4, 3) not null default 0.9,
            max_wrong_rate numeric(5, 4) not null default 0.02,
            updated_by int,
            updated_at timestamptz not null default now()
        )
        """
    )
    op.execute(
        "create table autonomy_global (id int primary key check (id = 1), kill_switch boolean not null default false)"
    )
    op.execute("insert into autonomy_global (id, kill_switch) values (1, false)")
    op.execute(
        """
        create table autonomy_outcome (
            id bigserial primary key,
            decision_type text not null,
            at timestamptz not null default now(),
            auto boolean not null,
            wrong boolean not null default false,
            ref text
        )
        """
    )
    op.execute("create index ix_autonomy_outcome on autonomy_outcome (decision_type, at)")


def downgrade() -> None:
    for t in ("autonomy_outcome", "autonomy_global", "autonomy_setting", "agent_step", "agent_run"):
        op.execute(f"drop table {t}")
