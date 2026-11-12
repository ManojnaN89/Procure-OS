"""Stock reserved when it arrives, estimates that say where they came from, and joiner kits as ERP demand

A request line keeps the source of its estimate (`est_source`: the contract, the order the last price was paid on, or the
standard price). What a purchase request buys for a material request line may be cut to zero when stock covers it, and
the line's cover counts only what is still to arrive (asked for, less what was received or closed on its orders), so
stock that arrived is reserved, not counted twice. The joiner kits of an office (what each kind of new joiner gets) are
kept, and a joiner plan becomes material requirements of kind `joiner`, with the joiner's department, and a material
request per plan and department.

Revision ID: 0055
Revises: 0054
"""

from alembic import op

revision = "0055"
down_revision = "0054"
branch_labels = None
depends_on = None

# each line with what live purchase requests still bring for it: asked for, less what already arrived for it (what a
# request line's orders received or closed goes to its material request lines earliest need-by first)
COVER = """
    create view material_request_cover as
    with done as (
        select request_no, request_line_no,
               sum((received_qty + short_closed_qty) * coalesce(base_qty, qty) / qty) as done
        from po_line where status <> 'cancelled' and qty > 0 group by request_no, request_line_no
    ),
    links as (
        select b.mr_line_id, b.qty, coalesce(d.done, 0) as done,
               coalesce(sum(b.qty) over (partition by b.request_no, b.request_line_no order by m.need_by, b.id
                                         rows between unbounded preceding and 1 preceding), 0) as before
        from material_request_buy b
        join material_request_line m on m.id = b.mr_line_id
        join request_line rl on rl.request_no = b.request_no and rl.line_no = b.request_line_no
        join request r on r.no = b.request_no
        left join done d on d.request_no = b.request_no and d.request_line_no = b.request_line_no
        where m.status <> 'cancelled' and rl.status <> 'cancelled' and r.status not in ('cancelled', 'rejected')
    )
    select l.id, l.mr_no, l.line_no, l.item_code, l.location_code, l.uom, l.need_qty, l.reserved_qty, l.issued_qty,
           l.need_by, l.status, coalesce(c.requested, 0) as requested_qty
    from material_request_line l
    left join (
        select mr_line_id, sum(greatest(qty - greatest(done - before, 0), 0)) as requested from links group by mr_line_id
    ) c on c.mr_line_id = l.id
"""

OLD_COVER = """
    create view material_request_cover as
    select l.id, l.mr_no, l.line_no, l.item_code, l.location_code, l.uom, l.need_qty, l.reserved_qty, l.issued_qty,
           l.need_by, l.status, coalesce(b.requested, 0) as requested_qty
    from material_request_line l
    left join (
        select b.mr_line_id, sum(b.qty) as requested
        from material_request_buy b
        join request_line rl on rl.request_no = b.request_no and rl.line_no = b.request_line_no
        join request r on r.no = b.request_no
        where rl.status <> 'cancelled' and r.status not in ('cancelled', 'rejected')
        group by b.mr_line_id
    ) b on b.mr_line_id = l.id
"""


def upgrade() -> None:
    op.execute("alter table request_line add column est_source text")
    op.execute("alter table material_request_buy drop constraint material_request_buy_qty_check")
    op.execute("alter table material_request_buy add constraint material_request_buy_qty_check check (qty >= 0)")
    op.execute("drop view material_request_cover")
    op.execute(COVER)
    op.execute("alter table material_requirement drop constraint material_requirement_source_kind_check")
    op.execute(
        "alter table material_requirement add constraint material_requirement_source_kind_check "
        "check (source_kind in ('production_order', 'mrp', 'joiner'))"
    )
    op.execute("alter table material_requirement add column department_code text")
    op.execute("alter table material_request alter column production_order_id drop not null")
    op.execute(
        "alter table material_request add column source_kind text not null default 'production_order' "
        "check (source_kind in ('production_order', 'joiner'))"
    )
    op.execute("alter table material_request add column department_code text")
    op.execute(
        """
        create table joiner_kit (
            id bigserial primary key,
            kit text not null,
            item_code text not null,
            qty numeric(18,4) not null check (qty > 0),
            unit text not null,
            base_qty numeric(18,4) not null check (base_qty > 0),
            import_id bigint not null references demand_import (id),
            created_at timestamptz not null default now(),
            unique (kit, item_code)
        )
        """
    )


def downgrade() -> None:
    op.execute("drop table joiner_kit")
    op.execute("alter table material_request drop column department_code")
    op.execute("alter table material_request drop column source_kind")
    op.execute("alter table material_request alter column production_order_id set not null")
    op.execute("alter table material_requirement drop column department_code")
    op.execute("alter table material_requirement drop constraint material_requirement_source_kind_check")
    op.execute(
        "alter table material_requirement add constraint material_requirement_source_kind_check "
        "check (source_kind in ('production_order', 'mrp'))"
    )
    op.execute("drop view material_request_cover")
    op.execute(OLD_COVER)
    op.execute("alter table material_request_buy drop constraint material_request_buy_qty_check")
    op.execute("alter table material_request_buy add constraint material_request_buy_qty_check check (qty > 0)")
    op.execute("alter table request_line drop column est_source")
