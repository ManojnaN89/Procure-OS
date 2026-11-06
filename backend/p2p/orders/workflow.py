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

from p2p.approvals import rules
from p2p.approvals.workflow import ApprovalError, name_of, org_code_of
from p2p.formulas.calendar import business_today
from p2p.platform import audit, permissions, tasks, users
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


def submit(db: Session, tenant: str, po_no: str, actor: Actor, *, today: date | None = None) -> str:
    """Send the order for approval, or approve it on the spot when the rule says so. Returns the order's status."""
    today = today or business_today()
    po = _locked(db, po_no)
    if po["status"] != "draft":
        raise ApprovalError(f"{po_no} is {po['status']}; only a draft order can be submitted")
    if po["hold_reason"]:
        raise ApprovalError(f"{po_no} is on hold: {po['hold_reason']}")
    rule = rules.find_rule(db, "po", Decimal(po["total"]), today)
    steps = rules.resolve_chain(db, rule.roles, requester=None, department=None, on_date=today)
    round_no = int(po["approval_round"]) + 1
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
    if rule.is_automatic:
        reason = f"Auto-approved under rule {rule.code} v{rule.version}: the total {po['total']} is within the automatic band."
        db.execute(
            text(
                "update po set status = 'approved', approved_at = now(), rule_code = :rc, rule_version = :rv, "
                "decision_reason = :why, approval_round = :r where no = :n"
            ),
            {"rc": rule.code, "rv": rule.version, "why": reason, "r": round_no, "n": po_no},
        )
        audit.record(
            db,
            Actor("rule", rule.code),
            "po.auto_approve",
            "po",
            po_no,
            reason=reason,
            rule_version=f"{rule.code}:v{rule.version}",
        )
        db.commit()
        return "approved"
    for n, step in enumerate(steps, start=1):
        db.execute(
            text(
                "insert into po_approval_step (po_no, round, step, role, approver, delegate, status) "
                "values (:p, :r, :s, :role, :who, :dele, :st)"
            ),
            {
                "p": po_no,
                "r": round_no,
                "s": n,
                "role": step.role,
                "who": step.approver,
                "dele": step.delegate,
                "st": "pending" if n == 1 else "waiting",
            },
        )
    names = " then ".join(name_of(db, s.approver) for s in steps)
    db.execute(
        text(
            "update po set status = 'submitted', rule_code = :rc, rule_version = :rv, approval_round = :r, "
            "decision_reason = :why where no = :n"
        ),
        {
            "rc": rule.code,
            "rv": rule.version,
            "r": round_no,
            "why": f"Waiting for approval by {names} under rule {rule.code} v{rule.version}",
            "n": po_no,
        },
    )
    db.commit()
    _open_step(db, tenant, po_no, po["total"], 1, round_no)
    return "submitted"


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
        title=f"Approve purchase order {po_no} ({total})",
        assignee=user_id,
        sla_hours=_sla_hours(db),
        payload={"po_no": po_no, "step": step},
    )
    db.execute(
        text("update po_approval_step set task_id = :t where po_no = :p and round = :r and step = :s"),
        {"t": task_id, "p": po_no, "r": round_no, "s": step},
    )
    db.commit()


def _acting(db: Session, po_no: str, user_id: int) -> tuple[dict[str, Any], dict[str, Any], str]:
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
    buyer = po["buyer"]
    if buyer and me == buyer:
        raise ApprovalError("segregation of duties: the buyer who ordered cannot approve the order")
    if not permissions.can_anywhere(db, user_id, "po.approve"):
        raise permissions.PermissionDenied("this role may not approve purchase orders")
    return po, dict(step), me


def can_act(db: Session, po_no: str, user_id: int) -> bool:
    try:
        _acting(db, po_no, user_id)
    except (ApprovalError, permissions.PermissionDenied):
        return False
    return True


def approve(db: Session, tenant: str, po_no: str, user_id: int, comment: str | None = None) -> str:
    po, step, me = _acting(db, po_no, user_id)
    actor = Actor("user", str(user_id))
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
            "step": step["step"],
            "role": step["role"],
            "approver": me,
            "on_behalf_of": step["approver"] if me != step["approver"] else None,
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
        who = ", ".join(
            name_of(db, r[0])
            for r in db.execute(
                text("select acted_by from po_approval_step where po_no = :p and round = :r order by step"),
                {"p": po_no, "r": po["approval_round"]},
            )
        )
        db.execute(
            text("update po set status = 'approved', approved_at = now(), decision_reason = :why where no = :n"),
            {"n": po_no, "why": f"Approved by {who} under rule {po['rule_code']} v{po['rule_version']}"},
        )
        audit.record(
            db, actor, "po.approved", "po", po_no, before={"status": "submitted"}, after={"status": "approved"}
        )
    db.commit()
    if step["task_id"]:
        tasks.complete_task(db, int(step["task_id"]), user_id)
    if following is not None:
        _open_step(db, tenant, po_no, po["total"], int(following[0]), int(po["approval_round"]))
    return "submitted" if following is not None else "approved"


def reject(db: Session, po_no: str, user_id: int, reason: str | None) -> str:
    """Refuse the order. It is cancelled and its lines are ready to be ordered again."""
    if not reason or not reason.strip():
        raise ApprovalError("a reason is required")
    po, step, me = _acting(db, po_no, user_id)
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
