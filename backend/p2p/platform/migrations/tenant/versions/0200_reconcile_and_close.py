"""Reconcile and close (block 13): short-close by risk, two-track closure, GSTR-2B, price measures, month close and
the accounting export.

Revision ID: 0200
Revises: 0150 (the owner chains 0150 -> ... -> 0200 at merge)
"""

from alembic import op

revision = "0200"
down_revision = "0150"
branch_labels = None
depends_on = None

SHORT_CLOSE_STATUS = "'waiting', 'closed', 'rejected', 'stale', 'withdrawn'"
MEASURES = "'ppv', 'contract_variance', 'market_gap', 'saving_potential', 'saving_realised'"
OUTCOMES = "'matched', 'differs', 'gstin_differs', 'only_2b', 'only_books', 'duplicate'"

# R2.7: a short-closed line keeps its commitment until the order closes, so the order part no longer subtracts
# short_closed_qty (the view is otherwise unchanged from 0090)
BUDGET_VIEW = """
create or replace view budget_use as
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
       greatest(0, round(pl.qty * pl.unit_price, 2)
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
    # a line short-closed before this migration takes its own status; the order goes back to received (OP-28)
    op.execute(
        "alter table po_line drop constraint po_line_status_check, add constraint po_line_status_check "
        "check (status in ('open', 'part_received', 'received', 'closed', 'short_closed', 'cancelled'))"
    )
    op.execute(
        "update po_line set status = 'short_closed' where status = 'closed' and coalesce(short_closed_qty, 0) > 0"
    )
    op.execute(
        "update po set status = 'received', closed_at = null where status = 'closed' "
        "and exists (select 1 from po_line l where l.po_no = po.no and l.status = 'short_closed')"
    )

    op.execute(
        f"""
        create table po_short_close (
            id bigserial primary key,
            po_no text not null references po (no) on delete cascade,
            lines jsonb not null default '[]',
            value numeric(18,2) not null default 0,
            risk text not null check (risk in ('low', 'high')),
            flags jsonb not null default '[]',
            rule_code text,
            rule_version integer,
            approver text,
            proposal_id integer references proposal (id) on delete set null,
            reason text not null,
            status text not null default 'waiting' check (status in ({SHORT_CLOSE_STATUS})),
            asked_by text not null,
            asked_at timestamptz not null default now(),
            decided_by text,
            decided_at timestamptz,
            note text
        )
        """
    )
    op.execute("create unique index po_short_close_waiting on po_short_close (po_no) where status = 'waiting'")
    op.execute(
        """
        insert into po_short_close (po_no, lines, value, risk, flags, reason, status, asked_by, asked_at,
            decided_by, decided_at, note)
        select p.no,
            coalesce((select jsonb_agg(jsonb_build_object('line', l.line_no,
                'balance', l.short_closed_qty, 'value', round(l.short_closed_qty * l.unit_price, 2)))
                from po_line l where l.po_no = p.no and coalesce(l.short_closed_qty, 0) > 0), '[]'),
            coalesce((select sum(round(l.short_closed_qty * l.unit_price, 2)) from po_line l
                where l.po_no = p.no and coalesce(l.short_closed_qty, 0) > 0), 0),
            'low', '[]', coalesce(p.closed_reason, 'closed before short-close was recorded'), 'closed',
            'migration', coalesce(p.closed_at, now()), 'migration', coalesce(p.closed_at, now()),
            'closed before month-end close existed'
        from po p where p.status = 'closed' and p.closed_reason is not null
        """
    )

    op.execute(
        """
        create table item_standard_price (
            id bigserial primary key,
            item_code text not null references item (code) on delete cascade,
            price numeric(18,4) not null check (price >= 0),
            version integer not null,
            effective_from date not null,
            effective_to date,
            status text not null default 'active' check (status in ('proposed', 'active', 'rejected')),
            reason text,
            proposal_id integer references proposal (id) on delete set null,
            created_by text,
            created_at timestamptz not null default now(),
            approved_by text,
            approved_at timestamptz,
            unique (item_code, version)
        )
        """
    )
    op.execute(
        "insert into item_standard_price (item_code, price, version, effective_from, status, reason, created_by) "
        "select code, standard_price, 1, date '2000-01-01', 'active', 'from the item master at setup', 'system:setup' "
        "from item where standard_price is not null"
    )

    op.execute(
        f"""
        create table price_measure (
            id bigserial primary key,
            measure text not null check (measure in ({MEASURES})),
            part text,
            source_type text not null,
            source_ref text not null,
            source_line integer not null default 0,
            item_code text,
            supplier_code text,
            po_no text,
            po_line_no integer,
            on_date date not null,
            period text not null,
            qty numeric(18,4) not null default 0,
            unit_price numeric(18,4),
            reference_price numeric(18,4),
            reference text not null,
            amount numeric(18,2) not null default 0,
            pct numeric(9,4),
            basis jsonb not null default '{{}}',
            recorded_at timestamptz not null default now(),
            unique (measure, part, source_type, source_ref, source_line)
        )
        """
    )
    op.execute("create index price_measure_period on price_measure (period, measure)")

    op.execute(
        """
        create table gstr2b_import (
            id bigserial primary key,
            gstin text not null,
            period text not null,
            generated_on date,
            file_name text,
            file_hash text not null,
            tolerance_version integer,
            documents integer not null default 0,
            outcomes jsonb not null default '{}',
            current boolean not null default true,
            imported_by text not null,
            imported_at timestamptz not null default now(),
            unique (gstin, file_hash)
        )
        """
    )
    op.execute(
        f"""
        create table gstr2b_line (
            id bigserial primary key,
            import_id bigint not null references gstr2b_import (id) on delete cascade,
            supplier_gstin text,
            doc_type text not null,
            doc_no text,
            doc_date date,
            taxable numeric(18,2) not null default 0,
            igst numeric(18,2) not null default 0,
            cgst numeric(18,2) not null default 0,
            sgst numeric(18,2) not null default 0,
            cess numeric(18,2) not null default 0,
            reverse_charge boolean not null default false,
            itc_available boolean not null default true,
            itc_reason text,
            our_invoice_no text,
            outcome text not null check (outcome in ({OUTCOMES})),
            differences jsonb,
            reason text
        )
        """
    )
    op.execute("create index gstr2b_line_import on gstr2b_line (import_id, id)")

    op.execute(
        """
        create table period_close (
            period text primary key,
            closed_by text not null,
            closed_at timestamptz not null default now(),
            reason text not null,
            checklist jsonb not null default '{}',
            grni jsonb not null default '[]',
            grni_total numeric(18,2) not null default 0,
            export_id bigint
        )
        """
    )
    op.execute(
        """
        create table accounting_export (
            id bigserial primary key,
            period text not null unique,
            format text not null,
            vouchers integer not null default 0,
            debit numeric(18,2) not null default 0,
            credit numeric(18,2) not null default 0,
            content text not null,
            file_hash text not null,
            made_by text not null,
            made_at timestamptz not null default now()
        )
        """
    )
    op.execute(
        """
        create table accounting_export_doc (
            export_id bigint not null references accounting_export (id) on delete cascade,
            doc_type text not null,
            doc_no text not null,
            event_date date,
            primary key (doc_type, doc_no)
        )
        """
    )
    op.execute(
        """
        create table ledger_map (
            id bigserial primary key,
            purpose text not null,
            gl_code text references gl_account (code),
            ledger_name text not null,
            version integer not null,
            effective_from date not null,
            effective_to date,
            unique (purpose, version)
        )
        """
    )

    op.execute(
        "alter table bank_statement_line drop constraint bank_statement_line_status_check, "
        "add constraint bank_statement_line_status_check check (status in "
        "('matched', 'waiting', 'open', 'confirmed', 'not_ours'))"
    )
    op.execute(
        "alter table vendor_statement_line drop constraint vendor_statement_line_doc_type_check, "
        "add constraint vendor_statement_line_doc_type_check check (doc_type in "
        "('invoice', 'credit_note', 'debit_note', 'payment', 'opening', 'closing'))"
    )
    op.execute(
        "alter table vendor_statement_line drop constraint vendor_statement_line_outcome_check, "
        "add constraint vendor_statement_line_outcome_check check (outcome in "
        "('matched', 'amount_differs', 'only_theirs', 'only_ours', 'balance'))"
    )
    op.execute(
        "alter table vendor_statement_line add column flag text, add column note text, "
        "add column resolved_by text, add column resolved_at timestamptz"
    )
    op.execute(BUDGET_VIEW)

    op.execute(
        "insert into role_permission (role, action) values "
        "('Finance', 'price.standard'), ('Finance', 'accounts.export'), ('Finance', 'gst.reconcile'), "
        "('AP', 'gst.reconcile') on conflict do nothing"
    )
    op.execute(
        "insert into org_setting (key, value, description) values "
        "('ppv_recognition', 'invoice', 'When accounting PPV is recognised: invoice, receipt or both'), "
        "('accounts_export_format', 'generic_csv', 'The accounting export file format') on conflict do nothing"
    )


def downgrade() -> None:
    op.execute("delete from org_setting where key in ('ppv_recognition', 'accounts_export_format')")
    op.execute("delete from role_permission where action in ('price.standard', 'accounts.export', 'gst.reconcile')")
    op.execute("delete from vendor_statement_line where outcome = 'balance'")
    op.execute(
        "alter table vendor_statement_line drop constraint vendor_statement_line_doc_type_check, "
        "add constraint vendor_statement_line_doc_type_check check (doc_type in "
        "('invoice', 'credit_note', 'debit_note', 'payment'))"
    )
    op.execute(
        "alter table vendor_statement_line drop constraint vendor_statement_line_outcome_check, "
        "add constraint vendor_statement_line_outcome_check check (outcome in "
        "('matched', 'amount_differs', 'only_theirs', 'only_ours'))"
    )
    op.execute(
        "alter table vendor_statement_line drop column resolved_at, drop column resolved_by, "
        "drop column note, drop column flag"
    )
    op.execute("update bank_statement_line set status = 'open' where status = 'not_ours'")
    op.execute(
        "alter table bank_statement_line drop constraint bank_statement_line_status_check, "
        "add constraint bank_statement_line_status_check check (status in ('matched', 'waiting', 'open', 'confirmed'))"
    )
    for table in (
        "ledger_map",
        "accounting_export_doc",
        "accounting_export",
        "period_close",
        "gstr2b_line",
        "gstr2b_import",
        "price_measure",
        "item_standard_price",
    ):
        op.execute(f"drop table {table}")  # noqa: S608 - fixed names
    op.execute("drop table po_short_close")
    op.execute("update po_line set status = 'closed' where status = 'short_closed'")
    op.execute(
        "alter table po_line drop constraint po_line_status_check, add constraint po_line_status_check "
        "check (status in ('open', 'part_received', 'received', 'closed', 'cancelled'))"
    )
