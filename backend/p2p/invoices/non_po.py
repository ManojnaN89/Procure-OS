"""Invoices without a purchase order: the rule in force, the authoriser, and the two decisions on a bill.

A clean bill waits for the cost centre's owner to say the supply was received and belongs there (the authorisation), then
for the people the `non_po_invoice` rule names (the approval by amount). Neither the person who entered it nor the
authoriser can approve it; a deputy in force may act for either. Every decision writes its audit row, and the last one
books the invoice's due date. The words a person reads are fixed here so the emails and the screens say the same thing.
"""

from collections.abc import Mapping
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from p2p.approvals import budget, rules
from p2p.approvals.workflow import name_of, org_code_of
from p2p.formulas import non_po
from p2p.formulas.calendar import business_today
from p2p.formulas.money import format_inr
from p2p.invoices.service import InvoiceError
from p2p.platform import audit, notifications, reference, tasks
from p2p.platform.sod import Actor, user

SLA_HOURS = 18
AUTHORISE = "invoice_authorise"
APPROVE = "invoice_approve"
OPEN = ("waiting", "pending")


def _money(value: Any) -> str:
    return f"Rs {format_inr(value, symbol=False)}"


def _link(no: str) -> str:
    from p2p.config import get_settings  # noqa: PLC0415 - only emails need the public address

    return f"{get_settings().public_base_url.rstrip('/')}/#/invoices/{no}"


# ---- the rule and its categories -------------------------------------------------------------------


def _rule(row: Any) -> non_po.Rule:
    return non_po.Rule(
        int(row["id"]),
        str(row["category_code"]),
        str(row["category_name"]),
        int(row["version"]),
        Decimal(row["max_value"]),
        row["supporting_doc"],
        row["default_gl"],
        row["stocked"] in (True, "true"),
    )


def _rule_sql(where: str) -> str:
    return (
        "select r.*, c.name as category_name, c.default_gl, c.stocked from non_po_rule r "  # noqa: S608 - fixed fragments only
        "join item_category c on c.code = r.category_code "
        f"where {where} "
        "order by r.category_code, r.version desc"
    )


def rule_in_force(db: Session, category: str, on: date) -> non_po.Rule | None:
    """The enabled rule in force for a category on a date: the latest version that started, still open."""
    row = (
        db.execute(
            text(
                _rule_sql(
                    "r.category_code = :c and r.enabled and r.effective_from <= :d "
                    "and (r.effective_to is null or r.effective_to >= :d)"
                )
            ),
            {"c": category, "d": on},
        )
        .mappings()
        .first()
    )
    return _rule(row) if row else None


def rules_in_force(db: Session, on: date) -> list[non_po.Rule]:
    """Every enabled rule in force on a date, the latest version of each category."""
    rows = (
        db.execute(
            text(
                _rule_sql("r.enabled and r.effective_from <= :d and (r.effective_to is null or r.effective_to >= :d)")
            ),
            {"d": on},
        )
        .mappings()
        .all()
    )
    seen: dict[str, non_po.Rule] = {}
    for row in rows:
        seen.setdefault(str(row["category_code"]), _rule(row))
    return list(seen.values())


def approved_categories(db: Session, supplier: str, on: date) -> list[non_po.Rule]:
    """The categories a supplier may bill without an order: a rule in force and the supplier's approval for it."""
    approved = {
        str(r[0])
        for r in db.execute(
            text("select category_code from supplier_category where supplier_code = :s"), {"s": supplier}
        )
    }
    return [r for r in rules_in_force(db, on) if r.category_code in approved]


def _cost_centre(db: Session, code: str | None) -> dict[str, Any] | None:
    if not code:
        return None
    row = (
        db.execute(
            text(
                "select c.code, c.name, c.department_code, c.location_code, c.owner_user, c.active, "
                "d.head_user from cost_centre c left join department d on d.code = c.department_code "
                "where c.code = :c"
            ),
            {"c": code},
        )
        .mappings()
        .first()
    )
    return dict(row) if row else None


def last_cost_centre(db: Session, supplier: str, category: str) -> str | None:
    """The cost centre of the supplier's last bill in a category (the draft's proposal, shown as an assumption)."""
    return db.execute(
        text(
            "select cost_centre_code from invoice where supplier_code = :s and category_code = :c "
            "and kind = 'non_po' and status <> 'cancelled' order by created_at desc limit 1"
        ),
        {"s": supplier, "c": category},
    ).scalar()


# ---- the authoriser ---------------------------------------------------------------------------------


def authoriser_for(db: Session, cost_centre: dict[str, Any], entered_by: str | None) -> str:
    """The cost centre's owner, else the head of its department, else the owner's manager: the first who is not the
    person who entered the bill (OP-3)."""
    candidates: list[str] = []
    if cost_centre.get("owner_user"):
        candidates.append(str(cost_centre["owner_user"]))
    if cost_centre.get("head_user"):
        candidates.append(str(cost_centre["head_user"]))
    if cost_centre.get("owner_user"):
        manager = db.execute(
            text("select manager_code from org_user where code = :c"), {"c": cost_centre["owner_user"]}
        ).scalar()
        if manager:
            candidates.append(str(manager))
    for code in candidates:
        if code != entered_by:
            return code
    raise InvoiceError("nobody other than the person who entered this bill may authorise it: name a cost centre owner")


def deputy_of(db: Session, person: str, on: date) -> str | None:
    return rules.deputy_on(db, person, on)


def _login(db: Session, tenant: str, org: str | None) -> int | None:
    return notifications.login_of(db, tenant, org)


def _email(db: Session, tenant: str, org: str | None, subject: str, body: str) -> None:
    login = _login(db, tenant, org)
    if login is not None:
        notifications.notify(db, tenant, login, "invoice", subject, body)


def _task(
    db: Session,
    tenant: str,
    *,
    kind: str,
    person: str,
    title: str,
    no: str,
    location: str | None,
    now: datetime | None,
) -> int | None:
    login, deputy = _login(db, tenant, person), None
    boss = db.execute(text("select manager_code from org_user where code = :c"), {"c": person}).scalar()
    chain = [_login(db, tenant, str(boss))] if boss else []
    if login is None:
        return None
    return tasks.create_task(
        db,
        tenant,
        kind=kind,
        title=title,
        assignee=login,
        sla_hours=SLA_HOURS,
        escalation_chain=[x for x in chain if x is not None] or None,
        payload={"invoice_no": no, "calendar_location": location, "person": person, "deputy": deputy},
        now=now,
        calendar=reference.calendar_for_location(db, location),
        email=False,  # the bill's own email below carries the words
    )


# ---- entering: from a clean bill to its authoriser --------------------------------------------------


def to_authorise(db: Session, tenant: str, no: str, *, now: datetime | None = None) -> str:
    """A clean bill waits for its authoriser: the person, the task and the email. Returns the authoriser's code."""
    inv = db.execute(text("select * from invoice where no = :n for update"), {"n": no}).mappings().one()
    cc = _cost_centre(db, inv["cost_centre_code"])
    if cc is None or not cc["active"]:
        raise InvoiceError("a bill without an order needs an active cost centre")
    entered = _entered_code(db, dict(inv))
    authoriser = authoriser_for(db, cc, entered)
    db.execute(
        text("update invoice set status = 'awaiting_authorisation', authoriser = :a where no = :n"),
        {"a": authoriser, "n": no},
    )
    deputy = deputy_of(db, authoriser, business_today())
    who = deputy or authoriser
    supplier = db.execute(
        text("select coalesce(trade_name, legal_name) from supplier where code = :c"), {"c": inv["supplier_code"]}
    ).scalar()
    category = db.execute(text("select name from item_category where code = :c"), {"c": inv["category_code"]}).scalar()
    title = f"Authorise {supplier}'s bill {inv['supplier_invoice_no']} for {_money(inv['total'])} ({category}, {cc['name']})"
    _task(db, tenant, kind=AUTHORISE, person=who, title=title, no=no, location=cc["location_code"], now=now)
    for person in dict.fromkeys([who, authoriser]):
        _email(
            db,
            tenant,
            person,
            f"Bill to authorise: {supplier} {inv['supplier_invoice_no']}",
            f"{supplier} has billed {_money(inv['total'])} for {category} on {cc['name']} without a purchase order "
            f"({inv['supplier_invoice_no']} of {inv['invoice_date']:%d %b %Y}). Please confirm it was received and belongs "
            f"to you, or reject it: {_link(no)}",
        )
    audit.record(
        db,
        user(int(inv["entered_by"]) if inv["entered_by"] else 0),
        "invoice.to_authorise",
        "invoice",
        no,
        after={"authoriser": authoriser, "deputy": deputy, "rule": inv["non_po_rule_id"]},
    )
    db.commit()
    return authoriser


def _entered_code(db: Session, inv: Mapping[str, Any]) -> str | None:
    """The person behind the login that entered the bill, as an org code."""
    entered = inv.get("entered_by")
    if not entered:
        return None
    if str(entered).isdigit():
        return org_code_of(db, int(entered))
    return str(entered)


# ---- the decisions ---------------------------------------------------------------------------------


def _locked(db: Session, no: str) -> dict[str, Any]:
    row = db.execute(text("select * from invoice where no = :n for update"), {"n": no}).mappings().first()
    if row is None:
        raise InvoiceError(f"{no} does not exist")
    return dict(row)


def _me(db: Session, user_id: int) -> str:
    code = org_code_of(db, user_id)
    if code is None:
        raise InvoiceError("this login is not linked to a person in the organisation")
    return code


def _refuse_segregation(*, me: str, entered: str | None, authoriser: str | None) -> None:
    if me == entered or me == authoriser:
        raise InvoiceError("segregation of duties: the person who entered or authorised the bill cannot decide it")


def _may(ctx: str | None, approver: str, delegate: str | None, dep: str | None) -> bool:
    return ctx is not None and ctx in {approver, delegate, dep}


def _over(db: Session, no: str, today: date) -> tuple[list[budget.GroupCheck], bool]:
    checks = budget.check_invoice(db, no, today)
    return checks, budget.blocked(checks)


def _over_words(checks: list[budget.GroupCheck]) -> str:
    for c in checks:
        if c.check.status == "over" and c.policy == "block":
            return f"over the budget of cost centre {', '.join(c.cost_centres) or c.location} for {c.gl_code} by {_money(c.check.over_by)}: give a reason to approve it"
    return "over the budget: give a reason to approve it"


def _reason_for_budget(db: Session, actor: Actor, no: str, checks: list[budget.GroupCheck], reason: str | None) -> None:
    given = (reason or "").strip()
    if not given:
        raise InvoiceError(_over_words(checks))
    db.execute(text("update invoice set over_budget_reason = :r where no = :n"), {"r": given, "n": no})
    audit.record(
        db, actor, "invoice.approve_over_budget", "invoice", no,
        after={"reason": given, "checks": [c.as_json() for c in checks]},
    )  # fmt: skip


def _open_step(db: Session, tenant: str, inv: dict[str, Any], step_no: int, *, now: datetime | None) -> None:
    """The current step: pending, its task and its email; the ones after it wait."""
    step = (
        db.execute(
            text("select * from invoice_approval_step where invoice_no = :n and step = :s"),
            {"n": inv["no"], "s": step_no},
        )
        .mappings()
        .one()
    )
    db.execute(
        text("update invoice_approval_step set status = 'pending' where invoice_no = :n and step = :s"),
        {"n": inv["no"], "s": step_no},
    )
    cc = _cost_centre(db, inv["cost_centre_code"])
    location = cc["location_code"] if cc else None
    who = str(step["delegate"] or step["approver"])
    supplier = db.execute(
        text("select coalesce(trade_name, legal_name) from supplier where code = :c"), {"c": inv["supplier_code"]}
    ).scalar()
    category = db.execute(text("select name from item_category where code = :c"), {"c": inv["category_code"]}).scalar()
    title = f"Approve {supplier}'s bill {inv['supplier_invoice_no']} for {_money(inv['total'])}"
    task_id = _task(db, tenant, kind=APPROVE, person=who, title=title, no=inv["no"], location=location, now=now)
    if task_id is not None:
        db.execute(
            text("update invoice_approval_step set task_id = :t where invoice_no = :n and step = :s"),
            {"t": task_id, "n": inv["no"], "s": step_no},
        )
    checks, _ = _over(db, inv["no"], business_today())
    _email(
        db,
        tenant,
        who,
        f"Bill to approve: {supplier} {inv['supplier_invoice_no']}",
        f"{_money(inv['total'])} for {category} on {cc['name'] if cc else inv['cost_centre_code']}, authorised by "
        f"{name_of(db, inv['authorised_by'])} on {inv['authorised_on']:%d %b %Y}. Budget: {budget.summary(checks)} "
        f"{_link(inv['no'])}",
    )
    audit.record(
        db, audit.SYSTEM, "invoice.approve_step", "invoice", inv["no"],
        after={"step": step_no, "approver": step["approver"], "opened": True},
    )  # fmt: skip


def authorise(
    db: Session,
    tenant: str,
    no: str,
    user_id: int,
    *,
    note: str | None = None,
    over_budget_reason: str | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """The authoriser confirms the supply was received and belongs to the cost centre; the chain follows."""
    today = business_today()
    inv = _locked(db, no)
    if inv["kind"] != "non_po":
        raise InvoiceError(f"{no} is not a bill without an order")
    if inv["status"] != "awaiting_authorisation":
        raise InvoiceError(f"{no} is {inv['status']}, not waiting for authorisation")
    me = _me(db, user_id)
    dep = deputy_of(db, str(inv["authoriser"] or ""), today)
    if me not in {inv["authoriser"], dep}:
        raise InvoiceError(f"it is not your turn: {no} waits for {name_of(db, inv['authoriser'])}")
    entered_code = _entered_code(db, inv)
    if me == entered_code:
        raise InvoiceError("segregation of duties: the person who entered the bill cannot authorise it")
    cc = _cost_centre(db, inv["cost_centre_code"])
    if cc is None:
        raise InvoiceError("the bill has no cost centre")
    try:
        rule = rules.find_rule(
            db,
            "non_po_invoice",
            Decimal(inv["total"]),
            today,
            category=inv["category_code"],
            department=cc["department_code"],
            location=cc["location_code"],
        )
    except rules.ApprovalRuleError as exc:
        raise InvoiceError(str(exc)) from None
    checks, over = _over(db, no, today)
    actor = user(user_id)
    if over and not rule.roles:
        _reason_for_budget(db, actor, no, checks, over_budget_reason)
    try:
        steps = rules.resolve_chain(
            db,
            rule.roles,
            requester=None,
            department=cc["department_code"],
            on_date=today,
            location=cc["location_code"],
            sources=rule.sources,
            exclude=frozenset(x for x in (str(entered_code or ""), str(inv["authoriser"] or ""), me) if x),
        )
    except rules.ApprovalRuleError as exc:
        raise InvoiceError(str(exc)) from None
    db.execute(
        text(
            "update invoice set authorised_by = :b, authorised_at = :t, authorised_on = :d, "
            "approval_rule_code = :rc, approval_rule_version = :rv where no = :n"
        ),
        {"b": me, "t": now or datetime.now(UTC), "d": today, "rc": rule.code, "rv": rule.version, "n": no},
    )
    audit.record(
        db, actor, "invoice.authorise", "invoice", no,
        after={"authorised_by": me, "on": today, "rule": rule.code, "version": rule.version, "note": note},
    )  # fmt: skip
    for task_id in db.execute(
        text("select id from task where kind = :k and payload->>'invoice_no' = :n and status in ('open', 'overdue')"),
        {"k": AUTHORISE, "n": no},
    ).scalars():
        tasks.complete_task(db, int(task_id), user_id, now=now)
    inv = _locked(db, no)
    if not steps:
        db.execute(
            text("update invoice set status = 'approved', approved_by = :b where no = :n"),
            {"b": str(user_id), "n": no},
        )
        audit.record(
            db, actor, "invoice.approve", "invoice", no,
            after={"approved_by": str(user_id), "rule": rule.code, "auto": True},
        )  # fmt: skip
        from p2p.invoices import payment  # noqa: PLC0415 - payment builds on this module

        payment.book_invoice(db, no)
        db.commit()
        return {
            "status": "approved",
            "approvers": [],
            "rule": {"code": rule.code, "version": rule.version},
            "budget": [c.as_json() for c in checks],
        }
    for n, step in enumerate(steps, start=1):
        db.execute(
            text(
                "insert into invoice_approval_step (invoice_no, step, role, approver, delegate, status, rule_id, "
                "rule_code, rule_version) values (:i, :s, :r, :a, :d, :st, :rid, :rc, :rv)"
            ),
            {
                "i": no, "s": n, "r": step.role, "a": step.approver, "d": step.delegate,
                "st": "waiting", "rid": step.source.rule_id if step.source else None,
                "rc": step.source.code if step.source else rule.code,
                "rv": step.source.version if step.source else rule.version,
            },
        )  # fmt: skip
    db.execute(text("update invoice set status = 'awaiting_approval' where no = :n"), {"n": no})
    db.commit()
    _open_step(db, tenant, _locked(db, no), 1, now=now)
    db.commit()
    return {
        "status": "awaiting_approval",
        "approvers": [{"step": n, "name": name_of(db, s.approver), "role": s.role} for n, s in enumerate(steps, 1)],
        "rule": {"code": rule.code, "version": rule.version},
        "budget": [c.as_json() for c in checks],
    }


def approve(
    db: Session,
    tenant: str,
    no: str,
    user_id: int,
    *,
    note: str | None = None,
    over_budget_reason: str | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """The open step's approver approves; the last step approves the bill and books it."""
    today = business_today()
    inv = _locked(db, no)
    if inv["kind"] != "non_po":
        raise InvoiceError(f"{no} is not a bill without an order")
    if inv["status"] != "awaiting_approval":
        raise InvoiceError(f"{no} is {inv['status']}, not waiting for approval")
    me = _me(db, user_id)
    step = (
        db.execute(
            text(
                "select * from invoice_approval_step where invoice_no = :n and status = 'pending' order by step limit 1"
            ),
            {"n": no},
        )
        .mappings()
        .first()
    )
    if step is None:
        raise InvoiceError(f"{no} has no step waiting for a decision")
    dep = deputy_of(db, str(step["approver"]), today)
    if me not in {step["approver"], step["delegate"], dep}:
        raise InvoiceError(f"it is not your turn: {no} waits for {name_of(db, step['delegate'] or step['approver'])}")
    from p2p.platform import permissions  # noqa: PLC0415 - the screens build on permissions

    if not permissions.can_anywhere(db, user_id, "invoice.approve"):
        raise InvoiceError("this role may not approve a bill")
    _refuse_segregation(
        me=me,
        entered=_entered_code(db, inv),
        authoriser=inv["authorised_by"],
    )
    last = not db.execute(
        text(
            "select 1 from invoice_approval_step where invoice_no = :n and step > :s and status in ('waiting', 'pending')"
        ),
        {"n": no, "s": step["step"]},
    ).first()
    checks, over = _over(db, no, today)
    actor = user(user_id)
    if over and last:
        _reason_for_budget(db, actor, no, checks, over_budget_reason)
    db.execute(
        text(
            "update invoice_approval_step set status = 'approved', acted_by = :b, acted_at = :t, comment = :c "
            "where id = :i"
        ),
        {"b": me, "t": now or datetime.now(UTC), "c": note, "i": step["id"]},
    )
    audit.record(
        db, actor, "invoice.approve_step", "invoice", no,
        after={"step": step["step"], "approver": step["approver"], "acted_by": me, "note": note},
    )  # fmt: skip
    if step["task_id"] is not None:
        tasks.complete_task(db, int(step["task_id"]), user_id, now=now)
    if not last:
        nxt = db.execute(
            text("select min(step) from invoice_approval_step where invoice_no = :n and status = 'waiting'"),
            {"n": no},
        ).scalar()
        if nxt is None:
            raise InvoiceError(f"{no} has no step left to open")
        db.execute(text("update invoice set status = 'awaiting_approval' where no = :n"), {"n": no})
        db.commit()
        _open_step(db, tenant, _locked(db, no), int(nxt), now=now)
        db.commit()
        return {"status": "awaiting_approval", "next": name_of(db, _step_person(db, no, int(nxt)))}
    db.execute(
        text("update invoice set status = 'approved', approved_by = :b where no = :n"), {"b": str(user_id), "n": no}
    )
    audit.record(db, actor, "invoice.approve", "invoice", no, after={"approved_by": str(user_id)})
    from p2p.invoices import payment  # noqa: PLC0415 - payment builds on this module

    payment.book_invoice(db, no)
    db.commit()
    return {"status": "approved", "next": None}


def _step_person(db: Session, no: str, step: int) -> str:
    row = db.execute(
        text("select approver, delegate from invoice_approval_step where invoice_no = :n and step = :s"),
        {"n": no, "s": step},
    ).first()
    return str(row[1] or row[0]) if row else "someone"


def reject(db: Session, tenant: str, no: str, user_id: int, reason: str, *, now: datetime | None = None) -> None:
    """The authoriser rejects before authorising, or the open approver rejects: the bill is cancelled and AP told."""
    if not (reason or "").strip():
        raise InvoiceError("a reason is needed")
    today = business_today()
    inv = _locked(db, no)
    me = _me(db, user_id)
    if inv["status"] == "awaiting_authorisation":
        dep = deputy_of(db, str(inv["authoriser"] or ""), today)
        if me not in {inv["authoriser"], dep}:
            raise InvoiceError(f"it is not your turn: {no} waits for {name_of(db, inv['authoriser'])}")
    elif inv["status"] == "awaiting_approval":
        step = db.execute(
            text(
                "select approver, delegate from invoice_approval_step where invoice_no = :n and status = 'pending' limit 1"
            ),
            {"n": no},
        ).first()
        if step is None:
            raise InvoiceError(f"{no} has no step waiting for a decision")
        dep = deputy_of(db, str(step[0]), today)
        if me not in {step[0], step[1], dep}:
            raise InvoiceError(f"it is not your turn: {no} waits for {name_of(db, step[1] or step[0])}")
    else:
        raise InvoiceError(f"{no} is {inv['status']}, not waiting for a decision")
    words = f"Rejected by {name_of(db, me)}: {reason.strip()}"
    db.execute(text("update invoice set status = 'cancelled', cancel_reason = :r where no = :n"), {"r": words, "n": no})
    db.execute(
        text(
            "update invoice_approval_step set status = 'cancelled' where invoice_no = :n and status in ('waiting', 'pending')"
        ),
        {"n": no},
    )
    for task_id in db.execute(
        text(
            "select id from task where payload->>'invoice_no' = :n and kind = any(:k) and status in ('open', 'overdue')"
        ),
        {"n": no, "k": [AUTHORISE, APPROVE]},
    ).scalars():
        tasks.cancel_task(db, int(task_id), user_id, reason="the bill was rejected")
    audit.record(
        db, user(user_id), "invoice.reject", "invoice", no,
        before={"status": inv["status"]}, after={"status": "cancelled", "reason": words},
    )  # fmt: skip
    db.commit()
    entered = inv["entered_by"]
    if entered and str(entered).isdigit():
        supplier = db.execute(
            text("select coalesce(trade_name, legal_name) from supplier where code = :c"), {"c": inv["supplier_code"]}
        ).scalar()
        login = int(entered)
        notifications.notify(
            db,
            tenant,
            login,
            "invoice",
            f"Bill {inv['supplier_invoice_no']} rejected",
            f"{name_of(db, me)} rejected {supplier}'s bill {inv['supplier_invoice_no']}: {reason.strip()}. "
            "It is cancelled; enter it again if the supplier corrects it.",
        )


# ---- what a screen shows ---------------------------------------------------------------------------


def steps(db: Session, no: str) -> list[dict[str, Any]]:
    return [
        {
            "step": r["step"],
            "role": r["role"],
            "name": name_of(db, r["approver"]),
            "delegate": name_of(db, r["delegate"]) if r["delegate"] else None,
            "status": r["status"],
            "acted_by": name_of(db, r["acted_by"]) if r["acted_by"] else None,
            "acted_at": r["acted_at"].isoformat() if r["acted_at"] else None,
            "comment": r["comment"],
            "rule": {"code": r["rule_code"], "version": r["rule_version"]},
        }
        for r in db.execute(
            text("select * from invoice_approval_step where invoice_no = :n order by step"), {"n": no}
        ).mappings()
    ]


def may(db: Session, no: str, user_id: int) -> tuple[bool, bool]:
    """(can_authorise, can_approve) for this login and this bill, by who they are and what their roles grant."""
    from p2p.platform import permissions  # noqa: PLC0415 - the screens build on permissions

    inv = db.execute(text("select * from invoice where no = :n"), {"n": no}).mappings().first()
    if inv is None or inv["kind"] != "non_po":
        return False, False
    me = org_code_of(db, user_id)
    today = business_today()
    can_authorise = False
    if inv["status"] == "awaiting_authorisation" and me:
        dep = deputy_of(db, str(inv["authoriser"] or ""), today)
        can_authorise = me in {inv["authoriser"], dep} and me != _entered_code(db, dict(inv))
    can_approve = False
    if inv["status"] == "awaiting_approval" and me and permissions.can_anywhere(db, user_id, "invoice.approve"):
        step = db.execute(
            text(
                "select approver, delegate from invoice_approval_step where invoice_no = :n and status = 'pending' limit 1"
            ),
            {"n": no},
        ).first()
        if step is not None:
            dep = deputy_of(db, str(step[0]), today)
            can_approve = (
                me in {step[0], step[1], dep} and me != _entered_code(db, dict(inv)) and me != inv["authorised_by"]
            )
    return can_authorise, can_approve
