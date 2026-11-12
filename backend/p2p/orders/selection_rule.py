"""The supplier selection rule in force: how quotes are compared (total cost or a weighted score), the weights, the
tie-break order and whether GST input credit is claimed. One row of `supplier_selection_rule` per version; a change
closes the version in force and adds the next, so every selection can be repeated against the version it used."""

from dataclasses import asdict
from datetime import date, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from p2p.formulas import selection as sel
from p2p.platform import audit
from p2p.platform.sod import Actor


class SelectionRuleError(Exception):
    pass


def _in_force(db: Session, as_of: date) -> dict[str, Any] | None:
    row = (
        db.execute(
            text(
                "select * from supplier_selection_rule where effective_from <= :d "
                "and (effective_to is null or effective_to >= :d) order by version desc limit 1"
            ),
            {"d": as_of},
        )
        .mappings()
        .first()
    )
    return dict(row) if row else None


def _rule(row: dict[str, Any]) -> sel.SelectionRule:
    return sel.SelectionRule(
        version=int(row["version"]),
        method=str(row["method"]),
        weights=sel.Weights(
            Decimal(row["weight_price"]),
            Decimal(row["weight_delivery"]),
            Decimal(row["weight_quality"]),
            Decimal(row["weight_payment_terms"]),
        ),
        tie_break=tuple(t for t in str(row["tie_break"]).split(",") if t),
        input_credit=bool(row["input_credit"]),
    )


def rule_for(db: Session, as_of: date) -> sel.SelectionRule:
    row = _in_force(db, as_of)
    if row is None:
        raise SelectionRuleError(f"no supplier selection rule is in force on {as_of}")
    return _rule(row)


def save_rule(
    db: Session,
    actor: Actor,
    *,
    effective_from: date,
    method: str | None = None,
    weights: sel.Weights | None = None,
    tie_break: tuple[str, ...] | None = None,
    input_credit: bool | None = None,
    note: str | None = None,
) -> int:
    """Make the next version from a date; what is not given is kept from the version in force. Returns the version."""
    current_row = _in_force(db, effective_from)
    if current_row is None:
        raise SelectionRuleError("the company has no supplier selection rule to change")
    current = _rule(current_row)
    new = sel.SelectionRule(
        version=int(db.execute(text("select coalesce(max(version), 0) + 1 from supplier_selection_rule")).scalar_one()),
        method=method or current.method,
        weights=weights or current.weights,
        tie_break=current.tie_break if tie_break is None else tie_break,
        input_credit=current.input_credit if input_credit is None else input_credit,
    )
    problems = sel.check_rule(new.method, new.weights, new.tie_break)
    if problems:
        raise SelectionRuleError("; ".join(problems))
    db.execute(
        text("update supplier_selection_rule set effective_to = :t where id = :i"),
        {"t": effective_from - timedelta(days=1), "i": current_row["id"]},
    )
    w = new.weights
    db.execute(
        text(
            "insert into supplier_selection_rule (version, effective_from, method, weight_price, weight_delivery, "
            "weight_quality, weight_payment_terms, tie_break, input_credit, note, created_by) values "
            "(:v, :f, :m, :wp, :wd, :wq, :wt, :tb, :ic, :n, :by)"
        ),
        {
            "v": new.version,
            "f": effective_from,
            "m": new.method,
            "wp": w.price,
            "wd": w.delivery,
            "wq": w.quality,
            "wt": w.payment_terms,
            "tb": ",".join(new.tie_break),
            "ic": new.input_credit,
            "n": note,
            "by": f"{actor.kind}:{actor.id}",
        },
    )
    audit.record(
        db,
        actor,
        "supplier_selection.change",
        "supplier_selection_rule",
        f"v{new.version}",
        before=asdict(current),
        after={**asdict(new), "effective_from": effective_from},
        reason=note,
        rule_version=new.label,
    )
    db.commit()
    return new.version
