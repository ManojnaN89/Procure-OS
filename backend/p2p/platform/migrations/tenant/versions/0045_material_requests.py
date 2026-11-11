"""Material requests: one per production order with a line per component, the stock set aside for each line, what has
been issued, and which purchase request lines buy the shortfall; a view of each line with what is still asked for

Revision ID: 0045
Revises: 0044
"""

from alembic import op

revision = "0045"
down_revision = "0044"
branch_labels = None
depends_on = None

# the store makes material requests, runs the store check, issues and cancels them
GRANTS = [("Store", "material.manage")]


def upgrade() -> None:
    op.execute(
        """
        create table material_request (
            no text primary key,
            production_order_id bigint not null references production_order (id),
            order_no text not null,
            product_code text not null,
            location_code text not null,
            need_by date not null,
            status text not null default 'open' check (status in ('open', 'issued', 'cancelled')),
            cancel_reason text,
            created_by text,
            created_at timestamptz not null default now()
        )
        """
    )
    op.execute("create index material_request_order on material_request (production_order_id)")
    # quantities are in the item's base unit; set aside plus issued never exceeds the need
    op.execute(
        """
        create table material_request_line (
            id bigserial primary key,
            mr_no text not null references material_request (no),
            line_no integer not null,
            requirement_id bigint not null unique references material_requirement (id),
            item_code text not null,
            location_code text not null,
            uom text not null,
            need_qty numeric(18,4) not null check (need_qty > 0),
            reserved_qty numeric(18,4) not null default 0 check (reserved_qty >= 0),
            issued_qty numeric(18,4) not null default 0 check (issued_qty >= 0),
            need_by date not null,
            status text not null default 'open' check (status in ('open', 'issued', 'cancelled')),
            check (reserved_qty + issued_qty <= need_qty),
            unique (mr_no, line_no)
        )
        """
    )
    op.execute("create index material_request_line_open on material_request_line (item_code, location_code, status)")
    # which purchase request line buys a material request line's shortfall, and how much of it (base unit); one request
    # line may later buy for several material request lines
    op.execute(
        """
        create table material_request_buy (
            id bigserial primary key,
            mr_line_id bigint not null references material_request_line (id),
            request_no text not null,
            request_line_no integer not null,
            qty numeric(18,4) not null check (qty > 0),
            created_at timestamptz not null default now(),
            foreign key (request_no, request_line_no) references request_line (request_no, line_no)
        )
        """
    )
    op.execute("create index material_request_buy_line on material_request_buy (mr_line_id)")
    # each line with what live purchase requests still ask for it (a rejected or cancelled request no longer counts)
    op.execute(
        """
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
    )
    for role, action in GRANTS:
        op.execute(f"insert into role_permission (role, action) values ('{role}', '{action}') on conflict do nothing")  # noqa: S608


def downgrade() -> None:
    for role, action in GRANTS:
        op.execute(f"delete from role_permission where role = '{role}' and action = '{action}'")  # noqa: S608
    op.execute("drop view material_request_cover")
    op.execute("drop table material_request_buy")
    op.execute("drop table material_request_line")
    op.execute("drop table material_request")
