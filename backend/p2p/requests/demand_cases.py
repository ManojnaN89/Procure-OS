"""Cases a change in the ERP's demand leaves for a person (rule Q21): the buyer, when what is now too much is already
ordered or out for quotation (or the supplier promises later than the new date), and the store, when more was issued
than is now needed.

A case is a task of kind `demand_change` (owner, due date in working days on the place's calendar, escalation to the
owner's manager) with its document, lines and excess in `demand_change_case`. The platform does not touch the order:
the person revises it, cancels it or keeps the goods, then closes the case with what they did.
"""

import json
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from p2p.approvals.workflow import name_of, org_code_of
from p2p.formulas.calendar import IST
from p2p.platform import audit, notifications, permissions, reference, settings, tasks
from p2p.platform.sod import user

KIND = "demand_change"
DEFAULT_DAYS = "2"
OUTCOMES = {
    "buyer": ("reduced", "cancelled", "kept_for_stock", "expedited", "not_possible"),
    "store": ("returned_to_stock", "kept_for_production"),
}
NOTE_NEEDED = ("kept_for_stock", "not_possible")
ACTS = {"buyer": "po.create", "store": "material.manage"}  # who may close a case besides its owner
LINKS = {
    "po": "#/purchase-orders/{}",
    "rfq": "#/rfqs/{}",
    "request": "#/requisitions/{}",
    "material_request": "#/material-requests",
}


class CaseError(Exception):
    """The case cannot be closed: `status` is the HTTP answer (404, 403, 409, 422)."""

    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status


@dataclass
class Pending:
    """A case worked out while a change is applied, opened once the change is committed."""

    change_id: int
    owner_kind: str  # buyer or store
    doc_type: str  # po, rfq, request or material_request
    doc_no: str
    location: str  # whose calendar the due date is counted on
    title: str
    next_step: str
    owners: list[str | None] = field(default_factory=list)  # buyer: the people to try first (org codes)
    lines: list[dict[str, Any]] = field(default_factory=list)
    excess: dict[str, Decimal] = field(default_factory=dict)
    amount: Decimal | None = None
    late: date | None = None  # a case to ask the supplier for this date


def _first_buyer(db: Session) -> int:
    found = db.execute(text("select user_id from user_profile where role = 'Buyer' order by user_id limit 1")).scalar()
    return int(found or db.execute(text("select min(user_id) from user_profile")).scalar() or 0)


def _first_store(db: Session) -> int:
    found = db.execute(
        text(
            "select min(ur.user_id) from user_role ur join role_permission rp on rp.role = ur.role "
            "where rp.action = 'material.manage'"
        )
    ).scalar()
    return int(found or _first_buyer(db))


def owner(db: Session, tenant: str, case: Pending) -> int:
    """The login who acts: the document's buyer, else the category's, else the first buyer; or the first store person."""
    if case.owner_kind == "store":
        return _first_store(db)
    return buyer_login(db, tenant, case.owners)


def buyer_login(db: Session, tenant: str, codes: list[str | None]) -> int:
    """The first of these people (org codes) who has a login, else the first buyer."""
    for code in codes:
        login = notifications.login_of(db, tenant, code)
        if login:
            return login
    return _first_buyer(db)


def chain(db: Session, tenant: str, login: int) -> list[int]:
    """Escalation goes to the owner's manager."""
    code = org_code_of(db, login)
    manager = db.execute(text("select manager_code from org_user where code = :c"), {"c": code}).scalar()
    up = notifications.login_of(db, tenant, manager)
    return [up] if up and up != login else []


def open_case(db: Session, tenant: str, case: Pending) -> dict[str, Any]:
    """The task and its case row. Commits (the task tells its owner). Returns the case as a change shows it."""
    login = owner(db, tenant, case)
    calendar = reference.calendar_for_location(db, case.location)
    days = settings.number(db, "demand_change_case_days", DEFAULT_DAYS)
    payload = {
        "doc_type": case.doc_type, "doc_no": case.doc_no, "change_id": case.change_id, "owner_kind": case.owner_kind,
        "next_step": case.next_step, "calendar_location": case.location,
    }  # fmt: skip
    task_id = tasks.create_task(
        db, tenant, kind=KIND, title=case.title[:200], assignee=login, sla_hours=days * calendar.hours_per_day,
        escalation_chain=chain(db, tenant, login), payload=payload, calendar=calendar, email_note=case.next_step,
    )  # fmt: skip
    db.execute(
        text(
            "insert into demand_change_case (task_id, change_id, owner_kind, doc_type, doc_no, lines, excess, amount) "
            "values (:t, :c, :o, :dt, :dn, cast(:l as jsonb), cast(:e as jsonb), :a)"
        ),
        {
            "t": task_id, "c": case.change_id, "o": case.owner_kind, "dt": case.doc_type, "dn": case.doc_no,
            "l": json.dumps(case.lines, default=str), "e": json.dumps({k: str(v) for k, v in case.excess.items()}),
            "a": case.amount,
        },
    )  # fmt: skip
    due = db.execute(text("select due_at from task where id = :i"), {"i": task_id}).scalar_one()
    db.commit()
    code = org_code_of(db, login)
    return {
        "task_id": task_id, "owner_kind": case.owner_kind, "doc_type": case.doc_type, "doc_no": case.doc_no,
        "assignee": name_of(db, code) if code else f"user {login}", "assignee_id": login, "assignee_code": code,
        "due_at": due.isoformat(), "excess": {k: f"{v.normalize():f}" for k, v in case.excess.items()},
        "amount": None if case.amount is None else str(case.amount), "lines": case.lines,
    }  # fmt: skip


def close(db: Session, tenant: str, user_id: int, task_id: int, outcome: str, note: str | None) -> dict[str, Any]:
    """The owner (or someone who acts for the kind of case) says what was done; the task is done. Commits."""
    row = (
        db.execute(
            text(
                "select c.owner_kind, c.doc_no, c.closed_at, t.assignee, t.status from demand_change_case c "
                "join task t on t.id = c.task_id where c.task_id = :t for update of c"
            ),
            {"t": task_id},
        )
        .mappings()
        .first()
    )
    if row is None:
        db.rollback()
        raise CaseError(404, f"there is no demand change case {task_id}")
    if int(row["assignee"]) != user_id and not permissions.can_anywhere(db, user_id, ACTS[row["owner_kind"]]):
        db.rollback()
        raise CaseError(403, "only the case's owner, or someone who acts for them, may close it")
    if row["closed_at"] is not None or row["status"] in ("done", "cancelled"):
        db.rollback()
        raise CaseError(409, f"case {task_id} is already closed")
    if outcome not in OUTCOMES[row["owner_kind"]]:
        db.rollback()
        raise CaseError(422, f"the outcome must be one of {', '.join(OUTCOMES[row['owner_kind']])}")
    note = (note or "").strip() or None
    if outcome in NOTE_NEEDED and not note:
        db.rollback()
        raise CaseError(422, "say why in the note")
    who = org_code_of(db, user_id) or f"user {user_id}"
    db.execute(
        text(
            "update demand_change_case set outcome = :o, note = :n, closed_by = :b, closed_at = now() where task_id = :t"
        ),
        {"o": outcome, "n": note, "b": who, "t": task_id},
    )
    audit.record(
        db, user(user_id), "demand.case_close", "task", task_id, before={"status": row["status"]},
        after={"outcome": outcome, "doc_no": row["doc_no"]}, reason=note,
    )  # fmt: skip
    db.commit()
    tasks.complete_task(db, task_id, user_id)
    return {"task_id": task_id, "status": "done", "outcome": outcome}


def open_cases(db: Session) -> list[dict[str, Any]]:
    """Every case still open, with its task: for the exceptions screen."""
    rows = db.execute(
        text(
            "select c.task_id, c.owner_kind, c.doc_type, c.doc_no, c.amount, t.title, t.assignee, t.created_at, "
            "t.payload from demand_change_case c join task t on t.id = c.task_id "
            "where c.closed_at is null and t.status in ('open', 'overdue') order by c.task_id"
        )
    ).mappings()
    return [dict(r) for r in rows]


def since(created_at: datetime) -> date:
    return created_at.astimezone(IST).date()


def link(doc_type: str, doc_no: str) -> str:
    return LINKS[doc_type].format(doc_no)
