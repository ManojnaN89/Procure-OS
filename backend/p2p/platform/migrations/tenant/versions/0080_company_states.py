"""Cutover of open documents (block 0d, task 7.4)

The documents a company still has open in its old system at go-live (orders, receipts, service confirmations, invoices,
advances) are loaded once, in state cutover: each order on a carrier request `CUT-<order>` (channel `cutover`), its lines
with source `cutover`, its invoices with channel `cutover`. Every load is one `cutover_import` row with what loaded, what
was there already, what was left out and the old system's control totals; `cutover_supplier_ytd` keeps what was bought
from each supplier earlier in the financial year (tax at source counts it). Admin and Finance may load them.

Revision ID: 0080
Revises: 0072
"""

from alembic import op

revision = "0080"
down_revision = "0072"
branch_labels = None
depends_on = None

# table, check, the values before, the value added
CHECKS = (
    ("request", "request_channel_check", "channel", ("form", "email", "chat", "planning", "unordered_receipt")),
    ("invoice", "invoice_channel_check", "channel", ("email", "upload", "portal", "manual")),
    ("po_line", "po_line_source_check", "source", ("contract", "award", "direct")),
    ("service_entry", "service_entry_via_check", "via", ("app", "emailed link")),
)


def _check(table: str, name: str, column: str, values: tuple[str, ...]) -> None:
    op.execute(f"alter table {table} drop constraint {name}")
    listed = ", ".join(f"'{v}'" for v in values)
    op.execute(f"alter table {table} add constraint {name} check ({column} in ({listed}))")


def upgrade() -> None:
    for table, name, column, values in CHECKS:
        _check(table, name, column, (*values, "cutover"))
    op.execute(
        """
        create table cutover_import (
            id serial primary key,
            at timestamptz not null default now(),
            imported_by integer not null,
            files jsonb not null,
            dry_run boolean not null,
            loaded jsonb not null,
            already jsonb not null,
            rejected integer not null,
            control jsonb not null,
            control_ok boolean not null
        )
        """
    )
    op.execute(
        """
        create table cutover_supplier_ytd (
            supplier_code text not null references supplier (code),
            fy text not null,
            taxable numeric(18, 2) not null,
            primary key (supplier_code, fy)
        )
        """
    )
    for role in ("Admin", "Finance"):
        op.execute(
            f"insert into role_permission (role, action) values ('{role}', 'company.cutover') on conflict do nothing"  # noqa: S608
        )


def downgrade() -> None:
    op.execute("delete from role_permission where action = 'company.cutover'")
    op.execute("drop table cutover_supplier_ytd")
    op.execute("drop table cutover_import")
    for table, name, column, values in CHECKS:
        op.execute(f"delete from {table} where {column} = 'cutover'")  # noqa: S608 - fixed names
        _check(table, name, column, values)
