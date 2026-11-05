"""Model gateway: a log of every model call and a cache for repeated prompts

Revision ID: 0010
Revises: 0009
"""

from alembic import op

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        create table model_call (
            id bigserial primary key,
            at timestamptz not null default now(),
            step text not null,
            provider text,
            model text,
            prompt_version text,
            tokens_in int not null default 0,
            tokens_out int not null default 0,
            cost_usd numeric(12, 6) not null default 0,
            ms int not null default 0,
            attempts int not null default 1,
            outcome text not null check (outcome in ('ok', 'cache_hit', 'error', 'refusal', 'invalid')),
            error text,
            request_hash text
        )
        """
    )
    op.execute("create index ix_model_call_step on model_call (step, at)")
    op.execute(
        """
        create table model_cache (
            request_hash text primary key,
            response jsonb not null,
            created_at timestamptz not null default now()
        )
        """
    )


def downgrade() -> None:
    op.execute("drop table model_cache")
    op.execute("drop table model_call")
