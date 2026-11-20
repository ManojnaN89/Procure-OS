"""The one place that writes the audit log.

Every service that changes data calls record() in the same transaction.
"""

import json
from dataclasses import dataclass
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


CHAINED = ("audit_event", "setup_change")


@dataclass(frozen=True)
class ChainBreak:
    table: str
    id: int
    reason: str


def verify_chain(db: Session, table: str = "audit_event") -> ChainBreak | None:
    """Walk a hash-chained table (migration 0059) in id order; the first row that does not link, or None when whole.

    A row whose content was changed no longer matches its own hash; a row taken out leaves the next one pointing at a
    hash that is not before it. Rows taken off the end leave no trace here: that needs the last hash kept elsewhere.
    """
    if table not in CHAINED:
        raise ValueError(f"{table} has no hash chain")
    found = db.execute(
        text(
            "select id, altered from (select id, row_hash is distinct from chain_row_hash(prev_hash, t) as altered, "  # noqa: S608
            "prev_hash is distinct from coalesce(lag(row_hash) over (order by id), repeat('0', 64)) as unlinked "
            f"from {table} t) x where altered or unlinked order by id limit 1"  # the table is one of CHAINED
        )
    ).first()
    if found is None:
        return None
    reason = (
        "the row was changed after it was written" if found.altered else "the row before it is missing or was changed"
    )
    return ChainBreak(table, found.id, reason)
