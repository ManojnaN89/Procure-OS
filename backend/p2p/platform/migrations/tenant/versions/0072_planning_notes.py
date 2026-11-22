"""Explanations of material requests written by the planning agent (task 11b.1)

A note keeps the engine's figures the agent was given (`snapshot`, and its hash: the same figures are never explained
twice), the agent's run, and whether the explanation was accepted or only the facts are shown (with the reasons).
The capability is off until an administrator and a second one switch it on (decision type `planning.explain`).

Revision ID: 0072
Revises: 0071
"""

from alembic import op

revision = "0072"
down_revision = "0071"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        create table planning_note (
            id bigserial primary key,
            mr_no text not null,
            run_id integer references agent_run (id),
            status text not null check (status in ('accepted', 'facts_only')),
            summary text,
            lines jsonb,
            snapshot jsonb not null,
            snapshot_hash text not null,
            reasons text[] not null default '{}',
            created_by text not null,
            created_at timestamptz not null default now(),
            unique (mr_no, snapshot_hash)
        )
        """
    )
    op.execute(
        "insert into autonomy_setting (decision_type, level) values ('planning.explain', 'off') "
        "on conflict (decision_type) do nothing"
    )


def downgrade() -> None:
    op.execute("delete from autonomy_setting where decision_type = 'planning.explain'")
    op.execute("drop table planning_note")
