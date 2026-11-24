"""Match and cases (block 11): every reason per line, the rule version, the variance, and one open case per key.

Revision ID: 0180
Revises: 0150
"""

from alembic import op

revision = "0180"
down_revision = "0150"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "alter table match_result add column reasons text[] not null default '{}', "
        "add column rules smallint not null default 1, "
        "add column variance_value numeric(18,2), "
        "add column resolution_kind text check (resolution_kind in ('price_accepted', 'released'))"
    )
    op.execute("update match_result set reasons = array[reason_codes] where reason_codes is not null")
    op.execute("alter table match_result alter column rules set default 2")
    op.execute(
        "alter table invoice add column variance_accepted numeric(18,2) not null default 0, "
        "add column match_rules smallint"
    )
    op.execute("update invoice set match_rules = 1 where tolerance_version is not null")
    op.execute("alter table task add column case_key text, add column step text, add column resolution text")
    op.execute(
        "create unique index ux_task_open_case on task (case_key) "
        "where case_key is not null and status in ('open', 'overdue')"
    )
    # Fails loudly if two releases for one invoice are pending today: that state must be fixed before this index exists.
    op.execute(
        "create unique index ux_proposal_open_release on proposal ((payload->>'invoice')) "
        "where action = 'invoice.release_block' and status = 'pending'"
    )
    op.execute(
        "alter table invoice_diagnosis add column query_email_id int references email_message (id), "
        "add column query_sent_by int, add column query_sent_at timestamptz"
    )
    # The AP agent's cause for a service hold has its own fix; the original check did not know it.
    op.execute("alter table invoice_diagnosis drop constraint invoice_diagnosis_fix_check")
    op.execute(
        "alter table invoice_diagnosis add constraint invoice_diagnosis_fix_check check "
        "(fix in ('credit_note', 'corrected_invoice', 'wait_for_receipt', 'wait_for_confirmation', 'request_release'))"
    )
    op.execute(
        "insert into org_setting (key, value, description) values ('case_due_days', '2', "
        "'Working days to act on a stuck item before it moves to the manager') on conflict do nothing"
    )


def downgrade() -> None:
    op.execute("delete from org_setting where key = 'case_due_days'")
    op.execute("update invoice_diagnosis set fix = 'wait_for_receipt' where fix = 'wait_for_confirmation'")
    op.execute("alter table invoice_diagnosis drop constraint invoice_diagnosis_fix_check")
    op.execute(
        "alter table invoice_diagnosis add constraint invoice_diagnosis_fix_check check "
        "(fix in ('credit_note', 'corrected_invoice', 'wait_for_receipt', 'request_release'))"
    )
    op.execute(
        "alter table invoice_diagnosis drop column query_sent_at, drop column query_sent_by, drop column query_email_id"
    )
    op.execute("drop index ux_proposal_open_release")
    op.execute("drop index ux_task_open_case")
    op.execute("alter table task drop column resolution, drop column step, drop column case_key")
    op.execute("alter table invoice drop column match_rules, drop column variance_accepted")
    op.execute(
        "alter table match_result drop column resolution_kind, drop column variance_value, drop column rules, drop column reasons"
    )
