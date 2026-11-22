"""Maker-checker on the company's controls (D-22): a change to rules, rule versions, policies and tolerances, automation,
roles, permissions, limits, whether a person may log in and who their manager is, is a proposal that a different
administrator approves. Nothing changes before that. A change not approved within seven days expires (nothing applied),
and while one change to a setting waits, a second change to the same setting is refused.

Each kind of change has one handler that checks the change and, when told to, writes it. The handler runs twice: when
the change is proposed (to refuse nonsense at once and to show the old and the new value) and when it is approved
(checked again against the setup as it is then, and written with the setup history and the audit row, in the name of the
administrator who proposed it, and the history row names the one who approved it). The proposer never approves
(segregation of duties in `proposals`), and nobody approves a change to their own access, roles, limits or manager. Stopping automation (the kill switch on) is not a change of this kind: it is
immediate.
"""

import json
from collections.abc import Callable
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.orm import Session

from p2p.platform import proposals, routing, sod, worker
from p2p.platform.rows import rows as _rows
from p2p.platform.sod import Actor

ACTION = "setup.change"
EXPIRE_JOB = "setup_change_expire"
EXPIRES_AFTER = timedelta(days=7)

# While an approved change is written: who approved it (the setup history records it beside the proposer).
APPROVED_BY: ContextVar[str | None] = ContextVar("setup_change_approved_by", default=None)


@dataclass(frozen=True)
class Change:
    """What a change does: the setting it touches (area and key, as in the setup history), from what, to what."""

    area: str
    key: str
    old: Any
    new: Any
    result: dict[str, Any] = field(default_factory=dict)  # what the screen gets back once it is applied
    person: int | None = None  # the login whose own access, roles, limits or manager it changes


Handler = Callable[[Session, Actor, dict[str, Any], str, bool], Change]
HANDLERS: dict[str, tuple[str, Handler]] = {}  # kind -> (permission to propose and approve it, handler)


def _json(value: Any) -> Any:
    """Plain JSON as the history keeps it (amounts as text, never as floats)."""
    return json.loads(json.dumps(value, default=str))


def handler(kind: str, permission: str) -> Callable[[Handler], Handler]:
    def wrap(fn: Handler) -> Handler:
        HANDLERS[kind] = (permission, fn)
        return fn

    return wrap


def submit(
    db: Session, maker: Actor, kind: str, args: dict[str, Any], reason: str, *, tenant: str | None = None
) -> dict[str, Any]:
    """Check the change and record it as a proposal for another administrator. Raises the handler's HTTP error."""
    permission, fn = HANDLERS[kind]
    change = _check(db, fn, maker, args, reason)
    if change.person is not None and str(change.person) == maker.id:
        raise HTTPException(403, "you cannot change your own access, roles or limits; another administrator has to")
    _none_waiting(db, change)
    pid = proposals.propose(
        db,
        maker,
        action=ACTION,
        summary=f"Change {change.area.replace('_', ' ')} {change.key}: {reason}",
        payload={
            "kind": kind,
            "args": args,
            "reason": reason,
            "area": change.area,
            "key": change.key,
            "old": _json(change.old),
            "new": _json(change.new),
        },
        required_permission=permission,
        risk="high",
        expires_at=datetime.now(UTC) + EXPIRES_AFTER,
    )
    if tenant:  # the worker expires it when its time is up, even if nobody opens the list of waiting changes
        worker.enqueue(tenant, EXPIRE_JOB, {"proposal_id": pid}, run_at=datetime.now(UTC) + EXPIRES_AFTER)
    return {
        "status": "waiting",
        "proposal_id": pid,
        "old": _json(change.old),
        "new": _json(change.new),
        "message": "Saved for approval: another administrator has to approve it before it applies",
    }


WHOLE_SET = {"tolerance": "the tolerances"}  # one waiting change for the whole set (owner decision 10, 22 Nov 2026)


def _none_waiting(db: Session, change: Change) -> None:
    """One change at a time to a setting: a second one while the first waits would be decided against a stale value.
    The tolerances are one setting: they are versioned as a set."""
    whole = change.area in WHOLE_SET
    waiting = db.execute(
        text(
            "select id from proposal where action = :a and status = 'pending' and (expires_at is null or expires_at > now()) "
            "and payload->>'area' = :area and (:whole or payload->>'key' = :key) limit 1"
        ),
        {"a": ACTION, "area": change.area, "key": change.key, "whole": whole},
    ).scalar()
    if waiting is not None:
        what = WHOLE_SET[change.area] if whole else f"{change.area.replace('_', ' ')} {change.key}"
        raise HTTPException(
            409, f"a change to {what} is already waiting for approval (proposal {waiting}); approve or reject it first"
        )


def _check(db: Session, fn: Handler, maker: Actor, args: dict[str, Any], reason: str) -> Change:
    """Run the handler's checks inside a savepoint that is always undone, so the transaction (and any lock the caller
    holds) is left as it was."""
    savepoint = db.begin_nested()
    try:
        return fn(db, maker, args, reason, False)
    finally:
        savepoint.rollback()


def _run(db: Session, payload: dict[str, Any], apply: bool, maker: Actor) -> Change:
    entry = HANDLERS.get(str(payload.get("kind")))
    if entry is None:
        raise ValueError(f"unknown setup change '{payload.get('kind')}'")
    args, reason = dict(payload.get("args") or {}), str(payload.get("reason") or "")
    try:
        return entry[1](db, maker, args, reason, True) if apply else _check(db, entry[1], maker, args, reason)
    except HTTPException as exc:  # refused now: the setup changed since it was proposed
        raise ValueError(str(exc.detail)) from None


@proposals.guard(ACTION)
def _not_for_yourself(db: Session, payload: dict[str, Any], approver: Actor) -> None:
    """Nobody widens their own powers, as maker or as checker (DF-15)."""
    try:
        change = _run(db, payload, False, approver)
    except ValueError:
        return  # no longer possible: approving records why, so the proposer sees it
    if change.person is not None and str(change.person) == approver.id:
        raise PermissionError("this changes your own access, roles or limits; another administrator has to approve it")


@proposals.executor(ACTION)
def _apply(db: Session, payload: dict[str, Any], approver: Actor) -> dict[str, Any]:
    running = proposals.RUNNING.get()
    maker = running[1] if running else approver
    token = APPROVED_BY.set(approver.id)
    try:
        return _run(db, payload, True, maker).result
    finally:
        APPROVED_BY.reset(token)


def pending(db: Session, now: datetime | None = None) -> list[dict[str, Any]]:
    """Changes waiting for a second administrator, with the old value as it is now, the new value and the reason; and
    those that expired in the last seven days unapproved (nothing applied), so the proposer sees what became of them."""
    now = now or datetime.now(UTC)
    proposals.expire_due(db, now, action=ACTION)
    names = {r[0]: str(r[1]) for r in db.execute(text("select user_id, display_name from user_profile"))}
    out = []
    for p in _rows(
        db,
        "select id, payload, proposed_by_id, required_permission, created_at, status, expires_at from proposal "
        "where action = :a and (status = 'pending' or (status = 'expired' and decided_at > :since)) order by id",
        a=ACTION,
        since=now - EXPIRES_AFTER,
    ):
        payload = p["payload"]
        maker = sod.user(int(p["proposed_by_id"]))
        stored = {"area": payload.get("area") or payload.get("kind"), "key": payload.get("key") or ""}
        row: dict[str, Any] = {**stored, "old": payload.get("old"), "new": payload.get("new"), "problem": None}
        if p["status"] == "pending":
            try:
                change = _run(db, payload, False, maker)
                row.update(area=change.area, key=change.key, old=_json(change.old), new=_json(change.new))
            except ValueError as exc:
                row["problem"] = str(exc)
        out.append(
            {
                **row,
                "proposal_id": p["id"],
                "reason": payload.get("reason"),
                "by": int(p["proposed_by_id"]),
                "by_name": names.get(int(p["proposed_by_id"]), str(p["proposed_by_id"])),
                "at": p["created_at"].isoformat(),
                "permission": p["required_permission"],
                "expired": p["status"] == "expired",
                "expires_at": p["expires_at"].isoformat() if p["expires_at"] else None,
            }
        )
    return out


@worker.register(EXPIRE_JOB)
def _expire(ctx: worker.JobContext) -> None:
    """A control change nobody decided within its time: it expires and nothing is applied."""
    with Session(routing.engine_for(ctx.tenant)) as db:
        proposals.expire_due(db, ctx.now, action=ACTION)
