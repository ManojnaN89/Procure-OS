"""The buying route of a request line (D37): the rule version in force and the facts from our own records.

The thresholds are rows of `buying_route_rule`, one per version; a category row can only make the company's rule
stricter. The facts are the item's last purchase, the supplier's delivery record, the prices on file and what was
already asked for the same item in the window. The comparisons themselves are in formulas/buying_route.py.
"""

from dataclasses import dataclass, field, replace
from datetime import date, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from p2p.formulas import buying_route as br
from p2p.formulas.money import round_money
from p2p.formulas.sourcing import PriceOption, pick_contract_price
from p2p.platform import audit
from p2p.platform.sod import Actor
from p2p.sourcing import documents
from p2p.suppliers import scoring

THRESHOLDS = (
    "agree_above",
    "reorder_within_days",
    "reorder_on_time_pct",
    "reorder_below",
    "reorder_price_pct",
    "two_quotes_from",
    "three_quotes_above",
    "window_days",
)
TZ_NAME = "Asia/Kolkata"


class RouteError(Exception):
    pass


# ---- the rule ---------------------------------------------------------------------------------


def in_force_row(db: Session, category: str | None, as_of: date) -> dict[str, Any] | None:
    """The row in force on the date: the company's (category None) or a category's own."""
    row = (
        db.execute(
            text(
                "select * from buying_route_rule where category_code is not distinct from :c and effective_from <= :d "
                "and (effective_to is null or effective_to >= :d) order by version desc limit 1"
            ),
            {"c": category, "d": as_of},
        )
        .mappings()
        .first()
    )
    return dict(row) if row else None


def rule_for(db: Session, category: str | None, as_of: date) -> br.RouteRule:
    """The company's rule in force on the date, made stricter by the category's row when it has one."""
    base = in_force_row(db, None, as_of)
    if base is None:
        raise RouteError(f"no buying route rule is in force on {as_of}")
    rule = br.RouteRule(
        version=f"buying_route v{base['version']}",
        agree_above=Decimal(base["agree_above"]),
        reorder_within_days=int(base["reorder_within_days"]),
        reorder_on_time_pct=Decimal(base["reorder_on_time_pct"]),
        reorder_below=Decimal(base["reorder_below"]),
        reorder_price_pct=Decimal(base["reorder_price_pct"]),
        two_quotes_from=Decimal(base["two_quotes_from"]),
        three_quotes_above=Decimal(base["three_quotes_above"]),
        window_days=int(base["window_days"]),
    )
    own = in_force_row(db, category, as_of) if category else None
    if own is None:
        return rule
    return br.stricter(rule, own, f"{rule.version} + {category} v{own['version']}")


def save_rule(
    db: Session,
    actor: Actor,
    values: dict[str, Any],
    *,
    category: str | None = None,
    effective_from: date,
    note: str | None = None,
) -> int:
    """Make the next version of the company's rule (category None) or of a category's row, from a date. The version in
    force until then ends the day before. A category row keeps only the thresholds it names. Returns the version."""
    unknown = set(values) - set(THRESHOLDS)
    if unknown:
        raise RouteError(f"not a threshold of the buying route: {', '.join(sorted(unknown))}")
    current = in_force_row(db, category, effective_from)
    if category is None:
        if current is None:
            raise RouteError("the company has no buying route rule to change")
        values = {k: values.get(k, current[k]) for k in THRESHOLDS}
    problems = br.check_rule(values)
    if problems:
        raise RouteError("; ".join(problems))
    version = int(
        db.execute(
            text(
                "select coalesce(max(version), 0) + 1 from buying_route_rule where category_code is not distinct from :c"
            ),
            {"c": category},
        ).scalar_one()
    )
    if current is not None:
        db.execute(
            text("update buying_route_rule set effective_to = :t where id = :i"),
            {"t": effective_from - timedelta(days=1), "i": current["id"]},
        )
    db.execute(
        text(
            f"insert into buying_route_rule (category_code, version, effective_from, note, created_by, {', '.join(values)}) "  # noqa: S608 - column names are checked against THRESHOLDS
            f"values (:c, :v, :f, :n, :by, {', '.join(':' + k for k in values)})"
        ),
        {"c": category, "v": version, "f": effective_from, "n": note, "by": f"{actor.kind}:{actor.id}", **values},
    )
    label = f"{category or 'company'} v{version}"
    audit.record(
        db,
        actor,
        "buying_route.change",
        "buying_route_rule",
        label,
        before={k: current[k] for k in THRESHOLDS} if current else None,
        after={**values, "effective_from": effective_from},
        reason=note,
        rule_version=f"buying_route {label}",
    )
    db.commit()
    return version


def mark_single_source(db: Session, actor: Actor, item_code: str, supplier_code: str | None, reason: str) -> None:
    """Mark an item as bought from one supplier only (or clear the mark with supplier None)."""
    before = db.execute(
        text("select supplier_code from item_single_source where item_code = :i"), {"i": item_code}
    ).scalar()
    if supplier_code is None:
        db.execute(text("delete from item_single_source where item_code = :i"), {"i": item_code})
    else:
        db.execute(
            text(
                "insert into item_single_source (item_code, supplier_code, reason, marked_by) values (:i, :s, :r, :by) "
                "on conflict (item_code) do update set supplier_code = :s, reason = :r, marked_by = :by, marked_at = now()"
            ),
            {"i": item_code, "s": supplier_code, "r": reason, "by": f"{actor.kind}:{actor.id}"},
        )
    audit.record(
        db,
        actor,
        "item.single_source",
        "item",
        item_code,
        before={"supplier": before},
        after={"supplier": supplier_code},
        reason=reason,
    )
    db.commit()


# ---- the facts --------------------------------------------------------------------------------


@dataclass(frozen=True)
class Purchase:
    supplier_code: str
    price: Decimal
    on: date


@dataclass
class Route:
    """The route of one line with what it was based on, and where it buys when it buys directly."""

    choice: br.Choice
    rule: br.RouteRule
    line_value: Decimal | None
    window_value: Decimal | None
    supplier_code: str | None = None
    price: Decimal | None = None


@dataclass
class Router:
    """Decides the routes of the lines of one sourcing run. Lines of the same item decided earlier in the run count
    towards the window, as do the decisions already saved."""

    db: Session
    as_of: date
    _added: dict[str, Decimal] = field(default_factory=dict)
    _histories: dict[str, scoring.History] | None = None

    def decide(
        self, line: dict[str, Any], contract_price: Decimal | None, contract_reason: str, emergency: bool
    ) -> Route:
        db, item, qty = self.db, line["item_code"], Decimal(line["qty"])
        rule = rule_for(db, line["category"], self.as_of)
        single = self._single_source(item)
        last = self.last_purchase(item, line["uom"]) if item else None
        last_price = self.price_on_file(last.supplier_code, item, line["uom"], qty) if last else None
        single_price = self._known_price(single, item, line["uom"], qty, last) if single else None
        # the value is the price the line will be bought at when it is known (a contract, the single source), else the
        # requester's estimate, else the item's standard price, else what the last supplier charges now (or charged)
        unit = next(
            (
                Decimal(p)
                for p in (
                    contract_price,
                    single_price,
                    line.get("est_price"),
                    line.get("standard_price"),
                    last_price,
                    last and last.price,
                )
                if p
            ),
            None,
        )
        line_value = round_money(qty * unit) if unit is not None else None
        window = None if line_value is None else line_value + self.earlier_value(item, rule.window_days)
        facts = br.Facts(
            contract=contract_reason if contract_price is not None else None,
            contract_note=contract_reason,
            single_source=single,
            emergency=emergency,
            window_value=window,
        )
        if last is not None:
            pace = self._on_time(last.supplier_code)
            facts = replace(
                facts,
                last_supplier=last.supplier_code,
                last_supplier_problem=self.supplier_problem(last.supplier_code),
                days_since_last=(self.as_of - last.on).days,
                on_time=br.on_time_pct(pace.on_time, pace.receipts),
                price_change=br.change_pct(last_price or last.price, last.price),
            )
        choice = br.choose(facts, rule)
        route = Route(choice, rule, line_value, window)
        if choice.route == "single_source" and single:
            route.supplier_code, route.price = single, single_price
        elif choice.route == "reorder" and last:
            route.supplier_code, route.price = last.supplier_code, last_price or last.price
        elif choice.route == "emergency":
            route.supplier_code, route.price = self._emergency_supplier(item, line["uom"], qty, last)
        if item and line_value is not None:
            self._added[item] = self._added.get(item, Decimal(0)) + line_value
        return route

    def earlier_value(self, item: str | None, days: int) -> Decimal:
        """What was already decided for the item in the window (today and the days before it), this run included."""
        if not item:
            return Decimal(0)
        saved = self.db.execute(
            text(
                "select coalesce(sum(d.line_value), 0) from sourcing_decision d join request_line rl "
                "on rl.request_no = d.request_no and rl.line_no = d.line_no where rl.item_code = :i "
                "and rl.status <> 'cancelled' and d.decided_on > :start and d.decided_on <= :d"
            ),
            {"i": item, "start": self.as_of - timedelta(days=days), "d": self.as_of},
        ).scalar_one()
        return Decimal(saved) + self._added.get(item, Decimal(0))

    def last_purchase(self, item: str, uom: str | None) -> Purchase | None:
        """The item's last order line on an approved order (not cancelled), on or before the date."""
        row = self.db.execute(
            text(
                "select po.supplier_code, l.unit_price, (po.created_at at time zone :tz)::date from po_line l "
                "join po on po.no = l.po_no where l.item_code = :i and l.uom is not distinct from :u "
                "and po.status not in ('draft', 'submitted', 'cancelled') and l.status <> 'cancelled' "
                "and (po.created_at at time zone :tz)::date <= :d order by po.created_at desc, po.no desc limit 1"
            ),
            {"tz": TZ_NAME, "i": item, "u": uom, "d": self.as_of},
        ).first()
        return Purchase(row[0], Decimal(row[1]), row[2]) if row else None

    def price_on_file(self, supplier: str, item: str, uom: str | None, qty: Decimal) -> Decimal | None:
        """The supplier's price in force for the item and quantity (a price list or a contract that is in force)."""
        rows = self.db.execute(
            text(
                "select p.contract_code, p.price, coalesce(p.min_qty, 0), p.lead_days, p.valid_to from price_record p "
                "left join contract c on c.code = p.contract_code where p.supplier_code = :s and p.item_code = :i "
                "and p.uom is not distinct from :u and p.valid_from <= :d and (p.valid_to is null or p.valid_to >= :d) "
                "and (p.contract_code is null or (c.status = 'active' and c.valid_from <= :d "
                "and (c.valid_to is null or c.valid_to >= :d)))"
            ),
            {"s": supplier, "i": item, "u": uom, "d": self.as_of},
        ).all()
        pick = pick_contract_price(
            [PriceOption(supplier, r[0] or "", Decimal(r[1]), Decimal(r[2]), r[3], r[4]) for r in rows], qty
        )
        return pick.price if pick else None

    def supplier_problem(self, code: str) -> str | None:
        """Why a supplier cannot be bought from today, or None."""
        status = self.db.execute(text("select status from supplier where code = :c"), {"c": code}).scalar()
        if status != "active":
            return f"{code} is {status or 'unknown'}"
        docs = documents.check_supplier(self.db, code, self.as_of)
        return None if docs.ok else f"{code}: {'; '.join(docs.blocking)}"

    def _on_time(self, supplier: str) -> scoring.History:
        if self._histories is None:
            self._histories = scoring.histories(self.db, self.as_of)
        return self._histories.get(supplier, scoring.History())

    def _single_source(self, item: str | None) -> str | None:
        if not item:
            return None
        found = self.db.execute(
            text("select supplier_code from item_single_source where item_code = :i"), {"i": item}
        ).scalar()
        return str(found) if found else None

    def _known_price(
        self, supplier: str, item: str, uom: str | None, qty: Decimal, last: Purchase | None
    ) -> Decimal | None:
        on_file = self.price_on_file(supplier, item, uom, qty)
        if on_file is not None:
            return on_file
        return last.price if last and last.supplier_code == supplier else None

    def _emergency_supplier(
        self, item: str | None, uom: str | None, qty: Decimal, last: Purchase | None
    ) -> tuple[str | None, Decimal | None]:
        """In an emergency the last supplier, if it can still be used; otherwise the lowest price on file."""
        if not item:
            return None, None
        if last and self.supplier_problem(last.supplier_code) is None:
            return last.supplier_code, self._known_price(last.supplier_code, item, uom, qty, last)
        codes = [
            r[0]
            for r in self.db.execute(
                text("select distinct supplier_code from price_record where item_code = :i order by 1"), {"i": item}
            )
        ]
        priced = [
            (price, code)
            for code in codes
            if self.supplier_problem(code) is None and (price := self.price_on_file(code, item, uom, qty)) is not None
        ]
        if not priced:
            return None, None
        price, code = min(priced)
        return code, price
