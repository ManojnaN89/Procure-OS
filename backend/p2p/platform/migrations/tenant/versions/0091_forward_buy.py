"""Forward buying (task 13.5): the versioned rule and the recommendation record

`forward_buy_rule` rows: the company (no scope), a category, an item, an item at a location. An empty field inherits from
the next wider row in force; the company row has every field. A change is the next version of the same scope from a
date. Version 1 of the company row is the recommended default of Q27 (not yet validated).

`forward_buy` keeps one recommendation with the engine's figures as they were worked out, the buyer's and the
approver's decisions, and the request it made. At most one is live (open or accepted) per item and location.

Revision ID: 0091
Revises: 0090
"""

from alembic import op

revision = "0091"
down_revision = "0090"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        create table forward_buy_rule (
            id bigserial primary key,
            version integer not null check (version > 0),
            category_code text,
            item_code text,
            location_code text,
            effective_from date not null,
            effective_to date,
            enabled boolean,
            min_gap_pct numeric(9,4) check (min_gap_pct >= 0),
            min_trend_months integer check (min_trend_months >= 2),
            max_cover_months integer check (max_cover_months >= 0),
            holding_cost_pct_month numeric(9,4) check (holding_cost_pct_month >= 0),
            min_net_saving_inr numeric(18,2) check (min_net_saving_inr >= 0),
            shelf_life_months integer check (shelf_life_months >= 0),
            storage_max_qty numeric(18,4) check (storage_max_qty >= 0),
            note text,
            entered_by text,
            entered_at timestamptz default now(),
            check (location_code is null or item_code is not null),
            check (category_code is null or item_code is null),
            check (category_code is not null or item_code is not null or (enabled is not null and min_gap_pct is not null
                and min_trend_months is not null and max_cover_months is not null and holding_cost_pct_month is not null
                and min_net_saving_inr is not null))
        )
        """
    )
    op.execute(
        "create unique index forward_buy_rule_scope_version on forward_buy_rule "
        "(coalesce(category_code, ''), coalesce(item_code, ''), coalesce(location_code, ''), version)"
    )
    op.execute(
        "insert into forward_buy_rule (version, effective_from, enabled, min_gap_pct, min_trend_months, max_cover_months, "
        "holding_cost_pct_month, min_net_saving_inr, note, entered_by) values (1, '2000-01-01', true, 5.0000, 6, 3, "
        "1.5000, 10000.00, 'Recommended default, needs validation (Q27)', 'system')"
    )
    op.execute(
        """
        create table forward_buy (
            id bigserial primary key,
            item_code text not null,
            location_code text not null,
            request_no text,
            line_no integer,
            status text not null check (status in ('open', 'accepted', 'approved', 'declined', 'rejected', 'superseded',
                'lapsed')),
            rule_label text not null,
            evaluation_id bigint references rule_evaluation (id),
            benchmark_id bigint references market_benchmark (id),
            price_now numeric(18,4),
            trend_now numeric(18,4),
            slope numeric(18,4),
            dip_pct numeric(9,4),
            avg_use numeric(18,4),
            cover numeric(18,4),
            qty_base numeric(18,4),
            qty numeric(18,4),
            est_price numeric(18,4),
            uom text,
            months jsonb not null,
            value numeric(18,2),
            gross_saving numeric(18,2),
            holding_cost numeric(18,2),
            net_saving numeric(18,2),
            limit_kind text,
            explanation text,
            explanation_source text check (explanation_source in ('template', 'ai')),
            created_at timestamptz not null default now(),
            decided_by text,
            decided_at timestamptz,
            decision_reason text,
            accepted_qty numeric(18,4),
            accepted_net_saving numeric(18,2),
            proposal_id bigint,
            approved_by text,
            approved_at timestamptz,
            new_request_no text,
            superseded_by bigint,
            lapse_reason text
        )
        """
    )
    op.execute(
        "create unique index forward_buy_one_live on forward_buy (item_code, location_code) "
        "where status in ('open', 'accepted')"
    )


def downgrade() -> None:
    op.execute("drop table forward_buy")
    op.execute("drop table forward_buy_rule")
