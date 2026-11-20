"""Prospective suppliers (task 4.4, D-03)

A prospect is a supplier registered by a buyer to quote before it is fully onboarded. It may receive an RFQ and quote;
its quote counts toward the minimum only after a person other than the buyer who invited or registered it checks its
identity, and while no related-party flag on it is open. Selecting it is a conditional award: no order is made until
it is activated. Possible conflicts (shared contacts, address, mail domain, a match with a blocked supplier) are flags
for independent review. An RFQ keeps the quotation-count rule it was made under.

Revision ID: 0061
Revises: 0060
"""

from alembic import op

revision = "0061"
down_revision = "0060"
branch_labels = None
depends_on = None

SETTINGS = (
    ("prospect_qualification_days", "10", "Working days a conditional award waits for the prospect to be activated"),
    (
        "identity_check_methods",
        "pan_and_gstin_online,gst_certificate_seen,call_back_registered_number",
        "How the identity of a prospect may be checked",
    ),
    (
        "public_mail_domains",
        "gmail.com,yahoo.com,yahoo.co.in,outlook.com,hotmail.com,rediffmail.com,icloud.com",
        "Mail domains anyone can use: sharing one is not a related-party flag",
    ),
)


def upgrade() -> None:
    op.execute(
        "alter table supplier add column registered_by text, add column invited_by text, "
        "add column identity_checked_by text, add column identity_checked_at timestamptz, "
        "add column identity_method text, add column identity_document_ids bigint[], add column identity_note text"
    )
    op.execute(
        """
        create table supplier_flag (
            id bigserial primary key,
            supplier_code text not null references supplier (code) deferrable initially deferred,
            kind text not null check (kind in ('same_contact', 'same_address', 'same_domain', 'staff_contact',
                'blocked_match', 'same_quote')),
            other_ref text,
            detail text,
            raised_at timestamptz not null default now(),
            cleared_by text,
            cleared_at timestamptz,
            clear_reason text
        )
        """
    )
    op.execute("create index supplier_flag_open on supplier_flag (supplier_code) where cleared_at is null")
    op.execute(
        "alter table award add column conditional boolean not null default false, "
        "add column lapsed_at timestamptz, add column lapse_reason text"
    )
    op.execute("alter table rfq add column min_quotes_rule text")
    op.execute("alter table rfq_supplier add column invited_by text")
    # open RFQs keep the count they were made under from now on
    op.execute(
        "update rfq r set min_quotes = coalesce(r.min_quotes, (select c.min_quotes from item_category c "
        "where c.code = r.category_code), (select cast(value as integer) from org_setting "
        "where key = 'min_quotes_default'), 2), min_quotes_rule = 'backfilled 0061' where r.status in ('draft', 'sent')"
    )
    for key, value, about in SETTINGS:
        op.execute(
            f"insert into org_setting (key, value, description) values ('{key}', '{value}', '{about}') "  # noqa: S608
            "on conflict (key) do nothing"
        )


def downgrade() -> None:
    op.execute(f"delete from org_setting where key in ({', '.join(repr(k) for k, _, _ in SETTINGS)})")  # noqa: S608
    op.execute("alter table rfq_supplier drop column invited_by")
    op.execute("alter table rfq drop column min_quotes_rule")
    op.execute("alter table award drop column lapse_reason, drop column lapsed_at, drop column conditional")
    op.execute("drop table supplier_flag")
    op.execute(
        "alter table supplier drop column identity_note, drop column identity_document_ids, "
        "drop column identity_method, drop column identity_checked_at, drop column identity_checked_by, "
        "drop column invited_by, drop column registered_by"
    )
