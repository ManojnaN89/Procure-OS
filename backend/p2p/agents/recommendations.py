"""The recommendation record (task 11a.3, D39 rule 7): what an agent recommended, on what, and who confirmed it.

One row per recommendation: the capability and kind, the business key, the agent's words, the evidence (tool results,
documents, mails), the engine's figures it was given and their hash, the rule versions, the run and workflow, the
proposal a person decides, who directed the agent (the maker, C1) and who confirmed it. The evidence never changes once
written (a database trigger). A newer recommendation of the same kind and key supersedes an open one.

Before a person's confirmation runs, the figures are worked out again (platform/workflows.py): changed figures make the
recommendation stale and ask for a fresh one, and the confirmation does not run. Whoever directed the agent never
confirms its recommendation.
"""

import json
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from p2p.platform import audit, permissions, workflows
from p2p.platform.sod import Actor

KEY = "_recommendation_id"  # in a proposal's payload: the recommendation it was made from (executors drop `_` keys)


class RecommendationError(Exception):
    def __init__(self, message: str, status: int = 409) -> None:
        super().__init__(message)
        self.status = status


def record(
    db: Session,
    *,
    capability: str,
    kind: str,
    business_key: str,
    summary: str,
    evidence: list[dict[str, Any]],
    engine_figures: dict[str, Any],
    actor: Actor,
    rule_versions: dict[str, Any] | None = None,
    run_id: int | None = None,
    workflow_id: int | None = None,
    event_id: int | None = None,
    directed_by: int | None = None,
) -> int:
    """Keep a recommendation; an open one of the same kind and key is superseded by it. Does not commit."""
    if not summary.strip():
        raise ValueError("a recommendation needs the agent's words")
    if not evidence:
        raise ValueError("a recommendation needs its evidence")
    if not engine_figures:
        raise ValueError("a recommendation needs the engine's figures it was based on")
    figures = json.loads(json.dumps(engine_figures, default=str))
    basis_hash = workflows.hash_of(figures)
    rec_id = int(
        db.execute(
            text(
                "insert into recommendation (capability, kind, business_key, workflow_id, run_id, event_id, summary, "
                "evidence, engine_figures, rule_versions, basis_hash, created_by, directed_by) values (:c, :k, :b, :w, "
                ":r, :e, :s, cast(:ev as jsonb), cast(:f as jsonb), cast(:rv as jsonb), :h, :by, :d) returning id"
            ),
            {
                "c": capability, "k": kind, "b": business_key, "w": workflow_id, "r": run_id, "e": event_id,
                "s": summary, "ev": json.dumps(evidence, default=str), "f": json.dumps(figures),
                "rv": json.dumps(rule_versions or {}, default=str), "h": basis_hash, "by": f"{actor.kind}:{actor.id}",
                "d": directed_by,
            },
        ).scalar_one()
    )  # fmt: skip
    older = db.execute(
        text(
            "update recommendation set status = 'superseded', superseded_by = :n "
            "where kind = :k and business_key = :b and status = 'proposed' and id <> :n returning id"
        ),
        {"n": rec_id, "k": kind, "b": business_key},
    ).scalars()
    audit.record(
        db, actor, "recommendation.record", "recommendation", rec_id,
        after={"capability": capability, "kind": kind, "key": business_key, "basis_hash": basis_hash,
               "supersedes": list(older)},
        rule_version=json.dumps(rule_versions, sort_keys=True, default=str) if rule_versions else None,
    )  # fmt: skip
    return rec_id


def get(db: Session, rec_id: int, *, lock: bool = False) -> dict[str, Any] | None:
    row = (
        db.execute(
            text("select * from recommendation where id = :i" + (" for update" if lock else "")),  # noqa: S608 - fixed text
            {"i": rec_id},
        )
        .mappings()
        .first()
    )
    return dict(row) if row else None


def link_proposal(db: Session, rec_id: int, proposal_id: int) -> None:
    db.execute(text("update recommendation set proposal_id = :p where id = :i"), {"p": proposal_id, "i": rec_id})
    audit.record(db, audit.SYSTEM, "recommendation.link", "recommendation", rec_id, after={"proposal": proposal_id})


def _set(db: Session, rec_id: int, status: str, action: str, actor: Actor, **cols: Any) -> None:
    sets = ", ".join(f"{k} = :{k}" for k in cols)
    db.execute(
        text(f"update recommendation set status = :s{', ' + sets if sets else ''} where id = :i"),  # noqa: S608 - fixed column names
        {"s": status, "i": rec_id, **cols},
    )
    audit.record(db, actor, action, "recommendation", rec_id, after={"status": status, **cols})


def decide(db: Session, rec_id: int, user_id: int, *, accepted: bool, note: str | None = None) -> None:
    """A person confirms or turns down a recommendation. The person who directed the agent may turn it down, never
    confirm it. Does not commit."""
    rec = get(db, rec_id, lock=True)
    if rec is None:
        raise RecommendationError(f"recommendation {rec_id} not found", 404)
    if rec["status"] != "proposed":
        raise RecommendationError(f"recommendation {rec_id} is {rec['status']} and cannot be decided")
    if accepted and rec["directed_by"] is not None and int(rec["directed_by"]) == user_id:
        raise permissions.PermissionDenied("whoever directed the agent cannot confirm what it recommends (C1)")
    _set(
        db, rec_id, "accepted" if accepted else "rejected",
        "recommendation.accept" if accepted else "recommendation.reject", Actor("user", str(user_id)),
        decided_by=user_id, decided_at=_now(db), decision_note=note,
    )  # fmt: skip


def _now(db: Session) -> Any:
    return db.execute(text("select now()")).scalar_one()


def mark_stale(db: Session, rec_id: int, reason: str) -> dict[str, Any]:
    rec = get(db, rec_id, lock=True)
    if rec is None:
        raise RecommendationError(f"recommendation {rec_id} not found", 404)
    if rec["status"] == "proposed":
        _set(db, rec_id, "stale", "recommendation.stale", audit.SYSTEM, stale_reason=reason[:500])
    return rec


def withdraw(db: Session, rec_id: int, reason: str) -> None:
    db.execute(
        text(
            "update recommendation set status = 'withdrawn', decision_note = :r where id = :i and status = 'proposed'"
        ),
        {"r": reason, "i": rec_id},
    )
    audit.record(db, audit.SYSTEM, "recommendation.withdraw", "recommendation", rec_id, reason=reason)


def hand_over(db: Session, rec_id: int, task_id: int) -> None:
    _set(db, rec_id, "handed_over", "recommendation.hand_over", audit.SYSTEM, task_id=task_id)


# ---- the hooks proposals call -------------------------------------------------------------------------------------


def check_before_confirm(db: Session, payload: dict[str, Any], user_id: int) -> str | None:
    """Before a person's confirmation of a proposal made from a recommendation runs: the maker cannot confirm it, and
    its figures must still hold. Returns why it is stale (it is then marked stale and a fresh one asked for), else None.
    Raises PermissionDenied for the maker."""
    rec_id = payload.get(KEY)
    if rec_id is None:
        return None
    rec = get(db, int(rec_id))
    if rec is None or rec["status"] != "proposed":
        return None
    if rec["directed_by"] is not None and int(rec["directed_by"]) == user_id:
        raise permissions.PermissionDenied("whoever directed the agent cannot confirm what it recommends (C1)")
    got = workflows.revalidate(db, str(rec["kind"]), str(rec["business_key"]), str(rec["basis_hash"]))
    if isinstance(got, workflows.Stale):
        workflows.refresh(db, int(rec_id), got)
        return got.reason
    return None


def on_decision(db: Session, payload: dict[str, Any], user_id: int, *, accepted: bool, note: str | None) -> None:
    """A proposal made from a recommendation was decided: the recommendation records it in the same transaction."""
    rec_id = payload.get(KEY)
    if rec_id is None:
        return
    rec = get(db, int(rec_id))
    if rec is not None and rec["status"] == "proposed":
        decide(db, int(rec_id), user_id, accepted=accepted, note=note)
