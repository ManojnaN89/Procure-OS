"""Tracing (task 11a.4): who started an agent run and for which capability; model calls linked to their run

`agent_run` records the capability, who or what started it (`user:<id>`, `job:<id>`, `service:<name>`, `event:<id>`),
the person on whose behalf, the dev switcher's `via`, and its workflow, event and job. `model_call` records the run,
capability, person, workflow and the document an AI step read. Prompts and raw answers are not stored (policy in
docs/model-selection.md).

Revision ID: 0103
Revises: 0102
"""

from alembic import op

revision = "0103"
down_revision = "0102"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        alter table agent_run
            add column capability text,
            add column started_by text,
            add column user_id int,
            add column via text,
            add column workflow_id int references workflow (id),
            add column event_id bigint references event (id),
            add column job_id bigint
        """
    )
    op.execute(
        """
        update agent_run set started_by = 'unknown', capability = case agent
            when 'assistant' then 'assistant' when 'invoice_exceptions' then 'invoice_ap'
            when 'planning' then 'planning' when 'intake' then 'intake' when 'supplier_comms' then 'execution' end
        """
    )
    op.execute(
        """
        alter table model_call
            add column run_id int references agent_run (id),
            add column capability text,
            add column user_id int,
            add column workflow_id int,
            add column entity text,
            add column entity_id text
        """
    )
    op.execute("create index ix_model_call_run on model_call (run_id)")
    op.execute("create index ix_model_call_month on model_call (at)")
    op.execute("create index ix_agent_run_capability on agent_run (capability, status)")


def downgrade() -> None:
    op.execute("drop index ix_agent_run_capability")
    op.execute("drop index ix_model_call_month")
    op.execute("drop index ix_model_call_run")
    op.execute(
        "alter table model_call drop column run_id, drop column capability, drop column user_id, "
        "drop column workflow_id, drop column entity, drop column entity_id"
    )
    op.execute(
        "alter table agent_run drop column capability, drop column started_by, drop column user_id, drop column via, "
        "drop column workflow_id, drop column event_id, drop column job_id"
    )
