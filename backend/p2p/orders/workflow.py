"""Approval of purchase orders by the rule table (document 'po'): submit, approve, reject.

The same ideas as the approval of requests (approvals/workflow.py): the rule picks a chain of roles, the chain becomes
people, each step is a person's decision, every change writes an audit row. A rejected order is cancelled and its lines
go back to being ready, so a buyer can order them again.
"""

from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from p2p.approvals import budget, rules
from p2p.approvals.workflow import ApprovalError, name_of, notify_person, org_code_of
from p2p.formulas.calendar import business_today
from p2p.formulas.money import format_inr
from p2p.orders import approved_versions, dispatch, touchless
from p2p.platform import audit, otp, permissions, proposals, settings, tasks, users
from p2p.platform.sod import Actor

DEFAULT_SLA_HOURS = 24


def _locked(db: Session, po_no: str) -> dict[str, Any]:
    row = db.execute(text("select * from po where no = :n for update"), {"n": po_no}).mappings().first()
    if row is None:
        raise ApprovalError(f"purchase order {po_no} does not exist")
    return dict(row)


def _sla_hours(db: Session) -> Decimal:
    value = db.execute(text("select value from org_setting where key = 'approval_reminder_hours'")).scalar()
    return Decimal(str(value)) if value else Decimal(DEFAULT_SLA_HOURS)


# The people who shaped an order, from the audit log: who submitted or revised it, who awarded the quotation its lines
# came from and who changed the buying route of those lines. A revision a price approver made by accepting a supplier's
# price is that person's approval, not their making, so it is left out.
_MAKERS_SQL = """
select actor_id from audit_event
 where actor_type = 'user' and entity = 'po' and entity_id = :n and action = 'po.submit'
union
select r.changed_by from po_revision r
 where r.po_no = :n and not exists (
   select 1 from po_change c where c.po_no = r.po_no and c.version_made = r.version and c.proposal_id is not null)
union
select a.actor_id from audit_event a
  join rfq_line q on q.rfq_no = a.entity_id
  join po_line l on l.request_no = q.request_no and l.request_line_no = q.request_line_no
 where a.actor_type = 'user' and a.entity = 'rfq' and a.action = 'rfq.award' and l.po_no = :n
union
select a.actor_id from audit_event a
  join po_line l on a.entity_id = l.request_no || '/' || l.request_line_no
 where a.actor_type = 'user' and a.entity = 'request' and l.po_no = :n
   and (a.action = 'sourcing.route_change_proposed' or a.action = 'sourcing.path'
        or (a.action = 'sourcing.route_change' and a.after ->> 'route' = 'rfq'))
"""


def makers(db: Session, po: dict[str, Any]) -> frozenset[str]:
    """Who made the order: its buyer, who submitted or revised it, who awarded its quotation, who changed its lines'
    buying route and who chose path A or B for a line (D43). None of them approves it or its price, at any value, by any path (C1)."""
    people = {str(po["buyer"])} if po["buyer"] else set()
    for (uid,) in db.execute(text(_MAKERS_SQL), {"n": po["no"]}):
        code = org_code_of(db, int(uid)) if str(uid).isdigit() else None
        if code:
            people.add(code)
    return frozenset(people)


def refuse_maker(db: Session, po: dict[str, Any], person: str | None) -> None:
    """Segregation of duties on every path that approves an order or its price: its makers never do."""
    if person and person in makers(db, po):
        raise ApprovalError("segregation of duties: the person who made the order cannot approve it")


def _above(db: Session, rule: rules.ApprovalRule, today: date) -> rules.ApprovalRule:
    """The next order rule up: the first one past this rule's top."""
    nobody = "nobody who did not make the order can approve it; add an approver to the order rules"
    if rule.max_value is None:
        raise ApprovalError(nobody)
    try:
        return rules.find_rule(db, "po", rule.max_value + Decimal("0.01"), today)
    except rules.ApprovalRuleError:
        raise ApprovalError(nobody) from None


def _chain(
    db: Session, po: dict[str, Any], rule: rules.ApprovalRule, today: date, also: frozenset[str] = frozenset()
) -> tuple[rules.ApprovalRule, list[rules.Step]]:
    """People to approve the order, never one who made it. A person approves only in a band that needs one: a band with
    no approver (only touchless orders are approved there, by the rule) and a chain made only of the order's makers both
    go up to the next rule."""
    never = makers(db, po) | also  # `also`: who is submitting it now, not yet in the audit log
    while True:
        if not rule.is_automatic:
            try:
                return rule, rules.resolve_chain(
                    db, rule.roles, requester=None, department=None, on_date=today, exclude=never
                )
            except rules.ApprovalRuleError:
                pass  # nobody left once the makers are taken out
        rule = _above(db, rule, today)


def people_for(
    db: Session, po: dict[str, Any], total: Decimal, today: date, also: frozenset[str] = frozenset()
) -> tuple[rules.ApprovalRule, list[rules.Step]]:
    """The band and people who would approve an order at this total, never one of its makers (nor `also`): the excess
    revision asks the person the `po` rows name for the new total (9.3.1, OP-6). A band with nobody moves up."""
    return _chain(db, po, rules.find_rule(db, "po", total, today), today, also)


def refuse_outside_band(db: Session, po: dict[str, Any], person: str | None, today: date | None = None) -> None:
    """Accepting an order's price is for the people its approval band names, or a higher band, never its makers. This
    is the approver's limit for that decision, from the same rule rows that approve the order (DF-08)."""
    refuse_maker(db, po, person)
    today = today or business_today()
    rule, steps = _chain(db, po, rules.find_rule(db, "po", Decimal(po["total"]), today), today)
    allowed = {s.approver for s in steps} | {s.delegate for s in steps if s.delegate}
    while rule.max_value is not None:
        try:
            rule, steps = _chain(db, po, _above(db, rule, today), today)
        except (ApprovalError, rules.ApprovalRuleError):
            break
        allowed |= {s.approver for s in steps} | {s.delegate for s in steps if s.delegate}
    if person not in allowed:
        raise ApprovalError(
            f"{po['no']} totals {format_inr(po['total'])}, above what you may approve; its approval band decides it"
        )


def record_refusal(db: Session, actor: Actor, supplier: str, status: str, ref: str) -> None:
    """An order to a supplier that is not active is refused by every route, touchless included, and the refusal is
    audited (D-03 R5)."""
    audit.record(
        db, actor, "po.issue_refused", "po", ref, after={"supplier": supplier, "status": status},
        reason=f"the supplier {supplier} is {status}, not active",
    )  # fmt: skip
    db.commit()


def refuse_inactive(db: Session, po: dict[str, Any], actor: Actor) -> None:
    status = db.execute(text("select status from supplier where code = :c"), {"c": po["supplier_code"]}).scalar()
    if status != "active":
        db.rollback()
        record_refusal(db, actor, str(po["supplier_code"]), str(status), str(po["no"]))
        raise ApprovalError(f"the supplier {po['supplier_code']} is {status}, not active: the order cannot be issued")


def submit(db: Session, tenant: str, po_no: str, actor: Actor, *, today: date | None = None) -> str:
    """Send the order for approval, or approve it on the spot when it is touchless. Returns the order's status.

    Only the company's touchless policy approves without a person (approval by the rule, never by the buyer). Every
    other order goes to people, none of whom made it."""
    today = today or business_today()
    po = _locked(db, po_no)
    if po["status"] != "draft":
        raise ApprovalError(f"{po_no} is {po['status']}; only a draft order can be submitted")
    if po["hold_reason"] and str(po["hold_reason"]).startswith(NO_APPROVER):  # tried again once the rules may allow it
        db.execute(text("update po set hold_reason = null where no = :n"), {"n": po_no})
        po["hold_reason"] = None
    if po["hold_reason"]:
        raise ApprovalError(f"{po_no} is on hold: {po['hold_reason']}")
    # an order to a supplier that is not active may wait for a person (never touchless: its test needs an active
    # supplier), but it is never issued: approving and sending it are refused (D-03 R5)
    first = rules.find_rule(db, "po", Decimal(po["total"]), today)
    # the budget again, on the order's final prices (the request was checked on estimates)
    checks = budget.check_order(db, po_no, today)
    over = [c for c in checks if c.check.status == "over"]
    budget_note = " " + budget.summary(checks) if over else ""
    if any(c.policy == "block" for c in over) and not po["budget_accepted"]:
        return _hold_for_budget(db, tenant, po_no, budget.summary(checks), actor)
    round_no = int(po["approval_round"]) + 1
    if first.is_automatic and touchless.decide(db, po_no, first, today):
        return _approve_by_rule(db, tenant, po_no, po, first, round_no, actor, budget_note)
    submitter = org_code_of(db, int(actor.id)) if actor.kind == "user" and str(actor.id).isdigit() else None
    try:
        rule, steps = _chain(db, po, first, today, frozenset({submitter}) if submitter else frozenset())
    except ApprovalError as exc:  # every approver the rules name made it: held, with the reason on the order (C1)
        return _hold_no_approver(db, tenant, po, str(exc), actor)
    if rule is not first:
        db.execute(text("update po set approval_tier = :t where no = :n"), {"t": touchless.tier_of(rule), "n": po_no})
    audit.record(
        db,
        actor,
        "po.submit",
        "po",
        po_no,
        before={"status": "draft"},
        after={"rule": rule.code, "version": rule.version, "approvers": [s.approver for s in steps]},
        rule_version=f"{rule.code}:v{rule.version}",
    )
    for n, step in enumerate(steps, start=1):
        db.execute(
            text(
                "insert into po_approval_step (po_no, round, step, role, approver, delegate, status, version) "
                "values (:p, :r, :s, :role, :who, :dele, :st, :ver)"
            ),
            {
                "p": po_no,
                "r": round_no,
                "s": n,
                "role": step.role,
                "who": step.approver,
                "dele": step.delegate,
                "st": "pending" if n == 1 else "waiting",
                "ver": po["version"],  # the version this step approves (DF-07)
            },
        )
    names = " then ".join(name_of(db, s.approver) for s in steps)
    why = f"Waiting for approval by {names} under rule {rule.code} v{rule.version}"
    if first.is_automatic:
        why += ". Not touchless, so a person approves it, never the buyer who made it"
    db.execute(
        text(
            "update po set status = 'submitted', rule_code = :rc, rule_version = :rv, approval_round = :r, "
            "decision_reason = :why where no = :n"
        ),
        {"rc": rule.code, "rv": rule.version, "r": round_no, "why": why + budget_note.rstrip(), "n": po_no},
    )
    db.commit()
    if not first.is_automatic:
        touchless.decide(db, po_no, rule, today)  # never touchless above the touchless band; the note says so
    _open_step(db, tenant, po_no, po["total"], 1, round_no)
    return "submitted"


def _approve_by_rule(
    db: Session,
    tenant: str,
    po_no: str,
    po: dict[str, Any],
    rule: rules.ApprovalRule,
    round_no: int,
    actor: Actor,
    budget_note: str,
) -> str:
    """A touchless order: approved by the company's policy (the rule and its version), then sent by the system."""
    audit.record(
        db,
        actor,
        "po.submit",
        "po",
        po_no,
        before={"status": "draft"},
        after={"rule": rule.code, "version": rule.version, "approvers": []},
        rule_version=f"{rule.code}:v{rule.version}",
    )
    reason = (
        f"Touchless under rule {rule.code} v{rule.version} (up to {format_inr(rule.max_value)}): every touchless check "
        f"passed, so the company's policy approves the total {format_inr(po['total'])}; no person approved it."
        f"{budget_note}"
    )
    db.execute(
        text(
            "update po set status = 'approved', approved_at = now(), rule_code = :rc, rule_version = :rv, "
            "decision_reason = :why, approval_round = :r where no = :n"
        ),
        {"rc": rule.code, "rv": rule.version, "why": reason, "r": round_no, "n": po_no},
    )
    version = approved_versions.record(
        db, po_no, approved_by=f"rule:{rule.code}", rule_code=rule.code, rule_version=rule.version,
        band=approved_versions.band_max(db, rule),
    )  # fmt: skip
    audit.record(
        db,
        Actor("rule", rule.code),
        "po.auto_approve",
        "po",
        po_no,
        after={"version": version},
        reason=reason,
        rule_version=f"{rule.code}:v{rule.version}",
    )
    db.commit()
    touchless.issue(db, po_no, tenant)
    return "approved"


NO_APPROVER = "Nobody may approve it:"


def _hold_no_approver(db: Session, tenant: str, po: dict[str, Any], why: str, actor: Actor) -> str:
    """No person the approval rules name is free of the order: it waits, visibly, until the rules name someone who is.
    Submitting it again tries once more."""
    hold = f"{NO_APPROVER} {why}"
    db.execute(text("update po set hold_reason = :h where no = :n"), {"h": hold, "n": po["no"]})
    audit.record(db, actor, "po.no_approver", "po", po["no"], reason=hold)
    db.commit()
    notify_person(
        db, tenant, po["buyer"], "po", f"{po['no']} is held: the rules name no one free to decide it",
        "Everyone the order rules name for this amount made the order. An administrator has to name someone else in "
        "the rules; then submit it again.",
        link=f"#/purchase-orders/{po['no']}",
    )  # fmt: skip
    db.commit()
    return "held"


def _hold_for_budget(db: Session, tenant: str, po_no: str, summary: str, actor: Actor) -> str:
    """The budget policy blocks an order that goes over it at its final prices: a person may accept it knowingly."""
    hold = f"{summary} The budget policy blocks it until a person accepts."
    pid = proposals.propose(
        db,
        actor,
        action="po.accept_variance",
        summary=f"Accept {po_no} although it is over budget: {summary}",
        payload={"po_no": po_no, "tenant": tenant},
        required_permission="po.approve",
        risk="medium",
    )
    db.execute(
        text("update po set hold_reason = :h, hold_proposal_id = :p where no = :n"), {"h": hold, "p": pid, "n": po_no}
    )
    audit.record(db, actor, "po.budget_hold", "po", po_no, reason=hold)
    db.commit()
    return "held"


def _open_step(db: Session, tenant: str, po_no: str, total: Decimal, step: int, round_no: int) -> None:
    row = db.execute(
        text("select approver, delegate from po_approval_step where po_no = :p and round = :r and step = :s"),
        {"p": po_no, "r": round_no, "s": step},
    ).one()
    who = row[1] or row[0]
    email = db.execute(text("select email from org_user where code = :c"), {"c": who}).scalar()
    user_id = users.user_id_by_email(tenant, str(email)) if email else None
    if user_id is None:
        return  # no login yet: the step waits, and shows on the order page
    task_id = tasks.create_task(
        db,
        tenant,
        kind="approval",
        title=f"Approve purchase order {po_no} ({format_inr(total)})",
        assignee=user_id,
        sla_hours=_sla_hours(db),
        payload={"po_no": po_no, "step": step},
    )
    db.execute(
        text("update po_approval_step set task_id = :t where po_no = :p and round = :r and step = :s"),
        {"t": task_id, "p": po_no, "r": round_no, "s": step},
    )
    db.commit()


def _acting(
    db: Session, po_no: str, user_id: int, version: int | None = None
) -> tuple[dict[str, Any], dict[str, Any], str]:
    """The order, its open step and the person, when it is this person's turn on the version they saw."""
    po = _locked(db, po_no)
    if po["status"] != "submitted":
        raise ApprovalError(f"{po_no} is {po['status']}, not waiting for approval")
    step = (
        db.execute(
            text(
                "select * from po_approval_step where po_no = :p and round = :r and status = 'pending' order by step limit 1"
            ),
            {"p": po_no, "r": po["approval_round"]},
        )
        .mappings()
        .first()
    )
    if step is None:
        raise ApprovalError(f"{po_no} has no step waiting for a decision")
    me = org_code_of(db, user_id)
    if me is None:
        raise ApprovalError("this login is not linked to a person in the organisation, so it cannot approve")
    if me not in (step["approver"], step["delegate"]):
        raise ApprovalError(
            f"it is not your turn: {po_no} is waiting for {name_of(db, step['delegate'] or step['approver'])}"
        )
    refuse_maker(db, po, me)
    if not permissions.can_anywhere(db, user_id, "po.approve"):
        raise permissions.PermissionDenied("this role may not approve purchase orders")
    current = int(po["version"])
    if (version is not None and int(version) != current) or (step["version"] not in (None, current)):
        raise ApprovalError(
            f"{po_no} has changed since you opened it: it is now version {current}. Open it again to see the change"
        )
    return po, dict(step), me


def can_act(db: Session, po_no: str, user_id: int) -> bool:
    try:
        _acting(db, po_no, user_id)
    except (ApprovalError, permissions.PermissionDenied):
        return False
    return True


def approve(
    db: Session,
    tenant: str,
    po_no: str,
    user_id: int,
    comment: str | None = None,
    *,
    version: int | None = None,
    code: str | None = None,
) -> str:
    """Approve the open step of the version the person saw (`version`; omitted: the current one, recorded). Above the
    code limit the person's one-time code for this version and step is needed first; it is used up with the approval."""
    po, step, me = _acting(db, po_no, user_id, version)
    with_code = needs_code(db, po)
    if with_code:
        _check_code(db, po, step, user_id, code)
    actor = Actor("user", str(user_id))
    refuse_inactive(db, po, actor)  # the supplier may have been blocked while the order waited (D-03 R5)
    db.execute(
        text(
            "update po_approval_step set status = 'approved', acted_by = :me, acted_at = now(), comment = :c where id = :i"
        ),
        {"me": me, "c": comment, "i": step["id"]},
    )
    audit.record(
        db,
        actor,
        "po.approve",
        "po",
        po_no,
        after={
            "version": po["version"],  # the version of the order this person approved
            "step": step["step"],
            "role": step["role"],
            "approver": me,
            "on_behalf_of": step["approver"] if me != step["approver"] else None,
            "with_code": with_code,
        },
        reason=comment,
        rule_version=f"{po['rule_code']}:v{po['rule_version']}",
    )
    following = db.execute(
        text(
            "update po_approval_step set status = 'pending' where id = (select id from po_approval_step where po_no = :p "
            "and round = :r and status = 'waiting' order by step limit 1) returning step, approver, delegate"
        ),
        {"p": po_no, "r": po["approval_round"]},
    ).first()
    if following is not None:
        db.execute(
            text("update po set decision_reason = :why where no = :n"),
            {
                "n": po_no,
                "why": f"Waiting for approval by {name_of(db, following[2] or following[1])} under rule {po['rule_code']} v{po['rule_version']}",
            },
        )
    else:
        people = [
            str(r[0])
            for r in db.execute(
                text("select acted_by from po_approval_step where po_no = :p and round = :r order by step"),
                {"p": po_no, "r": po["approval_round"]},
            )
        ]
        who = ", ".join(name_of(db, c) for c in people)
        db.execute(
            text("update po set status = 'approved', approved_at = now(), decision_reason = :why where no = :n"),
            {"n": po_no, "why": f"Approved by {who} under rule {po['rule_code']} v{po['rule_version']}"},
        )
        approved_versions.record(
            db, po_no, approved_by=",".join(people), rule_code=po["rule_code"], rule_version=po["rule_version"],
            band=approved_versions.band_of(db, po["rule_code"], po["rule_version"]),
        )  # fmt: skip
        audit.record(
            db, actor, "po.approved", "po", po_no, before={"status": "submitted"},
            after={"status": "approved", "version": po["version"]},
        )  # fmt: skip
    db.commit()
    if following is None:
        dispatch.queue_if_automatic(db, tenant, po_no)
    if step["task_id"]:
        tasks.complete_task(db, int(step["task_id"]), user_id)
    if following is not None:
        _open_step(db, tenant, po_no, po["total"], int(following[0]), int(po["approval_round"]))
    return "submitted" if following is not None else "approved"


def reject(db: Session, po_no: str, user_id: int, reason: str | None, *, version: int | None = None) -> str:
    """Refuse the order (the version the person saw). It is cancelled and its lines are ready to be ordered again. No
    code: rejecting commits no money."""
    if not reason or not reason.strip():
        raise ApprovalError("a reason is required")
    po, step, me = _acting(db, po_no, user_id, version)
    db.execute(
        text(
            "update po_approval_step set status = 'rejected', acted_by = :me, acted_at = now(), comment = :c where id = :i"
        ),
        {"me": me, "c": reason, "i": step["id"]},
    )
    db.execute(
        text("update po_approval_step set status = 'cancelled' where po_no = :p and round = :r and status = 'waiting'"),
        {"p": po_no, "r": po["approval_round"]},
    )
    cancel_order(db, po_no, f"{name_of(db, me)}: {reason}")
    audit.record(
        db,
        Actor("user", str(user_id)),
        "po.reject",
        "po",
        po_no,
        before={"status": "submitted"},
        after={"status": "cancelled", "step": step["step"], "approver": me},
        reason=reason,
        rule_version=f"{po['rule_code']}:v{po['rule_version']}",
    )
    db.commit()
    if step["task_id"]:
        tasks.complete_task(db, int(step["task_id"]), user_id)
    return "cancelled"


def cancel_order(db: Session, po_no: str, reason: str) -> None:
    """Cancel an order and give its lines back: they are ready to be ordered again."""
    db.execute(
        text("update po set status = 'cancelled', cancel_reason = :r, decision_reason = :r where no = :n"),
        {"n": po_no, "r": reason},
    )
    lines = db.execute(text("select request_no, request_line_no from po_line where po_no = :n"), {"n": po_no}).all()
    db.execute(text("update po_line set status = 'cancelled' where po_no = :n"), {"n": po_no})
    for request_no, line_no in lines:
        db.execute(
            text("update request_line set status = 'sourcing' where request_no = :n and line_no = :l"),
            {"n": request_no, "l": line_no},
        )
        db.execute(
            text("update request set status = 'approved' where no = :n and status = 'closed'"), {"n": request_no}
        )


def steps_of(db: Session, po_no: str) -> list[dict[str, Any]]:
    return [
        dict(r)
        for r in db.execute(
            text(
                "select s.step, s.role, s.approver, a.name as approver_name, a.designation, s.delegate, d.name as delegate_name, "
                "s.status, s.acted_by, s.acted_at, s.comment from po_approval_step s "
                "join po p on p.no = s.po_no and p.approval_round = s.round "
                "left join org_user a on a.code = s.approver left join org_user d on d.code = s.delegate "
                "where s.po_no = :n order by s.step"
            ),
            {"n": po_no},
        ).mappings()
    ]


# ---- the one-time code to approve an order above the limit (D-07, task 1.2) ---------------------------------------

CODES = otp.OneTimeCodes("po_approval")
NEED_CODE = "this needs a one-time code: ask for one (a code works once)"


def needs_code(db: Session, po: dict[str, Any]) -> bool:
    """Approving an order above `approval_otp_above` (its total with GST, the value its band uses; the limit itself
    needs none) needs a one-time code emailed to the approver."""
    return otp.needed(db, Decimal(po["total"]))


def code_limit(db: Session) -> Decimal:
    return settings.number(db, "approval_otp_above", otp.DEFAULT_LIMIT)


def _code_ref(po: dict[str, Any], step: dict[str, Any]) -> str:
    """A code is for one person at one step of one round of one version: a revision makes it useless."""
    return f"{po['no']}:{po['version']}:{po['approval_round']}:{step['step']}"


def _check_code(db: Session, po: dict[str, Any], step: dict[str, Any], user_id: int, code: str | None) -> None:
    if not (code or "").strip():
        raise ApprovalError(NEED_CODE)
    try:
        CODES.verify(db, _code_ref(po, step), user_id, code)
    except otp.CodeError as exc:
        raise ApprovalError(str(exc)) from None


def send_code(db: Session, po_no: str, user_id: int) -> str:
    """Email the person whose turn it is a code to approve this version of the order. Returns the address."""
    po, step, _ = _acting(db, po_no, user_id)
    if not needs_code(db, po):
        db.rollback()
        raise ApprovalError(f"{po_no} needs no code: it is not above {format_inr(code_limit(db))}")
    try:
        return CODES.issue(db, _code_ref(po, step), user_id, subject=f"Your code to approve purchase order {po_no}")
    except otp.CodeError as exc:
        db.rollback()
        raise ApprovalError(str(exc)) from None
