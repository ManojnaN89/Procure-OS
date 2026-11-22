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

from p2p.formulas.money import format_inr
from p2p.platform import audit
from p2p.platform.sod import Actor

WINDOW = timedelta(days=30)

# Decisions that a person always makes (rule 5). No setting, by anyone, lets them happen without one.
LOCKED = frozenset({"payment_run.approve", "supplier.set_status", "invoice.release_block"})

# What each decision is called on the Automation screen, so a reason names it the way a person sees it.
LABELS = {
    "rfq.send": "Send a request for quotation",
    "rfq.award": "Award to the recommended supplier",
    "quote.read": "Accept a quote read from an email",
    "po.send": "Send an approved purchase order",
    "po.issue": "Issue a touchless purchase order",
    "invoice.read": "Enter an invoice read from a document",
    "supplier.change": "Apply a small change a supplier asks for",
    "supplier.remind": "Chase a supplier by email",
    "planning.explain": "Explain a material request's shortages",
    "payment_run.approve": "Approve a payment run",
    "supplier.set_status": "Activate or block a supplier",
    "invoice.release_block": "Release a payment block on an invoice",
    # the other permissions a proposal can wait for, named for the admin's home page
    "payment.approve": "Approve a payment run",
    "invoice.approve": "Release a payment block on an invoice",
    "po.approve": "Approve a purchase order or its price",
    "supplier.activate": "Activate or block a supplier",
}
LEVEL_WORDS = {"propose": "a person confirms it first", "off": "it is done by hand"}


def label(decision_type: str) -> str:
    return LABELS.get(decision_type, decision_type.replace(".", " ").replace("_", " "))


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
    name = label(decision_type)
    if decision_type in LOCKED:
        return GateDecision(False, f"“{name}” is always decided by a person")
    from p2p.agents import capabilities  # noqa: PLC0415 - the switch's module reads the automation settings

    cap = capabilities.of_decision(decision_type)
    if cap is not None and not capabilities.is_on(db, cap.code):
        return GateDecision(False, f"the {cap.label} capability is switched off")
    if db.execute(text("select kill_switch from autonomy_global where id = 1")).scalar_one():
        return GateDecision(False, "the kill switch is on")
    row = db.execute(
        text("select level, value_cap, min_confidence, max_wrong_rate from autonomy_setting where decision_type = :d"),
        {"d": decision_type},
    ).first()
    if row is None or row[0] != "auto":
        level = row[0] if row else "propose"
        return GateDecision(
            False, f"{LEVEL_WORDS.get(level, 'a person decides')} (automation setting “{name}”: {level})"
        )
    level, cap, min_conf, max_wrong = row
    if cap is not None and (value is None or value > cap):
        shown = format_inr(value) if value is not None else "an unknown value"
        return GateDecision(False, f"{shown} is above the automatic limit of {format_inr(cap)} for “{name}”")
    if confidence is None or Decimal(str(confidence)) < min_conf:
        return GateDecision(False, f"the reading's confidence {confidence} is below the {min_conf} needed")
    n, wrong = db.execute(
        text(
            "select count(*), count(*) filter (where wrong) from autonomy_outcome "
            "where decision_type = :d and auto and at >= :since"
        ),
        {"d": decision_type, "since": now - WINDOW},
    ).one()
    if n and Decimal(wrong) / Decimal(n) > max_wrong:
        allowed = f"{(Decimal(max_wrong) * 100).normalize():f}%"
        return GateDecision(
            False, f"{wrong} of {n} automatic decisions in 30 days were wrong, more than the {allowed} allowed"
        )
    return GateDecision(True, "allowed by the automation settings")


def record_outcome(
    db: Session,
    decision_type: str,
    *,
    auto: bool,
    ref: str | None = None,
    now: datetime | None = None,
    recommendation_id: int | None = None,
) -> int:
    row = db.execute(
        text(
            "insert into autonomy_outcome (decision_type, auto, ref, at, recommendation_id) "
            "values (:d, :a, :r, :n, :rec) returning id"
        ),
        {"d": decision_type, "a": auto, "r": ref, "n": now or datetime.now(UTC), "rec": recommendation_id},
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
    if decision_type in LOCKED and level != "propose":
        raise ValueError(f"'{decision_type}' is always decided by a person and cannot be set to '{level}'")
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
