"""One workflow per business key, and revalidation of what an agent's work was based on (task 11a.2, D39 rules 4, 5).

A trigger for a business key that already has an open workflow of the same kind joins it: it never starts a second
request, RFQ, order, mail or job. Before a person's confirmation runs (and before any automatic action), the engine's
figures behind a recommendation are worked out again: if they changed, or the basis has a problem (a supplier no longer
active, stale ERP data), the action does not run and a fresh recommendation is asked for.
"""

import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from p2p.platform import audit

ACTOR = audit.SYSTEM
OPEN = ("open", "waiting")


def hash_of(figures: Any) -> str:
    """The hash of a set of figures (keys sorted): equal figures, equal hash."""
    return hashlib.sha256(json.dumps(figures, sort_keys=True, default=str).encode()).hexdigest()


@dataclass(frozen=True)
class Joined:
    id: int
    started: bool


@dataclass(frozen=True)
class Basis:
    hash: str
    figures: dict[str, Any]
    problems: list[str] = field(default_factory=list)  # reasons the work cannot go ahead whatever the figures


@dataclass(frozen=True)
class Fresh:
    basis: Basis | None


@dataclass(frozen=True)
class Stale:
    reason: str
    basis: Basis


BasisFn = Callable[[Session, str], Basis]
BASIS: dict[str, BasisFn] = {}


def register_basis(kind: str, fn: BasisFn) -> None:
    """Register how the engine's figures behind work (or a recommendation) of this kind are worked out for a business
    key. A kind with no basis registered is not revalidated: its confirmation goes ahead (see `revalidate`)."""
    BASIS[kind] = fn


def basis(kind: str) -> Callable[[BasisFn], BasisFn]:
    """Decorator form of `register_basis`."""

    def wrap(fn: BasisFn) -> BasisFn:
        BASIS[kind] = fn
        return fn

    return wrap


def join_or_start(
    db: Session,
    kind: str,
    business_key: str,
    *,
    capability: str,
    event_id: int | None = None,
    basis_hash: str | None = None,
) -> Joined:
    """The open workflow of this kind and key, started if there is none (the unique index decides). Does not commit."""
    got = db.execute(
        text(
            "insert into workflow (kind, business_key, capability, basis_hash, first_event_id, last_event_id) "
            "values (:k, :b, :c, :h, :e, :e) on conflict (kind, business_key) where status in ('open', 'waiting') "
            "do nothing returning id"
        ),
        {"k": kind, "b": business_key, "c": capability, "h": basis_hash, "e": event_id},
    ).scalar()
    started = got is not None
    if got is None:
        got = db.execute(
            text(
                "update workflow set last_event_id = coalesce(:e, last_event_id), basis_hash = coalesce(:h, basis_hash), "
                "updated_at = now() where kind = :k and business_key = :b and status in ('open', 'waiting') returning id"
            ),
            {"k": kind, "b": business_key, "h": basis_hash, "e": event_id},
        ).scalar_one()
    if event_id is not None:
        db.execute(text("update event set workflow_id = :w where id = :e"), {"w": got, "e": event_id})
    audit.record(
        db, ACTOR, "workflow.start" if started else "workflow.join", "workflow", got,
        after={"kind": kind, "key": business_key, "event": event_id},
    )  # fmt: skip
    return Joined(int(got), started)


def set_waiting(db: Session, workflow_id: int, run_id: int | None) -> None:
    db.execute(
        text("update workflow set status = 'waiting', run_id = coalesce(:r, run_id), updated_at = now() where id = :i"),
        {"r": run_id, "i": workflow_id},
    )


def close(db: Session, workflow_id: int, status: str, reason: str, *, task_id: int | None = None) -> bool:
    """Close an open workflow: done, cancelled or handed over (to the task). Does not commit."""
    if status not in ("done", "cancelled", "handed_over"):
        raise ValueError(f"a workflow cannot be closed as '{status}'")
    done = db.execute(
        text(
            "update workflow set status = :s, close_reason = :r, task_id = coalesce(:t, task_id), closed_at = now(), "
            "updated_at = now() where id = :i and status in ('open', 'waiting') returning id"
        ),
        {"s": status, "r": reason, "t": task_id, "i": workflow_id},
    ).first()
    if done:
        audit.record(db, ACTOR, "workflow.close", "workflow", workflow_id, after={"status": status}, reason=reason)
    return done is not None


def open_for(db: Session, capability: str) -> list[dict[str, Any]]:
    return [
        dict(r)
        for r in db.execute(
            text(
                "select id, kind, business_key, last_event_id, run_id from workflow "
                "where capability = :c and status in ('open', 'waiting') order by id"
            ),
            {"c": capability},
        ).mappings()
    ]


def revalidate(db: Session, kind: str, business_key: str, expected_hash: str) -> Fresh | Stale:
    """Work the basis out again through the engine: stale when the figures changed or it has a problem. A kind with no
    registered basis is Fresh(None): not revalidated, and the confirmation goes ahead."""
    fn = BASIS.get(kind)
    if fn is None:
        return Fresh(None)  # nothing registered to check against
    now = fn(db, business_key)
    if now.problems:
        return Stale("; ".join(now.problems), now)
    if now.hash != expected_hash:
        return Stale("the figures it was based on have changed", now)
    return Fresh(now)


def refresh(db: Session, recommendation_id: int, stale: Stale) -> None:
    """A stale recommendation: marked so, and an event asks for a fresh one by the normal route. Does not commit."""
    from p2p.agents import recommendations  # noqa: PLC0415 - recommendations call back into this module
    from p2p.platform import events  # noqa: PLC0415

    rec = recommendations.mark_stale(db, recommendation_id, stale.reason)
    events.emit(
        db, f"{rec['kind']}.refresh", f"{rec['business_key']}:{stale.basis.hash}", source="revalidation",
        payload={"recommendation_id": recommendation_id, "reason": stale.reason},
    )  # fmt: skip
