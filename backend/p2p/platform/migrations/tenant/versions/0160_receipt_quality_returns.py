"""Goods receipt and services (block 9: tasks 3.6, 3.8, 4.2, 4.3, 4.5)

- A receipt can be reversed (status `posted`/`reversed`) and says where it came from (`platform`, `erp` or `cutover`),
  with the ERP's own key (`external_ref`, one per order) and who inspected it.
- `gr_line.qty_excess`: the accepted units above the order held in quarantine for the buyer (C3, 4.2).
- Service entries can be reversed the same way.
- Serial numbers get an id and a status (DF-31): only a `received` serial is unique per item, so a rejected unit's
  number no longer blocks its replacement.
- Holds gain `source = 'excess'` and `closed_by_doc`; stock movements gain `receipt_reversal` and `scrap`; budget
  actuals gain `receipt_reversal` and `return`.
- New: `receipt_excess` (goods above the order, waiting for the buyer), `supplier_return` and its lines (RTN),
  `recovery_claim` (RCL, open; block 12 adds the debit note and settlement), and the two number series.

Revision ID: 0160
Revises: 0150
"""

import sqlalchemy as sa
from alembic import op

revision = "0160"
down_revision = "0150"
branch_labels = None
depends_on = None

SERIES = "{prefix}-{loc}-{fy4}-{seq:05d}"


def upgrade() -> None:
    op.execute(
        "insert into role_permission (role, action) values "
        "('Quality', 'grn.inspect'), ('Quality', 'stock.view'), ('Quality', 'stock.quality'), "
        "('Quality', 'request.view'), ('Store', 'grn.inspect') on conflict do nothing"
    )
    op.execute(
        "alter table goods_receipt "
        "add column status text not null default 'posted' check (status in ('posted', 'reversed')), "
        "add column source text not null default 'platform' check (source in ('platform', 'erp', 'cutover')), "
        "add column external_ref text, add column inspected_by text, add column inspected_at timestamptz, "
        "add column reversed_on date, add column reversed_by text, add column reversal_reason text, "
        "add column reversal_proposal_id bigint references proposal (id)"
    )
    op.execute("update goods_receipt set source = 'cutover' where posted_by = 'cutover'")
    op.execute(
        "create unique index goods_receipt_external on goods_receipt (external_ref, po_no) "
        "where external_ref is not null"
    )
    op.execute(
        "alter table gr_line add column qty_excess numeric(18,4) not null default 0 check (qty_excess >= 0), "
        "add column external_line text"
    )
    op.execute(
        "alter table service_entry "
        "add column status text not null default 'posted' check (status in ('posted', 'reversed')), "
        "add column reversed_on date, add column reversed_by text, add column reversal_reason text, "
        "add column reversal_proposal_id bigint references proposal (id)"
    )
    op.execute(
        "alter table serial_number drop constraint serial_number_pkey, add column id bigserial primary key, "
        "add column status text not null default 'received' "
        "check (status in ('received', 'rejected', 'returned', 'reversed', 'scrapped')), "
        "add column return_no text"
    )
    op.execute(
        "create unique index serial_number_received on serial_number (item_code, serial) where status = 'received'"
    )
    op.execute(
        "alter table stock_hold drop constraint stock_hold_source_check, add constraint stock_hold_source_check "
        "check (source in ('receipt', 'manual', 'import', 'decision', 'excess')), add column closed_by_doc text"
    )
    op.execute(
        "alter table stock_movement drop constraint stock_movement_type_check, add constraint stock_movement_type_check "
        "check (type in ('receipt', 'issue', 'return', 'adjust', 'transfer_in', 'transfer_out', 'requalify', "
        "'receipt_reversal', 'scrap'))"
    )
    op.execute(
        "alter table budget_actual drop constraint budget_actual_source_check, "
        "add constraint budget_actual_source_check "
        "check (source in ('payment', 'receipt', 'receipt_reversal', 'return'))"
    )
    op.execute(
        """
        create table receipt_excess (
            id bigserial primary key,
            gr_no text not null references goods_receipt (no),
            po_no text not null,
            po_line_no integer not null,
            qty numeric(18,4) not null check (qty > 0),
            uom text,
            hold_id bigint references stock_hold (id),
            status text not null default 'waiting'
                check (status in ('waiting', 'proposed', 'accepted', 'returning', 'returned', 'reversed')),
            task_id integer references task (id),
            proposal_id bigint references proposal (id),
            version_made integer,
            return_no text,
            decided_by text,
            decided_at timestamptz,
            reason text,
            created_at timestamptz not null default now()
        )
        """
    )
    op.execute("create index receipt_excess_open on receipt_excess (status, created_at) where status = 'waiting'")
    op.execute(
        """
        create table supplier_return (
            no text primary key,
            supplier_code text not null references supplier (code),
            location_code text not null references location (code),
            status text not null default 'draft' check (status in ('draft', 'dispatched', 'cancelled')),
            source text not null check (source in ('store', 'gate', 'excess', 'unordered')),
            reason text not null,
            challan text,
            vehicle text,
            dispatched_on date,
            created_by text not null,
            dispatched_by text,
            task_id integer references task (id),
            created_at timestamptz not null default now()
        )
        """
    )
    op.execute(
        """
        create table supplier_return_line (
            return_no text not null references supplier_return (no),
            line_no integer not null,
            item_code text references item (code),
            description text,
            qty numeric(18,4) not null check (qty > 0),
            uom text,
            quality text not null check (quality in ('blocked', 'quarantine', 'none')),
            hold_id bigint references stock_hold (id),
            gr_no text references goods_receipt (no),
            po_no text,
            po_line_no integer,
            unordered_no text,
            counted boolean not null default false,
            serials jsonb not null default '[]',
            unit_cost numeric(18,4),
            outcome text check (outcome in ('replace', 'no_replacement', 'none')),
            outcome_by text,
            outcome_at timestamptz,
            claim_no text,
            primary key (return_no, line_no)
        )
        """
    )
    op.execute(
        """
        create table recovery_claim (
            no text primary key,
            supplier_code text not null references supplier (code),
            po_no text not null,
            po_line_no integer not null,
            return_no text not null,
            return_line_no integer not null,
            qty numeric(18,4) not null check (qty > 0),
            unit_price numeric(18,4) not null,
            taxable numeric(18,2) not null,
            cgst numeric(18,2) not null,
            sgst numeric(18,2) not null,
            igst numeric(18,2) not null,
            total numeric(18,2) not null,
            invoices text[] not null,
            reason text not null,
            status text not null default 'open' check (status in ('open', 'settled', 'cancelled')),
            debit_note_no text,
            raised_by text not null,
            raised_at timestamptz not null default now()
        )
        """
    )
    op.execute("alter table unordered_receipt add column return_no text references supplier_return (no)")
    for doc, prefix in (("supplier_return", "RTN"), ("recovery_claim", "RCL")):
        op.get_bind().execute(
            sa.text(
                "insert into number_series (document_type, prefix, scope, format, reset) "
                "select :d, :p, 'location', :f, 'fy' where exists (select 1 from number_series) "
                "and not exists (select 1 from number_series where document_type = :d)"
            ),
            {"d": doc, "p": prefix, "f": SERIES},
        )


def downgrade() -> None:
    op.execute("alter table unordered_receipt drop column return_no")
    for table in ("recovery_claim", "supplier_return_line", "supplier_return", "receipt_excess"):
        op.execute(f"drop table {table}")
    op.execute("delete from number_series where document_type in ('supplier_return', 'recovery_claim')")
    op.execute(
        "alter table budget_actual drop constraint budget_actual_source_check, "
        "add constraint budget_actual_source_check check (source in ('payment', 'receipt'))"
    )
    op.execute(
        "alter table stock_movement drop constraint stock_movement_type_check, "
        "add constraint stock_movement_type_check check (type in ('receipt', 'issue', 'return', 'adjust', "
        "'transfer_in', 'transfer_out', 'requalify'))"
    )
    op.execute("alter table stock_hold drop column closed_by_doc")
    op.execute(
        "alter table stock_hold drop constraint stock_hold_source_check, add constraint stock_hold_source_check "
        "check (source in ('receipt', 'manual', 'import', 'decision'))"
    )
    op.execute("drop index serial_number_received")
    # the old key allowed one row per serial only: a serial received again after a rejection has two
    op.execute(
        "delete from serial_number s where s.status <> 'received' "
        "and exists (select 1 from serial_number r where r.item_code = s.item_code and r.serial = s.serial "
        "and r.status = 'received')"
    )
    op.execute("alter table serial_number drop column return_no, drop column status, drop column id")
    op.execute("alter table serial_number add constraint serial_number_pkey primary key (item_code, serial)")
    op.execute(
        "alter table service_entry drop column reversal_proposal_id, drop column reversal_reason, "
        "drop column reversed_by, drop column reversed_on, drop column status"
    )
    op.execute("alter table gr_line drop column external_line, drop column qty_excess")
    op.execute("drop index goods_receipt_external")
    op.execute(
        "alter table goods_receipt drop column reversal_proposal_id, drop column reversal_reason, "
        "drop column reversed_by, drop column reversed_on, drop column inspected_at, drop column inspected_by, "
        "drop column external_ref, drop column source, drop column status"
    )
    op.execute("delete from role_permission where action = 'grn.inspect'")
