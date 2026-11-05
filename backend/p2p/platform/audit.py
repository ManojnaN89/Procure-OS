"""The one place that writes the audit log.

Every service that changes data calls record() in the same transaction.
"""

import json
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from p2p.platform.sod import Actor

SYSTEM = Actor("service", "system")


def _json(value: Any) -> str | None:
    return None if value is None else json.dumps(value, default=str, sort_keys=True)


def record(
    db: Session,
    actor: Actor,
    action: str,
    entity: str,
    entity_id: object,
    *,
    before: Any = None,
    after: Any = None,
    reason: str | None = None,
    rule_version: str | None = None,
    model: str | None = None,
    prompt_version: str | None = None,
) -> None:
    """Add an audit row to the caller's transaction; it commits or rolls back with the write it describes."""
    db.execute(
        text(
            "insert into audit_event (actor_type, actor_id, action, entity, entity_id, before, after, reason, "
            "rule_version, model, prompt_version) values (:t, :i, :a, :e, :eid, "
            "cast(:b as jsonb), cast(:af as jsonb), :r, :rv, :m, :pv)"
        ),
        {
            "t": actor.kind,
            "i": actor.id,
            "a": action,
            "e": entity,
            "eid": str(entity_id),
            "b": _json(before),
            "af": _json(after),
            "r": reason,
            "rv": rule_version,
            "m": model,
            "pv": prompt_version,
        },
    )
