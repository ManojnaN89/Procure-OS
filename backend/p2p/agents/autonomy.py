"""FR37: the autonomy gate. May an action happen without a person?

Yes only if all hold: the decision type is set to 'auto', the value is within its cap, the model's confidence is high
enough, the 30-day rate of wrong automatic decisions is within its limit, and the kill switch is off. Anything else
becomes a proposal for a person. The settings are data, changed by an administrator, and the check reads them every
time, so flipping the kill switch stops automatic action on the next decision.
"""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import text
from sqlalchemy.orm import Session

from p2p.platform import audit
from p2p.platform.sod import Actor

WINDOW = timedelta(days=30)


@dataclass(frozen=True)
class GateDecision:
    allowed: bool
    reason: str


def gate(
    db: Session,
    decision_type: str,
    *,
    value: Decimal | None,
    confidence: float | None,
    now: datetime | None = None,
) -> GateDecision:
    now = now or datetime.now(UTC)
    if db.execute(text("select kill_switch from autonomy_global where id = 1")).scalar_one():
        return GateDecision(False, "the kill switch is on")
    row = db.execute(
        text("select level, value_cap, min_confidence, max_wrong_rate from autonomy_setting where decision_type = :d"),
        {"d": decision_type},
    ).first()
    if row is None or row[0] != "auto":
        return GateDecision(False, f"'{decision_type}' is set to {row[0] if row else 'propose'}, not auto")
    level, cap, min_conf, max_wrong = row
    if cap is not None and (value is None or value > cap):
        return GateDecision(False, f"value {value} is above the cap {cap}")
    if confidence is None or Decimal(str(confidence)) < min_conf:
        return GateDecision(False, f"confidence {confidence} is below {min_conf}")
    n, wrong = db.execute(
        text(
            "select count(*), count(*) filter (where wrong) from autonomy_outcome "
            "where decision_type = :d and auto and at >= :since"
        ),
        {"d": decision_type, "since": now - WINDOW},
    ).one()
    if n and Decimal(wrong) / Decimal(n) > max_wrong:
        return GateDecision(False, f"{wrong} of {n} automatic decisions in 30 days were wrong, above {max_wrong}")
    return GateDecision(True, "within the autonomy limits")


def record_outcome(
    db: Session, decision_type: str, *, auto: bool, ref: str | None = None, now: datetime | None = None
) -> int:
    row = db.execute(
        text("insert into autonomy_outcome (decision_type, auto, ref, at) values (:d, :a, :r, :n) returning id"),
        {"d": decision_type, "a": auto, "r": ref, "n": now or datetime.now(UTC)},
    ).scalar_one()
    db.commit()
    return int(row)


def mark_wrong(db: Session, outcome_id: int, actor: Actor, reason: str) -> None:
    """A person says an automatic decision was wrong. It counts against the track record."""
    db.execute(text("update autonomy_outcome set wrong = true where id = :i"), {"i": outcome_id})
    audit.record(db, actor, "autonomy.marked_wrong", "autonomy_outcome", outcome_id, reason=reason)
    db.commit()


def configure(
    db: Session,
    actor: Actor,
    decision_type: str,
    *,
    level: str,
    value_cap: Decimal | None = None,
    min_confidence: Decimal = Decimal("0.9"),
    max_wrong_rate: Decimal = Decimal("0.02"),
) -> None:
    db.execute(
        text(
            "insert into autonomy_setting (decision_type, level, value_cap, min_confidence, max_wrong_rate) "
            "values (:d, :l, :c, :m, :w) on conflict (decision_type) do update set level = :l, value_cap = :c, "
            "min_confidence = :m, max_wrong_rate = :w, updated_at = now()"
        ),
        {"d": decision_type, "l": level, "c": value_cap, "m": min_confidence, "w": max_wrong_rate},
    )
    audit.record(
        db,
        actor,
        "autonomy.configure",
        "autonomy_setting",
        decision_type,
        after={
            "level": level,
            "value_cap": value_cap,
            "min_confidence": min_confidence,
            "max_wrong_rate": max_wrong_rate,
        },
    )
    db.commit()


def set_kill_switch(db: Session, actor: Actor, on: bool, reason: str) -> None:
    db.execute(text("update autonomy_global set kill_switch = :o where id = 1"), {"o": on})
    audit.record(db, actor, "autonomy.kill_switch", "autonomy_global", 1, after={"on": on}, reason=reason)
    db.commit()
