"""Approval bound to the order's version (task 0.2, block 4; DF-07, DF-08, Q19)

Each approval step is for one version of the order (`po_approval_step.version`). When an order becomes approved, the
version and a snapshot of what it said are kept (`po.approved_version`, `po_approved_version`), so a later revision is
measured from the approved version and small changes add up. A revision keeps the version it was compared with and its
material reasons (`po_revision.baseline_version`, `material`); a supplier's price change keeps the first approved price
it was judged against (`po_change.base_value`).

Backfill: steps of the current round get the order's version; every approved order gets one snapshot of its current state
(approved by 'backfill'), its band from its rule, and `approved_version` = its version.

Revision ID: 0110
Revises: 0080
"""

from alembic import op

revision = "0110"
down_revision = "0080"
branch_labels = None
depends_on = None


# the steps of the current round get the order's version; every approved order gets one snapshot of its current state,
# the band of the rule that approved it (none for the top band: no po rule above it) and approved_version
BACKFILL = [
    "update po_approval_step s set version = p.version from po p where p.no = s.po_no and s.round = p.approval_round",
    """
    insert into po_approved_version (po_no, version, approved_at, approved_by, rule_code, rule_version, band_max,
                                     total, snapshot)
    select p.no, p.version, p.approved_at, 'backfill', p.rule_code, p.rule_version,
           (select r.max_value from approval_rule r
             where r.document = 'po' and r.rule_code = p.rule_code and r.version = p.rule_version
               and exists (select 1 from approval_rule u where u.document = 'po' and u.min_value > r.max_value)
             limit 1),
           p.total,
           jsonb_build_object(
             'supplier_code', p.supplier_code, 'ship_to_location', p.ship_to_location,
             'payment_terms_code', p.payment_terms_code,
             'lines', coalesce((select jsonb_agg(jsonb_build_object(
                 'line_no', l.line_no, 'item_code', l.item_code, 'qty', l.qty::text,
                 'unit_price', l.unit_price::text, 'need_by', l.need_by) order by l.line_no)
               from po_line l where l.po_no = p.no and l.status <> 'cancelled'), '[]'::jsonb))
      from po p where p.approved_at is not null
    """,
    "update po set approved_version = version where approved_at is not null",
]


def upgrade() -> None:
    op.execute("alter table po add column approved_version integer")
    op.execute("alter table po_approval_step add column version integer")
    op.execute(
        "alter table po_revision add column baseline_version integer, "
        "add column material jsonb not null default '[]'::jsonb"
    )
    op.execute("alter table po_change add column base_value text")
    op.execute(
        """
        create table po_approved_version (
            id bigserial primary key,
            po_no text not null references po (no) on delete cascade,
            version integer not null,
            approved_at timestamptz not null default now(),
            approved_by text not null,
            rule_code text,
            rule_version integer,
            band_max numeric(18,2),
            total numeric(18,2) not null,
            snapshot jsonb not null,
            unique (po_no, version)
        )
        """
    )
    for statement in BACKFILL:
        op.execute(statement)


def downgrade() -> None:
    op.execute("drop table po_approved_version")
    op.execute("alter table po_change drop column base_value")
    op.execute("alter table po_revision drop column material, drop column baseline_version")
    op.execute("alter table po_approval_step drop column version")
    op.execute("alter table po drop column approved_version")
