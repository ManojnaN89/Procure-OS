"""Case flow: one case per purchase, moving through a fixed set of states.

The state machine is deterministic: a case can only move along the transitions listed here, and every move is
written to case_event and to the audit log.
"""

import json
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from p2p.platform import audit
from p2p.platform.sod import Actor

STATES = ("request", "approval", "sourcing", "order", "supplier", "receipt", "invoice", "payment", "closed")

TRANSITIONS: dict[str, frozenset[str]] = {
    "request": frozenset({"approval", "closed"}),
    "approval": frozenset({"sourcing", "order", "request", "closed"}),  # back to request when returned
    "sourcing": frozenset({"order", "approval", "closed"}),
    "order": frozenset({"supplier", "closed"}),
    "supplier": frozenset({"receipt", "order", "closed"}),  # back to order when the supplier changes it
    "receipt": frozenset({"invoice", "supplier", "closed"}),
    "invoice": frozenset({"payment", "receipt", "closed"}),
    "payment": frozenset({"closed", "invoice"}),
    "closed": frozenset(),
}


class TransitionError(Exception):
    pass


def create_case(
    db: Session, ref: str, actor: Actor, *, title: str | None = None, data: dict[str, Any] | None = None
) -> int:
    case_id = int(
        db.execute(
            text("insert into purchase_case (ref, title, data) values (:r, :t, cast(:d as jsonb)) returning id"),
            {"r": ref, "t": title, "d": json.dumps(data or {}, default=str)},
        ).scalar_one()
    )
    _event(db, case_id, "created", None, "request", actor, None)
    audit.record(db, actor, "case.create", "case", ref, after={"state": "request", "title": title})
    db.commit()
    return case_id


def state_of(db: Session, case_id: int) -> str:
    return str(db.execute(text("select state from purchase_case where id = :i"), {"i": case_id}).scalar_one())


def transition(
    db: Session,
    case_id: int,
    to_state: str,
    actor: Actor,
    *,
    event: str | None = None,
    detail: dict[str, Any] | None = None,
) -> str:
    """Move the case to another state if the flow allows it; otherwise raise TransitionError."""
    row = db.execute(text("select ref, state from purchase_case where id = :i for update"), {"i": case_id}).one()
    ref, current = str(row[0]), str(row[1])
    if to_state not in TRANSITIONS.get(current, frozenset()):
        db.rollback()
        raise TransitionError(f"a case in '{current}' cannot move to '{to_state}'")
    db.execute(
        text("update purchase_case set state = :s, updated_at = now() where id = :i"), {"s": to_state, "i": case_id}
    )
    _event(db, case_id, event or f"moved_to_{to_state}", current, to_state, actor, detail)
    audit.record(db, actor, "case.transition", "case", ref, before={"state": current}, after={"state": to_state})
    db.commit()
    return to_state


def log_event(db: Session, case_id: int, event: str, actor: Actor, detail: dict[str, Any] | None = None) -> None:
    """Add an event that does not change the state (a reminder, an escalation, a received email)."""
    state = state_of(db, case_id)
    _event(db, case_id, event, state, state, actor, detail)


def _event(
    db: Session, case_id: int, event: str, old: str | None, new: str | None, actor: Actor, detail: dict[str, Any] | None
) -> None:
    db.execute(
        text(
            "insert into case_event (case_id, event, from_state, to_state, actor_type, actor_id, detail) "
            "values (:c, :e, :f, :t, :at, :ai, cast(:d as jsonb))"
        ),
        {
            "c": case_id,
            "e": event,
            "f": old,
            "t": new,
            "at": actor.kind,
            "ai": actor.id,
            "d": json.dumps(detail, default=str) if detail else None,
        },
    )
