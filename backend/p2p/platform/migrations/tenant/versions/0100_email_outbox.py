"""The email outbox (task 11a.1, DF-23): a mail is recorded in the sender's transaction before it is sent

An outgoing `email_message` row is written with status `queued` together with the work it is about; the job `email`
(keyed by the Message-ID) and the request itself send it after the commit, and only a row still `queued` is sent, under
a row lock, so a mail is never lost with a rollback nor sent twice. The full body (with any signed link) is kept
encrypted in `sealed` only until it is sent; `body_text` never holds the secret part. Incoming rows keep status null.

Revision ID: 0100
Revises: 0091
"""

from alembic import op

revision = "0100"
down_revision = "0091"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        alter table email_message
            add column status text check (status in ('queued', 'sent', 'failed', 'cancelled')),
            add column queued_at timestamptz,
            add column attempts int not null default 0,
            add column last_error text,
            add column sealed text,
            add column reply_to text,
            add column sender text,
            add column job_kind text
        """
    )
    op.execute(
        "update email_message set status = 'sent', queued_at = coalesce(sent_at, created_at) where direction = 'out'"
    )
    op.execute("create index ix_email_outbox on email_message (status, queued_at) where status = 'queued'")
    op.execute("alter table rfq_supplier add column message_id text")


def downgrade() -> None:
    op.execute("alter table rfq_supplier drop column message_id")
    op.execute("drop index ix_email_outbox")
    op.execute(
        "alter table email_message drop column status, drop column queued_at, drop column attempts, "
        "drop column last_error, drop column sealed, drop column reply_to, drop column sender, drop column job_kind"
    )
