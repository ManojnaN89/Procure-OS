"""Supplier confirmation (block 8: task 1.3 acknowledgement code, 11b.3, D53)

- `po_version.ack_*`: how a version was acknowledged: the contact's address, the link and one-time code used, the email
  and its sender authentication, the supplier's changes an accepted answer was made of, the note of how the supplier
  confirmed. Written only by `orders/dispatch.acknowledge`; the row stays the acknowledgement of its version (block 7).
- `supplier_link.sent_to`: the address a respond link was mailed to, the only address its code goes to (OP-1).
- `po_change.po_version`: the version the supplier asked on; a new status `stale` for a change asked on terms that no
  longer stand (OP-11).
- `po_commitment.po_version`, `source`, `acknowledgement_id`: one row per line and date the supplier committed to.
- `supplier_chase.po_version`: chasers are counted per version.
- `email_message.po_version`: the version an outbound order mail carried, so a reply answers that version (OP-12).
- settings `supplier_chase_days` (now working days) and `supplier_chase_max` (per version).

Revision ID: 0150
Revises: 0140
"""

from alembic import op

revision = "0150"
down_revision = "0140"
branch_labels = None
depends_on = None

_STATUSES = "'proposed', 'waiting_approval', 'accepted', 'rejected', 'withdrawn'"
# rows made before: a link's address is its version's mailing, else the order's first; a change was asked on the version
# before the one it made, else the version the order is at; a chaser was for the version the order is at
BACKFILL = [
    "update supplier_link l set sent_to = v.sent_to from po_version v "
    "where v.po_no = l.po_no and v.version = l.po_version and l.sent_to is null",
    "update supplier_link l set sent_to = p.sent_to from po p where p.no = l.po_no and l.sent_to is null",
    "update po_change c set po_version = coalesce(c.version_made - 1, p.version) from po p "
    "where p.no = c.po_no and c.po_version is null",
    "update supplier_chase s set po_version = p.version from po p where p.no = s.po_no and s.po_version is null",
]


def upgrade() -> None:
    op.execute(
        "alter table po_version add column ack_address text, "
        "add column ack_link_id bigint references supplier_link (id), add column ack_code_id bigint, "
        "add column ack_email_id integer references email_message (id), add column ack_sender_auth jsonb, "
        "add column ack_change_ids bigint[], add column ack_note text"
    )
    op.execute("alter table supplier_link add column sent_to text")
    op.execute("alter table po_change add column po_version integer")
    op.execute(
        "alter table po_change drop constraint po_change_status_check, add constraint po_change_status_check "
        f"check (status in ({_STATUSES}, 'stale'))"
    )
    op.execute(
        "alter table po_commitment add column po_version integer, add column source text "
        "check (source in ('ack_link', 'ack_email', 'ack_app', 'agent_date', 'agent_qty')), "
        "add column acknowledgement_id bigint references po_version (id) on delete set null"
    )
    op.execute("alter table supplier_chase add column po_version integer")
    op.execute("alter table email_message add column po_version integer")
    for statement in BACKFILL:
        op.execute(statement)
    op.execute(
        "insert into org_setting (key, value, description) values "
        "('supplier_chase_days', '3', 'Working days between reminders to acknowledge an order'), "
        "('supplier_chase_max', '3', 'Reminders to acknowledge one version of an order at most') "
        "on conflict do nothing"
    )


def downgrade() -> None:
    op.execute("alter table email_message drop column po_version")
    op.execute("alter table supplier_chase drop column po_version")
    op.execute("alter table po_commitment drop column acknowledgement_id, drop column source, drop column po_version")
    op.execute("update po_change set status = 'withdrawn' where status = 'stale'")
    op.execute(
        f"alter table po_change drop constraint po_change_status_check, add constraint po_change_status_check "
        f"check (status in ({_STATUSES}))"
    )
    op.execute("alter table po_change drop column po_version")
    op.execute("alter table supplier_link drop column sent_to")
    op.execute(
        "alter table po_version drop column ack_note, drop column ack_change_ids, drop column ack_sender_auth, "
        "drop column ack_email_id, drop column ack_link_id, drop column ack_code_id, drop column ack_address"
    )
