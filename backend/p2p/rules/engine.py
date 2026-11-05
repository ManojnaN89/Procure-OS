"""Rules engine: editable decision tables (GoRules JDM) with versions.

A table is saved as a draft, then published as the next version. An evaluation uses the published version and
stores its input, output and trace, so any past decision can be reproduced against the version it used.

Rule tables decide things (which approver, which tolerance, which route). They do not compute money: amounts are
only compared with thresholds here, and the formulas package does the arithmetic.
"""

import json
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

import zen
from sqlalchemy import text
from sqlalchemy.orm import Session

from p2p.platform import audit
from p2p.platform.sod import Actor


class RuleError(Exception):
    pass


@dataclass(frozen=True)
class Evaluation:
    id: int
    key: str
    version: int
    result: dict[str, Any]


def _plain(value: Any) -> Any:
    """Make an input JSON-safe. Decimals are only ever compared with thresholds here, so int or float is fine."""
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() else float(value)
    if isinstance(value, dict):
        return {k: _plain(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [_plain(v) for v in value]
    return value


def _compile(jdm: dict[str, Any]) -> Any:
    try:
        return zen.ZenEngine().create_decision(json.dumps(jdm))
    except Exception as exc:  # noqa: BLE001 - the engine raises plain exceptions for a bad table
        raise RuleError(f"not a valid decision table: {str(exc)[:200]}") from None


def save_draft(db: Session, key: str, jdm: dict[str, Any], actor: Actor, note: str | None = None) -> int:
    """Create the draft of a table, or replace the existing draft. Returns the draft's version number."""
    _compile(jdm)
    draft = db.execute(
        text("select id, version, jdm from rule_table where key = :k and status = 'draft'"), {"k": key}
    ).first()
    payload = json.dumps(jdm)
    if draft:
        db.execute(
            text("update rule_table set jdm = cast(:j as jsonb), note = :n, created_by = :a where id = :i"),
            {"j": payload, "n": note, "a": f"{actor.kind}:{actor.id}", "i": draft[0]},
        )
        version = int(draft[1])
        audit.record(
            db, actor, "rule.draft_update", "rule_table", f"{key}@{version}", before=draft[2], after=jdm, reason=note
        )
    else:
        version = int(
            db.execute(
                text("select coalesce(max(version), 0) + 1 from rule_table where key = :k"), {"k": key}
            ).scalar_one()
        )
        db.execute(
            text(
                "insert into rule_table (key, version, status, jdm, note, created_by) "
                "values (:k, :v, 'draft', cast(:j as jsonb), :n, :a)"
            ),
            {"k": key, "v": version, "j": payload, "n": note, "a": f"{actor.kind}:{actor.id}"},
        )
        audit.record(db, actor, "rule.draft_create", "rule_table", f"{key}@{version}", after=jdm, reason=note)
    db.commit()
    return version


def publish(db: Session, key: str, actor: Actor, reason: str | None = None) -> int:
    """Make the draft the live version. The previous live version is archived, not deleted."""
    draft = db.execute(
        text("select id, version, jdm from rule_table where key = :k and status = 'draft'"), {"k": key}
    ).first()
    if draft is None:
        raise RuleError(f"no draft to publish for '{key}'")
    _compile(draft[2])
    previous = db.execute(
        text("update rule_table set status = 'archived' where key = :k and status = 'published' returning version"),
        {"k": key},
    ).scalar()
    db.execute(text("update rule_table set status = 'published', published_at = now() where id = :i"), {"i": draft[0]})
    audit.record(
        db,
        actor,
        "rule.publish",
        "rule_table",
        f"{key}@{draft[1]}",
        before={"published_version": previous},
        after={"published_version": draft[1]},
        reason=reason,
        rule_version=f"{key}@{draft[1]}",
    )
    db.commit()
    return int(draft[1])


def _load(db: Session, key: str, version: int | None) -> tuple[int, dict[str, Any]]:
    if version is None:
        row = db.execute(
            text("select version, jdm from rule_table where key = :k and status = 'published'"), {"k": key}
        ).first()
    else:
        row = db.execute(
            text("select version, jdm from rule_table where key = :k and version = :v"), {"k": key, "v": version}
        ).first()
    if row is None:
        raise RuleError(f"no {'published ' if version is None else ''}version of '{key}' found")
    return int(row[0]), row[1]


def evaluate(
    db: Session,
    key: str,
    data: dict[str, Any],
    actor: Actor,
    *,
    version: int | None = None,
    case_ref: str | None = None,
) -> Evaluation:
    """Run the published table (or a given version) and record the input, output and trace."""
    ver, jdm = _load(db, key, version)
    clean = _plain(data)
    out = _compile(jdm).evaluate(clean, {"trace": True})
    result: dict[str, Any] = out["result"]
    eval_id = db.execute(
        text(
            "insert into rule_evaluation (key, version, input, output, trace, actor, case_ref) values "
            "(:k, :v, cast(:i as jsonb), cast(:o as jsonb), cast(:t as jsonb), :a, :c) returning id"
        ),
        {
            "k": key,
            "v": ver,
            "i": json.dumps(clean, default=str),
            "o": json.dumps(result, default=str),
            "t": json.dumps(out.get("trace"), default=str),
            "a": f"{actor.kind}:{actor.id}",
            "c": case_ref,
        },
    ).scalar_one()
    db.commit()
    return Evaluation(int(eval_id), key, ver, result)


def reproduce(db: Session, evaluation_id: int) -> dict[str, Any]:
    """Run a past evaluation again with its stored input against the version it used."""
    row = db.execute(
        text("select key, version, input from rule_evaluation where id = :i"), {"i": evaluation_id}
    ).first()
    if row is None:
        raise RuleError(f"evaluation {evaluation_id} not found")
    _, jdm = _load(db, row[0], int(row[1]))
    result: dict[str, Any] = _compile(jdm).evaluate(row[2])["result"]
    return result
