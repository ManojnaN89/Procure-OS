"""Payment evidence (block 12: tasks 2.1, 2.2, 2.3, 2.4, 2.5 and 2.7)

- `payment` gets its states (planned, approved, in_file, recorded, settled, failed, reversed, cancelled), the value date,
  the account it went to, who recorded it and the proof, the bank line that settled or failed it, and its reason.
- `payment_run` keeps the approval rule it was decided under, the file hash and how it was cancelled.
- `payment_run_approval`: one row per step of the run's approval chain, with the person and the code that let them.
- `invoice.in_run`, `pay_by`, `offset_used`, `reason`, `source_ref`.
- `tds_entry` per section with its reference row and its state (proposed, confirmed, cancelled).
- `advance_request` (request, own approval, payment, recovery), and `advance.request_id` and `settled_on`.
- `bank_statement_line.direction` (a credit line may be a returned payment).
- settings `payment_run_otp_above`, `tds_auto_confirm_zero`, `rtgs_from`; `manual_mark_paid` is removed.
- permissions `payment.record`, `tds.confirm`, `advance.request`, `advance.approve`.

Revision ID: 0190
Revises: 0150
"""

from alembic import op

revision = "0190"
down_revision = "0150"
branch_labels = None
depends_on = None

PAYMENT_STATUSES = "'planned', 'approved', 'in_file', 'recorded', 'settled', 'failed', 'reversed', 'cancelled'"
TDS_STATUSES = "'proposed', 'confirmed', 'cancelled'"
ADVANCE_STATUSES = (
    "'requested', 'approved', 'rejected', 'in_run', 'paid', 'recovering', 'recovered', 'used', 'cancelled'"
)

# what a payment was: a run that waited, was approved, was filed; and whether the bank's own line settles it
BACKFILL_STATUS = [
    "update payment p set status = case r.status when 'proposed' then 'planned' when 'approved' then 'approved' "
    "when 'file_generated' then 'in_file' when 'cancelled' then 'cancelled' when 'paid' then 'recorded' "
    "else 'planned' end from payment_run r where r.no = p.run_no",
    "update payment p set status = 'settled', settled_line_id = b.id, settled_at = b.confirmed_at "
    "from bank_statement_line b where b.payment_no = p.no and b.status in ('matched', 'confirmed')",
    "update payment set status = 'settled' where run_no like 'CUT-%'",
    "update payment set value_date = paid_on",
]
# the run an invoice is already waiting in; pay_by is its due date moved to the last working day on or before it
BACKFILL_INVOICE = [
    "update invoice i set in_run = p.run_no from payment_allocation a join payment p on p.no = a.payment_no "
    "join payment_run r on r.no = p.run_no where a.invoice_no = i.no "
    "and r.status in ('proposed', 'approved', 'file_generated')",
    "update invoice i set pay_by = coalesce((select max(d)::date from generate_series(i.due_date - 14, i.due_date, "
    "interval '1 day') d where extract(isodow from d) between 1 and 5 and not exists "
    "(select 1 from ref_holiday h where h.holiday_date = d::date and h.kind = 'closed')), i.due_date) "
    "where i.due_date is not null",
]


def upgrade() -> None:
    op.execute(
        "alter table payment add column status text not null default 'planned' "
        f"check (status in ({PAYMENT_STATUSES})), "
        "add column value_date date, add column bank_account_id bigint references supplier_bank_account (id), "
        "add column recorded_by text, add column recorded_at timestamptz, "
        "add column evidence_document_id bigint references document (id), "
        "add column settled_at timestamptz, add column settled_line_id bigint references bank_statement_line (id), "
        "add column failed_by text, add column failed_at timestamptz, add column fail_reason text, "
        "add column fail_line_id bigint references bank_statement_line (id), add column cancel_reason text"
    )
    op.execute("create unique index payment_utr on payment (upper(utr)) where utr is not null")
    for statement in BACKFILL_STATUS:
        op.execute(statement)

    op.execute(
        "alter table payment_run add column rule_code text, add column rule_version integer, "
        "add column file_hash text, add column cancelled_at timestamptz, add column cancel_reason text"
    )
    op.execute(
        """
        create table payment_run_approval (
            run_no text not null references payment_run (no),
            step integer not null,
            role text not null,
            approver text not null,
            delegate text,
            approved_by text,
            approved_at timestamptz,
            code_id bigint references one_time_code (id),
            primary key (run_no, step)
        )
        """
    )

    op.execute(
        "alter table invoice add column in_run text references payment_run (no), add column pay_by date, "
        "add column offset_used numeric(18,2) not null default 0, add column reason text, add column source_ref text"
    )
    for statement in BACKFILL_INVOICE:
        op.execute(statement)

    op.execute(
        "alter table tds_entry add column section_code text, add column ref_tds_section_id integer, "
        "add column status text not null default 'confirmed' "
        f"check (status in ({TDS_STATUSES})), "
        "add column proposed jsonb, add column confirmed_by text, add column confirmed_at timestamptz, "
        "add column change_reason text"
    )
    op.execute("update tds_entry set section_code = '194Q', confirmed_by = 'rule'")
    op.execute("drop index tds_entry_invoice")
    op.execute(
        "create unique index tds_entry_invoice_section on tds_entry (invoice_no, section_code) "
        "where invoice_no is not null and status <> 'cancelled'"
    )
    op.execute("alter table cutover_supplier_ytd add column section text not null default '194Q'")
    op.execute(
        "alter table cutover_supplier_ytd drop constraint cutover_supplier_ytd_pkey, "
        "add primary key (supplier_code, fy, section)"
    )

    op.execute(
        f"""
        create table advance_request (
            id bigserial primary key,
            po_no text not null references po (no),
            po_version integer not null,
            pct numeric(7,3) not null,
            gross numeric(18,2) not null,
            taxable numeric(18,2) not null,
            status text not null check (status in ({ADVANCE_STATUSES})),
            reason text,
            requested_by text not null,
            requested_at timestamptz not null default now(),
            proposal_id bigint,
            approver text,
            approved_by text,
            approved_at timestamptz,
            payment_no text references payment (no),
            recovery_note text references invoice (no),
            cancel_reason text
        )
        """
    )
    op.execute(
        "create unique index advance_request_live on advance_request (po_no) "
        "where status not in ('rejected', 'cancelled', 'recovered', 'used')"
    )
    op.execute(
        "alter table advance add column request_id bigint references advance_request (id), add column settled_on date"
    )

    op.execute(
        "alter table bank_statement_line add column direction text not null default 'debit' "
        "check (direction in ('debit', 'credit'))"
    )

    op.execute(
        "insert into org_setting (key, value, description) values "
        "('payment_run_otp_above', '0.00', "
        "'Approving a payment run above this total needs a one-time code (0: every run that pays money)'), "
        "('tds_auto_confirm_zero', 'true', "
        "'A zero TDS under the item category''s own section needs no confirmation'), "
        "('rtgs_from', '200000.00', 'A payment above this goes by RTGS, else NEFT') "
        "on conflict do nothing"
    )
    op.execute("delete from org_setting where key = 'manual_mark_paid'")
    op.execute(
        "insert into role_permission (role, action) values ('Finance', 'payment.record'), "
        "('Finance', 'tds.confirm'), ('Finance', 'advance.request'), ('Buyer', 'advance.request'), "
        "('Approver', 'advance.approve') on conflict do nothing"
    )


def downgrade() -> None:
    op.execute(
        "delete from role_permission where (role, action) in (('Finance', 'payment.record'), "
        "('Finance', 'tds.confirm'), ('Finance', 'advance.request'), ('Buyer', 'advance.request'), "
        "('Approver', 'advance.approve'))"
    )
    op.execute("delete from org_setting where key in ('payment_run_otp_above', 'tds_auto_confirm_zero', 'rtgs_from')")
    op.execute("alter table bank_statement_line drop column direction")
    op.execute("alter table advance drop column settled_on, drop column request_id")
    op.execute("drop table advance_request")
    op.execute("alter table cutover_supplier_ytd drop constraint cutover_supplier_ytd_pkey")
    op.execute("alter table cutover_supplier_ytd drop column section")
    op.execute("alter table cutover_supplier_ytd add primary key (supplier_code, fy)")
    op.execute("drop index tds_entry_invoice_section")
    op.execute("create unique index tds_entry_invoice on tds_entry (invoice_no) where invoice_no is not null")
    op.execute(
        "alter table tds_entry drop column change_reason, drop column confirmed_at, drop column confirmed_by, "
        "drop column proposed, drop column status, drop column ref_tds_section_id, drop column section_code"
    )
    op.execute(
        "alter table invoice drop column source_ref, drop column reason, drop column offset_used, "
        "drop column pay_by, drop column in_run"
    )
    op.execute("drop table payment_run_approval")
    op.execute(
        "alter table payment_run drop column cancel_reason, drop column cancelled_at, drop column file_hash, "
        "drop column rule_version, drop column rule_code"
    )
    op.execute("drop index payment_utr")
    op.execute(
        "alter table payment drop column cancel_reason, drop column fail_line_id, drop column fail_reason, "
        "drop column failed_at, drop column failed_by, drop column settled_line_id, drop column settled_at, "
        "drop column evidence_document_id, drop column recorded_at, drop column recorded_by, "
        "drop column bank_account_id, drop column value_date, drop column status"
    )
