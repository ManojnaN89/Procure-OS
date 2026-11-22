"""The budget engine (task 3.7): what counts against a budget, stock at receipt (D-10), period locks and year end (D-09)

`budget_actual` gets the receipt as a source (a stocked line is actual when it is accepted), the order line, request line
and location it belongs to, and the month the event happened (`event_period`) beside the month it is booked in
(`period`: the first open month when the event's month is locked). `budget` rows may be adjustments (a supplement,
return or transfer approved as a proposal). A locked month never changes; the year end keeps the open commitments it
carried into the new year. The view `budget_use` is the one place the three parts of what is used are counted: request
lines not yet ordered (at their estimate), open order lines (at the order price, less what is already actual on them)
and actuals.

Revision ID: 0090
Revises: 0080
"""

from alembic import op

revision = "0090"
down_revision = "0080"
branch_labels = None
depends_on = None

# the location a row spends at: its cost centre's, else where it is delivered (aliases: l request line, r request)
VIEW = """
create view budget_use as
select 'request'::text as part, l.request_no, l.line_no as request_line_no, null::text as po_no, null::int as po_line_no,
       l.gl_code::text as gl_code, l.cost_centre_code, r.department_code,
       coalesce(l.delivery_location, r.ship_to_location) as charge_location,
       coalesce((select c.location_code from cost_centre c where c.code = l.cost_centre_code), l.delivery_location, r.ship_to_location) as location_code,
       null::text as period, round(l.qty * coalesce(l.est_price, 0), 2) as amount
from request_line l join request r on r.no = l.request_no
where r.status in ('submitted', 'approved') and l.status in ('open', 'sourcing') and l.gl_code is not null
union all
select 'order', pl.request_no, pl.request_line_no, pl.po_no, pl.line_no,
       coalesce(l.gl_code, pl.gl_code)::text, l.cost_centre_code, r.department_code,
       coalesce(l.delivery_location, r.ship_to_location, p.ship_to_location),
       coalesce((select c.location_code from cost_centre c where c.code = l.cost_centre_code), p.ship_to_location, l.delivery_location, r.ship_to_location),
       null::text,
       greatest(0, round((pl.qty - pl.short_closed_qty) * pl.unit_price, 2)
           - coalesce((select sum(a.amount) from budget_actual a where a.po_no = pl.po_no and a.po_line_no = pl.line_no), 0))
from po_line pl join po p on p.no = pl.po_no
left join request_line l on l.request_no = pl.request_no and l.line_no = pl.request_line_no
left join request r on r.no = pl.request_no
where pl.status not in ('cancelled', 'closed') and p.status not in ('cancelled', 'rejected', 'closed')
  and coalesce(l.gl_code, pl.gl_code) is not null
union all
select 'spent', a.request_no, a.request_line_no, a.po_no, a.po_line_no, a.gl_code::text,
       coalesce(l.cost_centre_code, a.cost_centre_code), r.department_code,
       coalesce(l.delivery_location, r.ship_to_location, a.location_code),
       coalesce((select c.location_code from cost_centre c where c.code = coalesce(l.cost_centre_code, a.cost_centre_code)), a.location_code,
                l.delivery_location, r.ship_to_location),
       a.period, a.amount
from budget_actual a
left join request_line l on l.request_no = a.request_no and l.line_no = a.request_line_no
left join request r on r.no = a.request_no
where a.gl_code is not null
"""


def upgrade() -> None:
    op.execute("alter table budget_actual alter column invoice_no drop not null")
    op.execute(
        """
        alter table budget_actual
            add column source text not null default 'payment' check (source in ('payment', 'receipt')),
            add column gr_no text,
            add column po_no text,
            add column po_line_no integer,
            add column request_no text,
            add column request_line_no integer,
            add column location_code text,
            add column event_period text,
            add column posted_at timestamptz not null default now()
        """
    )
    op.execute("update budget_actual set event_period = period")
    op.execute(
        "update budget_actual a set location_code = (select p.ship_to_location from invoice_line il "
        "join po p on p.no = il.po_no where il.invoice_no = a.invoice_no order by il.line_no limit 1)"
    )
    op.execute("create index budget_actual_po_line on budget_actual (po_no, po_line_no)")
    op.execute("create index budget_actual_gl_period on budget_actual (gl_code, period)")
    op.execute(
        """
        alter table budget
            add column kind text not null default 'plan' check (kind in ('plan', 'adjustment')),
            add column reason text,
            add column proposal_id bigint
        """
    )
    op.execute(
        """
        create table budget_period_lock (
            period text primary key check (period ~ '^[0-9]{4}-(0[1-9]|1[0-2])$'),
            locked_by text not null,
            locked_at timestamptz not null default now(),
            reason text not null
        )
        """
    )
    op.execute(
        """
        create table budget_year_end (
            fy text primary key check (fy ~ '^[0-9]{4}-[0-9]{2}$'),
            closed_by text not null,
            closed_at timestamptz not null default now(),
            note text,
            carried jsonb not null,
            totals jsonb not null
        )
        """
    )
    op.execute(VIEW)
    op.execute("insert into role_permission (role, action) values ('Finance', 'budget.manage') on conflict do nothing")


def downgrade() -> None:
    op.execute("delete from role_permission where action = 'budget.manage'")
    op.execute("drop view budget_use")
    op.execute("drop table budget_year_end")
    op.execute("drop table budget_period_lock")
    op.execute("alter table budget drop column proposal_id, drop column reason, drop column kind")
    op.execute("drop index budget_actual_gl_period")
    op.execute("drop index budget_actual_po_line")
    op.execute(
        "alter table budget_actual drop column posted_at, drop column event_period, drop column location_code, "
        "drop column request_line_no, drop column request_no, drop column po_line_no, drop column po_no, "
        "drop column gr_no, drop column source"
    )
    op.execute("delete from budget_actual where invoice_no is null")
    op.execute("alter table budget_actual alter column invoice_no set not null")
