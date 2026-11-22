"""The life of a request through approval: submit, approve, reject, return.

Deterministic. The rule table decides whether a request approves itself or who has to approve it; this module only
carries that out, one step at a time, and writes an audit row for every change. Rules read: approvals/rules.py.
"""

import json
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from p2p.approvals import budget, rules
from p2p.formulas.calendar import business_today
from p2p.formulas.money import format_inr
from p2p.platform import audit, notifications, permissions, tasks, users, worker
from p2p.platform.sod import Actor


class ApprovalError(Exception):
    """The action is not allowed now (wrong status, not this person's turn, missing reason...)."""


@dataclass
class Submitted:
    request_no: str
    status: str  # approved (automatically) or submitted (waiting for people)
    rule_code: str
    rule_version: int
    reason: str
    approvers: list[str]  # person codes, in order


# ---- who is acting ----------------------------------------------------------------------------


def org_code_of(db: Session, user_id: int) -> str | None:
    """The person in the organisation behind a login, found by email."""
    email = notifications.email_of(user_id)
    if not email:
        return None
    row = db.execute(text("select code from org_user where lower(email) = :e and active"), {"e": email.lower()}).first()
    return str(row[0]) if row else None


def name_of(db: Session, code: str | None) -> str:
    if not code:
        return "someone"
    row = db.execute(text("select name from org_user where code = :c"), {"c": code}).first()
    return str(row[0]) if row else code


def notify_person(
    db: Session, tenant: str | None, code: str | None, kind: str, title: str, body: str = "", *, link: str | None = None
) -> bool:
    """Tell a person of the organisation (in the app and by email) if they have a login. False if nobody was told."""
    if not tenant or not code:
        return False
    email = db.execute(text("select email from org_user where code = :c and active"), {"c": code}).scalar()
    user_id = users.user_id_by_email(tenant, str(email)) if email else None
    if user_id is None:
        return False
    notifications.notify(db, tenant, user_id, kind, title, body, link=link)
    return True


def _tell_requester(db: Session, tenant: str | None, req: dict[str, Any], title: str, body: str) -> None:
    """The requester hears how their request went; an automatic restock request has no requester and tells nobody."""
    notify_person(db, tenant, req["requester"], "request", title, body, link=f"#/requisitions/{req['no']}")


def _locked(db: Session, no: str) -> dict[str, Any]:
    row = db.execute(text("select * from request where no = :n for update"), {"n": no}).mappings().first()
    if row is None:
        raise ApprovalError(f"request {no} does not exist")
    return dict(row)


def _requires_budget(db: Session) -> bool:
    """Whether the company wants auto-approval only when the budget allows it (the default)."""
    value = db.execute(text("select value from org_setting where key = 'auto_approve_requires_budget'")).scalar()
    return value != "false"


# ---- submit -----------------------------------------------------------------------------------


def submit(db: Session, tenant: str, no: str, actor: Actor, *, today: date | None = None) -> Submitted:
    """Send a draft (or a returned request) for approval, or approve it on the spot when the rule says so."""
    today = today or business_today()
    req = _locked(db, no)
    if req["status"] not in ("draft", "returned"):
        raise ApprovalError(f"request {no} is {req['status']}; only a draft or a returned request can be submitted")
    categories = {
        r[0]
        for r in db.execute(
            text("select category_code from request_line where request_no = :n and status <> 'cancelled'"), {"n": no}
        )
    }
    category = next(iter(categories)) if len(categories) == 1 else None
    spend = "capex" if req["type"] == "capex" else "opex"
    value = Decimal(req["total_value"])

    where: dict[str, Any] = {
        "spend_type": spend,
        "category": category,
        "department": req["department_code"],
        "location": req["ship_to_location"],
    }
    rule = rules.find_rule(db, "request", value, today, **where)
    checks = budget.check_request(db, no, today)
    over = [c for c in checks if c.check.status == "over"]
    if budget.blocked(checks):
        raise ApprovalError(f"request {no} cannot be submitted: {budget.summary(checks)} The budget policy blocks it.")
    budget_note = budget.summary(checks)
    noted = over or any(c.check.status == "warn" for c in checks)
    if over and rule.is_automatic and _requires_budget(db):
        # a request that is over budget never approves itself: it goes to the first rule above the automatic band
        while rule.is_automatic:
            rule = rules.find_rule(db, "request", rule.max_value + Decimal("0.01"), today, **where)
        budget_note += " It cannot approve itself, so it goes to a person."
    db.execute(
        text("update request set budget_check = cast(:b as jsonb) where no = :n"),
        {"b": json.dumps([c.as_json() for c in checks]), "n": no},
    )
    steps = rules.resolve_chain(
        db,
        rule.roles,
        requester=req["requester"],
        department=req["department_code"],
        on_date=today,
        location=req["ship_to_location"],
        sources=rule.sources,
    )
    round_no = int(req["approval_round"]) + 1
    audit.record(
        db,
        actor,
        "request.submit",
        "request",
        no,
        before={"status": req["status"]},
        after={
            "rule": rule.code,
            "version": rule.version,
            "approvers": [s.approver for s in steps],
            "rows": sorted({s.source.rule_id for s in steps if s.source}),
            "budget": budget_note,
        },
        rule_version=f"{rule.code}:v{rule.version}",
    )
    common = {"n": no, "rc": rule.code, "rv": rule.version, "round": round_no, "now": datetime.now(UTC)}

    if rule.is_automatic:
        reason = (
            f"Auto-approved under rule {rule.code} v{rule.version}: {format_inr(value)} is within the automatic limit "
            f"of {format_inr(rule.max_value)}. {budget_note}"
        )
        db.execute(
            text(
                "update request set status = 'approved', rule_code = :rc, rule_version = :rv, decision_reason = :why, "
                "submitted_at = :now, decided_at = :now, approval_round = :round where no = :n"
            ),
            {**common, "why": reason},
        )
        audit.record(
            db,
            Actor("rule", rule.code),
            "request.auto_approve",
            "request",
            no,
            before={"status": req["status"]},
            after={"status": "approved"},
            reason=reason,
            rule_version=f"{rule.code}:v{rule.version}",
        )
        db.commit()
        worker.enqueue(tenant, "source_request", {"request_no": no})
        queue_forward_buy(db, tenant, no)
        _tell_requester(
            db,
            tenant,
            req,
            f"Request {no} approved",
            "It was within the automatic limit, so it was approved at once. The buyer takes it from here.",
        )
        return Submitted(no, "approved", rule.code, rule.version, reason, [])

    for n, step in enumerate(steps, start=1):
        db.execute(
            text(
                "insert into approval_step (request_no, round, step, role, approver, delegate, status, rule_id, rule_code, "
                "rule_version, route_reason) values (:no, :round, :step, :role, :who, :dele, :status, :rid, :rc, :rv, :why)"
            ),
            {
                "no": no,
                "round": round_no,
                "step": n,
                "role": step.role,
                "who": step.approver,
                "dele": step.delegate,
                "status": "pending" if n == 1 else "waiting",
                "rid": step.source.rule_id if step.source else None,
                "rc": step.source.code if step.source else rule.code,
                "rv": step.source.version if step.source else rule.version,
                "why": step.source.why if step.source else None,
            },
        )
    names = " then ".join(name_of(db, s.approver) for s in steps)
    db.execute(
        text(
            "update request set status = 'submitted', rule_code = :rc, rule_version = :rv, submitted_at = :now, "
            "decided_at = null, decision_reason = :why, approval_round = :round where no = :n"
        ),
        {
            **common,
            "why": f"Waiting for approval by {names} under rule {rule.code} v{rule.version}."
            + (f" {budget_note}" if noted else ""),
        },
    )
    db.commit()
    queue_forward_buy(db, tenant, no)
    _open_step(db, tenant, no, req["total_value"], 1, round_no)
    return Submitted(
        no, "submitted", rule.code, rule.version, f"Needs approval by {names}", [s.approver for s in steps]
    )


def queue_forward_buy(db: Session, tenant: str | None, no: str) -> None:
    """A request with stocked items was submitted: each item and place is evaluated for forward buying (task 13.5),
    once however many requests ask for it before the worker gets to it."""
    if not tenant:
        return
    for item, place in db.execute(
        text(
            "select distinct l.item_code, coalesce(l.delivery_location, r.ship_to_location) from request_line l "
            "join request r on r.no = l.request_no join item i on i.code = l.item_code where l.request_no = :n "
            "and i.stocked and l.status <> 'cancelled'"
        ),
        {"n": no},
    ):
        worker.enqueue_unique(
            tenant, "forward_buy_item", {"item": item, "location": place}, datetime.now(UTC), same_payload=True
        )


def _open_step(db: Session, tenant: str, no: str, value: Decimal, step: int, round_no: int) -> None:
    """Hand the step to the person whose turn it is: the approval email with its links, a task that escalates after
    `approval_escalation_days` working days, and one reminder after `approval_reminder_days` (approvals/chasing.py)."""
    from p2p.approvals import approval_email, chasing  # noqa: PLC0415 - both use this module

    row = (
        db.execute(
            text(
                "select s.approver, s.delegate, r.requester from approval_step s join request r on r.no = s.request_no "
                "where s.request_no = :n and s.round = :r and s.step = :s"
            ),
            {"n": no, "r": round_no, "s": step},
        )
        .mappings()
        .one()
    )
    delegate = row["delegate"]
    if delegate is None:  # a deputy named after the request was submitted takes the step too
        deputy = rules.deputy_on(db, row["approver"], business_today())
        delegate = deputy if deputy != row["requester"] else None
    now = datetime.now(UTC)
    db.execute(
        text(
            "update approval_step set delegate = :d, opened_at = :t where request_no = :n and round = :r and step = :s"
        ),
        {"d": delegate, "t": now, "n": no, "r": round_no, "s": step},
    )
    if delegate and row["delegate"] is None:
        audit.record(
            db, audit.SYSTEM, "request.deputy", "request", no,
            after={"step": step, "approver": row["approver"], "deputy": delegate}, reason="the approver named a deputy",
        )  # fmt: skip
    db.commit()
    who = delegate or row["approver"]
    user_id = notifications.login_of(db, tenant, who)
    if user_id is None:
        return  # no login yet: the step waits, and shows on the request page
    boss = chasing.escalation_target(db, tenant, no, round_no, step, who)
    task_id = tasks.create_task(
        db,
        tenant,
        kind="approval",
        title=f"Approve request {no} ({format_inr(value)})",
        assignee=user_id,
        sla_hours=chasing.escalation_days(db) * chasing.calendar(db).hours_per_day,
        escalation_chain=[boss] if boss else None,
        payload={"request_no": no, "step": step},
        email=False,  # the approval email below carries the request and the links
        now=now,
    )
    db.execute(
        text("update approval_step set task_id = :t where request_no = :n and round = :r and step = :s"),
        {"t": task_id, "n": no, "r": round_no, "s": step},
    )
    db.commit()
    approval_email.send(db, tenant, no, round_no, step, user_id)
    chasing.queue_reminder(db, tenant, no, round_no, step, now)


# ---- acting on a step -------------------------------------------------------------------------


def _acting(db: Session, no: str, user_id: int) -> tuple[dict[str, Any], dict[str, Any], str]:
    """The locked request, the step waiting for this person, and their code. Raises if it is not their turn."""
    req = _locked(db, no)
    if req["status"] != "submitted":
        raise ApprovalError(f"request {no} is {req['status']}, not waiting for approval")
    step = (
        db.execute(
            text(
                "select * from approval_step where request_no = :n and round = :r and status = 'pending' order by step limit 1"
            ),
            {"n": no, "r": req["approval_round"]},
        )
        .mappings()
        .first()
    )
    if step is None:
        raise ApprovalError(f"request {no} has no step waiting for a decision")
    me = org_code_of(db, user_id)
    if me is None:
        raise ApprovalError("this login is not linked to a person in the organisation, so it cannot approve")
    if me not in (step["approver"], step["delegate"]):
        raise ApprovalError(
            f"it is not your turn: {no} is waiting for {name_of(db, step['delegate'] or step['approver'])}"
        )
    if me == req["requester"]:
        raise ApprovalError("segregation of duties: the requester cannot approve their own request")
    if not permissions.can_anywhere(db, user_id, "request.approve"):
        raise permissions.PermissionDenied("this role may not approve requests")
    return req, dict(step), me


def _lower_quantities(
    db: Session, req: dict[str, Any], quantities: dict[int, Decimal], actor: Actor, reason: str | None
) -> None:
    """The approver approves less than was asked: a quantity can only go down (a higher one would need the rules asked again),
    never to nothing (that is a rejection), and the reason is kept. The request's value follows."""
    if not (reason or "").strip():
        raise ApprovalError("a reason is needed when you change a quantity")
    no = req["no"]
    changed: list[dict[str, Any]] = []
    for line_no, wanted in sorted(quantities.items()):
        line = (
            db.execute(
                text(
                    "select qty, base_qty from request_line where request_no = :n and line_no = :l and status <> 'cancelled' for update"
                ),
                {"n": no, "l": line_no},
            )
            .mappings()
            .first()
        )
        if line is None:
            raise ApprovalError(f"{no} has no line {line_no}")
        old, new = Decimal(line["qty"]), Decimal(wanted).quantize(Decimal("0.0001"))
        if new <= 0:
            raise ApprovalError(f"line {line_no}: a quantity must be more than zero (reject the request to refuse it)")
        if new > old:
            raise ApprovalError(f"line {line_no}: an approver can lower a quantity, not raise it ({old} asked)")
        if new == old:
            continue
        base = None if line["base_qty"] is None else (Decimal(line["base_qty"]) * new / old).quantize(Decimal("0.0001"))
        db.execute(
            text("update request_line set qty = :q, base_qty = :b where request_no = :n and line_no = :l"),
            {"q": new, "b": base, "n": no, "l": line_no},
        )
        changed.append({"line": line_no, "from": str(old), "to": str(new)})
    if not changed:
        return
    total = db.execute(
        text(
            "update request set total_value = (select coalesce(round(sum(qty * coalesce(est_price, 0)), 2), 0) from request_line "
            "where request_no = :n and status <> 'cancelled') where no = :n returning total_value"
        ),
        {"n": no},
    ).scalar_one()
    audit.record(
        db, actor, "request.quantity_edit", "request", no,
        before={"total_value": str(req["total_value"])}, after={"lines": changed, "total_value": str(total)}, reason=reason,
    )  # fmt: skip
    req["total_value"] = total


def approve(
    db: Session,
    tenant: str,
    no: str,
    user_id: int,
    comment: str | None = None,
    *,
    quantities: dict[int, Decimal] | None = None,
    via: str | None = None,
) -> str:
    """Approve the current step, optionally with lower quantities (a reason is then needed). `via` says how the decision
    came in when it was not the screen (an emailed link). Returns the request's status afterwards (submitted if more steps
    follow, else approved)."""
    req, step, me = _acting(db, no, user_id)
    actor = Actor("user", str(user_id))
    if quantities:
        _lower_quantities(db, req, quantities, actor, comment)
    # the budget again: other requests may have used it since this one was submitted
    checks = budget.check_request(db, no, business_today())
    if budget.blocked(checks):
        db.rollback()
        raise ApprovalError(
            f"request {no} cannot be approved now: {budget.summary(checks)} The budget policy blocks it."
        )
    db.execute(
        text("update request set budget_check = cast(:b as jsonb) where no = :n"),
        {"b": json.dumps([c.as_json() for c in checks]), "n": no},
    )
    db.execute(
        text(
            "update approval_step set status = 'approved', acted_by = :me, acted_at = now(), comment = :c where id = :i"
        ),
        {"me": me, "c": comment, "i": step["id"]},
    )
    audit.record(
        db,
        actor,
        "request.approve",
        "request",
        no,
        after={
            "step": step["step"],
            "role": step["role"],
            "approver": me,
            "on_behalf_of": step["approver"] if me != step["approver"] else None,
            "via": via,
            "budget": budget.summary(checks),
        },
        reason=comment,
        rule_version=f"{req['rule_code']}:v{req['rule_version']}",
    )
    following = db.execute(
        text(
            "update approval_step set status = 'pending' where id = (select id from approval_step where request_no = :n "
            "and round = :r and status = 'waiting' order by step limit 1) returning step"
        ),
        {"n": no, "r": req["approval_round"]},
    ).first()
    if following is not None:
        nxt = db.execute(
            text("select approver, delegate from approval_step where request_no = :n and round = :r and step = :s"),
            {"n": no, "r": req["approval_round"], "s": following[0]},
        ).one()
        db.execute(
            text("update request set decision_reason = :why where no = :n"),
            {
                "n": no,
                "why": f"Waiting for approval by {name_of(db, nxt[1] or nxt[0])} under rule {req['rule_code']} v{req['rule_version']}",
            },
        )
    if following is None:
        who = ", ".join(
            name_of(db, r[0])
            for r in db.execute(
                text("select acted_by from approval_step where request_no = :n and round = :r order by step"),
                {"n": no, "r": req["approval_round"]},
            )
        )
        db.execute(
            text("update request set status = 'approved', decided_at = now(), decision_reason = :why where no = :n"),
            {"n": no, "why": f"Approved by {who} under rule {req['rule_code']} v{req['rule_version']}"},
        )
        audit.record(
            db, actor, "request.approved", "request", no, before={"status": "submitted"}, after={"status": "approved"}
        )
    db.commit()
    if step["task_id"]:
        tasks.complete_task(db, int(step["task_id"]), user_id)
    if following is None:
        worker.enqueue(tenant, "source_request", {"request_no": no})
        _tell_requester(db, tenant, req, f"Request {no} approved", f"{who}. The buyer takes it from here.")
    if following is not None:
        _open_step(db, tenant, no, req["total_value"], int(following[0]), int(req["approval_round"]))
    return "submitted" if following is not None else "approved"


def on_escalated(db: Session, tenant: str, task_id: int, new_assignee: int) -> None:
    """An approval task moved on (to the approver's manager, else the next approver): that person may decide the step too
    (on the approver's behalf, and the audit says so) and is sent the approval email. The approver can still decide it.
    Nothing happens if the step is no longer waiting."""
    step = (
        db.execute(
            text(
                "select s.id, s.request_no, s.round, s.step, s.approver, r.requester from approval_step s join request r on r.no = s.request_no "
                "where s.task_id = :t and s.status = 'pending' and s.delegate is null for update of s"
            ),
            {"t": task_id},
        )
        .mappings()
        .first()
    )
    me = org_code_of(db, new_assignee)
    if step is None or me is None or me == step["requester"] or me == step["approver"]:
        return
    db.execute(text("update approval_step set delegate = :m where id = :i"), {"m": me, "i": step["id"]})
    audit.record(
        db, Actor("service", "escalation"), "request.escalate", "request", step["request_no"],
        after={"step_approver": step["approver"], "may_also_decide": me}, reason="the approval waited past its time",
    )  # fmt: skip
    from p2p.approvals import approval_email  # noqa: PLC0415 - the email module reads this one

    approval_email.send(
        db, tenant, step["request_no"], int(step["round"]), int(step["step"]), new_assignee, "escalated"
    )


def bulk_approve(
    db: Session, tenant: str, nos: list[str], user_id: int, comment: str | None = None
) -> dict[str, dict[str, str]]:
    """Approve several requests at once from the inbox. Each is decided on its own: the ones that are not this person's turn
    (or are otherwise refused) are left alone and come back with the reason."""
    out: dict[str, dict[str, str]] = {}
    for no in dict.fromkeys(nos):
        try:
            out[no] = {"status": approve(db, tenant, no, user_id, comment)}
        except (ApprovalError, permissions.PermissionDenied) as exc:
            db.rollback()
            out[no] = {"error": str(exc)}
    return out


REMIND_GAP_HOURS = 4


def remind_approver(db: Session, tenant: str, no: str, user_id: int) -> str:
    """The requester nudges whoever has their request. Allowed to the requester (or anyone who may see every request), only
    while the request waits for approval, and not more than once every few hours. Returns the name that was reminded."""
    req = _locked(db, no)
    me = org_code_of(db, user_id)
    if me != req["requester"] and not permissions.can_anywhere(db, user_id, "request.view"):
        raise permissions.PermissionDenied("only the requester can remind the approver")
    if req["status"] != "submitted":
        raise ApprovalError(f"request {no} is {req['status']}: nobody is deciding it now")
    step = (
        db.execute(
            text(
                "select approver, delegate from approval_step where request_no = :n and round = :r and status = 'pending' "
                "order by step limit 1"
            ),
            {"n": no, "r": req["approval_round"]},
        )
        .mappings()
        .first()
    )
    if step is None:
        raise ApprovalError(f"request {no} has no step waiting for a decision")
    recent = db.execute(
        text(
            "select 1 from audit_event where action = 'request.remind' and entity_id = :n "
            "and at > now() - make_interval(hours => :h) limit 1"
        ),
        {"n": no, "h": REMIND_GAP_HOURS},
    ).first()
    if recent is not None:
        raise ApprovalError(f"the approver was reminded in the last {REMIND_GAP_HOURS} hours; give it time")
    whom = step["delegate"] or step["approver"]
    told = notify_person(
        db, tenant, whom, "approval", f"Reminder: request {no} is waiting for you",
        f"{name_of(db, req['requester'])} asks you to look at request {no}.", link=f"#/requisitions/{no}",
    )  # fmt: skip
    audit.record(
        db, Actor("user", str(user_id)), "request.remind", "request", no,
        after={"approver": whom, "told": told}, reason="reminder from the requester",
    )  # fmt: skip
    db.commit()
    return name_of(db, whom)


def _stop(
    db: Session,
    no: str,
    user_id: int,
    reason: str | None,
    *,
    status: str,
    action: str,
    tenant: str | None = None,
    via: str | None = None,
) -> str:
    if not reason or not reason.strip():
        raise ApprovalError("a reason is required")
    req, step, me = _acting(db, no, user_id)
    db.execute(
        text("update approval_step set status = :s, acted_by = :me, acted_at = now(), comment = :c where id = :i"),
        {"s": status, "me": me, "c": reason, "i": step["id"]},
    )
    db.execute(
        text(
            "update approval_step set status = 'cancelled' where request_no = :n and round = :r and status = 'waiting'"
        ),
        {"n": no, "r": req["approval_round"]},
    )
    db.execute(
        text("update request set status = :s, decided_at = now(), decision_reason = :why where no = :n"),
        {"n": no, "s": status, "why": f"{name_of(db, me)}: {reason}"},
    )
    audit.record(
        db,
        Actor("user", str(user_id)),
        action,
        "request",
        no,
        before={"status": "submitted"},
        after={"status": status, "step": step["step"], "approver": me, "via": via},
        reason=reason,
        rule_version=f"{req['rule_code']}:v{req['rule_version']}",
    )
    db.commit()
    if step["task_id"]:
        tasks.complete_task(db, int(step["task_id"]), user_id)
    verb = "rejected" if status == "rejected" else "returned to you for changes"
    _tell_requester(db, tenant, req, f"Request {no} {verb}", f"{name_of(db, me)}: {reason}")
    return status


def reject(
    db: Session, no: str, user_id: int, reason: str | None, *, tenant: str | None = None, via: str | None = None
) -> str:
    """Refuse the request for good. Its lines stop counting as stock on the way. With the company's slug, the requester is
    told, and the store check runs again for what it bought for material requests (it buys none of it again by itself)."""
    status = _stop(db, no, user_id, reason, status="rejected", action="request.reject", tenant=tenant, via=via)
    from p2p.requests import material  # noqa: PLC0415 - material requests sit above approvals

    material.after_event(db, tenant, request_no=no, why=f"{no} was rejected")
    return status


def return_for_changes(
    db: Session, no: str, user_id: int, reason: str | None, *, tenant: str | None = None, via: str | None = None
) -> str:
    """Send the request back to be changed and submitted again (a new round). With the company's slug, the requester is told."""
    return _stop(db, no, user_id, reason, status="returned", action="request.return", tenant=tenant, via=via)


def steps_of(db: Session, no: str) -> list[dict[str, Any]]:
    """The steps of the latest round, with names, for the screen."""
    return [
        dict(r)
        for r in db.execute(
            text(
                "select s.step, s.role, s.approver, a.name as approver_name, a.designation, s.delegate, d.name as delegate_name, "
                "s.status, s.acted_by, s.acted_at, s.comment, s.route_reason, s.rule_code, s.rule_version, s.reminded_at "
                "from approval_step s "
                "join request r on r.no = s.request_no and r.approval_round = s.round "
                "left join org_user a on a.code = s.approver left join org_user d on d.code = s.delegate "
                "where s.request_no = :n order by s.step"
            ),
            {"n": no},
        ).mappings()
    ]


def can_act(db: Session, no: str, user_id: int) -> bool:
    """True when it is this login's turn to decide on the request (the screen shows the buttons only then)."""
    try:
        _acting(db, no, user_id)
    except (ApprovalError, permissions.PermissionDenied):
        return False
    return True
