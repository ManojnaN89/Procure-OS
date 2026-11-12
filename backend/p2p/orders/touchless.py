"""Touchless purchase orders (D35): an order in the buyer's tier made only of contract and safe-reorder lines is issued
and sent with no buyer, when every test of formulas/touchless.py passes.

The tests are run once, when the order is submitted, and kept on the order (`touchless_checks`, `touchless_note`) with
the version of the touchless rule they used; the audit row says the same. The actor is the system and the rule, never a
person, so no person's duties on the case change. An order that is not touchless waits for the buyer as before, and its
note says which tests stopped it. The rule is versioned rows of `touchless_rule`; the on/off switch per company is the
autonomy gate's decision type `po.issue`.
"""

import json
from dataclasses import asdict
from datetime import date, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from p2p.agents import autonomy
from p2p.approvals import budget, rules
from p2p.formulas import touchless as tl
from p2p.orders import contracts as contract_usage
from p2p.orders import dispatch
from p2p.platform import audit
from p2p.platform.sod import Actor

DECISION_TYPE = "po.issue"


class TouchlessRuleError(Exception):
    pass


def _row(db: Session, as_of: date) -> dict[str, Any] | None:
    row = (
        db.execute(
            text(
                "select * from touchless_rule where effective_from <= :d and (effective_to is null or effective_to >= :d) "
                "order by version desc limit 1"
            ),
            {"d": as_of},
        )
        .mappings()
        .first()
    )
    return dict(row) if row else None


def rule_for(db: Session, as_of: date) -> tl.TouchlessRule:
    row = _row(db, as_of)
    if row is None:
        raise TouchlessRuleError(f"no touchless rule is in force on {as_of}")
    limit = row["value_limit"]
    return tl.TouchlessRule(int(row["version"]), None if limit is None else Decimal(limit), Decimal(row["price_pct"]))


def save_rule(
    db: Session,
    actor: Actor,
    *,
    effective_from: date,
    value_limit: Decimal | None,
    price_pct: Decimal,
    note: str | None = None,
) -> int:
    """Make the next version from a date (an empty limit means the buyer's tier). Returns the version."""
    current = _row(db, effective_from)
    if price_pct < 0 or (value_limit is not None and value_limit < 0):
        raise TouchlessRuleError("the limit and the price tolerance cannot be negative")
    version = int(db.execute(text("select coalesce(max(version), 0) + 1 from touchless_rule")).scalar_one())
    if current is not None:
        db.execute(
            text("update touchless_rule set effective_to = :t where id = :i"),
            {"t": effective_from - timedelta(days=1), "i": current["id"]},
        )
    db.execute(
        text(
            "insert into touchless_rule (version, effective_from, value_limit, price_pct, note, created_by) "
            "values (:v, :f, :l, :p, :n, :by)"
        ),
        {
            "v": version,
            "f": effective_from,
            "l": value_limit,
            "p": price_pct,
            "n": note,
            "by": f"{actor.kind}:{actor.id}",
        },
    )
    new = tl.TouchlessRule(version, value_limit, price_pct)
    audit.record(
        db,
        actor,
        "touchless_rule.change",
        "touchless_rule",
        f"v{version}",
        before={"version": current["version"]} if current else None,
        after={**asdict(new), "effective_from": effective_from},
        reason=note,
        rule_version=new.label,
    )
    db.commit()
    return version


def tier_of(rule: rules.ApprovalRule) -> str:
    """The order's tier: 'buyer' when the rule needs no approver (the buyer's own issue is the approval), else the last
    role of the chain (the most senior person who approves it)."""
    return "buyer" if rule.is_automatic else rule.roles[-1]


def _buyer_tier_max(db: Session, rule: rules.ApprovalRule, today: date) -> Decimal | None:
    if rule.is_automatic:
        return rule.max_value
    try:
        lowest = rules.find_rule(db, "po", Decimal(0), today)
    except rules.ApprovalRuleError:
        return None
    return lowest.max_value if lowest.is_automatic else None


def facts(db: Session, po_no: str, rule: rules.ApprovalRule, today: date) -> tl.Facts:
    """What our own records say about the order, for the tests."""
    po = db.execute(text("select supplier_code, total from po where no = :n"), {"n": po_no}).mappings().one()
    status = db.execute(text("select status from supplier where code = :c"), {"c": po["supplier_code"]}).scalar()
    lines = tuple(
        tl.OrderLine(
            r["description"] or r["item_code"] or f"line {r['line_no']}",
            "contract"
            if r["source"] == "contract"
            else str(r["source_ref"])
            if r["source"] == "direct"
            else "quotation",
            Decimal(r["unit_price"]),
            None if r["est_price"] is None else Decimal(r["est_price"]),
        )
        for r in db.execute(
            text(
                "select l.line_no, l.description, l.item_code, l.source, l.source_ref, l.unit_price, "
                "coalesce(l.est_price, i.standard_price) as est_price from po_line l "
                "left join item i on i.code = l.item_code "
                "where l.po_no = :n and l.status <> 'cancelled' order by l.line_no"
            ),
            {"n": po_no},
        ).mappings()
    )  # the estimate is the requester's, else the item's standard price
    codes = sorted(
        {
            str(r[0])
            for r in db.execute(
                text(
                    "select source_ref from po_line where po_no = :n and source = 'contract' and status <> 'cancelled'"
                ),
                {"n": po_no},
            )
        }
    )
    ceilings = []
    for code in codes:
        use = contract_usage.usage(db, code)  # this order included: it is already made
        ceilings.append(tl.ContractCeiling(code, use.max_value, use.used))
    checks = budget.check_order(db, po_no, today)
    over = budget.summary(checks) if any(c.check.status == "over" for c in checks) else None
    gate = autonomy.gate(db, DECISION_TYPE, value=Decimal(po["total"]), confidence=1.0)  # no model guessed anything
    return tl.Facts(
        total=Decimal(po["total"]),
        buyer_tier=rule.is_automatic,
        buyer_tier_max=_buyer_tier_max(db, rule, today),
        supplier=str(po["supplier_code"]),
        supplier_status=str(status),
        lines=lines,
        contracts=tuple(ceilings),
        budget_over=over,
        gate_allowed=gate.allowed,
        gate_reason=gate.reason,
    )


def decide(db: Session, po_no: str, rule: rules.ApprovalRule, today: date) -> bool:
    """Run the tests, keep them on the order and in the audit log. Returns whether the order is touchless."""
    t_rule = rule_for(db, today)
    found = tl.checks(facts(db, po_no, rule, today), t_rule)
    ok = all(c.passed for c in found)
    note = tl.reason(found)
    versions = f"{t_rule.label}; {rule.code}:v{rule.version}"
    db.execute(
        text(
            "update po set approval_tier = :tier, touchless = :ok, touchless_checks = cast(:c as jsonb), "
            "touchless_note = :note, touchless_rule = :r where no = :n"
        ),
        {
            "tier": tier_of(rule),
            "ok": ok,
            "c": json.dumps([c.as_json() for c in found]),
            "note": note,
            "r": t_rule.label,
            "n": po_no,
        },
    )
    audit.record(
        db,
        Actor("rule", t_rule.label),
        "po.touchless_check",
        "po",
        po_no,
        after={"touchless": ok, "checks": [c.as_json() for c in found]},
        reason=note,
        rule_version=versions,
    )
    db.commit()
    return ok


def issue(db: Session, po_no: str) -> bool:
    """Send a touchless order to its supplier, as the system. If it cannot go, it waits for the buyer, and says why."""
    label = db.execute(text("select touchless_rule from po where no = :n"), {"n": po_no}).scalar()
    actor = Actor("rule", str(label))
    try:
        dispatch.send_po(db, po_no, actor)
    except dispatch.DispatchError as exc:
        db.rollback()
        note = f"Touchless, but it could not be sent ({exc}), so a buyer sends it"
        db.execute(text("update po set touchless = false, touchless_note = :t where no = :n"), {"t": note, "n": po_no})
        audit.record(db, actor, "po.touchless_failed", "po", po_no, reason=note, rule_version=str(label))
        db.commit()
        return False
    autonomy.record_outcome(db, DECISION_TYPE, auto=True, ref=po_no)
    return True
