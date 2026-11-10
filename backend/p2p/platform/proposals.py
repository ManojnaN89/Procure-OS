"""Human confirmation: a proposal is a sensitive action that waits for an allowed person.

Services and agents never run these actions themselves. They create a proposal. A person with the right permission
approves it (optionally after editing the payload) or rejects it. Approving runs the registered executor, which goes
through the normal service, after the same permission, limit and segregation-of-duties checks as any other approval.
Always a person: payment runs, vendor activation, releasing a payment block, accepting a price variance, approvals
above a limit. Whether other actions may skip this step is decided by the autonomy gate, not here.
"""

import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from p2p.platform import audit, permissions, sod
from p2p.platform.sod import Actor


class ProposalError(Exception):
    pass


@dataclass(frozen=True)
class ProposalRef:
    id: int
    action: str
    status: str
    payload: dict[str, Any]


Executor = Callable[[Session, dict[str, Any], Actor], dict[str, Any] | None]
EXECUTORS: dict[str, Executor] = {}


def executor(action: str) -> Callable[[Executor], Executor]:
    def wrap(fn: Executor) -> Executor:
        EXECUTORS[action] = fn
        return fn

    return wrap


def propose(
    db: Session,
    proposer: Actor,
    *,
    action: str,
    summary: str,
    payload: dict[str, Any],
    required_permission: str,
    risk: str = "medium",
    document: str | None = None,
    value: Decimal | None = None,
    case_id: int | None = None,
    expires_at: datetime | None = None,
) -> int:
    """Record the proposal. Nothing is executed."""
    pid = int(
        db.execute(
            text(
                "insert into proposal (action, summary, payload, proposed_by_type, proposed_by_id, proposed_for, risk, "
                "required_permission, document, value, case_id, expires_at) values (:a, :s, cast(:p as jsonb), :pt, :pi, "
                ":pf, :r, :rp, :d, :v, :c, :e) returning id"
            ),
            {
                "a": action,
                "s": summary,
                "p": json.dumps(payload, default=str),
                "pt": proposer.kind,
                "pi": proposer.id,
                "pf": proposer.acts_as,
                "r": risk,
                "rp": required_permission,
                "d": document,
                "v": value,
                "c": case_id,
                "e": expires_at,
            },
        ).scalar_one()
    )
    audit.record(
        db, proposer, "proposal.create", "proposal", pid, after={"action": action, "summary": summary, "value": value}
    )
    db.commit()
    return pid


def _load(db: Session, proposal_id: int) -> Any:
    row = db.execute(
        text(
            "select id, action, payload, proposed_by_type, proposed_by_id, proposed_for, required_permission, document, "
            "value, status, expires_at from proposal where id = :i for update"
        ),
        {"i": proposal_id},
    ).first()
    if row is None:
        raise ProposalError(f"proposal {proposal_id} not found")
    return row


def approve(
    db: Session,
    proposal_id: int,
    user_id: int,
    *,
    edited_payload: dict[str, Any] | None = None,
    note: str | None = None,
    now: datetime | None = None,
) -> ProposalRef:
    """An allowed person approves, optionally with an edited payload. The action then runs."""
    now = now or datetime.now(UTC)
    p = _load(db, proposal_id)
    if p.status != "pending":
        db.rollback()
        raise ProposalError(f"proposal {proposal_id} is already {p.status}")
    if p.expires_at is not None and p.expires_at <= now:
        _set(db, proposal_id, "expired", None, now, "expired before a decision")
        raise ProposalError(f"proposal {proposal_id} has expired")
    proposer = Actor(p.proposed_by_type, p.proposed_by_id, p.proposed_for)
    approver = sod.user(user_id)
    _authorize(db, p, user_id, proposer)

    payload = edited_payload if edited_payload is not None else p.payload
    status = "approved" if edited_payload is None else "edited"
    run = EXECUTORS.get(p.action)
    if run is None:
        db.rollback()
        raise ProposalError(f"no executor registered for '{p.action}'")
    try:
        result = run(db, payload, approver)
        execution = "ok"
    except Exception as exc:  # noqa: BLE001 - the failure is recorded on the proposal
        db.rollback()
        result, execution = {"error": f"{type(exc).__name__}: {str(exc)[:300]}"}, "failed"
    db.execute(
        text(
            "update proposal set status = :s, execution = :x, result = cast(:r as jsonb), payload = cast(:p as jsonb), "
            "decided_by = :u, decided_at = :n, decision_note = :note where id = :i"
        ),
        {
            "s": status,
            "x": execution,
            "r": json.dumps(result, default=str) if result is not None else None,
            "p": json.dumps(payload, default=str),
            "u": user_id,
            "n": now,
            "note": note,
            "i": proposal_id,
        },
    )
    audit.record(
        db,
        approver,
        f"proposal.{status}",
        "proposal",
        proposal_id,
        before=p.payload if edited_payload is not None else None,
        after={"payload": payload, "execution": execution, "result": result},
        reason=note,
    )
    db.commit()
    return ProposalRef(proposal_id, p.action, status, payload)


def reject(db: Session, proposal_id: int, user_id: int, reason: str, *, now: datetime | None = None) -> None:
    """Rejecting needs the same permission as approving. A reason is required."""
    if not reason.strip():
        raise ProposalError("a reason is required to reject")
    p = _load(db, proposal_id)
    if p.status != "pending":
        db.rollback()
        raise ProposalError(f"proposal {proposal_id} is already {p.status}")
    if not permissions.can_anywhere(db, user_id, p.required_permission):
        db.rollback()
        raise permissions.PermissionDenied(f"role does not allow {p.required_permission}")
    _set(db, proposal_id, "rejected", user_id, now or datetime.now(UTC), reason)


def withdraw(db: Session, proposal_id: int, user_id: int | None, note: str) -> None:
    """A proposal that is no longer needed (what it asked for was cancelled): it expires, and nobody has to decide it."""
    p = _load(db, proposal_id)
    if p.status != "pending":
        db.rollback()
        return
    _set(db, proposal_id, "expired", user_id, datetime.now(UTC), note)


def expire_due(db: Session, now: datetime | None = None) -> int:
    rows = db.execute(
        text(
            "update proposal set status = 'expired', decided_at = :n where status = 'pending' and expires_at <= :n returning id"
        ),
        {"n": now or datetime.now(UTC)},
    ).all()
    for r in rows:
        audit.record(db, audit.SYSTEM, "proposal.expired", "proposal", r[0])
    db.commit()
    return len(rows)


def _authorize(db: Session, p: Any, user_id: int, proposer: Actor) -> None:
    if not permissions.can_anywhere(db, user_id, p.required_permission):
        db.rollback()
        raise permissions.PermissionDenied(f"role does not allow {p.required_permission}")
    if p.value is not None and p.document and not permissions.within_limit(db, user_id, p.document, Decimal(p.value)):
        db.rollback()
        raise permissions.PermissionDenied(f"value {p.value} is above the user's limit for {p.document}")
    try:
        sod.check_segregation({"requester": proposer}, "approver", sod.user(user_id))
    except sod.SegregationError as exc:
        db.rollback()
        raise permissions.PermissionDenied(str(exc)) from None


def _set(db: Session, proposal_id: int, status: str, user_id: int | None, now: datetime, note: str) -> None:
    db.execute(
        text("update proposal set status = :s, decided_by = :u, decided_at = :n, decision_note = :note where id = :i"),
        {"s": status, "u": user_id, "n": now, "note": note, "i": proposal_id},
    )
    actor = sod.user(user_id) if user_id is not None else audit.SYSTEM
    audit.record(db, actor, f"proposal.{status}", "proposal", proposal_id, reason=note)
    db.commit()
