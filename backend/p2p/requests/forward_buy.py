"""Forward buying (task 13.5): when an item's market price is low against its trend, recommend buying more now, and
carry the buyer's and the approver's decision through to a planning request.

Who qualifies (Q27, recommended): a stocked goods item, not capex, at a stock location, with the forward-buy rule
enabled, no contract price in force for it there, and a live request line for it there (request submitted or approved,
line open or sourcing). The anchor is the live line with the latest need-by (ties: the highest request number). A
preview (no anchor) uses the evaluation month and writes nothing.

The figures come only from `formulas/forward_buy.py` on numbers read here (benchmarks, stock, open supply, need, budget);
a model never sets a quantity or a price. Every evaluation writes `rule_evaluation`. At most one recommendation is live
(open or accepted) per item and location: the same figures change nothing; different ones supersede it; one that no
longer qualifies lapses it. A recommendation the buyer rejected is not offered again until a newer benchmark counts.

The buyer accepts (up to the recommended quantity, whole purchase units) or rejects; accepting proposes the extra to
the first person the `forward_buy` approval rule names, never the buyer. Approving writes a separate planning request
(one line, the whole quantity for stock) and sends it through its own approval and budget check. Accepting and approving
first work the figures out again: if anything changed the record is superseded and the caller gets the new one.
"""

import json
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from p2p.approvals import budget, chasing
from p2p.approvals import rules as approval_rules
from p2p.approvals.workflow import name_of, org_code_of
from p2p.formulas import benchmarks as bench_rules
from p2p.formulas import forward_buy as fb
from p2p.formulas.money import format_inr, round_money
from p2p.formulas.scope import Scope
from p2p.masters import regions
from p2p.orders.quotes import last_paid
from p2p.platform import audit, notifications, permissions, proposals, tasks
from p2p.platform.sod import Actor, user
from p2p.sourcing import price_review
from p2p.sourcing.benchmarks import conversions
from p2p.sourcing.service import contract_options

ACTION = "forward_buy.approve"
LIVE = ("open", "accepted")
D = Decimal
MONTH_NAMES = ("January", "February", "March", "April", "May", "June", "July", "August", "September", "October",
               "November", "December")  # fmt: skip
UNIT_WORDS = {"KGS": "kg", "LTR": "litre", "NOS": "piece", "MTR": "metre", "PAC": "pack", "BOX": "box", "SET": "set"}


class ForwardBuyError(Exception):
    def __init__(self, message: str, status: int = 409, new_id: int | None = None) -> None:
        super().__init__(message)
        self.status, self.new_id = status, new_id


# ---- the rule ------------------------------------------------------------------------------------------------

FIELDS = ("enabled", "min_gap_pct", "min_trend_months", "max_cover_months", "holding_cost_pct_month",
          "min_net_saving_inr", "shelf_life_months", "storage_max_qty")  # fmt: skip


def _in_force(db: Session, on: date, *, category: str | None, item: str | None, location: str | None) -> Any:
    return (
        db.execute(
            text(
                "select * from forward_buy_rule where category_code is not distinct from :c and item_code is not "
                "distinct from :i and location_code is not distinct from :l and effective_from <= :d "
                "and (effective_to is null or effective_to >= :d) order by version desc limit 1"
            ),
            {"c": category, "i": item, "l": location, "d": on},
        )
        .mappings()
        .first()
    )


def rule_for(db: Session, item: str, category: str | None, location: str, on: date) -> fb.Rule:
    """The company row with each narrower row in force laid over it: category, item, item at the location."""
    scopes: list[tuple[str, str | None, str | None, str | None]] = [("forward_buy", None, None, None)]
    if category:
        scopes.append((category, category, None, None))
    scopes += [(item, None, item, None), (f"{item}@{location}", None, item, location)]
    values: dict[str, Any] = {}
    label: list[str] = []
    for name, cat, it, loc in scopes:
        row = _in_force(db, on, category=cat, item=it, location=loc)
        if row is None:
            continue
        label.append(f"{name} v{row['version']}")
        values.update({f: row[f] for f in FIELDS if row[f] is not None})
    if "min_gap_pct" not in values:
        raise ForwardBuyError(f"no forward-buy rule is in force on {on:%d %b %Y}", 409)
    shelf, storage = values.get("shelf_life_months"), values.get("storage_max_qty")
    return fb.Rule(
        " + ".join(label), bool(values["enabled"]), D(values["min_gap_pct"]), int(values["min_trend_months"]),
        int(values["max_cover_months"]), D(values["holding_cost_pct_month"]), D(values["min_net_saving_inr"]),
        None if shelf is None else int(shelf), None if storage is None else D(storage),
    )  # fmt: skip


def save_rule(
    db: Session, actor: Actor, values: dict[str, Any], *, effective_from: date, note: str | None, apply: bool = True
) -> int:
    """The next version of a rule's scope from a date (the version in force ends the day before). With apply False it
    only checks the change. Returns the version."""
    from datetime import timedelta

    cat, item, loc = (
        values.get("category_code") or None,
        values.get("item_code") or None,
        values.get("location_code") or None,
    )
    if loc and not item:
        raise ForwardBuyError("a location rule names its item", 422)
    if cat and item:
        raise ForwardBuyError("a rule is for a category or an item, not both", 422)
    own = {f: values.get(f) for f in FIELDS}
    for f, v in own.items():
        if v is not None and f != "enabled" and D(str(v)) < 0:
            raise ForwardBuyError(f"{f} cannot be negative", 422)
    if not cat and not item and any(own[f] is None for f in FIELDS[:6]):
        raise ForwardBuyError("the company rule has every value", 422)
    version = int(
        db.execute(
            text(
                "select coalesce(max(version), 0) + 1 from forward_buy_rule where category_code is not distinct from :c "
                "and item_code is not distinct from :i and location_code is not distinct from :l"
            ),
            {"c": cat, "i": item, "l": loc},
        ).scalar_one()
    )
    if not apply:
        return version
    current = _in_force(db, effective_from, category=cat, item=item, location=loc)
    if current is not None:
        db.execute(
            text("update forward_buy_rule set effective_to = :t where id = :i"),
            {"t": effective_from - timedelta(days=1), "i": current["id"]},
        )
    db.execute(
        text(
            "insert into forward_buy_rule (version, category_code, item_code, location_code, effective_from, enabled, "
            "min_gap_pct, min_trend_months, max_cover_months, holding_cost_pct_month, min_net_saving_inr, "
            "shelf_life_months, storage_max_qty, note, entered_by) values (:v, :c, :i, :l, :f, :enabled, :min_gap_pct, "
            ":min_trend_months, :max_cover_months, :holding_cost_pct_month, :min_net_saving_inr, :shelf_life_months, "
            ":storage_max_qty, :n, :by)"
        ),
        {"v": version, "c": cat, "i": item, "l": loc, "f": effective_from, "n": note, "by": actor.identity, **own},
    )
    audit.record(
        db, actor, "forward_buy_rule.change", "forward_buy_rule", f"{cat or item or 'company'}{'@' + loc if loc else ''}",
        after={"version": version, "effective_from": effective_from.isoformat(), **{k: str(v) for k, v in own.items()}},
        reason=note,
    )  # fmt: skip
    return version


# ---- what the engine reads ------------------------------------------------------------------------------------


@dataclass
class Anchor:
    request_no: str
    line_no: int
    need_by: date
    est_price: Decimal
    uom: str | None
    line_factor: Decimal  # stock units in one of the line's units
    department: str | None
    gl_code: str | None


@dataclass
class Evaluation:
    item: dict[str, Any]
    location: str
    on: date
    rule: fb.Rule | None
    reason: str  # '' when recommended
    result: fb.Result | None = None
    anchor: Anchor | None = None
    benchmark: dict[str, Any] | None = None
    points: list[tuple[int, Decimal]] | None = None
    unit: str | None = None  # purchase unit
    unit_factor: Decimal = D(1)
    est_price: Decimal | None = None  # per purchase unit

    @property
    def recommended(self) -> bool:
        return bool(self.result and self.result.recommended)

    def months(self) -> list[dict[str, str]]:
        if self.result is None:
            return []
        return [
            {
                "month": _shift(_month(self.on), c.x),
                "m": str(c.m),
                "expected_price": str(c.expected),
                "net_per_unit": str(fb.q4(c.net_per_unit)),
                "qty": str(fb.q4(c.qty)),
            }  # fmt: skip
            for c in self.result.months
        ]


def _month(d: date) -> str:
    return f"{d.year}-{d.month:02d}"


def _shift(month: str, n: int) -> str:
    y, m = int(month[:4]), int(month[5:]) - 1 + n
    return f"{y + m // 12}-{m % 12 + 1:02d}"


def _offset(d: date, on: date) -> int:
    return (d.year - on.year) * 12 + d.month - on.month


def _item(db: Session, code: str) -> dict[str, Any] | None:
    row = (
        db.execute(
            text(
                "select i.code, i.name, i.category_code, i.base_uom, i.purchase_uom, i.pack_size, i.stocked, i.capex, "
                "i.standard_price, (select u.factor from uom_conversion u where u.item_code = i.code "
                "and u.from_uom = i.purchase_uom and u.to_uom = i.base_uom) as factor from item i where i.code = :c"
            ),
            {"c": code},
        )
        .mappings()
        .first()
    )
    return dict(row) if row else None


def _purchase_unit(item: dict[str, Any]) -> tuple[str, Decimal]:
    unit = item["purchase_uom"] or item["base_uom"]
    factor = item["factor"] or (D(item["pack_size"] or 1) if unit != item["base_uom"] else D(1))
    return str(unit), D(factor)


_LIVE_LINES = (
    "from request_line l join request r on r.no = l.request_no where l.item_code = :i "
    "and coalesce(l.delivery_location, r.ship_to_location) = :loc and r.status in ('submitted', 'approved') "
    "and l.status in ('open', 'sourcing')"
)


def anchor_of(db: Session, item: str, location: str) -> Anchor | None:
    row = db.execute(
        text(
            "select l.request_no, l.line_no, l.need_by, l.est_price, l.uom, coalesce(l.base_qty, l.qty) / l.qty as f, "  # noqa: S608 - a fixed fragment
            f"r.department_code, l.gl_code {_LIVE_LINES} and l.qty > 0 and coalesce(r.source_ref, '') not like 'FB-%' "
            "order by l.need_by desc nulls last, l.request_no desc limit 1"
        ),
        {"i": item, "loc": location},
    ).first()
    if row is None:
        return None
    return Anchor(row[0], int(row[1]), row[2], D(row[3] or 0), row[4], D(row[5]), row[6], row[7])


def anchor_line(db: Session, request_no: str, line_no: int) -> Anchor | None:
    row = db.execute(
        text(
            "select l.request_no, l.line_no, l.need_by, l.est_price, l.uom, coalesce(l.base_qty, l.qty) / l.qty, "
            "r.department_code, l.gl_code from request_line l join request r on r.no = l.request_no "
            "where l.request_no = :n and l.line_no = :l and l.qty > 0"
        ),
        {"n": request_no, "l": line_no},
    ).first()
    if row is None:
        return None
    return Anchor(row[0], int(row[1]), row[2], D(row[3] or 0), row[4], D(row[5]), row[6], row[7])


def _supply(db: Session, item: str, location: str) -> Decimal:
    """Live request lines and open order balances, in stock units, whatever their dates."""
    lines = db.execute(
        text(f"select coalesce(sum(coalesce(l.base_qty, l.qty)), 0) {_LIVE_LINES}"), {"i": item, "loc": location}
    )  # noqa: S608
    orders = db.execute(
        text(
            "select coalesce(sum(coalesce(pl.base_qty, pl.qty) * greatest(pl.qty - pl.received_qty - pl.short_closed_qty, 0) "
            "/ pl.qty), 0) from po_line pl join po p on p.no = pl.po_no where pl.item_code = :i and p.ship_to_location = :loc "
            "and pl.qty > 0 and pl.status not in ('cancelled', 'closed') and p.status not in ('cancelled', 'rejected', 'closed')"
        ),
        {"i": item, "loc": location},
    )
    return D(lines.scalar_one()) + D(orders.scalar_one())


def _needs(db: Session, item: str, location: str, on: date) -> dict[int, Decimal]:
    """What open material requirements still need, by month from the evaluation month (anything overdue counts now)."""
    out: dict[int, Decimal] = {}
    for need_by, left in db.execute(
        text(
            "select m.need_by, m.base_qty - coalesce((select sum(ml.issued_qty) from material_request_line ml "
            "where ml.requirement_id = m.id), 0) from material_requirement m where m.status = 'open' "
            "and m.item_code = :i and m.location_code = :loc"
        ),
        {"i": item, "loc": location},
    ):
        k = max(0, _offset(need_by, on))
        out[k] = out.get(k, D(0)) + max(D(left), D(0))
    return out


def _issued(db: Session, item: str, location: str, on: date) -> Decimal:
    first, last = _shift(_month(on), -12), _shift(_month(on), -1)
    return D(
        db.execute(
            text(
                "select coalesce(sum(qty), 0) from stock_movement where item_code = :i and location_code = :loc "
                "and type = 'issue' and to_char(date, 'YYYY-MM') between :a and :b"
            ),
            {"i": item, "loc": location, "a": first, "b": last},
        ).scalar_one()
    )


def _usable(db: Session, item: str, location: str) -> Decimal:
    found = db.execute(
        text("select on_hand from stock_level where item_code = :i and location_code = :l"), {"i": item, "l": location}
    ).scalar()
    return D(found or 0)


def _benchmarks(
    db: Session, item: dict[str, Any], location: str, on: date
) -> tuple[dict[str, Any] | None, Decimal | None, list[tuple[int, Decimal]]]:
    """The counting benchmark with its price per stock unit, and the trend points of the 12 months before."""
    base = str(item["base_uom"])
    conv = conversions(db, item["code"])
    region_of = regions.region_of(db)
    scope = Scope.of([location])
    max_age = price_review.rule_for(db, item["category_code"], on).evidence_max_age_days
    rows = [
        dict(r)
        for r in db.execute(
            text("select * from market_benchmark where item_code = :i and basis = 'delivered'"), {"i": item["code"]}
        ).mappings()
    ]
    best: tuple[date, Decimal, dict[str, Any]] | None = None
    by_month: dict[int, list[Decimal]] = {}
    for r in rows:
        b = bench_rules.Benchmark(
            int(r["id"]), D(r["price"]), str(r["uom"]), r["effective_date"], r["region_code"], str(r["status"])
        )
        price = bench_rules.price_in(b, base, conv)
        if price is None or b.status != "verified" or not bench_rules.covers(b.region, scope, region_of):
            continue
        k = _offset(b.effective_date, on)
        if -12 <= k <= -1:
            by_month.setdefault(k, []).append(price)
        s = bench_rules.standing(
            b, on=on, max_age_days=max_age, scope=scope, region_of=region_of, uom=base, conversions=conv
        )
        if s.counts and (best is None or (b.effective_date, price) > (best[0], best[1])):
            best = (b.effective_date, price, r)
    points = [(k, fb.monthly_average(v)) for k, v in sorted(by_month.items())]
    return (best[2], best[1], points) if best else (None, None, points)


def _bought_ahead(db: Session, item: str, location: str) -> bool:
    """An approved forward buy for the item and place whose request is still open (not closed, cancelled or rejected).
    Its quantity counts as supply, and the date-blind cover would otherwise recommend buying ahead again at once."""
    return (
        db.execute(
            text(
                "select 1 from forward_buy f join request r on r.no = f.new_request_no where f.item_code = :i "
                "and f.location_code = :l and f.status = 'approved' and r.status not in ('closed', 'cancelled', 'rejected')"
            ),
            {"i": item, "l": location},
        ).first()
        is not None
    )


def _contract(db: Session, item: dict[str, Any], location: str, on: date) -> str | None:
    for unit in dict.fromkeys([item["purchase_uom"], item["base_uom"]]):
        found = contract_options(db, item["code"], unit, on, location)
        if found:
            return str(found[0].contract_code)
    return None


def _budget_left(db: Session, anchor: Anchor, on: date) -> Decimal | None:
    groups = [
        g for g in budget.check_request(db, anchor.request_no, on)
        if g.gl_code == anchor.gl_code and g.check.available is not None
    ]  # fmt: skip
    if not groups:
        return None
    return min(D(g.check.available or 0) - g.check.requested for g in groups)


def evaluate(
    db: Session, item_code: str, location: str, on: date, *, anchor: Anchor | None = None, preview: bool = False
) -> Evaluation:
    """What the engine finds for an item at a location on a date (nothing written)."""
    item = _item(db, item_code)
    if item is None:
        raise ForwardBuyError(f"item {item_code} is not known", 422)
    stock_place = db.execute(text("select is_stock_location from location where code = :l"), {"l": location}).first()
    if stock_place is None:
        raise ForwardBuyError(f"location {location} is not known", 422)
    rule = rule_for(db, item_code, item["category_code"], location, on)
    ev = Evaluation(item, location, on, rule, "")
    if not item["stocked"] or item["capex"] or not stock_place[0] or not rule.enabled:
        ev.reason = "not_eligible"
        return ev
    if _contract(db, item, location, on):
        ev.reason = "on_contract"
        return ev
    if not preview and _bought_ahead(db, item_code, location):
        ev.reason = "forward_buy_open"  # what an approved forward buy asked for is still coming: never buy ahead twice
        return ev
    if anchor is None and not preview:
        anchor = anchor_of(db, item_code, location)
        if anchor is None:
            ev.reason = "no_live_request"
            return ev
    ev.anchor = anchor
    row, price, points = _benchmarks(db, item, location, on)
    ev.benchmark, ev.points = row, points
    if row is None or price is None:
        ev.reason = "no_fresh_benchmark"
        return ev
    unit, factor = _purchase_unit(item)
    if anchor is not None:
        est = anchor.est_price * factor / (anchor.line_factor or D(1))
    else:
        from p2p.requests.estimates import for_item

        found = for_item(db, item_code, unit, D(1), on, item["standard_price"], location)
        est = found.price if found else D(item["standard_price"] or 0)
    paid = last_paid(db, [item_code]).get(item_code)
    inputs = fb.Inputs(
        price_now=price,
        points=points,
        avg_use=fb.average_use(_issued(db, item_code, location, on)),
        needs=_needs(db, item_code, location, on),
        usable=_usable(db, item_code, location),
        supply=_supply(db, item_code, location),
        anchor_offset=max(0, _offset(anchor.need_by, on)) if anchor and anchor.need_by else 0,
        est_price=est,
        unit_factor=factor,
        budget_left=_budget_left(db, anchor, on) if anchor else None,
        last_paid=_paid_recently(db, item_code, paid, on, factor),
    )
    ev.unit, ev.unit_factor, ev.est_price = unit, factor, est
    ev.result = fb.evaluate(rule, inputs)
    ev.reason = ev.result.reason
    return ev


def _paid_recently(
    db: Session, item: str, paid: tuple[Decimal, str] | None, on: date, factor: Decimal
) -> Decimal | None:
    """The price last paid per stock unit when the order is from the last 365 days."""
    if paid is None:
        return None
    when = db.execute(text("select created_at from po where no = :n"), {"n": paid[1]}).scalar()
    if when is None or (on - when.date()).days > 365:
        return None
    return fb.q4(D(paid[0]) / factor)


# ---- the record ------------------------------------------------------------------------------------------------


def _record_evaluation(db: Session, ev: Evaluation, actor: Actor) -> int:
    r = ev.result
    inputs = {
        "item": ev.item["code"], "location": ev.location, "on": ev.on.isoformat(),
        "anchor": f"{ev.anchor.request_no}/{ev.anchor.line_no}" if ev.anchor else None,
        "benchmark": ev.benchmark["id"] if ev.benchmark else None,
        "points": [[x, str(y)] for x, y in ev.points or []],
    }  # fmt: skip
    output = {
        "recommended": ev.recommended, "reason": ev.reason,
        "qty": str(r.units) if r else None, "net_saving": str(r.net_saving) if r else None,
        "limit": r.limit if r else None,
    }  # fmt: skip
    trace = {
        "rule": ev.rule.label if ev.rule else None,
        "price_now": str(r.price_now) if r else None, "trend_now": str(r.trend_now) if r and r.trend_now else None,
        "dip_pct": str(r.dip_pct) if r and r.dip_pct is not None else None, "months": ev.months(),
    }  # fmt: skip
    version = int(ev.rule.label.split(" v")[1].split(" ")[0]) if ev.rule else 0
    return int(
        db.execute(
            text(
                "insert into rule_evaluation (key, version, input, output, trace, actor, case_ref) values "
                "('forward_buy', :v, cast(:i as jsonb), cast(:o as jsonb), cast(:t as jsonb), :a, :ref) returning id"
            ),
            {
                "v": version,
                "i": json.dumps(inputs),
                "o": json.dumps(output),
                "t": json.dumps(trace),
                "a": actor.identity,
                "ref": f"{ev.item['code']}@{ev.location}",
            },  # fmt: skip
        ).scalar_one()
    )


def _live(db: Session, item: str, location: str) -> dict[str, Any] | None:
    row = (
        db.execute(
            text(
                "select * from forward_buy where item_code = :i and location_code = :l and status in ('open', 'accepted')"
            ),
            {"i": item, "l": location},
        )
        .mappings()
        .first()
    )
    return dict(row) if row else None


def load(db: Session, fid: int) -> dict[str, Any] | None:
    row = db.execute(text("select * from forward_buy where id = :i"), {"i": fid}).mappings().first()
    return dict(row) if row else None


def _verifier_name(db: Session, verified_by: Any) -> str:
    if not verified_by:
        return "nobody"
    by = str(verified_by)
    if by.startswith("user:"):
        code = db.execute(text("select display_name from user_profile where user_id = :u"), {"u": int(by[5:])}).scalar()
        return str(code) if code else by
    return name_of(db, by)


def _rupees(v: Decimal) -> str:
    return "Rs " + format_inr(v, symbol=False)


def _month_words(month: str, *, year: bool = True) -> str:
    name = MONTH_NAMES[int(month[5:]) - 1]
    return f"{name} {month[:4]}" if year else name


def explain_template(rec: dict[str, Any], item_name: str, verifier: str, holding_pct: Decimal) -> str:
    """The explanation in fixed words, every number from the record (the fallback the AI step may reword)."""
    unit = UNIT_WORDS.get(str(rec["uom_base"]), str(rec["uom_base"]).lower())
    months = rec["months"]
    first, last = months[0]["month"], months[-1]["month"]
    span = (
        _month_words(first)
        if first == last
        else f"{_month_words(first, year=first[:4] != last[:4])} to {_month_words(last)}"
    )
    slope = D(rec["slope"])
    how = f"rises about {_rupees(abs(slope))} a month" if slope >= 0 else f"falls about {_rupees(abs(slope))} a month"
    when = f"with the {_month_words(rec['anchor_month'], year=False)} delivery" if rec.get("anchor_month") else "now"
    qty = D(rec["qty_base"]).normalize()
    return (
        f"{item_name} costs {_rupees(D(rec['price_now']))} a {unit} on the market now ({rec['source_name']}, verified by "
        f"{verifier}), {D(rec['dip_pct']).quantize(D('0.01'))}% below its 12-month trend of {_rupees(D(rec['trend_now']))}, "
        f"which {how}. Buying {qty:f} {unit} more {when} covers {span} at about "
        f"{D(rec['avg_use']).quantize(D('0.01'))} {unit} a month. Saving {_rupees(D(rec['gross_saving']))} before holding, "
        f"{_rupees(D(rec['holding_cost']))} to hold the stock at {holding_pct.normalize():f}% a month: "
        f"{_rupees(D(rec['net_saving']))} net. Risks: the price may fall further; {_rupees(D(rec['value']))} is tied up "
        f"until {_month_words(last)}."
    )


def _template_of(db: Session, ev: Evaluation) -> str:
    r = ev.result
    assert r is not None and ev.benchmark is not None and ev.rule is not None
    rec = {
        "uom_base": ev.item["base_uom"], "months": ev.months(), "slope": r.slope, "price_now": r.price_now,
        "dip_pct": r.dip_pct, "trend_now": r.trend_now, "qty_base": r.qty_base, "avg_use": r.avg_use,
        "gross_saving": r.gross_saving, "holding_cost": r.holding_cost, "net_saving": r.net_saving, "value": r.value,
        "source_name": ev.benchmark["source_ref"] or ev.benchmark["source_name"],
        "anchor_month": _month(ev.anchor.need_by) if ev.anchor and ev.anchor.need_by else None,
    }  # fmt: skip
    return explain_template(
        rec, str(ev.item["name"]), _verifier_name(db, ev.benchmark["verified_by"]), ev.rule.holding_cost_pct_month
    )


def _same(rec: dict[str, Any], ev: Evaluation) -> bool:
    r = ev.result
    return bool(r and r.recommended and D(rec["qty"]) == r.units and D(rec["net_saving"]) == r.net_saving
                and rec["request_no"] == (ev.anchor.request_no if ev.anchor else None))  # fmt: skip


def _create(db: Session, ev: Evaluation, eid: int, actor: Actor) -> int:
    r = ev.result
    assert r is not None and ev.rule is not None
    fid = int(
        db.execute(
            text(
                "insert into forward_buy (item_code, location_code, request_no, line_no, status, rule_label, "
                "evaluation_id, benchmark_id, price_now, trend_now, slope, dip_pct, avg_use, cover, qty_base, qty, "
                "est_price, uom, months, value, gross_saving, holding_cost, net_saving, limit_kind, explanation, "
                "explanation_source) values (:i, :l, :rn, :ln, 'open', :rule, :e, :b, :p, :t, :s, :d, :a, :c, :qb, :q, "
                ":est, :u, cast(:m as jsonb), :v, :g, :h, :n, :lim, :x, 'template') returning id"
            ),
            {
                "i": ev.item["code"],
                "l": ev.location,
                "rn": ev.anchor.request_no if ev.anchor else None,
                "ln": ev.anchor.line_no if ev.anchor else None,
                "rule": ev.rule.label,
                "e": eid,
                "b": ev.benchmark["id"] if ev.benchmark else None,
                "p": r.price_now,
                "t": r.trend_now,
                "s": r.slope,
                "d": r.dip_pct,
                "a": r.avg_use,
                "c": r.cover,
                "qb": r.qty_base,
                "q": r.units,
                "est": ev.est_price,
                "u": ev.unit,
                "m": json.dumps(ev.months()),
                "v": r.value,
                "g": r.gross_saving,
                "h": r.holding_cost,
                "n": r.net_saving,
                "lim": r.limit,
                "x": _template_of(db, ev),
            },  # fmt: skip
        ).scalar_one()
    )
    audit.record(
        db, actor, "forward_buy.create", "forward_buy", fid,
        after={"item": ev.item["code"], "location": ev.location, "qty": str(r.units), "net_saving": str(r.net_saving),
               "request": ev.anchor.request_no if ev.anchor else None, "rule": ev.rule.label},
    )  # fmt: skip
    return fid


def _end(db: Session, rec: dict[str, Any], status: str, actor: Actor, *, why: str, new_id: int | None = None) -> None:
    db.execute(
        text("update forward_buy set status = :s, superseded_by = :n, lapse_reason = :w where id = :i"),
        {"s": status, "n": new_id, "w": why if status == "lapsed" else None, "i": rec["id"]},
    )
    if rec.get("proposal_id"):
        db.commit()
        proposals.withdraw(db, int(rec["proposal_id"]), None, f"forward buy FB-{rec['id']} {status}: {why}")
    action = "forward_buy.supersede" if status == "superseded" else "forward_buy.lapse"
    audit.record(db, actor, action, "forward_buy", rec["id"], after={"status": status, "new": new_id}, reason=why)


def _suppressed(db: Session, ev: Evaluation) -> bool:
    """The buyer rejected a recommendation for this item and place, and no newer benchmark counts since."""
    last = db.execute(
        text(
            "select f.benchmark_id, b.effective_date, b.id from forward_buy f left join market_benchmark b on b.id = f.benchmark_id "
            "where f.item_code = :i and f.location_code = :l and f.status = 'rejected' order by f.id desc limit 1"
        ),
        {"i": ev.item["code"], "l": ev.location},
    ).first()
    if last is None or ev.benchmark is None:
        return False
    now = (ev.benchmark["effective_date"], int(ev.benchmark["id"]))
    return last[1] is not None and now <= (last[1], int(last[2]))


@dataclass
class Outcome:
    item_code: str
    location_code: str
    recommended: bool
    reason: str
    qty: Decimal | None
    uom: str | None
    net_saving: Decimal | None
    outcome: str  # created, unchanged, superseded, lapsed, suppressed, nothing, dry run
    id: int | None = None


def evaluate_item(
    db: Session, tenant: str | None, item: str, location: str, on: date, actor: Actor = audit.SYSTEM
) -> Outcome:
    """Work the figures out for an item at a location, record the evaluation, and keep the one live recommendation in
    step with it. Commits."""
    live = _live(db, item, location)
    anchor = (
        anchor_line(db, live["request_no"], live["line_no"])
        if live and live["status"] == "accepted" and live["request_no"]
        else None
    )
    ev = evaluate(db, item, location, on, anchor=anchor)
    eid = _record_evaluation(db, ev, actor)
    r = ev.result
    out = Outcome(
        item,
        location,
        ev.recommended,
        ev.reason,
        r.units if r else None,
        ev.unit,
        r.net_saving if r else None,
        "nothing",
    )
    if live is not None and ev.recommended and _same(live, ev):
        out.outcome, out.id = "unchanged", int(live["id"])
    elif ev.recommended and _suppressed(db, ev) and live is None:
        out.outcome = "suppressed"
    elif ev.recommended:
        fid = _create_replacing(db, ev, eid, actor, live)
        out.outcome, out.id = ("superseded" if live else "created"), fid
    elif live is not None:
        _end(db, live, "lapsed", actor, why=ev.reason)
        out.outcome, out.id = "lapsed", int(live["id"])
    db.commit()
    if out.outcome in ("created", "superseded") and out.id:
        _queue_explanation(tenant, out.id)
    return out


def _create_replacing(db: Session, ev: Evaluation, eid: int, actor: Actor, live: dict[str, Any] | None) -> int:
    if live is not None:
        db.execute(text("update forward_buy set status = 'superseded' where id = :i"), {"i": live["id"]})
    fid = _create(db, ev, eid, actor)
    if live is not None:
        _end(db, live, "superseded", actor, why="the figures changed", new_id=fid)
    return fid


def _queue_explanation(tenant: str | None, fid: int) -> None:
    if not tenant:
        return
    from p2p.platform import worker

    try:
        worker.enqueue(tenant, "forward_buy_explain", {"id": fid})
    except Exception:  # noqa: BLE001 - the template stands without the job
        return


def candidates(db: Session, item: str | None = None, location: str | None = None) -> list[tuple[str, str]]:
    """Item and place pairs worth evaluating: a live request line, or a live recommendation."""
    rows = db.execute(
        text(
            "select distinct l.item_code, coalesce(l.delivery_location, r.ship_to_location) from request_line l "
            "join request r on r.no = l.request_no where r.status in ('submitted', 'approved') "
            "and l.status in ('open', 'sourcing') and l.item_code is not null "
            "union select item_code, location_code from forward_buy where status in ('open', 'accepted') order by 1, 2"
        )
    ).all()
    return [(str(a), str(b)) for a, b in rows if (item is None or a == item) and (location is None or b == location)]


def run(
    db: Session, tenant: str | None, on: date, *, item: str | None = None, location: str | None = None,
    dry_run: bool = False, actor: Actor = audit.SYSTEM,
) -> list[Outcome]:  # fmt: skip
    pairs = candidates(db, item, location)
    if item and location and (item, location) not in pairs:
        pairs.append((item, location))
    out = []
    for it, loc in pairs:
        if dry_run:
            ev = evaluate(db, it, loc, on, preview=False)
            r = ev.result
            out.append(Outcome(it, loc, ev.recommended, ev.reason, r.units if r else None, ev.unit,
                               r.net_saving if r else None, "dry run"))  # fmt: skip
            continue
        out.append(evaluate_item(db, tenant, it, loc, on, actor))
    return out


# ---- decisions -------------------------------------------------------------------------------------------------


def _open_record(db: Session, fid: int, *wanted: str) -> dict[str, Any]:
    rec = db.execute(text("select * from forward_buy where id = :i for update"), {"i": fid}).mappings().first()
    if rec is None:
        raise ForwardBuyError(f"forward buy {fid} not found", 404)
    if rec["status"] not in wanted:
        newer = rec["superseded_by"] if rec["status"] == "superseded" else None
        raise ForwardBuyError(f"forward buy FB-{fid} is {rec['status']}", 409, int(newer) if newer else None)
    return dict(rec)


def _revalidate(db: Session, tenant: str | None, rec: dict[str, Any], on: date, actor: Actor) -> Evaluation:
    """Work the figures out again on the same anchor; anything different supersedes the record (409 with the new id)."""
    anchor = anchor_line(db, rec["request_no"], rec["line_no"]) if rec["request_no"] else None
    ev = evaluate(db, rec["item_code"], rec["location_code"], on, anchor=anchor, preview=anchor is None)
    eid = _record_evaluation(db, ev, actor)
    if ev.recommended and _same(rec, ev):
        return ev
    if ev.recommended:
        new_id = _create_replacing(db, ev, eid, actor, rec)
        db.commit()
        _queue_explanation(tenant, new_id)
        raise ForwardBuyError(
            f"the figures changed: forward buy FB-{rec['id']} is replaced by FB-{new_id}", 409, new_id
        )
    _end(db, rec, "lapsed", actor, why=ev.reason)
    db.commit()
    raise ForwardBuyError(f"forward buy FB-{rec['id']} no longer qualifies ({ev.reason}) and has lapsed", 409)


def _approver(db: Session, value: Decimal, on: date, buyer: str | None) -> approval_rules.Step:
    try:
        rule = approval_rules.find_rule(db, "forward_buy", value, on)
    except approval_rules.ApprovalRuleError:
        raise ForwardBuyError("no approval rule names who approves forward buys") from None
    try:
        steps = approval_rules.resolve_chain(
            db, rule.roles, requester=None, department=None, on_date=on, exclude=frozenset({buyer} if buyer else set())
        )
    except approval_rules.ApprovalRuleError as exc:
        raise ForwardBuyError(f"the forward-buy approval rule names nobody who may approve: {exc}") from None
    if not steps:
        raise ForwardBuyError("no approval rule names who approves forward buys")
    return steps[0]


def accept(
    db: Session, tenant: str | None, fid: int, user_id: int, qty: Decimal | None, reason: str, on: date
) -> dict[str, Any]:
    why = (reason or "").strip()
    rec = _open_record(db, fid, "open")
    if not why:
        raise ForwardBuyError("a reason is needed to accept", 422)
    ev = _revalidate(db, tenant, rec, on, user(user_id))
    r = ev.result
    assert r is not None
    units = r.units if qty is None else D(str(qty))
    if units <= 0:
        raise ForwardBuyError("the quantity must be more than nothing", 422)
    if units > r.units:
        raise ForwardBuyError(f"the quantity is more than the {r.units} recommended", 422)
    if units != units.to_integral_value():
        raise ForwardBuyError(f"the quantity is whole purchase units ({ev.unit})", 422)
    taken = fb.rework(r, units, ev.unit_factor, ev.est_price or D(0)) if units != r.units else r
    buyer = org_code_of(db, user_id)
    step = _approver(db, taken.value, on, buyer)
    pid = proposals.propose(
        db, user(user_id), action=ACTION,
        summary=f"Buy {units.normalize():f} {ev.unit} more of {ev.item['name']} now (forward buy FB-{fid}): "
                f"{_rupees(taken.value)}, net saving {_rupees(taken.net_saving)}",
        payload={"tenant": tenant, "forward_buy_id": fid, "approver": step.approver, "delegate": step.delegate,
                 "buyer": buyer},
        required_permission="sourcing.route_approve", value=taken.value,
    )  # fmt: skip
    now = datetime.now(UTC)
    db.execute(
        text(
            "update forward_buy set status = 'accepted', decided_by = :b, decided_at = :t, decision_reason = :w, "
            "accepted_qty = :q, accepted_net_saving = :n, proposal_id = :p where id = :i"
        ),
        {"b": buyer or f"user:{user_id}", "t": now, "w": why, "q": units, "n": taken.net_saving, "p": pid, "i": fid},
    )
    audit.record(
        db, user(user_id), "forward_buy.accept", "forward_buy", fid,
        after={"qty": str(units), "net_saving": str(taken.net_saving), "proposal": pid, "approver": step.approver},
        reason=why,
    )  # fmt: skip
    db.commit()
    who = notifications.login_of(db, tenant, step.delegate or step.approver) if tenant else None
    if who and tenant:
        tasks.create_task(
            db, tenant, kind="forward_buy",
            title=f"Approve buying {units.normalize():f} {ev.unit} more of {ev.item['name']} (FB-{fid})"[:200],
            assignee=who, sla_hours=chasing.calendar(db).hours_per_day,  # one working day (spec 5.4)
            payload={"forward_buy_id": fid, "proposal_id": pid, "request_no": rec["request_no"],
                     "link": f"#/requisitions/{rec['request_no']}" if rec["request_no"] else None},
            actor=user(user_id),
        )  # fmt: skip
    return {
        "id": fid, "status": "accepted", "proposal_id": pid, "approver": name_of(db, step.approver),
        "accepted_qty": str(units), "accepted_net_saving": str(taken.net_saving),
    }  # fmt: skip


def reject(db: Session, fid: int, user_id: int, reason: str) -> dict[str, Any]:
    why = (reason or "").strip()
    _open_record(db, fid, "open")
    if not why:
        raise ForwardBuyError("a reason is needed to reject", 422)
    db.execute(
        text(
            "update forward_buy set status = 'rejected', decided_by = :b, decided_at = now(), decision_reason = :w where id = :i"
        ),
        {"b": org_code_of(db, user_id) or f"user:{user_id}", "w": why, "i": fid},
    )
    audit.record(db, user(user_id), "forward_buy.reject", "forward_buy", fid, after={"status": "rejected"}, reason=why)
    db.commit()
    return {"id": fid, "status": "rejected"}


def _named(db: Session, rec: dict[str, Any], user_id: int) -> dict[str, Any]:
    payload = db.execute(text("select payload from proposal where id = :i"), {"i": rec["proposal_id"]}).scalar_one()
    me = org_code_of(db, user_id)
    if me is not None and me == payload.get("buyer"):
        raise permissions.PermissionDenied("the buyer who accepted a forward buy does not approve it")
    if me not in (payload.get("approver"), payload.get("delegate")):
        raise permissions.PermissionDenied(
            f"this forward buy is for {name_of(db, str(payload.get('approver')))} to decide"
        )
    return dict(payload)


def approve(db: Session, tenant: str | None, fid: int, user_id: int, note: str | None, on: date) -> dict[str, Any]:
    rec = _open_record(db, fid, "accepted")
    _named(db, rec, user_id)
    db.rollback()  # release the row lock: the revalidation and the proposal take their own
    rec = _open_record(db, fid, "accepted")
    _revalidate(db, tenant, rec, on, user(user_id))
    db.commit()
    try:
        proposals.approve(db, int(rec["proposal_id"]), user_id, note=note)
    except proposals.ProposalError as exc:
        db.rollback()
        raise ForwardBuyError(str(exc)) from None
    done = load(db, fid) or {}
    result = db.execute(text("select execution, result from proposal where id = :i"), {"i": rec["proposal_id"]}).one()
    if result[0] != "ok":
        raise ForwardBuyError(f"the request could not be made: {(result[1] or {}).get('error', 'see the log')}")
    status = db.execute(text("select status from request where no = :n"), {"n": done.get("new_request_no")}).scalar()
    return {"id": fid, "status": done.get("status"), "request_no": done.get("new_request_no"), "request_status": status}


def decline(db: Session, fid: int, user_id: int, reason: str) -> dict[str, Any]:
    why = (reason or "").strip()
    rec = _open_record(db, fid, "accepted")
    _named(db, rec, user_id)
    if not why:
        raise ForwardBuyError("a reason is needed to decline", 422)
    try:
        proposals.reject(db, int(rec["proposal_id"]), user_id, why)
    except proposals.ProposalError as exc:
        db.rollback()
        raise ForwardBuyError(str(exc)) from None
    db.execute(
        text(
            "update forward_buy set status = 'declined', approved_by = :b, approved_at = now(), decision_reason = :w where id = :i"
        ),
        {"b": org_code_of(db, user_id), "w": why, "i": fid},
    )
    audit.record(db, user(user_id), "forward_buy.decline", "forward_buy", fid, after={"status": "declined"}, reason=why)
    db.commit()
    return {"id": fid, "status": "declined"}


@proposals.guard(ACTION)
def _may_approve(db: Session, payload: dict[str, Any], actor: Actor) -> None:
    rec = load(db, int(payload["forward_buy_id"]))
    if rec is None:
        raise ForwardBuyError("forward buy not found", 404)
    _named(db, rec, int(actor.id))


@proposals.executor(ACTION)
def _execute(db: Session, payload: dict[str, Any], actor: Actor) -> dict[str, Any]:
    """Approved: write the planning request for the extra quantity and send it into its own approval."""
    from p2p.requests import drafts

    fid = int(payload["forward_buy_id"])
    rec = load(db, fid)
    if rec is None or rec["status"] != "accepted":
        raise ForwardBuyError(f"forward buy {fid} is not waiting for approval")
    anchor = anchor_line(db, rec["request_no"], rec["line_no"]) if rec["request_no"] else None
    item = _item(db, rec["item_code"])
    assert item is not None
    unit, factor = _purchase_unit(item)
    units = D(rec["accepted_qty"])
    base = units * factor
    from p2p.formulas.calendar import business_today
    from p2p.requests.estimates import for_item

    today = business_today()
    est = for_item(db, item["code"], unit, units, today, item["standard_price"], rec["location_code"])
    price = est.price if est else D(rec["est_price"] or 0)
    info = drafts.item_info(db, [item["code"]])[item["code"]]
    department = anchor.department if anchor else drafts.planning_department(db)
    need_by = anchor.need_by if anchor and anchor.need_by else today
    reason = f"Forward buy FB-{fid}"
    line = drafts.DraftLine(
        item_code=item["code"], item_name=str(item["name"]), qty=units, uom=unit, base_qty=base, est_price=price,
        value=round_money(units * price), need_by=need_by, gl_code=(anchor.gl_code if anchor else None) or info["gl"],
        reason=reason, category_code=item["category_code"], stock_qty=base, est_source=est.source if est else None,
    )  # fmt: skip
    req = drafts.DraftRequest(rec["location_code"], item["category_code"], "normal", [line], department=department)
    first = (rec["explanation"] or "").split(". ")[0]
    no = drafts.write(
        db, actor, req, as_of=today, source_ref=f"FB-{fid}", justification=f"{reason}: {first}."[:1000],
        audit_extra={"forward_buy": fid},
    )  # fmt: skip
    now = datetime.now(UTC)
    db.execute(
        text(
            "update forward_buy set status = 'approved', approved_by = :b, approved_at = :t, new_request_no = :n where id = :i"
        ),
        {"b": org_code_of(db, int(actor.id)) or actor.identity, "t": now, "n": no, "i": fid},
    )
    audit.record(db, actor, "forward_buy.approve", "forward_buy", fid, after={"request_no": no, "qty": str(units)})
    db.commit()
    drafts.submit(db, str(payload.get("tenant") or ""), [req], actor, today)
    if req.submit_error:
        db.execute(
            text("update forward_buy set lapse_reason = :w where id = :i"),
            {"w": f"{no} stays a draft: {req.submit_error}", "i": fid},
        )
    return {"request_no": no, "request_status": req.outcome or "draft", "submit_error": req.submit_error}


# ---- what the screens show -----------------------------------------------------------------------------------


def _s(v: Any, places: int) -> str | None:
    return None if v is None else str(D(v).quantize(D(1).scaleb(-places)))


def as_json(db: Session, rec: dict[str, Any]) -> dict[str, Any]:
    bench = None
    if rec["benchmark_id"]:
        b = (
            db.execute(text("select * from market_benchmark where id = :i"), {"i": rec["benchmark_id"]})
            .mappings()
            .first()
        )
        if b:
            bench = {
                "id": b["id"], "price": _s(b["price"], 4), "uom": b["uom"], "effective_date": b["effective_date"].isoformat(),
                "source_name": b["source_name"], "source_ref": b["source_ref"],
                "verified_by": _verifier_name(db, b["verified_by"]),
            }  # fmt: skip
    item = _item(db, rec["item_code"]) or {}
    approver = None
    if rec["proposal_id"]:
        payload = db.execute(text("select payload from proposal where id = :i"), {"i": rec["proposal_id"]}).scalar()
        approver = name_of(db, (payload or {}).get("approver"))
    points = db.execute(text("select input from rule_evaluation where id = :i"), {"i": rec["evaluation_id"]}).scalar()
    return {
        "id": rec["id"], "item_code": rec["item_code"], "item_name": item.get("name"), "location_code": rec["location_code"],
        "request_no": rec["request_no"], "line_no": rec["line_no"], "status": rec["status"], "rule": rec["rule_label"],
        "evaluation_id": rec["evaluation_id"], "benchmark": bench,
        "price_now": _s(rec["price_now"], 4), "trend_now": _s(rec["trend_now"], 4), "slope": _s(rec["slope"], 4),
        "trend_points": len((points or {}).get("points") or []), "dip_pct": _s(rec["dip_pct"], 4),
        "avg_use": _s(rec["avg_use"], 4), "cover": _s(rec["cover"], 4), "months": rec["months"],
        "qty": _s(rec["qty"], 4), "qty_base": _s(rec["qty_base"], 4), "uom": rec["uom"], "base_uom": item.get("base_uom"),
        "est_price": _s(rec["est_price"], 4), "value": _s(rec["value"], 2), "gross_saving": _s(rec["gross_saving"], 2),
        "holding_cost": _s(rec["holding_cost"], 2), "net_saving": _s(rec["net_saving"], 2), "limit": rec["limit_kind"],
        "explanation": rec["explanation"], "explanation_source": rec["explanation_source"],
        "created_at": rec["created_at"].isoformat() if rec["created_at"] else None,
        "decided_by": rec["decided_by"], "decided_by_name": name_of(db, rec["decided_by"]) if rec["decided_by"] else None,
        "decided_at": rec["decided_at"].isoformat() if rec["decided_at"] else None,
        "decision_reason": rec["decision_reason"], "accepted_qty": _s(rec["accepted_qty"], 4),
        "accepted_net_saving": _s(rec["accepted_net_saving"], 2), "proposal_id": rec["proposal_id"], "approver": approver,
        "approved_by": rec["approved_by"], "approved_by_name": name_of(db, rec["approved_by"]) if rec["approved_by"] else None,
        "approved_at": rec["approved_at"].isoformat() if rec["approved_at"] else None,
        "new_request_no": rec["new_request_no"], "superseded_by": rec["superseded_by"], "lapse_reason": rec["lapse_reason"],
    }  # fmt: skip


def preview_json(ev: Evaluation) -> dict[str, Any]:
    r = ev.result
    return {
        "item_code": ev.item["code"], "location_code": ev.location, "as_of": ev.on.isoformat(),
        "recommended": ev.recommended, "reason": ev.reason, "rule": ev.rule.label if ev.rule else None,
        "request_no": ev.anchor.request_no if ev.anchor else None, "line_no": ev.anchor.line_no if ev.anchor else None,
        "benchmark_id": ev.benchmark["id"] if ev.benchmark else None,
        "price_now": _s(r.price_now, 4) if r else None, "trend_now": _s(r.trend_now, 4) if r else None,
        "slope": _s(r.slope, 4) if r else None, "dip_pct": _s(r.dip_pct, 4) if r else None,
        "trend_points": len(ev.points or []), "avg_use": _s(r.avg_use, 4) if r else None,
        "cover": _s(r.cover, 4) if r else None, "months": ev.months(), "qty": _s(r.units, 4) if r else None,
        "qty_base": _s(r.qty_base, 4) if r else None, "uom": ev.unit, "est_price": _s(ev.est_price, 4),
        "value": _s(r.value, 2) if r else None, "gross_saving": _s(r.gross_saving, 2) if r else None,
        "holding_cost": _s(r.holding_cost, 2) if r else None, "net_saving": _s(r.net_saving, 2) if r else None,
        "limit": r.limit if r else None,
    }  # fmt: skip
