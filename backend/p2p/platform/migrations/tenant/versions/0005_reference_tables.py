"""Reference data: states, units, HSN/SAC codes, GST rates, holidays, TDS sections

Revision ID: 0005
Revises: 0004
"""

from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "create table ref_load (id serial primary key, loaded_at timestamptz not null default now(), "
        "files jsonb not null)"
    )
    op.execute("create table ref_gst_state (code text primary key, name text not null)")
    op.execute("create table ref_uom (code text primary key, name text not null)")
    op.execute(
        "create table ref_hsn_sac (code text not null, kind text not null, level text, description text, "
        "primary key (code, kind))"
    )
    op.execute(
        """
        create table ref_gst_rate (
            id serial primary key,
            kind text not null check (kind in ('goods', 'services')),
            code_prefix text not null,
            description text,
            cgst numeric(6, 3) not null,
            sgst numeric(6, 3) not null,
            igst numeric(6, 3) not null,
            effective_from date not null,
            effective_to date,
            reverse_charge boolean not null default false,
            needs_review boolean not null default false,
            source text
        )
        """
    )
    op.execute("create index ix_ref_gst_rate_prefix on ref_gst_rate (code_prefix text_pattern_ops)")
    op.execute(
        """
        create table ref_holiday (
            id serial primary key,
            holiday_date date not null,
            name text not null,
            kind text not null,
            scope text not null,
            unique (holiday_date, scope, name)
        )
        """
    )
    op.execute(
        """
        create table ref_tds_section (
            id serial primary key,
            act text not null,
            section text not null,
            nature text,
            payee_type text,
            rate_pct numeric(6, 3),
            rate_no_pan_pct numeric(6, 3),
            threshold_single numeric(16, 2),
            threshold_annual numeric(16, 2),
            base_rule text,
            effective_from date not null,
            effective_to date,
            status text
        )
        """
    )


def downgrade() -> None:
    for t in ("ref_tds_section", "ref_holiday", "ref_gst_rate", "ref_hsn_sac", "ref_uom", "ref_gst_state", "ref_load"):
        op.execute(f"drop table {t}")
