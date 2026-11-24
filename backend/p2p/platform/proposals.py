"""Human confirmation: a proposal is a sensitive action that waits for an allowed person.

Services and agents never run these actions themselves. They create a proposal. A person with the right permission
approves it (optionally after editing the payload) or rejects it. Approving runs the registered executor, which goes
through the normal service, after the same permission, limit and segregation-of-duties checks as any other approval.
Always a person: payment runs, vendor activation, releasing a payment block, accepting a price variance, approvals
above a limit. Whether other actions may skip this step is decided by the autonomy gate, not here.
"""

import json
from collections.abc import Callable
from contextvars import ContextVar
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


class StaleError(ProposalError):
    """What a proposal was made for changed before it was decided; the proposal must be asked for again."""


@dataclass(frozen=True)
class ProposalRef:
    id: int
    action: str
    status: str
    payload: dict[str, Any]


Executor = Callable[[Session, dict[str, Any], Actor], dict[str, Any] | None]
EXECUTORS: dict[str, Executor] = {}


Guard = Callable[[Session, dict[str, Any], Actor], None]
GUARDS: dict[str, Guard] = {}

# NEEDS block 9: `platform/proposals.closer(action)` is block 9's hook (a rejected excess). Block 13 adds this interim
# so a rejected `po.short_close` knows its outcome; block 9's version replaces it at merge (same name and shape).
Closer = Callable[[Session, dict[str, Any], Actor, str, str], None]
CLOSERS: dict[str, Closer] = {}


def closer(action: str) -> Callable[[Closer], Closer]:
    """What happens to a proposal's subject when the proposal is rejected or expires (a row back to waiting)."""

    def wrap(fn: Closer) -> Closer:
        CLOSERS[action] = fn
        return fn

    return wrap


# While an executor runs: the proposal's id and who proposed it (an executor that records the maker reads it here).
RUNNING: ContextVar[tuple[int, Actor] | None] = ContextVar("proposal_running", default=None)


def executor(action: str) -> Callable[[Executor], Executor]:
    def wrap(fn: Executor) -> Executor:
        EXECUTORS[action] = fn
        return fn

    return wrap


def guard(action: str) -> Callable[[Guard], Guard]:
    """Checks an action needs on the approver beyond permission and limit (who the rule names, segregation of duties).
    They run before the decision is claimed, so a refused person leaves the proposal waiting for the right one."""

    def wrap(fn: Guard) -> Guard:
        GUARDS[action] = fn
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
    """An allowed person approves, and the action runs exactly as it was proposed.

    An approver who changes the payload does not approve: the changed version becomes a new proposal by that person,
    which someone else decides (DF-04). The decision is claimed and committed before the action runs, so an action that
    commits part of its work, or fails half way, can never be run a second time by another approver (DF-28)."""
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
    if edited_payload is not None and edited_payload != p.payload:
        return _replace(db, p, approver, edited_payload, now, note)

    run = EXECUTORS.get(p.action)
    if run is None:
        db.rollback()
        raise ProposalError(f"no executor registered for '{p.action}'")
    check = GUARDS.get(p.action)
    if check is not None:
        try:
            check(db, dict(p.payload), approver)
        except permissions.PermissionDenied:
            db.rollback()
            raise
        except StaleError:
            db.rollback()
            raise
        except Exception as exc:  # the action's own refusal (segregation of duties, not the named person): a 403
            db.rollback()
            raise permissions.PermissionDenied(str(exc)) from None
    _recommended(db, p, user_id, note, now)
    db.execute(
        text(
            "update proposal set status = 'approved', decided_by = :u, decided_at = :n, decision_note = :note where id = :i"
        ),
        {"u": user_id, "n": now, "note": note, "i": proposal_id},
    )
    audit.record(db, approver, "proposal.approved", "proposal", proposal_id, reason=note)
    db.commit()  # claimed: nobody else can approve it from here on
    running = RUNNING.set((proposal_id, proposer))
    try:
        result = run(db, dict(p.payload), approver)
        execution = "ok"
        db.commit()
    except Exception as exc:  # noqa: BLE001 - the failure is recorded on the proposal and shown to the person
        db.rollback()
        result, execution = {"error": f"{type(exc).__name__}: {str(exc)[:300]}"}, "failed"
    finally:
        RUNNING.reset(running)
    db.execute(
        text("update proposal set execution = :x, result = cast(:r as jsonb) where id = :i"),
        {"x": execution, "r": json.dumps(result, default=str) if result is not None else None, "i": proposal_id},
    )
    audit.record(
        db, approver, f"proposal.executed_{execution}", "proposal", proposal_id, after={"result": result}, reason=note
    )
    db.commit()
    return ProposalRef(proposal_id, p.action, "approved", dict(p.payload))


def _recommended(db: Session, p: Any, user_id: int, note: str | None, now: datetime) -> None:
    """A proposal made from an agent's recommendation (11a.2, 11a.3): its figures are checked again before anything
    runs. Changed figures close the proposal (a fresh recommendation is asked for) and stop the approval; otherwise the
    recommendation records who confirmed it, in the same transaction as the approval."""
    from p2p.agents import recommendations  # noqa: PLC0415 - agents build on this module

    if recommendations.KEY not in (p.payload or {}):
        return
    stale = recommendations.check_before_confirm(db, dict(p.payload), user_id)
    if stale is not None:
        why = f"the figures changed since this was recommended: {stale}; a fresh recommendation is being prepared"
        _set(db, p.id, "withdrawn", None, now, why)  # nobody can approve it on the old figures
        raise ProposalError(why)
    recommendations.on_decision(db, dict(p.payload), user_id, accepted=True, note=note)


def _replace(
    db: Session, p: Any, editor: Actor, payload: dict[str, Any], now: datetime, note: str | None
) -> ProposalRef:
    """The approver changed what was proposed: the original is closed and the changed version waits for someone else."""
    row = db.execute(text("select summary, risk, case_id, expires_at from proposal where id = :i"), {"i": p.id}).one()
    new_id = int(
        db.execute(
            text(
                "insert into proposal (action, summary, payload, proposed_by_type, proposed_by_id, proposed_for, risk, "
                "required_permission, document, value, case_id, expires_at) values (:a, :s, cast(:p as jsonb), :pt, :pi, "
                ":pf, :r, :rp, :d, :v, :c, :e) returning id"
            ),
            {
                "a": p.action, "s": f"{row[0]} (changed by the approver of proposal {p.id})",
                "p": json.dumps(payload, default=str), "pt": editor.kind, "pi": editor.id, "pf": editor.acts_as,
                "r": row[1], "rp": p.required_permission, "d": p.document, "v": p.value, "c": row[2], "e": row[3],
            },
        ).scalar_one()
    )  # fmt: skip
    why = f"changed before approval; the changed version is proposal {new_id}, for another person to decide"
    db.execute(
        text(
            "update proposal set status = 'rejected', decided_by = :u, decided_at = :n, decision_note = :w where id = :i"
        ),
        {"u": editor.id, "n": now, "w": why + (f". {note}" if note else ""), "i": p.id},
    )
    audit.record(
        db, editor, "proposal.replaced", "proposal", p.id, before=p.payload, after={"new": new_id}, reason=note
    )
    audit.record(
        db,
        editor,
        "proposal.create",
        "proposal",
        new_id,
        before={"replaces": p.id},
        after={"action": p.action, "payload": payload},
    )
    db.commit()
    return ProposalRef(new_id, p.action, "pending", payload)


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
    from p2p.agents import recommendations  # noqa: PLC0415 - agents build on this module

    recommendations.on_decision(db, dict(p.payload or {}), user_id, accepted=False, note=reason)
    _set(db, proposal_id, "rejected", user_id, now or datetime.now(UTC), reason)
    stop = CLOSERS.get(p.action)
    if stop is not None:
        stop(db, dict(p.payload or {}), sod.user(user_id), "rejected", reason)


def withdraw(db: Session, proposal_id: int, user_id: int | None, note: str) -> None:
    """A proposal that is no longer needed (what it asked for was cancelled): it expires, and nobody has to decide it."""
    p = _load(db, proposal_id)
    if p.status != "pending":
        db.rollback()
        return
    _set(db, proposal_id, "expired", user_id, datetime.now(UTC), note)


def expire_due(db: Session, now: datetime | None = None, *, action: str | None = None) -> int:
    """Proposals past their expiry without a decision become expired (of one action only, when it is given)."""
    moment = now or datetime.now(UTC)
    rows = db.execute(
        text(
            "update proposal set status = 'expired', decided_at = :n where status = 'pending' and expires_at <= :n "
            "and (cast(:a as text) is null or action = :a) returning id, action, payload"
        ),
        {"n": moment, "a": action},
    ).all()
    for r in rows:
        audit.record(db, audit.SYSTEM, "proposal.expired", "proposal", r[0])
        stop = CLOSERS.get(str(r[1]))
        if stop is not None:
            stop(db, dict(r[2] or {}), audit.SYSTEM, "expired", "expired before a decision")
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
