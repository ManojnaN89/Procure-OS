"""Quotes and supplier selection (block 6: tasks 1.3 quotes half, 12.6, 11b.2; D53, D40, D43, DF-33)

- An incoming mail keeps the business date it was stored (`received_on`, when a quote is late or not) and the result of
  our own mail server's sender authentication (`auth`).
- Each quote keeps how it came (`channel`: typed by a buyer or read from an email), the mail, the sender's address and
  registered contact, the sender authentication and the business date it was received (`received_on`).
- The old system's orders keep their unit price, the "last price paid" of a negotiation brief.
- A contract line whose price is above the market waits for the buyer's choice of path A or B (`path_case`).
- A recommendation may carry the model's rewording of its fixed words (`ai_note`), outside the frozen columns.
- Setting `mail_authserv_id`: whose Authentication-Results header is trusted.

Revision ID: 0130
Revises: 0120
"""

from alembic import op

revision = "0130"
down_revision = "0120"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("alter table email_message add column received_on date, add column auth jsonb")
    op.execute(
        "update email_message set received_on = (created_at at time zone 'Asia/Kolkata')::date where direction = 'in'"
    )
    op.execute(
        "alter table quote add column channel text not null default 'entered' check (channel in ('entered', 'email')), "
        "add column email_id integer references email_message (id), add column sender_address text, "
        "add column contact_id bigint, add column sender_auth jsonb, add column received_on date"
    )
    op.execute("update quote set received_on = (received_at at time zone 'Asia/Kolkata')::date")
    op.execute(
        "update quote set channel = 'email', email_id = cast(substr(document_ref, 7) as integer) "
        "where document_ref ~ '^email:[0-9]+$' and exists (select 1 from email_message m "
        "where m.id = cast(substr(document_ref, 7) as integer))"
    )
    op.execute("alter table quote alter column received_on set not null")
    op.execute("alter table order_history add column unit_price numeric(18,4)")
    op.execute(
        "alter table sourcing_decision add column path_case text "
        "check (path_case in ('waiting', 'chosen_a', 'chosen_b')), add column path_confirmed_by integer, "
        "add column path_recommendation_id integer references recommendation (id)"
    )
    op.execute(
        "alter table recommendation add column ai_note text, add column ai_note_model text, "
        "add column ai_note_at timestamptz"
    )
    op.execute(
        "insert into org_setting (key, value, description) values ('mail_authserv_id', '', "
        "'The authserv-id of our own receiving mail server; only its Authentication-Results header is trusted for "
        "supplier quotes (D53)') on conflict do nothing"
    )


def downgrade() -> None:
    op.execute("delete from org_setting where key = 'mail_authserv_id'")
    op.execute("alter table recommendation drop column ai_note, drop column ai_note_model, drop column ai_note_at")
    op.execute(
        "alter table sourcing_decision drop column path_case, drop column path_confirmed_by, "
        "drop column path_recommendation_id"
    )
    op.execute("alter table order_history drop column unit_price")
    op.execute(
        "alter table quote drop column channel, drop column email_id, drop column sender_address, "
        "drop column contact_id, drop column sender_auth, drop column received_on"
    )
    op.execute("alter table email_message drop column received_on, drop column auth")
