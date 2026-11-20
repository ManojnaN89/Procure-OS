"""Strategic reviews of contract prices (D40, D41; task 12.5).

A review is a task of kind `strategic_review` for the category's buyer. A plan, a contract near its end or a buyer's
request always opens one; a contract price above the market (or repeated price variance, from 13.2) opens one only
when the versioned threshold rule says the gap is worth it on verified, fresh, in-region benchmarks. Every evaluation
is written to `rule_evaluation` whether or not it goes ahead. One review is open per business key (trigger, contract,
item, scope); a second trigger joins it. The contract stays valid and is still used for ordering in every case.
"""

import json
from dataclasses import asdict, dataclass
from datetime import date, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from p2p.formulas import benchmarks as bench_rules
from p2p.formulas import price_gap as rules
from p2p.formulas.calendar import business_today
from p2p.formulas.scope import Scope, contains
from p2p.formulas.scope import label as scope_label
from p2p.masters import regions
from p2p.platform import audit, notifications, tasks
from p2p.platform.sod import Actor
from p2p.sourcing import benchmarks

KIND = "strategic_review"
ALWAYS = ("plan", "expiry", "buyer")  # open a review whatever the price
PRICE = ("price_gap", "ppv")  # only above the thresholds (ppv has no detector until 13.2)
TRIGGERS = ALWAYS + PRICE
FIELDS = ("gap_pct", "gap_inr_per_unit", "min_saving_inr", "evidence_max_age_days", "min_sources", "expiry_review_days")
SYSTEM = Actor("service", "price_review")


class ReviewError(Exception):
    def __init__(self, message: str, status: int = 422) -> None:
        super().__init__(message)
        self.status = status


# ---- the rule ------------------------------------------------------------------------------------


def in_force_row(db: Session, category: str | None, on: date) -> dict[str, Any] | None:
    row = (
        db.execute(
            text(
                "select * from price_threshold_rule where category_code is not distinct from :c and effective_from <= :d "
                "and (effective_to is null or effective_to >= :d) order by version desc limit 1"
            ),
            {"c": category, "d": on},
        )
        .mappings()
        .first()
    )
    return dict(row) if row else None


def rule_for(db: Session, category: str | None, on: date) -> rules.ThresholdRule:
    """The company's rule in force on the date, made stricter by the category's own row when it has one."""
    base = in_force_row(db, None, on)
    if base is None:
        raise ReviewError(f"no price threshold rule is in force on {on:%d %b %Y}")
    rule = rules.ThresholdRule(
        f"price_threshold v{base['version']}",
        Decimal(base["gap_pct"]),
        Decimal(base["gap_inr_per_unit"]),
        Decimal(base["min_saving_inr"]),
        int(base["evidence_max_age_days"]),
        int(base["min_sources"]),
        int(base["expiry_review_days"]),
    )
    own = in_force_row(db, category, on) if category else None
    return rules.stricter(rule, own, f"{rule.version} + {category} v{own['version']}") if own else rule


def check_values(values: dict[str, Any]) -> list[str]:
    problems = []
    for key, value in values.items():
        if key not in FIELDS:
            problems.append(f"not a field of the price threshold rule: {key}")
        elif value is not None and Decimal(str(value)) < 0:
            problems.append(f"{key} cannot be negative")
    if values.get("gap_pct") is not None and Decimal(str(values["gap_pct"])) > 100:
        problems.append("gap_pct cannot be above 100")
    if values.get("min_sources") is not None and Decimal(str(values["min_sources"])) < 1:
        problems.append("min_sources is at least 1")
    return problems


def save_rule(
    db: Session,
    actor: Actor,
    values: dict[str, Any],
    *,
    category: str | None,
    effective_from: date,
    note: str | None,
    apply: bool = True,
) -> int:
    """The next version of the company's rule (category None) or a category's row, from a date; the version in force
    ends the day before. A category row keeps only what it names. Returns the version; with apply False only checks."""
    if problems := check_values(values):
        raise ReviewError("; ".join(problems))
    current = in_force_row(db, category, effective_from)
    if category is None:
        if current is None:
            raise ReviewError("the company has no price threshold rule to change")
        values = {k: values.get(k, current[k]) for k in FIELDS}
    version = int(
        db.execute(
            text(
                "select coalesce(max(version), 0) + 1 from price_threshold_rule where category_code is not distinct from :c"
            ),
            {"c": category},
        ).scalar_one()
    )
    if not apply:
        return version
    if current is not None:
        db.execute(
            text("update price_threshold_rule set effective_to = :t where id = :i"),
            {"t": effective_from - timedelta(days=1), "i": current["id"]},
        )
    filled = {k: values.get(k, current[k] if current else None) for k in FIELDS}
    if None in filled.values():  # a category row starts from the company's values for what it does not name
        company = in_force_row(db, None, effective_from) or {}
        filled = {k: filled[k] if filled[k] is not None else company.get(k) for k in FIELDS}
    db.execute(
        text(
            "insert into price_threshold_rule (category_code, version, effective_from, note, created_by, "
            "gap_pct, gap_inr_per_unit, min_saving_inr, evidence_max_age_days, min_sources, expiry_review_days) "
            "values (:c, :v, :f, :n, :by, :gap_pct, :gap_inr_per_unit, :min_saving_inr, :evidence_max_age_days, "
            ":min_sources, :expiry_review_days)"
        ),
        {"c": category, "v": version, "f": effective_from, "n": note, "by": f"{actor.kind}:{actor.id}", **filled},
    )
    label = f"{category or 'company'} v{version}"
    audit.record(
        db, actor, "price_threshold.change", "price_threshold_rule", label,
        before={k: current[k] for k in FIELDS} if current else None,
        after={**filled, "effective_from": effective_from}, reason=note, rule_version=f"price_threshold {label}",
    )  # fmt: skip
    db.commit()
    return version


# ---- the price-gap test --------------------------------------------------------------------------


@dataclass
class Evaluation:
    id: int
    price_record_id: int
    contract: str
    item: str
    scope: str
    rule: str
    gap: rules.Gap
    evidence: list[dict[str, Any]]


def relevant_qty(db: Session, item: str, scope: Scope, on: date) -> Decimal:
    """Ordered of the item at locations inside the scope over the last 365 days; when nothing was, the open material
    requirements (production orders and the demand plan through the bill of materials) needed in the next 90 days."""
    region_of = regions.region_of(db)
    ordered = Decimal(0)
    for loc, qty in db.execute(
        text(
            "select po.ship_to_location, sum(l.qty) from po_line l join po on po.no = l.po_no where l.item_code = :i "
            "and po.status not in ('draft', 'submitted', 'cancelled') and l.status <> 'cancelled' "
            "and po.created_at::date > :since and po.created_at::date <= :d group by po.ship_to_location"
        ),
        {"i": item, "since": on - timedelta(days=365), "d": on},
    ):
        if contains(scope, loc, region_of):
            ordered += Decimal(qty)
    if ordered > 0:
        return ordered
    needed = Decimal(0)
    for loc, qty in db.execute(
        text(
            "select location_code, sum(base_qty) from material_requirement where item_code = :i and status = 'open' "
            "and need_by >= :d and need_by <= :until group by location_code"
        ),
        {"i": item, "d": on, "until": on + timedelta(days=90)},
    ):
        if contains(scope, loc, region_of):
            needed += Decimal(qty)
    return needed


@dataclass
class Assessment:
    row: dict[str, Any]  # the price record
    scope: Scope
    rule: rules.ThresholdRule
    qty: Decimal
    gap: rules.Gap
    described: list[dict[str, Any]]


def assess(db: Session, price_record_id: int, on: date) -> Assessment:
    """The test of one contract price on a date, without recording it (the price view shows it as evidence only)."""
    pr = db.execute(
        text(
            "select p.*, i.category_code from price_record p join item i on i.code = p.item_code where p.id = :i"
        ),
        {"i": price_record_id},
    ).mappings().first()  # fmt: skip
    if pr is None:
        raise ReviewError(f"price record {price_record_id} does not exist", 404)
    scope = regions.price_scope(dict(pr))
    rule = rule_for(db, pr["category_code"], on)
    rows = benchmarks.records(db, item=pr["item_code"])
    described = benchmarks.described(
        db, rows, on=on, max_age_days=rule.evidence_max_age_days, scope=scope, uom=pr["uom"]
    )
    conv = benchmarks.conversions(db, str(pr["item_code"]))
    markets = [
        p
        for r, d in zip(rows, described, strict=True)
        if d["counts"] and (p := bench_rules.price_in(benchmarks.as_rule(r), str(pr["uom"]), conv)) is not None
    ]
    qty = relevant_qty(db, str(pr["item_code"]), scope, on)
    return Assessment(dict(pr), scope, rule, qty, rules.test(Decimal(pr["price"]), markets, qty, rule), described)


def evaluate(db: Session, price_record_id: int, on: date, actor: Actor = SYSTEM) -> Evaluation:
    """Test one contract price against the counting benchmarks and record the evaluation (key `price_threshold`)."""
    a = assess(db, price_record_id, on)
    pr, scope, rule, qty, gap, described = a.row, a.scope, a.rule, a.qty, a.gap, a.described
    where = scope_label(scope, regions.names(db))
    inputs = {
        "price_record": price_record_id, "contract": pr["contract_code"], "item": pr["item_code"],
        "contract_price": str(pr["price"]), "uom": pr["uom"], "scope": where, "on": on.isoformat(),
        "relevant_qty": str(qty), "benchmarks": [d["id"] for d in described if d["counts"]],
    }  # fmt: skip
    output = {k: (str(v) if isinstance(v, Decimal) else v) for k, v in asdict(gap).items()}
    trace = {
        "rule": rule.version,
        "evidence": [{k: d[k] for k in ("id", "price", "uom", "counts", "why")} for d in described],
    }
    eid = int(
        db.execute(
            text(
                "insert into rule_evaluation (key, version, input, output, trace, actor, case_ref) values "
                "('price_threshold', :v, cast(:i as jsonb), cast(:o as jsonb), cast(:t as jsonb), :a, :ref) returning id"
            ),
            {
                "v": int(rule.version.split(" v")[1].split(" ")[0]),
                "i": json.dumps(inputs, default=str),
                "o": json.dumps(output, default=str),
                "t": json.dumps(trace, default=str),
                "a": actor.identity,
                "ref": pr["contract_code"],
            },  # fmt: skip
        ).scalar_one()
    )
    db.commit()
    return Evaluation(
        eid, price_record_id, str(pr["contract_code"]), str(pr["item_code"]), where, rule.version, gap, described
    )


# ---- reviews -------------------------------------------------------------------------------------


def _buyer(db: Session, tenant: str, item: str | None) -> int:
    code = (
        db.execute(
            text("select c.buyer_user from item i join item_category c on c.code = i.category_code where i.code = :i"),
            {"i": item},
        ).scalar()
        if item
        else None
    )
    login = notifications.login_of(db, tenant, code) if code else None
    if login:
        return login
    found = db.execute(text("select user_id from user_profile where role = 'Buyer' order by user_id limit 1")).scalar()
    return int(found or db.execute(text("select min(user_id) from user_profile")).scalar() or 0)


def _allows(db: Session, contract: str | None, on: date) -> dict[str, Any] | None:
    """What the current contract allows: it stays valid until its end, with its ceiling left."""
    if not contract:
        return None
    from p2p.orders import contracts as usage  # noqa: PLC0415 - orders builds on sourcing

    c = db.execute(text("select valid_to, max_value from contract where code = :c"), {"c": contract}).first()
    if c is None:
        return None
    use = usage.usage(db, contract)
    return {
        "valid_to": c[0].isoformat() if c[0] else None,
        "ceiling": str(c[1]) if c[1] is not None else None,
        "ceiling_left": str(use.remaining) if use.remaining is not None else None,
        "note": "The contract stays valid; a review never sets aside its obligations",
    }


def open_review(
    db: Session,
    tenant: str,
    *,
    trigger: str,
    contract: str | None,
    item: str | None,
    scope: str,
    reason: str,
    evaluation: Evaluation | None = None,
    actor: Actor = SYSTEM,
    on: date | None = None,
    signature: str | None = None,
    rule_version: str | None = None,
) -> tuple[int, bool]:
    """Open the review, or add this trigger to the open one for the same business key. Returns (task, joined).
    A detector passes a signature of what it found: finding the same again the next day adds nothing."""
    if trigger not in TRIGGERS:
        raise ReviewError(f"unknown trigger {trigger}")
    on = on or business_today()
    rule_version = rule_version or (evaluation.rule if evaluation else None)  # the threshold version that decided
    key = f"{trigger}|{contract or '-'}|{item or '-'}|{scope}"
    found = db.execute(
        text("select id, payload from task where kind = :k and status = 'open' and payload->>'key' = :key for update"),
        {"k": KIND, "key": key},
    ).first()
    entry = {
        "trigger": trigger, "on": on.isoformat(), "reason": reason,
        "evaluation": evaluation.id if evaluation else None, "signature": signature,
    }  # fmt: skip
    if found is not None:
        payload = dict(found[1])
        last = (payload.get("triggers") or [{}])[-1]
        if signature is not None and last.get("signature") == signature:
            db.rollback()
            return int(found[0]), True
        payload["triggers"] = [*payload.get("triggers", []), entry]
        db.execute(
            text("update task set payload = cast(:p as jsonb) where id = :i"),
            {"p": json.dumps(payload, default=str), "i": found[0]},
        )
        audit.record(db, actor, "review.joined", "task", found[0], after=entry, rule_version=rule_version)
        db.commit()
        return int(found[0]), True
    gap = evaluation.gap if evaluation else None
    payload = {
        "key": key, "trigger": trigger, "contract": contract, "item": item, "scope": scope, "reason": reason,
        "evaluation": evaluation.id if evaluation else None, "rule": evaluation.rule if evaluation else None,
        "gap_inr": str(gap.gap_inr) if gap and gap.gap_inr is not None else None,
        "gap_pct": str(gap.gap_pct) if gap and gap.gap_pct is not None else None,
        "saving": str(gap.saving) if gap and gap.saving is not None else None,
        "market": str(gap.market) if gap and gap.market is not None else None,
        "allows": _allows(db, contract, on), "triggers": [entry],
    }  # fmt: skip
    what = " ".join(x for x in (contract, item) if x)
    title = f"Strategic review ({trigger.replace('_', ' ')}): {what or 'sourcing'}"
    task_id = tasks.create_task(
        db, tenant, kind=KIND, title=title, assignee=_buyer(db, tenant, item), sla_hours=72,
        payload=payload, actor=actor, email=False,
    )  # fmt: skip
    audit.record(
        db, actor, "review.open", "task", task_id, after={"key": key, "reason": reason}, rule_version=rule_version
    )
    db.commit()
    return task_id, False


def buyer_trigger(
    db: Session, tenant: str, actor: Actor, *, contract: str | None, item: str | None, reason: str
) -> tuple[int, bool]:
    """A buyer asks for a review of a contract or an item: always opens one."""
    if not reason.strip():
        raise ReviewError("a reason is needed")
    if contract is None and item is None:
        raise ReviewError("name a contract or an item")
    if contract and db.execute(text("select 1 from contract where code = :c"), {"c": contract}).first() is None:
        raise ReviewError(f"unknown contract {contract}")
    if item and db.execute(text("select 1 from item where code = :c"), {"c": item}).first() is None:
        raise ReviewError(f"unknown item {item}")
    return open_review(
        db,
        tenant,
        trigger="buyer",
        contract=contract,
        item=item,
        scope="everywhere",
        reason=reason.strip(),
        actor=actor,
    )


def close(db: Session, task_id: int, user_id: int, outcome: str, reason: str) -> None:
    if outcome not in ("rfq_started", "no_action"):
        raise ReviewError("the outcome is rfq_started or no_action")
    if not reason.strip():
        raise ReviewError("a reason is needed")
    row = db.execute(
        text("select status, payload from task where id = :i and kind = :k for update"), {"i": task_id, "k": KIND}
    ).first()
    if row is None:
        raise ReviewError(f"review {task_id} does not exist", 404)
    if row[0] != "open":
        raise ReviewError(f"review {task_id} is {row[0]}", 409)
    payload = {**dict(row[1] or {}), "outcome": outcome, "close_reason": reason.strip()}
    db.execute(
        text(
            "update task set status = 'done', completed_at = now(), completed_by = :u, payload = cast(:p as jsonb) "
            "where id = :i"
        ),
        {"u": user_id, "p": json.dumps(payload, default=str), "i": task_id},
    )
    audit.record(
        db, Actor("user", str(user_id)), "review.closed", "task", task_id, after={"outcome": outcome}, reason=reason
    )
    db.commit()


def listing(db: Session, status: str = "open") -> list[dict[str, Any]]:
    rows = db.execute(
        text(
            "select id, title, status, assignee, payload, created_at from task where kind = :k "
            "and (:s = 'all' or status = :s) order by id desc"
        ),
        {"k": KIND, "s": status},
    ).mappings()
    return [
        {
            "task_id": r["id"], "title": r["title"], "status": r["status"], "assignee": r["assignee"],
            "created_at": r["created_at"].isoformat(), **(r["payload"] or {}),
        }
        for r in rows
    ]  # fmt: skip


# ---- the detectors -------------------------------------------------------------------------------


@dataclass
class RunReport:
    opened: list[str]
    joined: list[str]
    below: list[str]


def _contract_prices(db: Session, on: date, item: str | None = None) -> list[dict[str, Any]]:
    return [
        dict(r)
        for r in db.execute(
            text(
                "select p.id, p.contract_code, p.item_code from price_record p join contract c on c.code = p.contract_code "
                "where p.status = 'active' and c.status = 'active' and p.valid_from <= :d "
                "and (p.valid_to is null or p.valid_to >= :d) and c.valid_from <= :d "
                "and (c.valid_to is null or c.valid_to >= :d) and (cast(:i as text) is null or p.item_code = :i) "
                "order by p.contract_code, p.item_code, p.id"
            ),
            {"d": on, "i": item},
        ).mappings()
    ]


def run(db: Session, tenant: str, on: date, *, item: str | None = None, dry_run: bool = False) -> RunReport:
    """The detectors once: every contract price in force against the benchmarks, and contracts near their end."""
    report = RunReport([], [], [])
    for p in _contract_prices(db, on, item):
        ev = evaluate(db, int(p["id"]), on)
        label = f"{p['contract_code']} {p['item_code']} ({ev.scope})"
        if not ev.gap.goes_ahead:
            report.below.append(f"{label}: {ev.gap.why}")
            continue
        if dry_run:
            report.opened.append(f"{label}: would open")
            continue
        why = f"contract {ev.gap.gap_pct}% (Rs {ev.gap.gap_inr} per unit) above the market; saving Rs {ev.gap.saving}"
        found = ",".join(str(d["id"]) for d in ev.evidence if d["counts"])
        _, joined = open_review(
            db, tenant, trigger="price_gap", contract=ev.contract, item=ev.item, scope=ev.scope, reason=why,
            evaluation=ev, on=on, signature=f"{found}|{ev.gap.gap_pct}",
        )  # fmt: skip
        (report.joined if joined else report.opened).append(label)
    if item is None:
        for code, valid_to in db.execute(
            text("select code, valid_to from contract where status = 'active' and valid_to is not null order by code")
        ):
            company_rule = rule_for(db, None, on)
            if not rules.expiry_due(valid_to, on, company_rule.expiry_review_days):
                continue
            if dry_run:
                report.opened.append(f"{code} expiry: would open")
                continue
            _, joined = open_review(
                db, tenant, trigger="expiry", contract=str(code), item=None, scope="everywhere",
                reason=f"contract ends on {valid_to:%d %b %Y}", on=on, signature=valid_to.isoformat(),
                rule_version=company_rule.version,
            )  # fmt: skip
            (report.joined if joined else report.opened).append(f"{code} expiry")
    return report
