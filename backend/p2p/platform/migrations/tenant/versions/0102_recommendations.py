"""The recommendation record (task 11a.3, D39 rule 7): what an agent recommended, on what evidence and figures

One row per recommendation: the capability, the business key, the agent's words, the evidence, the engine's figures it
was given and their hash, the rule versions, the run, the proposal a person decides, who directed the agent (the maker)
and who confirmed it. The evidence never changes after it is written (a trigger refuses it, for the owner too).
`autonomy_outcome` can name the recommendation an automatic action followed. A proposal can be `withdrawn`: its figures
went stale before a person confirmed it.

Revision ID: 0102
Revises: 0101
"""

from alembic import op

revision = "0102"
down_revision = "0101"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        create table recommendation (
            id serial primary key,
            capability text not null,
            kind text not null,
            business_key text not null,
            workflow_id int references workflow (id),
            run_id int references agent_run (id),
            event_id bigint references event (id),
            summary text not null,
            evidence jsonb not null,
            engine_figures jsonb not null,
            rule_versions jsonb not null default '{}',
            basis_hash text not null,
            status text not null default 'proposed' check (
                status in ('proposed', 'accepted', 'rejected', 'stale', 'superseded', 'withdrawn', 'handed_over')
            ),
            proposal_id int references proposal (id),
            created_by text not null,
            directed_by int,
            decided_by int,
            decided_at timestamptz,
            decision_note text,
            stale_reason text,
            superseded_by int references recommendation (id),
            task_id int references task (id),
            created_at timestamptz not null default now()
        )
        """
    )
    op.execute("create index ix_recommendation_key on recommendation (business_key, status)")
    # a proposal whose figures went stale before its confirmation is closed as withdrawn (spec 2.3)
    op.execute("alter table proposal drop constraint proposal_status_check")
    op.execute(
        "alter table proposal add constraint proposal_status_check "
        "check (status in ('pending', 'approved', 'edited', 'rejected', 'expired', 'withdrawn'))"
    )
    op.execute("alter table autonomy_outcome add column recommendation_id int references recommendation (id)")
    op.execute(
        """
        create function recommendation_frozen() returns trigger language plpgsql as $$ begin
          if (new.summary, new.evidence, new.engine_figures, new.rule_versions, new.basis_hash, new.run_id,
              new.created_by, new.capability, new.kind, new.business_key)
             is distinct from (old.summary, old.evidence, old.engine_figures, old.rule_versions, old.basis_hash,
              old.run_id, old.created_by, old.capability, old.kind, old.business_key)
          then raise exception 'a recommendation''s evidence cannot be changed'; end if;
          return new;
        end $$
        """
    )
    op.execute(
        "create trigger trg_recommendation_frozen before update on recommendation "
        "for each row execute function recommendation_frozen()"
    )


def downgrade() -> None:
    op.execute("update proposal set status = 'expired' where status = 'withdrawn'")
    op.execute("alter table proposal drop constraint proposal_status_check")
    op.execute(
        "alter table proposal add constraint proposal_status_check "
        "check (status in ('pending', 'approved', 'edited', 'rejected', 'expired'))"
    )
    op.execute("alter table autonomy_outcome drop column recommendation_id")
    op.execute("drop table recommendation")
    op.execute("drop function recommendation_frozen()")
