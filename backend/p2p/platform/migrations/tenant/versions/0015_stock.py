"""Stock movements and the stock level of every item at every location

Revision ID: 0015
Revises: 0014
"""

from alembic import op

revision = "0015"
down_revision = "0014"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # source tells a loaded history from movements the platform records itself, so a re-import never touches those
    op.execute(
        """
        create table stock_movement (
            id text primary key,
            item_code text not null references item (code) deferrable initially deferred,
            location_code text not null references location (code) deferrable initially deferred,
            date date not null,
            type text not null check (type in ('receipt', 'issue', 'return', 'adjust', 'transfer_in', 'transfer_out')),
            qty numeric(18,4) not null,
            unit_cost numeric(18,4),
            cost_centre_code text,
            ref_doc text,
            source text not null default 'import' check (source in ('import', 'platform'))
        )
        """
    )
    op.execute("create index stock_movement_item_loc on stock_movement (item_code, location_code, date)")
    # receipts and transfers in add to stock; issues, returns to the vendor and transfers out take away; an adjustment
    # carries its own sign. Movements before the opening date are already inside the opening balance.
    op.execute(
        """
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
    )


def downgrade() -> None:
    op.execute("drop view stock_level")
    op.execute("drop table stock_movement")
