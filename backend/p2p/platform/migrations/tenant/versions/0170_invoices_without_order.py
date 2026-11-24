"""Invoices without an order (block 10, D-14), the one live invoice key (task 3.3), and the bill's place in the budget.

- `non_po_rule`: one row per category and version; a bill keeps the rule id it was entered under.
- `invoice`: `kind` (`po` or `non_po`), the category, cost centre and rule of a bill, its authoriser, the approval rule
  and version it was decided under, the duplicate it may be (`suspect_of`), the over-budget reason and `approved_by`
  (the invoice approver of rule 7). Statuses gain `awaiting_authorisation` and `awaiting_approval`.
- `invoice_line`: `po_no` and `po_line_no` may be null for a bill, which then carries its own category, cost centre, GL
  and location.
- `invoice_approval_step`: one row per person in the approval chain, in order.
- `invoice_document_link`: the bill copy and its supporting document.
- `invoice_draft`: the day and time the document was received, the sender's verdict, and the kind.
- `dedupe_key` is recomputed with the FR33 rule (leading zeros dropped from each run of digits), then two partial unique
  indexes make two live invoices under one key, or one IRN, impossible. If the recompute finds two live rows sharing a
  key or an IRN, the migration stops and lists them.
- `budget_use`: bills count in part `order` from authorisation until paid, on the line's own cost centre and GL.
- setting `non_po_duplicate_window_days`.

Revision ID: 0170
Revises: 0150
"""

import re
from collections.abc import Callable

import sqlalchemy as sa
from alembic import op

revision = "0170"
down_revision = "0150"
branch_labels = None
depends_on = None

_BUDGET_USE = """
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
select 'order', null::text, null::int, null::text, null::int, il.gl_code::text, il.cost_centre_code,
       cc.department_code, il.location_code, il.location_code, null::text, il.taxable
from invoice_line il join invoice i on i.no = il.invoice_no
left join cost_centre cc on cc.code = il.cost_centre_code
where i.kind = 'non_po' and il.po_no is null and il.gl_code is not null
  and i.status in ('awaiting_approval', 'approved', 'part_paid')
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

# the view as migration 0090 left it (a downgrade restores it)
_OLD_BUDGET_USE = """
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


def _key(number: str) -> str:
    """FR33's key: no leading zeros in a run of digits (a run of zeros keeps one 0), then upper case and no punctuation."""
    trimmed = re.sub(r"\d+", lambda m: m.group().lstrip("0") or "0", number.upper())
    return re.sub(r"[^A-Z0-9]", "", trimmed)


def _recompute(conn: sa.Connection, keyer: Callable[[str], str]) -> None:
    for no, number in conn.execute(sa.text("select no, supplier_invoice_no from invoice")).all():
        conn.execute(
            sa.text("update invoice set dedupe_key = :k where no = :n"),
            {"k": keyer(str(number)), "n": no},
        )


def _clashes(conn: sa.Connection) -> list[str]:
    """Two live invoices under one key or one IRN, in words; empty when the partial unique indexes can be made."""
    rows = conn.execute(
        sa.text(
            "select supplier_code, fy, type, dedupe_key, string_agg(no, ', ' order by no) from invoice "
            "where status <> 'cancelled' group by 1, 2, 3, 4 having count(*) > 1"
        )
    ).all()
    irns = conn.execute(
        sa.text(
            "select upper(irn), string_agg(no, ', ' order by no) from invoice "
            "where irn is not null and status <> 'cancelled' group by 1 having count(*) > 1"
        )
    ).all()
    return [f"{s}/{f}/{t}/{k}: {nos}" for s, f, t, k, nos in rows] + [f"IRN {irn}: {nos}" for irn, nos in irns]


def upgrade() -> None:
    op.execute(
        """
        create table non_po_rule (
            id bigserial primary key,
            category_code text not null references item_category (code),
            version int not null,
            effective_from date not null,
            effective_to date,
            enabled boolean not null default true,
            max_value numeric(14,2) not null check (max_value > 0),
            supporting_doc text,
            note text,
            created_by text,
            created_at timestamptz not null default now(),
            unique (category_code, version)
        )
        """
    )
    op.execute(
        """
        alter table invoice add column kind text not null default 'po' check (kind in ('po', 'non_po')),
            add column category_code text references item_category (code),
            add column cost_centre_code text references cost_centre (code),
            add column non_po_rule_id bigint references non_po_rule (id),
            add column authoriser text,
            add column authorised_by text,
            add column authorised_at timestamptz,
            add column authorised_on date,
            add column approval_rule_code text,
            add column approval_rule_version int,
            add column suspect_of text references invoice (no),
            add column not_duplicate_reason text,
            add column over_budget_reason text,
            add column approved_by text
        """
    )
    op.execute(
        "update invoice i set approved_by = coalesce("
        "(select m.resolved_by from match_result m where m.invoice_no = i.no and m.resolved_by is not null "
        "order by m.id desc limit 1), i.entered_by) "
        "where i.status in ('approved', 'part_paid', 'paid')"
    )
    op.execute(
        "alter table invoice drop constraint invoice_status_check, add constraint invoice_status_check "
        "check (status in ('received', 'matched', 'blocked', 'awaiting_authorisation', 'awaiting_approval', "
        "'approved', 'part_paid', 'paid', 'cancelled'))"
    )
    op.execute(
        """
        alter table invoice_line alter column po_no drop not null, alter column po_line_no drop not null,
            add column category_code text,
            add column cost_centre_code text,
            add column gl_code text,
            add column location_code text,
            add constraint invoice_line_order_or_bill check (
                (po_no is not null and po_line_no is not null)
                or (po_no is null and po_line_no is null and category_code is not null
                    and cost_centre_code is not null and gl_code is not null))
        """
    )
    op.execute(
        """
        create table invoice_approval_step (
            id bigserial primary key,
            invoice_no text not null references invoice (no),
            step int not null,
            role text not null,
            approver text not null,
            delegate text,
            status text not null check (status in ('waiting', 'pending', 'approved', 'rejected', 'cancelled')),
            acted_by text,
            acted_at timestamptz,
            comment text,
            task_id bigint,
            rule_id bigint,
            rule_code text,
            rule_version int,
            unique (invoice_no, step)
        )
        """
    )
    op.execute(
        """
        create table invoice_document_link (
            invoice_no text not null references invoice (no),
            document_id int not null references document (id),
            kind text not null check (kind in ('copy', 'supporting')),
            added_by text,
            primary key (invoice_no, document_id)
        )
        """
    )
    op.execute(
        "alter table invoice_draft add column received_on date, add column received_at timestamptz, "
        "add column sender_verdict text, add column kind text"
    )
    conn = op.get_bind()
    _recompute(conn, _key)
    listed = _clashes(conn)
    if listed:
        raise RuntimeError("two live invoices share a key or an IRN: " + "; ".join(listed))
    op.execute("drop index invoice_dedupe")
    op.execute(
        "create unique index invoice_live_key on invoice (supplier_code, fy, type, dedupe_key) "
        "where status <> 'cancelled'"
    )
    op.execute(
        "create unique index invoice_live_irn on invoice (upper(irn)) where irn is not null and status <> 'cancelled'"
    )
    op.execute("drop view budget_use")
    op.execute(_BUDGET_USE)
    op.execute(
        "insert into org_setting (key, value, description) values ('non_po_duplicate_window_days', '7', "
        "'A bill without an order with the same supplier, category and total within this many days of another is asked "
        "about') on conflict do nothing"
    )


def downgrade() -> None:
    count = op.get_bind().execute(sa.text("select count(*) from invoice where kind = 'non_po'")).scalar_one()
    if count:
        raise RuntimeError(f"{count} bills without an order exist: they are not dropped by a downgrade")
    op.execute("delete from org_setting where key = 'non_po_duplicate_window_days'")
    op.execute("drop view budget_use")
    op.execute(_OLD_BUDGET_USE)
    op.execute("drop index invoice_live_irn")
    op.execute("drop index invoice_live_key")
    _recompute(op.get_bind(), lambda number: re.sub(r"[^A-Z0-9]", "", str(number).upper()))
    op.execute("create index invoice_dedupe on invoice (supplier_code, fy, dedupe_key)")
    op.execute(
        "alter table invoice_draft drop column kind, drop column sender_verdict, "
        "drop column received_at, drop column received_on"
    )
    op.execute("drop table invoice_document_link")
    op.execute("drop table invoice_approval_step")
    op.execute(
        "alter table invoice_line drop constraint invoice_line_order_or_bill, "
        "drop column location_code, drop column gl_code, drop column cost_centre_code, drop column category_code"
    )
    op.execute("update invoice_line set po_no = '' where po_no is null")
    op.execute("update invoice_line set po_line_no = 0 where po_line_no is null")
    op.execute("alter table invoice_line alter column po_no set not null, alter column po_line_no set not null")
    op.execute(
        "alter table invoice drop constraint invoice_status_check, add constraint invoice_status_check "
        "check (status in ('received', 'matched', 'blocked', 'approved', 'part_paid', 'paid', 'cancelled'))"
    )
    op.execute(
        "alter table invoice drop column approved_by, drop column over_budget_reason, "
        "drop column not_duplicate_reason, drop column suspect_of, drop column approval_rule_version, "
        "drop column approval_rule_code, drop column authorised_on, drop column authorised_at, "
        "drop column authorised_by, drop column authoriser, drop column non_po_rule_id, "
        "drop column cost_centre_code, drop column category_code, drop column kind"
    )
    op.execute("drop table non_po_rule")
