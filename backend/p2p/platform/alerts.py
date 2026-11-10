"""Alerts raised by plain rules (no model): what needs attention, to whom, and when it no longer does.

Each rule looks at the data and returns the things that are wrong today. The engine keeps one alert per rule and subject
(`alert.key`): a new problem is raised once and the people who hold the right role are told through a notification with a
link; while it lasts nothing more is sent; when its cause goes away the alert and its notifications are resolved by
themselves; if it comes back it is raised again. The worker runs it every morning, and a person can run it on demand.
"""

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from p2p.formulas.calendar import IST, BusinessCalendar, business_today
from p2p.formulas.fy import fy_bounds
from p2p.formulas.kpis import working_days_between
from p2p.orders import contracts as contract_usage
from p2p.orders import dispatch
from p2p.platform import notifications, reference, routing, worker
from p2p.sourcing import documents

log = logging.getLogger(__name__)
JOB = "alerts"
RUN_AT = time(7, 30)  # India time


@dataclass(frozen=True)
class Finding:
    rule: str
    subject_type: str
    subject_id: str
    severity: str  # low, medium or high
    title: str
    detail: str
    link: str
    roles: tuple[str, ...]

    @property
    def key(self) -> str:
        return f"{self.rule}:{self.subject_id}"


@dataclass
class Context:
    db: Session
    today: date
    calendar: BusinessCalendar

    def setting(self, key: str, default: str) -> str:
        for table in ("app_setting", "org_setting"):
            value = self.db.execute(text(f"select value from {table} where key = :k"), {"k": key}).scalar()  # noqa: S608
            if value not in (None, ""):
                return str(value)
        return default

    def days(self, key: str, default: int) -> int:
        try:
            return int(self.setting(key, str(default)))
        except ValueError:
            return default

    def waited(self, since: datetime | date) -> int:
        start = since.astimezone(IST).date() if isinstance(since, datetime) else since
        return working_days_between(start, self.today, self.calendar)


# ---- the rules --------------------------------------------------------------------------------


def stale_requisitions(c: Context) -> list[Finding]:
    limit = c.days("stale_requisition_days", 3)
    out = []
    for no, value, since in c.db.execute(
        text("select no, total_value, coalesce(submitted_at, created_at) from request where status = 'submitted'")
    ):
        waited = c.waited(since)
        if waited > limit:
            out.append(
                Finding(
                    "stale_requisition", "request", no, "high" if waited > 2 * limit else "medium",
                    f"{no} has waited {waited} working days for approval",
                    f"Submitted request of {value:,.2f}; the limit is {limit} working days.", f"#/requisitions/{no}", ("Approver",),
                )
            )  # fmt: skip
    return out


def rfqs_without_quotes(c: Context) -> list[Finding]:
    rows = c.db.execute(
        text(
            "select r.no, r.due_date from rfq r where r.status = 'sent' and r.due_date < :d "
            "and not exists (select 1 from quote q where q.rfq_no = r.no)"
        ),
        {"d": c.today},
    )
    return [
        Finding(
            "rfq_no_quotes", "rfq", no, "medium", f"{no} is past its reply date with no quotes",
            f"Suppliers were to reply by {due:%d %b %Y}.", f"#/rfqs/{no}", ("Buyer",),
        )
        for no, due in rows
    ]  # fmt: skip


def orders_not_acknowledged(c: Context) -> list[Finding]:
    out = []
    for no, sent_at, supplier in c.db.execute(
        text(
            "select p.no, p.sent_at, coalesce(s.trade_name, s.legal_name) from po p join supplier s on s.code = p.supplier_code "
            "where p.status = 'sent'"
        )
    ):
        if dispatch.ack_overdue(c.db, sent_at, c.today):
            out.append(
                Finding(
                    "po_not_acknowledged", "po", no, "medium", f"{no} has not been acknowledged by {supplier}",
                    f"Sent on {sent_at.astimezone(IST):%d %b %Y}.", f"#/purchase-orders/{no}", ("Buyer",),
                )
            )  # fmt: skip
    return out


def late_deliveries(c: Context) -> list[Finding]:
    rows = c.db.execute(
        text(
            "select l.po_no, min(l.promised_date) as oldest, count(*) as lines from po_line l join po on po.no = l.po_no "
            "where l.promised_date < :d and l.status in ('open', 'part_received') and l.received_qty < l.qty "
            "and po.status in ('sent', 'acknowledged', 'part_received') group by l.po_no"
        ),
        {"d": c.today},
    )
    out = []
    for no, oldest, lines in rows:
        late = (c.today - oldest).days
        out.append(
            Finding(
                "late_delivery", "po", no, "high" if late > 7 else "medium", f"Goods on {no} are {late} day(s) late",
                f"{lines} line(s) were promised by {oldest:%d %b %Y} and are not all received.", f"#/purchase-orders/{no}", ("Buyer",),
            )
        )  # fmt: skip
    return out


def invoices_blocked_long(c: Context) -> list[Finding]:
    limit = c.days("invoice_block_alert_days", 3)
    out = []
    for no, number, since, reason in c.db.execute(
        text(
            "select no, supplier_invoice_no, received_on, block_reason from invoice "
            "where status = 'blocked' and payment_block <> 'duplicate' and type = 'invoice'"
        )
    ):
        waited = c.waited(since)
        if waited > limit:
            out.append(
                Finding(
                    "invoice_blocked", "invoice", no, "medium", f"Invoice {number} has been blocked for {waited} working days",
                    reason or "", f"#/invoices/{no}", ("AP",),
                )
            )  # fmt: skip
    return out


def invoices_falling_due(c: Context) -> list[Finding]:
    ahead = c.days("invoice_due_alert_days", 3)
    rows = c.db.execute(
        text(
            "select i.no, i.supplier_invoice_no, i.due_date, i.total, coalesce(s.trade_name, s.legal_name), "
            "s.msme_category in ('micro', 'small') from invoice i join supplier s on s.code = i.supplier_code "
            "where i.type = 'invoice' and i.status = 'approved' and i.due_date is not null and i.due_date <= :d "
            "and not exists (select 1 from payment_allocation a join payment p on p.no = a.payment_no "
            "join payment_run r on r.no = p.run_no where a.invoice_no = i.no and r.status in ('proposed', 'approved', 'file_generated'))"
        ),
        {"d": c.today + timedelta(days=ahead)},
    )
    out = []
    for no, number, due, total, supplier, msme in rows:
        overdue = due < c.today
        small = " The supplier is a small enterprise: the 45-day rule applies." if msme else ""
        out.append(
            Finding(
                "invoice_due", "invoice", no, "high" if overdue or msme else "medium",
                f"Invoice {number} of {supplier} {'is overdue' if overdue else 'is due soon'}",
                f"{total:,.2f} due {due:%d %b %Y}.{small}", "#/payments", ("Finance",),
            )
        )  # fmt: skip
    return out


def contracts_expiring(c: Context) -> list[Finding]:
    ahead = c.days("contract_expiring_days", 30)
    rows = c.db.execute(
        text("select code, valid_to from contract where status = 'active' and valid_to between :a and :b"),
        {"a": c.today, "b": c.today + timedelta(days=ahead)},
    )
    return [
        Finding(
            "contract_expiring", "contract", code, "medium" if (valid_to - c.today).days <= 7 else "low",
            f"Contract {code} ends on {valid_to:%d %b %Y}", f"{(valid_to - c.today).days} day(s) left.", f"#/contracts/{code}", ("Buyer",),
        )
        for code, valid_to in rows
    ]  # fmt: skip


def contracts_nearly_used(c: Context) -> list[Finding]:
    limit = c.days("contract_ceiling_alert_pct", 80)
    out = []
    for (code,) in c.db.execute(text("select code from contract where status = 'active' and max_value is not null")):
        use = contract_usage.usage(c.db, code)
        pct = use.pct_used
        if pct is not None and pct >= limit:
            out.append(
                Finding(
                    "contract_ceiling", "contract", code, "high" if pct >= 100 else "medium",
                    f"Contract {code} is {pct}% used", f"{use.used:,.2f} of {use.max_value:,.2f} ordered before tax.", f"#/contracts/{code}", ("Buyer",),
                )
            )  # fmt: skip
    return out


def supplier_documents(c: Context) -> list[Finding]:
    out = []
    for code, name in c.db.execute(
        text("select code, coalesce(trade_name, legal_name) from supplier where status = 'active' order by code")
    ):
        check = documents.check_supplier(c.db, code, c.today)
        if check.blocking:
            out.append(
                Finding(
                    "supplier_documents", "supplier", code, "high", f"{name} cannot be used: a required document is wrong",
                    "; ".join(check.blocking), f"#/vendors/{code}", ("Buyer",),
                )
            )  # fmt: skip
        elif check.warnings:
            out.append(
                Finding(
                    "supplier_documents", "supplier", code, "medium", f"{name}: a document is expiring or has expired",
                    "; ".join(check.warnings), f"#/vendors/{code}", ("Buyer",),
                )
            )  # fmt: skip
    return out


def budgets_nearly_used(c: Context) -> list[Finding]:
    limit = c.days("budget_alert_pct", 90)
    start, end = fy_bounds(c.today)
    first, last = start.strftime("%Y-%m"), c.today.strftime("%Y-%m")
    out = []
    for gl, planned in c.db.execute(
        text(
            "select gl_code, sum(amount) from budget where period between :a and :b and gl_code is not null "
            "and project_code is null group by gl_code having sum(amount) > 0"
        ),
        {"a": first, "b": last},
    ):
        committed = c.db.execute(
            text(
                "select coalesce(sum(l.qty * coalesce(l.est_price, 0)), 0) from request_line l join request r on r.no = l.request_no "
                "where r.status in ('submitted', 'approved', 'closed') and l.status <> 'cancelled' and l.gl_code = :g "
                "and (r.created_at at time zone 'Asia/Kolkata')::date between :s and :e"
            ),
            {"g": gl, "s": start, "e": end},
        ).scalar_one()
        pct = committed * 100 / planned
        if pct >= limit:
            out.append(
                Finding(
                    "budget_used", "gl", gl, "high" if pct >= 100 else "medium",
                    f"GL {gl}: {pct:.0f}% of the budget so far this year is committed",
                    f"{committed:,.2f} committed against {planned:,.2f} planned up to this month.", "#/requisitions", ("Approver", "Finance"),
                )
            )  # fmt: skip
    return out


RULES: dict[str, Callable[[Context], list[Finding]]] = {
    "stale_requisition": stale_requisitions,
    "rfq_no_quotes": rfqs_without_quotes,
    "po_not_acknowledged": orders_not_acknowledged,
    "late_delivery": late_deliveries,
    "invoice_blocked": invoices_blocked_long,
    "invoice_due": invoices_falling_due,
    "contract_expiring": contracts_expiring,
    "contract_ceiling": contracts_nearly_used,
    "supplier_documents": supplier_documents,
    "budget_used": budgets_nearly_used,
}


# ---- the engine -------------------------------------------------------------------------------


@dataclass
class RunResult:
    opened: list[str] = field(default_factory=list)
    resolved: list[str] = field(default_factory=list)
    open_now: int = 0
    failed_rules: list[str] = field(default_factory=list)


def _users_with(db: Session, roles: tuple[str, ...]) -> list[int]:
    return [
        r[0]
        for r in db.execute(
            text("select distinct user_id from user_role where role = any(:r) order by 1"), {"r": list(roles)}
        )
    ]


def _notify(db: Session, tenant: str, f: Finding, alert_id: int) -> None:
    for user_id in _users_with(db, f.roles):
        nid = notifications.notify(db, tenant, user_id, "alert", f.title, f.detail, link=f.link, email=False)
        db.execute(
            text("update notification set severity = :s, related_type = 'alert', related_id = :a where id = :n"),
            {"s": f.severity, "a": str(alert_id), "n": nid},
        )
    db.commit()


def run(db: Session, tenant: str, *, today: date | None = None) -> RunResult:
    """Look at the data, raise what is new, keep what lasts, resolve what has gone. Safe to run again at any time."""
    today = today or business_today()
    ctx = Context(db, today, reference.calendar_for(db))
    result = RunResult()
    found: dict[str, Finding] = {}
    evaluated: set[str] = set()
    for name, rule in RULES.items():
        try:
            for f in rule(ctx):
                found[f.key] = f
            evaluated.add(name)
        except Exception:  # noqa: BLE001 - one rule failing must not stop the others, nor resolve its alerts by mistake
            db.rollback()
            log.exception("alert rule %s failed", name)
            result.failed_rules.append(name)
    existing = {r["key"]: dict(r) for r in db.execute(text("select * from alert")).mappings()}
    for key, f in found.items():
        row = existing.get(key)
        if row is None:
            aid = db.execute(
                text(
                    "insert into alert (key, rule, subject_type, subject_id, severity, title, detail, link, roles) "
                    "values (:k, :r, :st, :si, :sv, :t, :d, :l, :ro) returning id"
                ),
                {
                    "k": key,
                    "r": f.rule,
                    "st": f.subject_type,
                    "si": f.subject_id,
                    "sv": f.severity,
                    "t": f.title,
                    "d": f.detail,
                    "l": f.link,
                    "ro": ",".join(f.roles),
                },
            ).scalar_one()
            db.commit()
            _notify(db, tenant, f, int(aid))
            result.opened.append(key)
        elif row["status"] == "resolved":
            db.execute(
                text(
                    "update alert set status = 'open', opened_at = now(), resolved_at = null, last_seen_at = now(), "
                    "times_opened = times_opened + 1, severity = :sv, title = :t, detail = :d where id = :i"
                ),
                {"sv": f.severity, "t": f.title, "d": f.detail, "i": row["id"]},
            )
            db.commit()
            _notify(db, tenant, f, int(row["id"]))
            result.opened.append(key)
        else:
            db.execute(
                text("update alert set last_seen_at = now(), severity = :sv, title = :t, detail = :d where id = :i"),
                {"sv": f.severity, "t": f.title, "d": f.detail, "i": row["id"]},
            )
    for key, row in existing.items():
        if row["status"] == "open" and row["rule"] in evaluated and key not in found:
            db.execute(
                text("update alert set status = 'resolved', resolved_at = now() where id = :i"), {"i": row["id"]}
            )
            db.execute(
                text(
                    "update notification set resolved = true, resolved_at = now() "
                    "where related_type = 'alert' and related_id = :a and not resolved"
                ),
                {"a": str(row["id"])},
            )
            result.resolved.append(key)
    db.commit()
    result.open_now = int(db.execute(text("select count(*) from alert where status = 'open'")).scalar_one())
    return result


# ---- the daily job ----------------------------------------------------------------------------


def next_run(now: datetime) -> datetime:
    tomorrow = now.astimezone(IST).date() + timedelta(days=1)
    return datetime.combine(tomorrow, RUN_AT, tzinfo=IST).astimezone(UTC)


def schedule(tenant: str, run_at: datetime | None = None) -> int | None:
    """Queue the daily alert run for a company unless one is already waiting."""
    return worker.enqueue_unique(tenant, JOB, {}, run_at or datetime.now(UTC))


@worker.register(JOB)
def _daily(ctx: worker.JobContext) -> None:
    with Session(routing.engine_for(ctx.tenant)) as db:
        run(db, ctx.tenant, today=ctx.now.astimezone(IST).date())
    worker.enqueue_unique(ctx.tenant, JOB, {}, next_run(ctx.now), exclude_id=ctx.id)


def open_alerts(db: Session, roles: list[str]) -> list[dict[str, Any]]:
    """The open alerts addressed to any of these roles, worst first."""
    rows = db.execute(
        text(
            "select * from alert where status = 'open' and string_to_array(roles, ',') && :r "
            "order by case severity when 'high' then 0 when 'medium' then 1 else 2 end, opened_at, id"
        ),
        {"r": roles},
    ).mappings()
    return [dict(r) for r in rows]
