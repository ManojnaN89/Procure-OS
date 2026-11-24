"""Setup to cutover (task 7.2, D-05): the setup database is kept as an archive and a clean one takes its place.

What was done in setup is test data, and the audit log and the setup history are insert only, so the clean database is
a new one: migrated to head, reference data loaded, and the company's configuration copied in (`CARRIED`: masters,
people, roles, limits, rules, settings, the loaded stock history). Transactions and test data (`NOT_CARRIED`) stay only
in the archive, which the platform team reads as the owner; the app login cannot connect to it. The new audit chain
starts with `company.cutover`, which names the archive and its last hashes. Run as the owner by the platform team only
(`p2p.cli company cutover`); nothing is undone automatically once the databases are renamed.
"""

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from alembic import command
from sqlalchemy import Connection, create_engine, select, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import NullPool

from p2p.formulas.words import count
from p2p.platform import audit, company_state, db_logins, readiness, reference, routing, tenants
from p2p.platform.company_state import StateError
from p2p.platform.control_models import Tenant, TenantArchive, TenantDatabase

IST = ZoneInfo("Asia/Kolkata")

# table -> which rows go to live (None: all of them); every tenant table is here or in NOT_CARRIED (a test checks)
CARRIED: dict[str, str | None] = {
    **{
        t: None
        for t in (
            # the master tables (masters/schema.py)
            "company",
            "bank",
            "department",
            "region",
            "location",
            "payment_terms",
            "gl_account",
            "item_category",
            "org_user",
            "org_user_role",
            "delegation",
            "cost_centre",
            "project",
            "budget",
            "company_bank_account",
            "gst_registration",
            "number_series",
            "org_setting",
            "tolerance_version",
            "tolerance",
            "approval_rule",
            "item",
            "item_location",
            "item_substitute",
            "uom_conversion",
            "stock_opening",
            "supplier",
            "supplier_address",
            "supplier_bank_account",
            "supplier_category",
            "supplier_category_scope",
            "supplier_contact",
            "supplier_document",
            "supplier_status_history",
            "supplier_flag",
            "contract",
            "price_record",
            "order_history",  # block 5: the old system's orders, history for a period RFQ's suggested quantity
            "sanction_entry",
            "price_threshold_rule",
            "market_benchmark",
            # people, rules and settings
            "user_profile",
            "user_role",
            "user_limit",
            "role_permission",
            "approver_role",
            "app_setting",
            "autonomy_setting",
            "autonomy_global",
            "buying_route_rule",
            "supplier_selection_rule",
            "touchless_rule",
            "rule_table",
            "item_single_source",
            "erp_column_map",
            "joiner_kit",
            "budget_period_lock",  # block 3: locks of the loaded history months
            "forward_buy_rule",  # block 3
            "ai_capability",  # AI foundation: the capability switches
            "non_po_rule",  # block 10: which categories may be billed without an order, and their limits
        )
    },  # fmt: skip
    "stock_movement": "source = 'import'",  # the loaded history; what the platform posted in setup is test data
    # the files carried rows point at (stored once, shared by storage key)
    "document": (
        "id in (select evidence_document_id from supplier_bank_account union select document_id from market_benchmark) "
        "or linked_type in ('supplier', 'supplier_document', 'contract')"
    ),
    "demand_import": "id in (select import_id from joiner_kit)",  # the import a carried joiner kit came from
}
NOT_CARRIED = (
    "advance", "advance_adjustment", "agent_run", "agent_step", "alert", "approval_step", "audit_event",
    "autonomy_outcome", "award", "bank_statement_line", "budget_actual", "case_event", "checkpoint_blobs",
    "checkpoint_migrations", "checkpoint_writes", "checkpoints", "cutover_import", "cutover_supplier_ytd",
    "delivery_reminder", "demand_change", "demand_change_case", "dispatch_note", "dispatch_note_line", "email_message",
    "goods_receipt", "gr_line", "intake_pending", "invoice", "invoice_charge", "invoice_diagnosis", "invoice_draft",
    "invoice_line", "invoice_approval_step", "invoice_document_link", "match_result", "material_request", "material_request_buy", "material_request_line",
    "material_requirement", "model_cache", "model_call", "notification", "number_counter", "one_time_code", "payment",
    "payment_allocation", "payment_run", "planning_note", "po", "po_approval_step", "po_change", "po_charge",
    "po_commitment", "po_line", "po_revision", "product_demand", "production_order", "proposal", "purchase_case", "quote",
    "quote_draft", "quote_line", "request", "request_check", "request_line", "rfq", "rfq_line", "rfq_supplier",
    "rule_evaluation", "serial_number", "service_entry", "setup_change", "sourcing_decision", "stock_hold",
    "supplier_chase", "supplier_link", "supplier_message", "supply_risk", "task", "tds_entry", "unordered_receipt",
    "vendor_statement", "vendor_statement_line",
    # blocks 3 and 4 and the AI foundation: transactions and their records
    "budget_year_end", "forward_buy", "po_approved_version", "event", "recommendation", "workflow",
    # block 5: period RFQs, award decisions and their letters, contract links
    "period_demand_line", "award_decision", "award_letter", "contract_link",
    # block 7: the versions of an order
    "po_version",
    # loaded fresh into the new database
    "ref_gst_rate", "ref_gst_state", "ref_holiday", "ref_hsn_sac", "ref_load", "ref_rbi_bank_rate", "ref_tds_section",
    "ref_uom",
)  # fmt: skip


class NotReady(Exception):
    pass


class CutoverFailed(Exception):
    pass


@dataclass
class Done:
    slug: str
    dry_run: bool
    archive: str = ""
    carried: dict[str, int] = field(default_factory=dict)
    jobs_cancelled: int = 0
    tokens_revoked: int = 0
    warnings: list[str] = field(default_factory=list)

    def lines(self) -> list[str]:
        rows = sum(self.carried.values())
        if self.dry_run:
            head = [
                f"{self.slug} is ready to cut over (dry run, nothing changed): {count(rows, 'row')} would be carried"
            ]
        else:
            head = [
                f"{self.slug} is in cutover: setup archived as {self.archive}, {count(rows, 'row')} carried, "
                f"{count(self.jobs_cancelled, 'job')} cancelled, {count(self.tokens_revoked, 'sign-in')} ended"
            ]
        return (
            head
            + [f"  {t}: {n}" for t, n in sorted(self.carried.items()) if n]
            + [f"  warning: {w}" for w in self.warnings]
        )


def _quoted(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


@contextmanager
def _owner(database: str) -> Iterator[Connection]:
    engine: Engine = create_engine(
        db_logins.owner_url(tenants.control_url().set(database=database)), poolclass=NullPool
    )
    try:
        with engine.connect() as conn:
            yield conn
    finally:
        engine.dispose()


def _tables(conn: Connection) -> set[str]:
    return {str(r[0]) for r in conn.execute(text("select tablename from pg_tables where schemaname = 'public'"))} - {
        "alembic_version"
    }


def _where(table: str) -> str:
    rule = CARRIED[table]
    return f" where {rule}" if rule else ""


def _counts(conn: Connection) -> dict[str, int]:
    return {
        t: int(conn.execute(text(f"select count(*) from {t}{_where(t)}")).scalar_one())  # noqa: S608 - fixed names
        for t in sorted(CARRIED)
    }


def _database(slug: str) -> str:
    with tenants._owner_control_session() as s:  # noqa: SLF001 - the platform team's command
        name = s.scalar(
            select(TenantDatabase.database_name)
            .join(Tenant, Tenant.id == TenantDatabase.tenant_id)
            .where(Tenant.slug == slug)
        )
    if name is None:
        raise StateError(f"unknown company '{slug}'", 404)
    return str(name)


def _ready(slug: str) -> None:
    with Session(routing.engine_for(slug)) as db:
        found = readiness.check(db, slug, group="setup")
    if not found.ready:
        raise NotReady(", ".join(found.blockers))


def archive_and_rebuild(slug: str, *, reason: str, operator: str, dry_run: bool = False) -> Done:
    """Setup to cutover: check, archive the setup database, make the clean one, carry the configuration, move the state."""
    if not reason.strip():
        raise StateError("a reason is needed", 422)
    company_state.check_move(company_state.state_of(slug), "cutover")
    _ready(slug)
    db_name = _database(slug)
    done = Done(slug, dry_run)
    if not dry_run:  # the setup database at head first, so both databases have the same tables and columns
        migrated = tenants.migrate_tenant(slug)
        if not migrated.ok:
            raise CutoverFailed(f"the setup database could not be brought up to date: {migrated.error}")
    with _owner(db_name) as src:
        done.carried = _counts(src)
    if dry_run:
        return done
    archive = f"{db_name}__setup_{datetime.now(IST):%Y%m%d%H%M}"
    new = f"{db_name}__new"
    with tenants._admin_connection() as admin:  # noqa: SLF001
        if admin.execute(text("select 1 from pg_database where datname = any(:n)"), {"n": [archive, new]}).first():
            raise CutoverFailed(f"a database {archive} or {new} already exists; wait a minute or drop the leftover")
    with Session(routing.engine_for(slug)) as db:
        audit.record(
            db, company_state._audit_actor(company_state.operator(operator)), "company.archived", "company", slug,  # noqa: SLF001
            after={"archive": archive, "reason": reason, "operator": operator}, reason=reason,
        )  # fmt: skip
        db.commit()
    _build(db_name, new, done)
    _swap(db_name, new, archive)
    _seal(slug, db_name, archive, reason, operator, done)
    return done


def _build(db_name: str, new: str, done: Done) -> None:
    """The clean database, made and filled; dropped again if anything goes wrong (the company stays in setup)."""
    with tenants._admin_connection() as admin:  # noqa: SLF001
        admin.execute(text(f"create database {_quoted(new)}"))
    try:
        url = tenants._render(db_logins.owner_url(tenants.control_url().set(database=new)))  # noqa: SLF001
        command.upgrade(tenants._config("tenant", url), "head")  # noqa: SLF001
        from p2p.agents.runtime import setup_checkpoints  # noqa: PLC0415

        setup_checkpoints(url)
        engine = create_engine(url, poolclass=NullPool)
        try:
            with Session(engine) as s:
                reference.load_reference(s)
        finally:
            engine.dispose()
        _copy(db_name, new)
        with _owner(new) as dst:
            got = {t: int(dst.execute(text(f"select count(*) from {t}")).scalar_one()) for t in CARRIED}  # noqa: S608
        differ = {t: (n, got[t]) for t, n in done.carried.items() if got[t] != n}
        if differ:
            raise CutoverFailed(f"rows differ after the copy (setup, new): {differ}")
    except Exception:
        with tenants._admin_connection() as admin:  # noqa: SLF001
            admin.execute(text(f"drop database if exists {_quoted(new)} with (force)"))
        raise


def _copy(db_name: str, new: str) -> None:
    """Every carried row, as it is (same keys), with the foreign keys put back and checked afterwards."""
    with _owner(new) as dst, _owner(db_name) as src:
        missing = _tables(dst) - set(CARRIED) - set(NOT_CARRIED)
        if missing:
            raise CutoverFailed(f"tables neither carried nor left in the archive: {', '.join(sorted(missing))}")
        keys = dst.execute(
            text(
                "select conrelid::regclass::text, conname, pg_get_constraintdef(oid) from pg_constraint where contype = 'f' "
                "and (conrelid::regclass::text = any(:t) or confrelid::regclass::text = any(:t))"
            ),
            {"t": list(CARRIED)},
        ).all()
        for table, name, _ in keys:
            dst.execute(text(f"alter table {table} drop constraint {_quoted(name)}"))
        dst.execute(text(f"truncate {', '.join(CARRIED)}"))  # what the migrations seeded is replaced by setup's own
        raw_src, raw_dst = src.connection.dbapi_connection, dst.connection.dbapi_connection
        assert raw_src is not None and raw_dst is not None  # noqa: S101 - open connections
        for table in CARRIED:
            columns = [
                str(r[0])
                for r in dst.execute(
                    text(
                        "select column_name from information_schema.columns where table_schema = 'public' and table_name = :t "
                        "and is_generated = 'NEVER' order by ordinal_position"
                    ),
                    {"t": table},
                )
            ]
            cols = ", ".join(_quoted(c) for c in columns)
            out_sql = f"copy (select {cols} from {table}{_where(table)}) to stdout"  # noqa: S608 - names of our tables
            _stream(raw_src, raw_dst, out_sql, f"copy {table} ({cols}) from stdin")
        for table, name, definition in keys:
            dst.execute(text(f"alter table {table} add constraint {_quoted(name)} {definition}"))
        for table, column, sequence in dst.execute(
            text(
                "select c.table_name, c.column_name, pg_get_serial_sequence(c.table_name, c.column_name) from "
                "information_schema.columns c where c.table_schema = 'public' and c.table_name = any(:t) "
                "and pg_get_serial_sequence(c.table_name, c.column_name) is not null"
            ),
            {"t": list(CARRIED)},
        ).all():
            top = f"coalesce((select max({column}) from {table}), 1), (select count(*) > 0 from {table})"  # noqa: S608
            dst.execute(text(f"select setval(:s, {top})"), {"s": sequence})
        dst.commit()


def _stream(raw_src: Any, raw_dst: Any, out_sql: str, in_sql: str) -> None:
    with raw_src.cursor() as rc, raw_dst.cursor() as wc, rc.copy(out_sql) as source, wc.copy(in_sql) as sink:
        for block in source:
            sink.write(block)


def _swap(db_name: str, new: str, archive: str) -> None:
    """The setup database becomes the archive and the clean one takes its name (the stored address stays the same)."""
    routing.clear_engine_cache()
    tenants.reset_control_engines()
    with tenants._admin_connection() as admin:  # noqa: SLF001
        for name in (db_name, new):
            admin.execute(
                text(
                    "select pg_terminate_backend(pid) from pg_stat_activity where datname = :n and pid <> pg_backend_pid()"
                ),
                {"n": name},
            )
        admin.execute(text(f"alter database {_quoted(db_name)} rename to {_quoted(archive)}"))
        admin.execute(text(f"alter database {_quoted(new)} rename to {_quoted(db_name)}"))
        app = _quoted(db_logins.app_role_name())
        admin.execute(text(f"revoke connect on database {_quoted(archive)} from public, {app}"))


def _seal(slug: str, db_name: str, archive: str, reason: str, operator: str, done: Done) -> None:
    """The archive recorded, jobs and sign-ins of setup ended, the new chain started, and the state moved."""
    done.archive = archive
    with _owner(archive) as arch:
        audit_rows = int(arch.execute(text("select count(*) from audit_event")).scalar_one())
        last_audit = arch.execute(text("select row_hash from audit_event order by id desc limit 1")).scalar()
        last_setup = arch.execute(text("select row_hash from setup_change order by id desc limit 1")).scalar()
    with tenants._owner_control_session() as s:  # noqa: SLF001
        tenant = s.scalar(select(Tenant).where(Tenant.slug == slug))
        assert tenant is not None  # noqa: S101 - checked before the swap
        s.add(
            TenantArchive(
                tenant_id=tenant.id, database_name=archive, operator=operator, reason=reason, audit_rows=audit_rows,
                audit_last_hash=last_audit, setup_change_last_hash=last_setup,
            )
        )  # fmt: skip
        done.jobs_cancelled = s.execute(  # type: ignore[attr-defined]
            text(
                "update job set status = 'cancelled', finished_at = now(), last_error = 'cancelled at cutover' "
                "where tenant_slug = :s and status in ('queued', 'running')"
            ),
            {"s": slug},
        ).rowcount
        done.tokens_revoked = s.execute(  # type: ignore[attr-defined]
            text(
                "update refresh_token set revoked_at = now() where revoked_at is null "
                "and user_id in (select id from app_user where tenant_id = :t)"
            ),
            {"t": tenant.id},
        ).rowcount
        s.commit()
    actor = company_state.operator(operator)
    with Session(routing.engine_for(slug)) as db:
        audit.record(
            db, company_state._audit_actor(actor), "company.cutover", "company", slug,  # noqa: SLF001
            after={
                "archive": archive, "archive_audit_rows": audit_rows, "archive_audit_last_hash": last_audit,
                "archive_setup_change_last_hash": last_setup, "carried": done.carried,
                "jobs_cancelled": done.jobs_cancelled, "tokens_revoked": done.tokens_revoked,
            },
            reason=reason,
        )  # fmt: skip
        db.commit()
    company_state.move(slug, "cutover", actor=actor, reason=reason)
