"""A request's life outside approval: the requester takes it back, cancels it, or copies it to start another.

Deterministic. Every change writes an audit row. Only the requester withdraws or cancels their own request (permission
`request.cancel_own`); a restock request made by the planning run has no requester, so a buyer (`replenishment.run`) may
cancel it. Anyone who may raise requests may copy one.
"""

import json
from datetime import date, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from p2p.agents import intake
from p2p.approvals import workflow as approvals
from p2p.formulas.calendar import business_today
from p2p.formulas.money import round_money
from p2p.platform import audit, numbering, permissions, tasks
from p2p.platform.sod import Actor, user

CANCELLABLE = ("draft", "returned", "submitted", "approved")


class LifecycleError(Exception):
    """The action is not allowed now (wrong status, not the requester, already being bought...)."""


def _locked(db: Session, no: str) -> dict[str, Any]:
    row = db.execute(text("select * from request where no = :n for update"), {"n": no}).mappings().first()
    if row is None:
        raise LifecycleError(f"request {no} does not exist")
    return dict(row)


def _check_owner(db: Session, req: dict[str, Any], user_id: int) -> str:
    """Who is acting. The requester may; so may a buyer for a restock request nobody raised."""
    me = approvals.org_code_of(db, user_id)
    if req["requester"]:
        if me != req["requester"]:
            raise LifecycleError("only the person who raised the request can do this")
        if not permissions.can_anywhere(db, user_id, "request.cancel_own"):
            raise permissions.PermissionDenied("this role may not withdraw or cancel requests")
    elif not permissions.can_anywhere(db, user_id, "replenishment.run"):
        raise permissions.PermissionDenied("only a buyer may cancel an automatic restock request")
    return me or f"user {user_id}"


def _close_open_steps(db: Session, no: str, round_no: int, user_id: int, why: str) -> list[str]:
    """End the approval steps still waiting and their tasks. Returns the people who had to decide."""
    open_steps = (
        db.execute(
            text(
                "select id, approver, delegate, task_id from approval_step where request_no = :n and round = :r "
                "and status in ('pending', 'waiting') order by step"
            ),
            {"n": no, "r": round_no},
        )
        .mappings()
        .all()
    )
    for step in open_steps:
        db.execute(
            text("update approval_step set status = 'cancelled', acted_at = now(), comment = :c where id = :i"),
            {"c": why, "i": step["id"]},
        )
    db.commit()
    for step in open_steps:
        if step["task_id"]:
            tasks.cancel_task(db, int(step["task_id"]), user_id, reason=why)
    return [str(s["delegate"] or s["approver"]) for s in open_steps]


def _started(db: Session, no: str) -> bool:
    """Whether the buyer has begun buying what an approved request asks for."""
    return bool(
        db.execute(
            text(
                "select exists (select 1 from sourcing_decision where request_no = :n) "
                "or exists (select 1 from request_line where request_no = :n and status in ('sourcing', 'ordered'))"
            ),
            {"n": no},
        ).scalar_one()
    )


def allowed(db: Session, no: str, user_id: int) -> dict[str, bool]:
    """What this login may do with the request now (the screen shows only these buttons)."""
    req = db.execute(text("select * from request where no = :n"), {"n": no}).mappings().first()
    out = {"withdraw": False, "cancel": False, "copy": permissions.can_anywhere(db, user_id, "request.create")}
    if req is None:
        return out
    try:
        _check_owner(db, dict(req), user_id)
    except (LifecycleError, permissions.PermissionDenied):
        return out
    out["withdraw"] = req["status"] == "submitted"
    out["cancel"] = req["status"] in CANCELLABLE and not (req["status"] == "approved" and _started(db, no))
    return out


def withdraw(db: Session, tenant: str, no: str, user_id: int) -> str:
    """The requester takes a request back from approval. It is a draft again, to be changed and submitted again."""
    req = _locked(db, no)
    if req["status"] != "submitted":
        raise LifecycleError(f"request {no} is {req['status']}; only a request waiting for approval can be withdrawn")
    me = _check_owner(db, req, user_id)
    waiting = _close_open_steps(db, no, int(req["approval_round"]), user_id, "withdrawn by the requester")
    db.execute(
        text("update request set status = 'draft', decided_at = null, decision_reason = :why where no = :n"),
        {"n": no, "why": "Withdrawn by the requester before it was decided."},
    )
    audit.record(
        db, Actor("user", str(user_id)), "request.withdraw", "request", no,
        before={"status": "submitted"}, after={"status": "draft", "by": me},
    )  # fmt: skip
    db.commit()
    if waiting:
        approvals.notify_person(
            db,
            tenant,
            waiting[0],
            "request",
            f"Request {no} was withdrawn",
            "The requester took it back. Nothing is needed from you.",
        )
    return "draft"


def cancel(db: Session, tenant: str, no: str, user_id: int, reason: str | None) -> str:
    """Stop a request for good. A request already being bought (quotations asked, ordered) is stopped by the buyer instead."""
    if not reason or not reason.strip():
        raise LifecycleError("a reason is required")
    req = _locked(db, no)
    if req["status"] not in CANCELLABLE:
        raise LifecycleError(f"request {no} is {req['status']} and cannot be cancelled")
    me = _check_owner(db, req, user_id)
    if req["status"] == "approved" and _started(db, no):
        raise LifecycleError(f"request {no} is already being bought: ask the buyer to stop it")
    waiting = _close_open_steps(db, no, int(req["approval_round"]), user_id, "the request was cancelled")
    db.execute(
        text("update request_line set status = 'cancelled' where request_no = :n and status = 'open'"), {"n": no}
    )
    db.execute(
        text("update request set status = 'cancelled', decided_at = now(), decision_reason = :why where no = :n"),
        {"n": no, "why": f"Cancelled by {approvals.name_of(db, me)}: {reason.strip()}"},
    )
    audit.record(
        db, Actor("user", str(user_id)), "request.cancel", "request", no,
        before={"status": req["status"]}, after={"status": "cancelled", "by": me}, reason=reason.strip(),
    )  # fmt: skip
    db.commit()
    if waiting:
        approvals.notify_person(
            db,
            tenant,
            waiting[0],
            "request",
            f"Request {no} was cancelled",
            f"Nothing is needed from you. Reason: {reason.strip()}",
        )
    return "cancelled"


def copy(db: Session, user_id: int, no: str, need_by: date | None = None, *, today: date | None = None) -> str:
    """Start a new draft from an earlier request: the same lines, in the copier's name, to be checked and submitted."""
    today = today or business_today()
    if not permissions.can_anywhere(db, user_id, "request.create"):
        raise permissions.PermissionDenied("this role may not raise purchase requests")
    person = intake._person(db, user_id)  # noqa: SLF001 - the same lookup the form and the assistant use
    if person is None:
        raise LifecycleError("your login is not linked to a person in the organisation yet")
    src = db.execute(text("select * from request where no = :n"), {"n": no}).mappings().first()
    if src is None:
        raise LifecycleError(f"request {no} does not exist")
    wanted = need_by or today + timedelta(days=7)
    if wanted < today:
        raise LifecycleError("the date it is needed by is in the past")
    # a line struck out of a live request is left behind; a cancelled request is copied whole (that is why one copies it)
    lines = (
        db.execute(
            text(
                "select * from request_line where request_no = :n and (status <> 'cancelled' or :whole) order by line_no"
            ),
            {"n": no, "whole": src["status"] == "cancelled"},
        )
        .mappings()
        .all()
    )
    if not lines:
        raise LifecycleError(f"request {no} has no lines left to copy")
    total = sum(
        (round_money(Decimal(r["qty"]) * Decimal(r["est_price"])) for r in lines if r["est_price"] is not None),
        Decimal(0),
    )
    new_no = numbering.next_number(db, "request", today, location_code=src["ship_to_location"])
    db.execute(
        text(
            "insert into request (no, requester, department_code, ship_to_location, type, priority, project_code, need_by, "
            "channel, source_ref, justification, status, total_value) values (:no, :req, :dep, :loc, :type, 'normal', :proj, "
            ":need, 'form', :src, :why, 'draft', :tot)"
        ),
        {
            "no": new_no, "req": person["code"], "dep": person["department_code"], "loc": src["ship_to_location"],
            "type": src["type"], "proj": src["project_code"], "need": wanted, "src": f"copy:{no}",
            "why": f"Copy of {no}", "tot": total,
        },
    )  # fmt: skip
    for n, r in enumerate(lines, start=1):
        db.execute(
            text(
                "insert into request_line (request_no, line_no, item_code, free_text, attributes, qty, uom, base_qty, est_price, "
                "need_by, delivery_location, cost_centre_code, gl_code, category_code, project_code, reason, status) values "
                "(:no, :n, :item, :free, cast(:attr as jsonb), :qty, :uom, :base, :price, :need, :loc, :cc, :gl, :cat, :proj, "
                ":reason, 'open')"
            ),
            {
                "no": new_no, "n": n, "item": r["item_code"], "free": r["free_text"],
                "attr": None if r["attributes"] is None else json.dumps(r["attributes"]),
                "qty": r["qty"], "uom": r["uom"], "base": r["base_qty"], "price": r["est_price"], "need": wanted,
                "loc": r["delivery_location"], "cc": r["cost_centre_code"], "gl": r["gl_code"], "cat": r["category_code"],
                "proj": r["project_code"], "reason": r["reason"],
            },
        )  # fmt: skip
    audit.record(
        db, user(user_id), "request.copy", "request", new_no,
        after={"status": "draft", "copied_from": no, "lines": len(lines), "total_value": str(total), "on_behalf_of": person["code"]},
    )  # fmt: skip
    db.commit()
    return new_no
