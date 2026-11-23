"""Buying route and strategic sourcing (block 5: tasks 1.4, 12.1, 12.2, 12.3; D42, D40, D45, D48, D53)

- D42: `rfq.send` is never automatic. A stored automatic setting goes back to `propose`; every RFQ keeps who confirmed
  its sending and how (`rfq.confirmed_by`, `confirmed_at`, `confirmed_via`). RFQs already sent are backfilled from
  their sending time and the approved `rfq.send` proposal.
- The old system's purchase orders are kept as history (`order_history`, loaded with the stock history): a period
  RFQ's suggested quantity counts the orders of the last 12 months.
- A period RFQ (`rfq.kind` 'period') asks prices for a period with no request: its lines point to a
  `period_demand_line`, never to a request line (one or the other, never neither, never both).
- An award on a period RFQ is a proposal (`award_decision`, `award.status` 'proposed') approved by the band's person;
  the letters it sends are `award_letter` rows. Awards get their own id, so a line may be awarded again after a
  rejected round; only one live award per line and supplier.
- A contract can come from an award (`contract.source_rfq`, `award_decision_id`) and is accepted by the supplier
  through a signed link (`contract_link`) and a one-time code sent to an address (`one_time_code.address`).

Revision ID: 0120
Revises: 0110
"""

from alembic import op

revision = "0120"
down_revision = "0110"
branch_labels = None
depends_on = None

BACKFILL = [
    "update autonomy_setting set level = 'propose', updated_at = now() where decision_type = 'rfq.send'",
    """
    update rfq r set confirmed_at = r.sent_at, confirmed_via = 'backfill',
           confirmed_by = (select p.decided_by from proposal p where p.id = r.proposal_id and p.status = 'approved')
     where r.sent_at is not null
    """,
]


def upgrade() -> None:
    op.execute(
        "alter table rfq add column kind text not null default 'operational' check (kind in ('operational', 'period')), "
        "add column period_from date, add column period_to date, add column review_task_id integer, "
        "add column confirmed_by integer, add column confirmed_at timestamptz, "
        "add column confirmed_via text check (confirmed_via in ('proposal', 'bulk', 'backfill'))"
    )
    op.execute("alter table rfq drop constraint if exists rfq_status_check")
    op.execute(
        "alter table rfq add constraint rfq_status_check "
        "check (status in ('draft', 'sent', 'closed', 'award_proposed', 'awarded', 'cancelled'))"
    )
    op.execute(
        """
        create table period_demand_line (
            id bigserial primary key,
            item_code text not null references item (code),
            location_code text not null,
            period_from date not null,
            period_to date not null check (period_to >= period_from),
            qty numeric(18,4) not null check (qty > 0),
            uom text,
            suggested_qty numeric(18,4),
            suggestion text,
            status text not null default 'on_rfq' check (status in ('on_rfq', 'awarded', 'cancelled')),
            review_task_id integer,
            created_by text not null,
            created_at timestamptz not null default now()
        )
        """
    )
    op.execute(
        """
        create table order_history (
            id bigserial primary key,
            po_no text not null,
            line_no integer not null,
            item_code text not null,
            location_code text not null,
            ordered_on date not null,
            qty numeric(18,4) not null,
            uom text,
            unique (po_no, line_no)
        )
        """
    )
    op.execute("create index order_history_item on order_history (item_code, location_code, ordered_on)")
    op.execute(
        "alter table rfq_line alter column request_no drop not null, alter column request_line_no drop not null, "
        "add column period_line_id bigint references period_demand_line (id), "
        "add constraint rfq_line_one_source check ("
        "(request_no is not null and request_line_no is not null and period_line_id is null) or "
        "(request_no is null and request_line_no is null and period_line_id is not null))"
    )
    op.execute(
        """
        create table award_decision (
            id bigserial primary key,
            rfq_no text not null references rfq (no),
            round integer not null,
            status text not null check (status in ('waiting', 'approved', 'rejected', 'withdrawn', 'stale')),
            proposed_by text not null,
            proposed_at timestamptz not null default now(),
            reason text,
            value_taxable numeric(18,2) not null,
            cgst numeric(18,2) not null,
            sgst numeric(18,2) not null,
            igst numeric(18,2) not null,
            value_total numeric(18,2) not null,
            rule_code text,
            rule_version integer,
            approver text,
            delegate text,
            proposal_id bigint,
            recommendation_id integer,
            regret_notes boolean not null default true,
            note text,
            note_source text check (note_source in ('template', 'ai')),
            decided_by text,
            decided_at timestamptz,
            decision_note text,
            unique (rfq_no, round)
        )
        """
    )
    op.execute("alter table award drop constraint award_pkey")
    op.execute(
        "alter table award add column id bigserial primary key, "
        "add column status text not null default 'final' "
        "check (status in ('final', 'proposed', 'approved', 'rejected', 'withdrawn')), "
        "add column decision_id bigint references award_decision (id)"
    )
    op.execute(
        "create unique index award_live on award (rfq_no, rfq_line_no, supplier_code) "
        "where status in ('final', 'proposed', 'approved')"
    )
    op.execute(
        """
        create table award_letter (
            id bigserial primary key,
            decision_id bigint not null references award_decision (id),
            supplier_code text not null,
            kind text not null check (kind in ('award', 'regret')),
            message_id text,
            contract_code text,
            queued_at timestamptz not null default now(),
            unique (decision_id, supplier_code, kind)
        )
        """
    )
    op.execute(
        "alter table contract add column source_rfq text, add column award_decision_id bigint, "
        "add column supplier_accepted_at timestamptz, add column supplier_accepted_by text, "
        "add column supplier_declined_at timestamptz, add column decline_reason text"
    )
    op.execute(
        """
        create table contract_link (
            id bigserial primary key,
            contract_code text not null references contract (code),
            supplier_code text not null,
            purpose text not null check (purpose = 'accept'),
            sent_to text not null,
            issued_at timestamptz not null,
            expires_at timestamptz not null,
            used_at timestamptz,
            used_for text
        )
        """
    )
    op.execute(
        "alter table one_time_code alter column user_id drop not null, add column address text, "
        "add constraint one_time_code_who check ((user_id is null) <> (address is null))"
    )
    for statement in BACKFILL:
        op.execute(statement)


def downgrade() -> None:
    op.execute("delete from one_time_code where user_id is null")
    op.execute(
        "alter table one_time_code drop constraint one_time_code_who, drop column address, "
        "alter column user_id set not null"
    )
    op.execute("drop table contract_link")
    op.execute(
        "alter table contract drop column decline_reason, drop column supplier_declined_at, "
        "drop column supplier_accepted_by, drop column supplier_accepted_at, drop column award_decision_id, "
        "drop column source_rfq"
    )
    op.execute("drop table award_letter")
    op.execute("delete from award where status not in ('final')")
    op.execute("drop index award_live")
    op.execute("alter table award drop column decision_id, drop column status, drop column id")
    op.execute("alter table award add primary key (rfq_no, rfq_line_no, supplier_code)")
    op.execute("drop table award_decision")
    op.execute("delete from rfq_line where period_line_id is not null")
    op.execute(
        "alter table rfq_line drop constraint rfq_line_one_source, drop column period_line_id, "
        "alter column request_no set not null, alter column request_line_no set not null"
    )
    op.execute("drop table period_demand_line")
    op.execute("drop table order_history")
    op.execute("update rfq set status = 'sent' where status = 'award_proposed'")
    op.execute("alter table rfq drop constraint rfq_status_check")
    op.execute(
        "alter table rfq add constraint rfq_status_check "
        "check (status in ('draft', 'sent', 'closed', 'awarded', 'cancelled'))"
    )
    op.execute(
        "alter table rfq drop column confirmed_via, drop column confirmed_at, drop column confirmed_by, "
        "drop column review_task_id, drop column period_to, drop column period_from, drop column kind"
    )
