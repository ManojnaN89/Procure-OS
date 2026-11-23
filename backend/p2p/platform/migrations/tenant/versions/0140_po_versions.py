"""Purchase order versions (block 7: task 0.2 versions, D48)

- `po_version`: one row per order and version, written when the version is made, with what it said (`snapshot`), the
  document mailed for it (`document_id`, `sent_at`, `sent_to`) and the supplier's acknowledgement of exactly that version
  (`acknowledged_at`, `acknowledged_by`, `ack_via`).
- `po.acknowledged_version`: the version the supplier acknowledged; a revision clears it.

Backfill (OP-13): a row for the current version of every existing order (`made_by` 'backfill'); earlier versions have
no row, since their header and tax are not fully known.

Revision ID: 0140
Revises: 0130
"""

from alembic import op

revision = "0140"
down_revision = "0130"
branch_labels = None
depends_on = None

# the same shape as formulas/po_versions.Snapshot.as_json
_LINES = """coalesce((select jsonb_agg(jsonb_build_object(
    'line_no', l.line_no, 'item_code', l.item_code, 'description', l.description, 'qty', l.qty::text, 'uom', l.uom,
    'unit_price', l.unit_price::text, 'gst_rate', l.gst_rate::text, 'taxable', l.taxable::text, 'cgst', l.cgst::text,
    'sgst', l.sgst::text, 'igst', l.igst::text, 'need_by', l.need_by::text, 'price_record_id', l.price_record_id,
    'contract_version', pr.version, 'status', l.status) order by l.line_no)
  from po_line l left join price_record pr on pr.id = l.price_record_id
  where l.po_no = p.no and l.status <> 'cancelled'), '[]'::jsonb)"""
_CHARGES = """coalesce((select jsonb_agg(jsonb_build_object('type', c.type, 'amount', c.amount::text,
    'gst_rate', c.gst_rate::text, 'cgst', c.cgst::text, 'sgst', c.sgst::text, 'igst', c.igst::text) order by c.id)
  from po_charge c where c.po_no = p.no), '[]'::jsonb)"""

# a row for the current version of every order, as it stands; acknowledged_version where it was acknowledged
BACKFILL = [
    f"""
    insert into po_version (po_no, version, made_at, made_by, snapshot, sent_at, sent_to, acknowledged_at)
    select p.no, p.version,
           coalesce((select max(r.changed_at) from po_revision r where r.po_no = p.no and r.version = p.version),
                    p.created_at, now()),
           'backfill',
           jsonb_build_object(
             'supplier_code', p.supplier_code, 'ship_to_location', p.ship_to_location, 'buyer_gstin', p.buyer_gstin,
             'payment_terms_code', p.payment_terms_code, 'delivery_terms', p.delivery_terms,
             'contract_code', p.contract_code, 'taxable', p.taxable::text, 'cgst', p.cgst::text,
             'sgst', p.sgst::text, 'igst', p.igst::text, 'total', p.total::text,
             'lines', {_LINES}, 'charges', {_CHARGES}),
           p.sent_at, p.sent_to, p.acknowledged_at
      from po p
     where not exists (select 1 from po_version v where v.po_no = p.no and v.version = p.version)
    """,  # noqa: S608 - fixed SQL parts, no input
    "update po set acknowledged_version = version where acknowledged_at is not null",
]


def upgrade() -> None:
    op.execute(
        """
        create table po_version (
          id bigserial primary key,
          po_no text not null references po (no) on delete cascade,
          version integer not null,
          made_at timestamptz not null default now(),
          made_by text not null,
          reason text,
          snapshot jsonb not null,
          document_id integer references document (id),
          sent_at timestamptz,
          sent_to text,
          acknowledged_at timestamptz,
          acknowledged_by text,
          ack_via text check (ack_via in ('link', 'app', 'email')),
          unique (po_no, version)
        )
        """
    )
    op.execute("alter table po add column acknowledged_version integer")
    for statement in BACKFILL:
        op.execute(statement)


def downgrade() -> None:
    op.execute("alter table po drop column acknowledged_version")
    op.execute("drop table po_version")
