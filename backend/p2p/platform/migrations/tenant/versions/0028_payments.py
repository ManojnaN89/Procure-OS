"""Payment: tax held back at booking, payment runs, payments and what they settled, and the actuals they leave in the budget

Revision ID: 0028
Revises: 0027
"""

from alembic import op

revision = "0028"
down_revision = "0027"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "alter table invoice add column tds_amount numeric(18,2) not null default 0, add column paid_on date, "
        "add column booked_at timestamptz"
    )
    op.execute(
        """
        create table tds_entry (
            id bigserial primary key,
            invoice_no text references invoice (no),
            payment_no text,
            section text not null,
            base numeric(18,2) not null,
            rate numeric(7,3) not null,
            amount numeric(18,2) not null,
            booked_on date not null,
            quarter text not null
        )
        """
    )
    op.execute("create unique index tds_entry_invoice on tds_entry (invoice_no) where invoice_no is not null")
    op.execute(
        """
        create table payment_run (
            no text primary key,
            run_date date not null,
            company_bank_account text,
            proposed_by text not null,
            approved_by text,
            approved_at timestamptz,
            status text not null default 'proposed'
                check (status in ('proposed', 'approved', 'file_generated', 'paid', 'cancelled')),
            total numeric(18,2) not null default 0,
            proposal_id bigint,
            file_text text,
            created_at timestamptz not null default now()
        )
        """
    )
    op.execute(
        """
        create table payment (
            no text primary key,
            run_no text not null references payment_run (no),
            type text not null default 'invoice' check (type in ('invoice', 'advance')),
            supplier_code text not null references supplier (code) deferrable initially deferred,
            po_no text,
            amount numeric(18,2) not null,
            tds_amount numeric(18,2) not null default 0,
            msme_interest numeric(18,2) not null default 0,
            mode text not null default 'neft' check (mode in ('neft', 'rtgs', 'imps')),
            reference text,
            utr text,
            paid_on date,
            remittance_sent_at timestamptz,
            created_at timestamptz not null default now()
        )
        """
    )
    op.execute("create index payment_run_idx on payment (run_no)")
    op.execute(
        """
        create table payment_allocation (
            payment_no text not null references payment (no) on delete cascade,
            invoice_no text not null references invoice (no),
            amount numeric(18,2) not null,
            primary key (payment_no, invoice_no)
        )
        """
    )
    op.execute("create index payment_allocation_invoice on payment_allocation (invoice_no)")
    # what was actually spent, by cost centre and GL account, once the invoice was paid
    op.execute(
        """
        create table budget_actual (
            id bigserial primary key,
            invoice_no text not null references invoice (no),
            cost_centre_code text,
            gl_code text,
            period text not null,
            amount numeric(18,2) not null
        )
        """
    )
    op.execute("create table ref_rbi_bank_rate (effective_from date primary key, rate_pct numeric(6,3) not null)")
    op.execute(
        "insert into role_permission (role, action) values ('Approver', 'payment.approve'), ('Finance', 'payment.view') "
        "on conflict do nothing"
    )


def downgrade() -> None:
    for table in ("ref_rbi_bank_rate", "budget_actual", "payment_allocation", "payment", "payment_run", "tds_entry"):
        op.execute(f"drop table {table}")
    op.execute("alter table invoice drop column tds_amount, drop column paid_on, drop column booked_at")
