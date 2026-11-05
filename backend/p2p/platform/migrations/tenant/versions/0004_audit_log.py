"""Audit log: insert only

Revision ID: 0004
Revises: 0003
"""

from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        create table audit_event (
            id bigserial primary key,
            at timestamptz not null default now(),
            actor_type text not null check (actor_type in ('user', 'agent', 'service', 'rule')),
            actor_id text not null,
            action text not null,
            entity text not null,
            entity_id text not null,
            before jsonb,
            after jsonb,
            reason text,
            rule_version text,
            model text,
            prompt_version text
        )
        """
    )
    op.execute("create index ix_audit_entity on audit_event (entity, entity_id)")
    op.execute("create index ix_audit_at on audit_event (at)")
    op.execute(
        """
        create function audit_event_is_append_only() returns trigger language plpgsql as $$
        begin
            raise exception 'audit_event is insert only (% refused)', tg_op;
        end;
        $$
        """
    )
    op.execute(
        "create trigger audit_event_no_change before update or delete on audit_event "
        "for each row execute function audit_event_is_append_only()"
    )
    op.execute(
        "create trigger audit_event_no_truncate before truncate on audit_event "
        "for each statement execute function audit_event_is_append_only()"
    )


def downgrade() -> None:
    op.execute("drop table audit_event")
    op.execute("drop function audit_event_is_append_only()")
