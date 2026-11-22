"""Quality of stock: usable, quarantine (waiting for a quality decision) and blocked (judged bad), task 4.1

Every movement carries the quality of the stock it moves; `requalify` moves stock from one quality to another (a pair of
rows with the same reference, signed like an adjustment). `stock_level` keeps its meaning and its columns: usable stock
only. `stock_quality_level` shows every quality. Each held lot is a `stock_hold` with what is left of it; a decision
closes it (or what is left of it) into usable or blocked stock.

Revision ID: 0070
Revises: 0067
"""

from alembic import op

revision = "0070"
down_revision = "0067"
branch_labels = None
depends_on = None

OLD_LEVEL = """
    create view stock_level as
    select p.item_code, p.location_code,
           coalesce(o.qty, 0) as opening_qty, o.as_of,
           coalesce(m.net, 0) as moved_qty,
           coalesce(o.qty, 0) + coalesce(m.net, 0) as on_hand,
           m.last_movement
    from (select item_code, location_code from stock_opening
          union select item_code, location_code from stock_movement) p
    left join stock_opening o on o.item_code = p.item_code and o.location_code = p.location_code
    left join lateral (
        select sum(case when s.type in ('receipt', 'transfer_in', 'adjust') then s.qty else -s.qty end) as net,
               max(s.date) as last_movement
        from stock_movement s
        where s.item_code = p.item_code and s.location_code = p.location_code
          and (o.as_of is null or s.date >= o.as_of)
    ) m on true
"""

# usable stock only: the opening balance is usable, held lots are counted by their own quality
LEVEL = """
    create view stock_level as
    select p.item_code, p.location_code,
           coalesce(o.qty, 0) as opening_qty, o.as_of,
           coalesce(m.net, 0) as moved_qty,
           coalesce(o.qty, 0) + coalesce(m.net, 0) as on_hand,
           m.last_movement
    from (select item_code, location_code from stock_opening
          union select item_code, location_code from stock_movement) p
    left join stock_opening o on o.item_code = p.item_code and o.location_code = p.location_code
    left join lateral (
        select sum(case when s.type in ('receipt', 'transfer_in', 'adjust', 'requalify') then s.qty else -s.qty end)
                   filter (where s.quality = 'usable') as net,
               max(s.date) as last_movement
        from stock_movement s
        where s.item_code = p.item_code and s.location_code = p.location_code
          and (o.as_of is null or s.date >= o.as_of)
    ) m on true
"""

QUALITY_LEVEL = """
    create view stock_quality_level as
    select item_code, location_code, 'usable'::text as quality, on_hand as qty from stock_level
    union all
    select s.item_code, s.location_code, s.quality,
           sum(case when s.type in ('receipt', 'transfer_in', 'adjust', 'requalify') then s.qty else -s.qty end)
    from stock_movement s
    left join stock_opening o on o.item_code = s.item_code and o.location_code = s.location_code
    where s.quality <> 'usable' and (o.as_of is null or s.date >= o.as_of)
    group by s.item_code, s.location_code, s.quality
"""


def upgrade() -> None:
    op.execute(
        "alter table stock_movement add column quality text not null default 'usable' "
        "check (quality in ('usable', 'quarantine', 'blocked'))"
    )
    op.execute("alter table stock_movement drop constraint stock_movement_type_check")
    op.execute(
        "alter table stock_movement add constraint stock_movement_type_check check (type in ('receipt', 'issue', "
        "'return', 'adjust', 'transfer_in', 'transfer_out', 'requalify'))"
    )
    op.execute("drop view stock_level")
    op.execute(LEVEL)
    op.execute(QUALITY_LEVEL)
    op.execute(
        """
        create table stock_hold (
            id bigserial primary key,
            item_code text not null references item (code),
            location_code text not null references location (code),
            quality text not null check (quality in ('quarantine', 'blocked')),
            qty numeric(18,4) not null check (qty > 0),
            left_qty numeric(18,4) not null check (left_qty >= 0),
            reason text not null,
            reference text,
            source text not null check (source in ('receipt', 'manual', 'import', 'decision')),
            import_id text,
            gr_no text,
            gr_line_no integer,
            parent_id bigint references stock_hold (id),
            held_by text not null,
            held_at timestamptz not null default now(),
            closed_at timestamptz
        )
        """
    )
    op.execute("create index stock_hold_open on stock_hold (item_code, location_code) where closed_at is null")
    op.execute("insert into role_permission (role, action) values ('Store', 'stock.quality') on conflict do nothing")


def downgrade() -> None:
    op.execute("delete from role_permission where role = 'Store' and action = 'stock.quality'")
    op.execute("drop table stock_hold")
    op.execute("drop view stock_quality_level")
    op.execute("drop view stock_level")
    op.execute("delete from stock_movement where quality <> 'usable' or type = 'requalify'")
    op.execute(OLD_LEVEL)
    op.execute("alter table stock_movement drop constraint stock_movement_type_check")
    op.execute(
        "alter table stock_movement add constraint stock_movement_type_check check (type in ('receipt', 'issue', "
        "'return', 'adjust', 'transfer_in', 'transfer_out'))"
    )
    op.execute("alter table stock_movement drop column quality")
