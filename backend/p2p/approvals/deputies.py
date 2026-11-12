"""An approver who will be away names a deputy for a date range; approvals that open in that range go to the deputy.

A row in the delegation table (the same table the master data loads). The chain is turned into people when a request is
submitted, and each later step checks again when it opens (approvals/rules.py deputy_on), so a deputy named after a
request was submitted still gets the steps that open while the approver is away. The deputy must be able to approve, and
never decides a request they raised themselves (workflow checks that at every decision).
"""

from datetime import date
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from p2p.approvals import workflow
from p2p.formulas.calendar import business_today
from p2p.platform import audit, notifications, permissions
from p2p.platform.sod import Actor


class DeputyError(Exception):
    """The deputy cannot be named (unknown person, oneself, dates the wrong way round, cannot approve)."""


def _me(db: Session, user_id: int) -> str:
    me = workflow.org_code_of(db, user_id)
    if me is None:
        raise DeputyError("this login is not linked to a person in the organisation")
    return me


def name_deputy(
    db: Session, tenant: str, user_id: int, deputy: str, start: date, end: date, reason: str | None = None
) -> int:
    """Name a deputy from `start` to `end` (both included). Returns the delegation's id."""
    me = _me(db, user_id)
    if deputy == me:
        raise DeputyError("a deputy is someone else")
    if end < start:
        raise DeputyError("the last day comes before the first")
    if start < business_today():
        raise DeputyError("a deputy starts today or later")
    if not db.execute(text("select 1 from org_user where code = :c and active"), {"c": deputy}).first():
        raise DeputyError(f"there is no active person {deputy}")
    login = notifications.login_of(db, tenant, deputy)
    if login is None or not permissions.can_anywhere(db, login, "request.approve"):
        raise DeputyError(f"{workflow.name_of(db, deputy)} cannot approve requests, so cannot stand in")
    new_id = int(
        db.execute(
            text(
                "insert into delegation (from_user, to_user, start_date, end_date, reason) values (:f, :t, :s, :e, :r) "
                "returning id"
            ),
            {"f": me, "t": deputy, "s": start, "e": end, "r": (reason or "").strip() or None},
        ).scalar_one()
    )
    audit.record(
        db, Actor("user", str(user_id)), "delegation.create", "delegation", new_id,
        after={"from": me, "to": deputy, "start": start.isoformat(), "end": end.isoformat()}, reason=reason,
    )  # fmt: skip
    db.commit()
    workflow.notify_person(
        db, tenant, deputy, "approval", f"{workflow.name_of(db, me)} named you deputy",
        f"From {start:%d %b %Y} to {end:%d %b %Y}, approvals for {workflow.name_of(db, me)} come to you.",
    )  # fmt: skip
    return new_id


def candidates(db: Session, user_id: int) -> list[dict[str, Any]]:
    """The people one could name (the server still checks that the one chosen may approve)."""
    me = _me(db, user_id)
    return [
        dict(r)
        for r in db.execute(
            text("select code, name from org_user where active and code <> :me order by name"), {"me": me}
        ).mappings()
    ]


def mine(db: Session, user_id: int) -> list[dict[str, Any]]:
    """The deputies this person has named that are not over yet."""
    me = _me(db, user_id)
    return [
        {**dict(r), "start_date": r["start_date"].isoformat(), "end_date": r["end_date"] and r["end_date"].isoformat()}
        for r in db.execute(
            text(
                "select d.id, d.to_user, u.name as to_name, d.start_date, d.end_date, d.reason from delegation d "
                "left join org_user u on u.code = d.to_user where d.from_user = :me "
                "and (d.end_date is null or d.end_date >= :today) order by d.start_date"
            ),
            {"me": me, "today": business_today()},
        ).mappings()
    ]


def remove(db: Session, user_id: int, delegation_id: int) -> None:
    """Take back a deputy one named (steps already given to the deputy stay with them)."""
    me = _me(db, user_id)
    row = (
        db.execute(text("select * from delegation where id = :i and from_user = :me"), {"i": delegation_id, "me": me})
        .mappings()
        .first()
    )
    if row is None:
        raise DeputyError("no such deputy of yours")
    db.execute(text("delete from delegation where id = :i"), {"i": delegation_id})
    audit.record(
        db, Actor("user", str(user_id)), "delegation.remove", "delegation", delegation_id,
        before={"from": me, "to": row["to_user"], "start": str(row["start_date"]), "end": str(row["end_date"])},
    )  # fmt: skip
    db.commit()
