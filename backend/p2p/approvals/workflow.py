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
from p2p.platform import audit, notifications, permissions, tasks, users, worker
from p2p.platform.sod import Actor

DEFAULT_SLA_HOURS = 24


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


def _sla_hours(db: Session) -> Decimal:
    value = db.execute(text("select value from org_setting where key = 'approval_reminder_hours'")).scalar()
    return Decimal(str(value)) if value else Decimal(DEFAULT_SLA_HOURS)


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

    rule = rules.find_rule(
        db, "request", value, today, spend_type=spend, category=category, department=req["department_code"]
    )
    checks = budget.check_request(db, no, today)
    over = [c for c in checks if c.check.status == "over"]
    if any(c.policy == "block" for c in over):
        raise ApprovalError(f"request {no} cannot be submitted: {budget.summary(checks)} The budget policy blocks it.")
    budget_note = budget.summary(checks)
    if over and rule.is_automatic and _requires_budget(db):
        # a request that is over budget never approves itself: it goes to the first rule above the automatic band
        while rule.is_automatic:
            rule = rules.find_rule(
                db,
                "request",
                rule.max_value + Decimal("0.01"),
                today,
                spend_type=spend,
                category=category,
                department=req["department_code"],
            )
        budget_note += " It cannot approve itself, so it goes to a person."
    db.execute(
        text("update request set budget_check = cast(:b as jsonb) where no = :n"),
        {"b": json.dumps([c.as_json() for c in checks]), "n": no},
    )
    steps = rules.resolve_chain(
        db, rule.roles, requester=req["requester"], department=req["department_code"], on_date=today
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
            "budget": budget_note,
        },
        rule_version=f"{rule.code}:v{rule.version}",
    )
    common = {"n": no, "rc": rule.code, "rv": rule.version, "round": round_no, "now": datetime.now(UTC)}

    if rule.is_automatic:
        reason = (
            f"Auto-approved under rule {rule.code} v{rule.version}: the value {value} is within the automatic band "
            f"(up to {rule.max_value:.2f}). {budget_note}"
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
                "insert into approval_step (request_no, round, step, role, approver, delegate, status) "
                "values (:no, :round, :step, :role, :who, :dele, :status)"
            ),
            {
                "no": no,
                "round": round_no,
                "step": n,
                "role": step.role,
                "who": step.approver,
                "dele": step.delegate,
                "status": "pending" if n == 1 else "waiting",
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
            + (f" {budget_note}" if over else ""),
        },
    )
    db.commit()
    _open_step(db, tenant, no, req["total_value"], 1, round_no)
    return Submitted(
        no, "submitted", rule.code, rule.version, f"Needs approval by {names}", [s.approver for s in steps]
    )


def _open_step(db: Session, tenant: str, no: str, value: Decimal, step: int, round_no: int) -> None:
    """Tell the person whose turn it is: a notification and a task that escalates if it waits too long."""
    row = (
        db.execute(
            text("select approver, delegate from approval_step where request_no = :n and round = :r and step = :s"),
            {"n": no, "r": round_no, "s": step},
        )
        .mappings()
        .one()
    )
    who = row["delegate"] or row["approver"]
    email = db.execute(text("select email from org_user where code = :c"), {"c": who}).scalar()
    user_id = users.user_id_by_email(tenant, str(email)) if email else None
    if user_id is None:
        return  # no login yet: the step waits, and shows on the request page
    from p2p.approvals import email_links  # noqa: PLC0415 - email_links uses this module

    try:
        link = email_links.url(tenant, no, round_no, step, user_id)
        note = f" To decide without signing in, open {link} (works once, expires in {email_links.EXPIRES_DAYS} days)."
    except email_links.LinkError:
        note = ""  # no signing secret: the approver decides in the app
    boss = db.execute(
        text(
            "select m.code from org_user a join org_user m on m.code = a.manager_code and m.active "
            "join request r on r.no = :n where a.code = :c and m.code <> coalesce(r.requester, '')"
        ),
        {"n": no, "c": who},
    ).scalar()  # a request that waits too long goes to the approver's own manager (never the person who asked)
    boss_login = notifications.login_of(db, tenant, str(boss)) if boss else None
    task_id = tasks.create_task(
        db,
        tenant,
        kind="approval",
        title=f"Approve request {no} ({value})",
        assignee=user_id,
        sla_hours=_sla_hours(db),
        escalation_chain=[boss_login] if boss_login else None,
        payload={"request_no": no, "step": step},
        email_note=note,
    )
    db.execute(
        text("update approval_step set task_id = :t where request_no = :n and round = :r and step = :s"),
        {"t": task_id, "n": no, "r": round_no, "s": step},
    )
    db.commit()


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


def on_escalated(db: Session, task_id: int, new_assignee: int) -> None:
    """An approval task moved to the approver's manager: the manager may decide the step too (on the approver's behalf, and
    the audit says so). The approver can still decide it. Nothing happens if the step is no longer waiting."""
    step = (
        db.execute(
            text(
                "select s.id, s.request_no, s.approver, r.requester from approval_step s join request r on r.no = s.request_no "
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
    """Refuse the request for good. Its lines stop counting as stock on the way. With the company's slug, the requester is told."""
    return _stop(db, no, user_id, reason, status="rejected", action="request.reject", tenant=tenant, via=via)


def return_for_changes(db: Session, no: str, user_id: int, reason: str | None, *, tenant: str | None = None) -> str:
    """Send the request back to be changed and submitted again (a new round). With the company's slug, the requester is told."""
    return _stop(db, no, user_id, reason, status="returned", action="request.return", tenant=tenant)


def steps_of(db: Session, no: str) -> list[dict[str, Any]]:
    """The steps of the latest round, with names, for the screen."""
    return [
        dict(r)
        for r in db.execute(
            text(
                "select s.step, s.role, s.approver, a.name as approver_name, a.designation, s.delegate, d.name as delegate_name, "
                "s.status, s.acted_by, s.acted_at, s.comment from approval_step s "
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
