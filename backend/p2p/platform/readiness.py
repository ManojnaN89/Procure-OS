"""Is the company ready to cut over and to go live (task 7.3)? Read only.

Each check names what it needs and every problem it finds. A failed blocker stops the cutover (setup group) or the
go-live (both groups); a warning is shown and accepted. People are counted only when they could act today: an active
login whose profile was written, holding a role that grants the permission. Separation coverage asks, for every control
that needs a second person, whether each person who may start it has someone else who may finish it.
"""

from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from p2p.approvals.rules import ApprovalRuleError, chain_roles, resolve_chain
from p2p.formulas.calendar import business_today
from p2p.formulas.words import count
from p2p.platform import numbering, setting_changes, tenants
from p2p.platform.control_models import AppUser, Tenant

GROUPS = ("all", "setup", "cutover")

# every document_type the platform numbers (a test reads every next_number call in backend/p2p and compares)
DOCUMENT_TYPES = (
    "goods_receipt",
    "invoice_booking",
    "material_request",
    "payment",
    "payment_run",
    "po",
    "request",
    "rfq",
    "service_receipt",
    "unordered_receipt",
)
# who may approve each kind of document the approval rules cover
APPROVE = {
    "request": "request.approve",
    "po": "po.approve",
    "po_amendment": "po.approve",
    "price_variance": "po.approve",
    "payment_run": "payment.approve",
    "supplier_activation": "supplier.activate",
    "forward_buy": "sourcing.route_approve",  # block 3: requests/forward_buy.py checks this permission
    "non_po_invoice": "invoice.approve",  # block 10: a bill without an order
}
ADMIN = ("admin.users", "admin.rules", "admin.settings")
# the five duties of one case, each by a different person (FR32, sod.DUTIES)
DUTIES = (
    ("requester", "request.create"),
    ("approver", "request.approve"),
    ("receiver", "grn.create"),
    ("invoice approver", "invoice.approve"),
    ("payer", "payment.approve"),
)


@dataclass(frozen=True)
class Separation:
    """A control that needs a second person: who may start it, who may finish it, and where the code enforces it."""

    code: str
    what: str  # what gets done, as in "<maker> could not get <what> done"
    makers: str  # a permission, or chain:<document> (the people the rules in force name for it)
    checkers: str
    enforced_in: tuple[str, ...]  # modules under backend/p2p


SEPARATION = (
    Separation("SEP-01", "a change to people and roles", "admin.users", "admin.users", ("platform/setting_changes.py", "platform/proposals.py")),
    Separation("SEP-02", "a change to approval rules", "admin.rules", "admin.rules", ("platform/setting_changes.py", "platform/proposals.py")),
    Separation("SEP-03", "a change to policies and automation", "admin.settings", "admin.settings", ("platform/setting_changes.py", "platform/proposals.py")),
    Separation("SEP-04", "a purchase order approved", "po.create", "chain:po", ("orders/workflow.py",)),
    Separation("SEP-05", "a supplier's status changed", "supplier.propose", "supplier.activate", ("masters/supplier_status.py", "platform/proposals.py")),
    Separation("SEP-06", "a supplier's bank account verified", "supplier.propose", "supplier.verify_bank", ("masters/suppliers.py",)),
    Separation("SEP-07", "a supplier's documents verified", "supplier.documents", "supplier.verify_docs", ("masters/documents_and_contracts.py",)),
    Separation("SEP-08", "a prospect's identity checked", "supplier.propose", "supplier.verify_docs", ("masters/prospects.py",)),
    Separation("SEP-09", "a contract or price version activated", "contract.manage", "contract.activate", ("masters/documents_and_contracts.py",)),
    Separation("SEP-10", "a market benchmark verified", "benchmark.enter", "benchmark.verify", ("sourcing/benchmarks.py",)),
    Separation("SEP-11", "a direct buy or a looser buying route agreed", "rfq.create", "sourcing.route_approve", ("sourcing/service.py",)),
    Separation("SEP-12", "goods received without an order approved", "grn.create", "grn.unordered_approve", ("receiving/unordered.py",)),
    Separation("SEP-13", "an invoice block released", "invoice.book", "invoice.approve", ("invoices/service.py", "invoices/non_po.py")),
    Separation("SEP-14", "a payment run approved", "payment.propose", "chain:payment_run", ("invoices/payment.py",)),
    Separation("SEP-15", "a bank match confirmed", "chain:payment_run", "payment.propose", ("invoices/bank_statement.py",)),
)  # fmt: skip
# modules with a segregation-of-duties refusal that another check covers
COVERED_ELSEWHERE = {
    "approvals/workflow.py": "approvers (a requester never approves their own request)",
    "receiving/goods.py": "duties (requester, receiver and invoice approver differ)",
    "receiving/services.py": "duties (requester, receiver and invoice approver differ)",
}


@dataclass(frozen=True)
class Check:
    code: str
    group: str
    title: str
    severity: str  # blocker or warning
    ok: bool
    problems: list[str] = field(default_factory=list)
    link: str = ""

    def view(self) -> dict[str, Any]:
        return {
            "code": self.code, "group": self.group, "title": self.title, "severity": self.severity, "ok": self.ok,
            "problems": list(self.problems), "link": self.link,
        }  # fmt: skip


@dataclass(frozen=True)
class Readiness:
    state: str
    checked_at: datetime
    checks: list[Check]
    ready: bool

    @property
    def blockers(self) -> list[str]:
        return [c.code for c in self.checks if not c.ok and c.severity == "blocker"]

    def view(self) -> dict[str, Any]:
        return {
            "state": self.state,
            "checked_at": self.checked_at.isoformat(),
            "ready": self.ready,
            "blockers": sum(1 for c in self.checks if not c.ok and c.severity == "blocker"),
            "warnings": sum(1 for c in self.checks if not c.ok and c.severity == "warning"),
            "checks": [c.view() for c in self.checks],
        }


class People:
    """Who could act in the company today: logins (active, made completely) with a profile, their permissions, and the
    person in the organisation each one is (same email)."""

    def __init__(self, db: Session, slug: str) -> None:
        with tenants.control_session() as s:
            logins = {
                int(uid): str(email)
                for uid, email in s.execute(
                    select(AppUser.id, AppUser.email)
                    .join(Tenant, Tenant.id == AppUser.tenant_id)
                    .where(Tenant.slug == slug, AppUser.active, AppUser.provisioned_at.is_not(None))
                )
            }
        profiles = {int(r[0]): str(r[1]) for r in db.execute(text("select user_id, display_name from user_profile"))}
        self.names = {uid: profiles[uid] for uid in logins if uid in profiles}
        self.perms: dict[str, set[int]] = {}
        for uid, action in db.execute(
            text("select distinct ur.user_id, rp.action from user_role ur join role_permission rp on rp.role = ur.role")
        ):
            if int(uid) in self.names:
                self.perms.setdefault(str(action), set()).add(int(uid))
        self.org: dict[str, dict[str, Any]] = {
            str(r["code"]): dict(r)
            for r in db.execute(
                text(
                    "select code, name, email, designation, department_code, location_code, manager_code, active from org_user"
                )
            ).mappings()
        }
        by_email = {email: uid for uid, email in logins.items() if uid in self.names}
        self.login_of = {
            code: by_email[str(p["email"]).strip().lower()]
            for code, p in self.org.items()
            if p["active"] is not False and p["email"] and str(p["email"]).strip().lower() in by_email
        }

    def holding(self, action: str) -> set[int]:
        return set(self.perms.get(action, set()))

    def name(self, uid: int) -> str:
        return self.names.get(uid, str(uid))

    def names_of(self, uids: set[int]) -> str:
        return ", ".join(sorted(self.name(u) for u in uids))

    def person_name(self, code: str | None) -> str:
        return str(self.org[code]["name"]) if code and code in self.org else (code or "nobody")


def _rules(db: Session, on: date) -> list[dict[str, Any]]:
    return [
        dict(r)
        for r in db.execute(
            text(
                "select rule_code, document, approver_chain, department_code, coalesce(location_code, 'ANY') as location_code "
                "from approval_rule where effective_from <= :o and (effective_to is null or effective_to >= :o) "
                "order by document, min_value, rule_code"
            ),
            {"o": on},
        ).mappings()
    ]


def _one(db: Session, sql: str, **params: Any) -> str | None:
    row = db.execute(text(sql), params).first()
    return str(row[0]) if row and row[0] is not None else None


def _fixed_people(db: Session, role: str, location: str) -> list[tuple[str, str | None]]:
    """(where, person code) for a chain role whose person does not depend on who asks: by designation, the head of a named
    department, or the head of the rule's location (every location for a rule of any location). Empty for the others."""
    row = db.execute(text("select source, value from approver_role where role = :r"), {"r": role}).first()
    if row is None:
        return []
    source, value = row
    if source == "designation":
        return [
            (
                str(value),
                _one(db, "select code from org_user where designation = :d and active order by code limit 1", d=value),
            )
        ]
    if source == "department_head_of":
        return [(f"head of {value}", _one(db, "select head_user from department where code = :c", c=value))]
    if source == "location_head":
        locations = (
            [location]
            if location != "ANY"
            else [str(r[0]) for r in db.execute(text("select code from location order by code"))]
        )
        return [
            (f"head of {loc}", _one(db, "select head_user from location where code = :c", c=loc)) for loc in locations
        ]
    return []


def chain_people(db: Session, people: People, document: str, on: date) -> set[int]:
    """The logins the rules in force for a document name (fixed sources only) who may approve it."""
    may = people.holding(APPROVE.get(document, f"{document}.approve"))
    found: set[int] = set()
    for r in _rules(db, on):
        if r["document"] != document:
            continue
        for role in chain_roles(str(r["approver_chain"]))[1]:
            for _, code in _fixed_people(db, role, r["location_code"]):
                uid = people.login_of.get(code or "")
                if uid in may:
                    found.add(uid)
    return found


def _holders(db: Session, people: People, who: str, on: date) -> set[int]:
    return chain_people(db, people, who.split(":", 1)[1], on) if who.startswith("chain:") else people.holding(who)


# ---- the checks ----------------------------------------------------------------------------------------------------


def _masters(db: Session) -> list[str]:
    problems = []
    n = int(db.execute(text("select count(*) from company")).scalar_one())
    if n != 1:
        problems.append(f"the company record is missing ({n} company rows; one is needed)")
    if not db.execute(text("select 1 from location where is_stock_location limit 1")).first():
        problems.append("no location holds stock")
    if not db.execute(text("select 1 from item limit 1")).first():
        problems.append("no items")
    if not db.execute(text("select 1 from supplier where status = 'active' limit 1")).first():
        problems.append("no active supplier")
    series = {str(r[0]) for r in db.execute(text("select document_type from number_series"))} | set(
        numbering.DEFAULT_SERIES
    )
    problems += [f"no number series for {t.replace('_', ' ')}" for t in DOCUMENT_TYPES if t not in series]
    return problems


def _admins(people: People) -> list[str]:
    problems = []
    for action in ADMIN:
        holders = people.holding(action)
        if len(holders) < 2:
            who = people.names_of(holders) or "nobody"
            problems.append(f"{action}: only {who}; two administrators are needed (one proposes, the other approves)")
    return problems


def _bands(db: Session, on: date) -> list[str]:
    from p2p.platform.setup_api import coverage_problems  # noqa: PLC0415 - the setup screens build on this module

    documents = sorted({str(r["document"]) for r in _rules(db, on)})
    return [p for d in documents for p in coverage_problems(db, d, on)]


def _approvers(db: Session, people: People, on: date) -> list[str]:
    """Every role of a rule in force is defined and names someone who may approve the document."""
    problems: list[str] = []
    defined = {str(r[0]): str(r[1]) for r in db.execute(text("select role, source from approver_role"))}
    for r in _rules(db, on):
        may = people.holding(APPROVE.get(r["document"], f"{r['document']}.approve"))
        for role in chain_roles(str(r["approver_chain"]))[1]:
            if role not in defined:
                problems.append(f"{r['rule_code']}: the role {role} is not defined in who approves")
                continue
            if defined[role] == "department_head":
                for code, head in db.execute(text("select code, head_user from department order by code")):
                    if people.login_of.get(str(head or "")) not in may:
                        problems.append(
                            f"{r['rule_code']}: {role} of {code} is {people.person_name(head)}, who may not approve it"
                        )
                continue
            for where, code in _fixed_people(db, role, r["location_code"]):
                if people.login_of.get(code or "") not in may:
                    problems.append(
                        f"{r['rule_code']}: {role} ({where}) is {people.person_name(code)}, who may not approve it"
                    )
    return list(dict.fromkeys(problems))


def _requesters(db: Session, people: People, on: date) -> list[str]:
    """Requesters for whom a request rule in force leaves nobody else to approve, or whose manager may not approve."""
    problems: list[str] = []
    may_ask = people.holding("request.create")
    may_approve = people.holding("request.approve")
    rules = [r for r in _rules(db, on) if r["document"] == "request" and not str(r["approver_chain"]).startswith("+")]
    managed = {str(r[0]) for r in db.execute(text("select role from approver_role where source = 'requester_manager'"))}
    for code, p in sorted(people.org.items()):
        if people.login_of.get(code) not in may_ask:
            continue
        for r in rules:
            roles = chain_roles(str(r["approver_chain"]))[1]
            if not roles or r["department_code"] not in ("ANY", p["department_code"]):
                continue
            if r["location_code"] not in ("ANY", p["location_code"]):
                continue
            try:
                resolve_chain(
                    db, roles, requester=code, department=p["department_code"], on_date=on, location=p["location_code"]
                )
            except ApprovalRuleError:
                problems.append(f"{p['name']}: a request in {r['rule_code']} has no one else to approve it")
                continue
            manager = p["manager_code"]
            if set(roles) & managed and manager and people.login_of.get(str(manager)) not in may_approve:
                problems.append(
                    f"{p['name']}: their manager {people.person_name(manager)} may not approve requests ({r['rule_code']})"
                )
    return list(dict.fromkeys(problems))


def _non_po_rules(db: Session, on: date) -> list[str]:
    """A category that may be billed without an order needs a `non_po_invoice` approval row: otherwise its bills wait
    for a rule that does not exist (block 10, 10.3.2)."""
    enabled = db.execute(
        text(
            "select count(*) from non_po_rule where enabled and effective_from <= :d "
            "and (effective_to is null or effective_to >= :d)"
        ),
        {"d": on},
    ).scalar_one()
    if not int(enabled):
        return []
    rows = db.execute(
        text(
            "select count(*) from approval_rule where document = 'non_po_invoice' and effective_from <= :d "
            "and (effective_to is null or effective_to >= :d)"
        ),
        {"d": on},
    ).scalar_one()
    if int(rows):
        return []
    return ["a category may be billed without an order but no non_po_invoice approval rule is in force"]


def _separation(db: Session, people: People, on: date) -> list[str]:
    problems: list[str] = []
    for rule in SEPARATION:
        makers = _holders(db, people, rule.makers, on)
        if not makers:
            continue  # not in use
        checkers = _holders(db, people, rule.checkers, on)
        if not checkers:
            problems.append(f"nobody may check {rule.what} ({rule.code})")
            continue
        for maker in sorted(makers, key=people.name):
            if not checkers - {maker}:
                problems.append(
                    f"{people.name(maker)} could not get {rule.what} done ({rule.code}): the only people who may check "
                    f"it are {people.names_of(checkers)}"
                )
    return problems


def _matched(sets: list[set[int]]) -> bool:
    """Whether each set can be given a different person of its own (augmenting paths)."""
    owner: dict[int, int] = {}

    def place(i: int, seen: set[int]) -> bool:
        for uid in sorted(sets[i]):
            if uid in seen:
                continue
            seen.add(uid)
            if uid not in owner or place(owner[uid], seen):
                owner[uid] = i
                return True
        return False

    return all(place(i, set()) for i in range(len(sets)))


def _duties(people: People) -> list[str]:
    """Five different people for the five duties of one case: a matching, not five counts."""
    sets = [people.holding(action) for _, action in DUTIES]
    if _matched(sets):
        return []
    held = "; ".join(f"{duty}: {people.names_of(s) or 'nobody'}" for (duty, _), s in zip(DUTIES, sets, strict=True))
    return [f"no five different people for requester, approver, receiver, invoice approver and payer ({held})"]


def _pending(db: Session) -> list[str]:
    rows = db.execute(
        text(
            "select id, payload->>'area' as area, payload->>'key' as key from proposal where action = :a "
            "and status = 'pending' and (expires_at is null or expires_at > now()) "
            "and coalesce(payload->>'area', '') <> 'company' order by id"  # the go-live itself
        ),
        {"a": setting_changes.ACTION},
    ).all()
    return [
        f"a change to {str(area or '').replace('_', ' ')} {key or ''} waits for approval (proposal {pid})".replace(
            "  ", " "
        )
        for pid, area, key in rows
    ]


def _company_bank(db: Session) -> list[str]:
    found = db.execute(text("select 1 from company_bank_account limit 1")).first()
    return [] if found else ["the company has no bank account to pay from"]


def _supplier_bank(db: Session) -> list[str]:
    problems = [
        f"{code} cannot be paid: no verified bank account"
        for (code,) in db.execute(
            text(
                "select s.code from supplier s where s.status = 'active' and not exists (select 1 from supplier_bank_account b "
                "where b.supplier_code = s.code and b.verified) order by s.code"
            )
        )
    ]
    problems += [
        f"{code} ****{str(account)[-4:]} verified by a file, not by a person (D-01)"
        for code, account in db.execute(
            text(
                "select supplier_code, account_no from supplier_bank_account where verified and verified_by is null "
                "order by supplier_code, account_no"
            )
        )
    ]
    return problems


def _holidays(db: Session, on: date) -> list[str]:
    start = date(on.year if on.month >= 4 else on.year - 1, 4, 1)
    end = date(start.year + 1, 3, 31)
    found = db.execute(
        text("select 1 from ref_holiday where holiday_date between :a and :b limit 1"), {"a": start, "b": end}
    ).first()
    return [] if found else [f"no holidays for the financial year {start.year}-{(start.year + 1) % 100:02d}"]


def _cutover_import(db: Session) -> list[str]:
    row = db.execute(
        text("select id, rejected, control_ok from cutover_import where not dry_run order by id desc limit 1")
    ).first()
    if row is None:
        return ["the open documents have not been loaded yet"]
    problems = []
    if int(row[1]):
        problems.append(f"the last load left out {count(int(row[1]), 'row')}: correct them and load the files again")
    if not row[2]:
        problems.append("the last load does not match the old system's control totals")
    return problems


def _stock_position(db: Session) -> list[str]:
    opening = db.execute(text("select 1 from stock_opening limit 1")).first()
    imported = db.execute(text("select 1 from audit_event where action = 'stock.import' limit 1")).first()
    return [] if opening or imported else ["no opening stock and no stock history loaded"]


def check(db: Session, slug: str, *, group: str = "all", on: date | None = None) -> Readiness:
    """Every check of the groups asked (all, setup or cutover)."""
    if group not in GROUPS:
        raise ValueError(f"group must be one of {', '.join(GROUPS)}")
    from p2p.platform import company_state  # noqa: PLC0415 - company_state builds on what this module builds on

    on = on or business_today()
    people = People(db, slug)
    plan: list[tuple[str, str, str, str, Any, str]] = [
        ("masters", "setup", "Master data loaded", "blocker", lambda: _masters(db), "/admin/setup"),
        ("admins", "setup", "Two administrators", "blocker", lambda: _admins(people), "/admin/setup?tab=people"),
        ("approval_bands", "setup", "Approval bands cover every value", "blocker", lambda: _bands(db, on), "/admin/setup?tab=approvals"),
        ("approvers", "setup", "Every approval role names someone who may approve", "blocker",
         lambda: _approvers(db, people, on), "/admin/setup?tab=approvals"),
        ("approvers", "setup", "Every requester has someone else to approve their requests", "warning",
         lambda: _requesters(db, people, on), "/admin/setup?tab=approvals"),
        ("non_po_rules", "setup", "Bills without an order have an approval rule", "warning",
         lambda: _non_po_rules(db, on), "/admin/buying?tab=non-po"),
        ("separation", "setup", "Every two-person control has a second person", "blocker",
         lambda: _separation(db, people, on), "/admin/setup?tab=roles"),
        ("duties", "setup", "Five different people for the five duties of a case", "blocker", lambda: _duties(people),
         "/admin/setup?tab=roles"),
        ("pending_changes", "setup", "No control change waiting for approval", "blocker", lambda: _pending(db), "/admin/setup"),
        ("company_bank", "setup", "The company's bank account", "blocker", lambda: _company_bank(db), "/admin/setup"),
        ("supplier_bank", "setup", "Suppliers can be paid", "warning", lambda: _supplier_bank(db), ""),
        ("holidays", "setup", "Holidays of this financial year", "warning", lambda: _holidays(db, on), ""),
        ("cutover_import", "cutover", "Open documents loaded and matching the control totals", "blocker",
         lambda: _cutover_import(db), "/admin/setup?tab=golive"),
        ("stock_position", "cutover", "Stock position loaded", "warning", lambda: _stock_position(db), ""),
    ]  # fmt: skip
    checks = []
    for code, grp, title, severity, run, link in plan:
        if group not in ("all", grp):
            continue
        problems = run()
        checks.append(Check(code, grp, title, severity, not problems, problems, link))
    ready = not any(not c.ok and c.severity == "blocker" for c in checks)
    return Readiness(company_state.state_of(slug), datetime.now(UTC), checks, ready)
