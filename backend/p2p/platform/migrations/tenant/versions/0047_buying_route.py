"""The buying route (D37): versioned thresholds, single-source items, emergency requests, direct buys, and what each
sourcing decision was based on

Revision ID: 0047
Revises: 0046
"""

from alembic import op

revision = "0047"
down_revision = "0046"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # One row per version. A row with no category is the company's rule; a category row only names the thresholds it
    # makes stricter (an empty column keeps the company's). A change closes the old row and adds the next version.
    op.execute(
        """
        create table buying_route_rule (
            id bigserial primary key,
            category_code text,
            version integer not null,
            effective_from date not null,
            effective_to date,
            agree_above numeric(18,2),
            reorder_within_days integer,
            reorder_on_time_pct numeric(5,2),
            reorder_below numeric(18,2),
            reorder_price_pct numeric(5,2),
            two_quotes_from numeric(18,2),
            three_quotes_above numeric(18,2),
            window_days integer,
            note text,
            created_by text,
            created_at timestamptz not null default now(),
            unique nulls not distinct (category_code, version)
        )
        """
    )
    op.execute(
        "insert into buying_route_rule (category_code, version, effective_from, agree_above, reorder_within_days, "
        "reorder_on_time_pct, reorder_below, reorder_price_pct, two_quotes_from, three_quotes_above, window_days, note, "
        "created_by) values (null, 1, '2000-01-01', 25000, 90, 80, 25000, 2, 25000, 200000, 30, "
        "'Plan default (D37)', 'system:migration')"
    )
    op.execute(
        """
        create table item_single_source (
            item_code text primary key,
            supplier_code text not null,
            reason text,
            marked_by text,
            marked_at timestamptz not null default now()
        )
        """
    )
    op.execute("alter table request drop constraint request_priority_check")
    op.execute(
        "alter table request add constraint request_priority_check check (priority in ('normal', 'urgent', 'emergency'))"
    )
    op.execute("alter table sourcing_decision drop constraint sourcing_decision_method_check")
    op.execute(
        "alter table sourcing_decision add constraint sourcing_decision_method_check "
        "check (method in ('contract', 'rfq', 'manual', 'direct'))"
    )
    op.execute(
        "alter table sourcing_decision add column route text, add column route_rule text, add column checks jsonb, "
        "add column line_value numeric(18,2), add column window_value numeric(18,2), add column min_quotes integer, "
        "add column decided_on date, add column needs_agreement boolean not null default false, "
        "add column proposal_id bigint"
    )
    op.execute("create index sourcing_decision_window on sourcing_decision (decided_on)")
    op.execute("alter table rfq add column min_quotes integer")  # the quotes the route asks for, when it sets them
    op.execute("alter table po_line drop constraint po_line_source_check")
    op.execute(
        "alter table po_line add constraint po_line_source_check check (source in ('contract', 'award', 'direct'))"
    )
    op.execute(
        "insert into role_permission (role, action) values ('Approver', 'sourcing.route_approve') on conflict do nothing"
    )


def downgrade() -> None:
    op.execute("delete from role_permission where action = 'sourcing.route_approve'")
    op.execute("alter table po_line drop constraint po_line_source_check")
    op.execute("alter table po_line add constraint po_line_source_check check (source in ('contract', 'award'))")
    op.execute("alter table rfq drop column min_quotes")
    op.execute("drop index sourcing_decision_window")
    for col in (
        "proposal_id",
        "needs_agreement",
        "decided_on",
        "min_quotes",
        "window_value",
        "line_value",
        "checks",
        "route_rule",
        "route",
    ):
        op.execute(f"alter table sourcing_decision drop column {col}")
    op.execute("alter table sourcing_decision drop constraint sourcing_decision_method_check")
    op.execute(
        "alter table sourcing_decision add constraint sourcing_decision_method_check "
        "check (method in ('contract', 'rfq', 'manual'))"
    )
    op.execute("alter table request drop constraint request_priority_check")
    op.execute("alter table request add constraint request_priority_check check (priority in ('normal', 'urgent'))")
    op.execute("drop table item_single_source")
    op.execute("drop table buying_route_rule")
