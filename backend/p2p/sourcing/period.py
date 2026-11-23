"""Period RFQs (task 12.1, D40 route B): prices asked for a period with no purchase request.

A buyer asks prices for items at one location for a period (usually from a strategic review): each line becomes a
period demand line and an RFQ line that points to it, never to a request line. The engine suggests each quantity (the
larger of the open requirements in the period and the last 12 months' orders pro rata, formulas/period.py); the
buyer's quantity is the RFQ's. Who is asked and how many quotes are wanted are the operational rules, on the period's
value. The RFQ is a draft whose sending a buyer confirms (D42). Nothing here commits money: no budget, request, order
or contract use comes from a period RFQ; budget and approval apply when a request later buys on the contract.
"""

from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from p2p.formulas import period as rules
from p2p.formulas.benchmarks import factor_to
from p2p.formulas.buying_route import quotes_needed
from p2p.formulas.money import format_qty
from p2p.formulas.scope import contains
from p2p.formulas.words import count
from p2p.masters import regions
from p2p.platform import audit, numbering, proposals, reference
from p2p.platform.sod import Actor
from p2p.sourcing import benchmarks, price_review, route, service
from p2p.sourcing.service import LineOutcome

OPEN = ("draft", "sent", "award_proposed")


class PeriodError(Exception):
    def __init__(self, message: str, status: int = 422) -> None:
        super().__init__(message)
        self.status = status


@dataclass(frozen=True)
class Suggested:
    qty: Decimal
    open_need: Decimal
    ordered_12m: Decimal
    months: int
    uom: str | None
    words: str

    def as_json(self) -> dict[str, Any]:
        return {
            "suggested_qty": format_qty(self.qty), "open_need": format_qty(self.open_need),
            "ordered_12m": format_qty(self.ordered_12m), "months": self.months, "uom": self.uom,
            "suggestion": self.words,
        }  # fmt: skip


@dataclass
class Created:
    rfq_no: str
    proposal_id: int | None
    invited: list[str]
    min_quotes: int
    note: str | None
    lines: list[dict[str, Any]] = field(default_factory=list)


# ---- the suggested quantity ------------------------------------------------------------------------------------


def _window(as_of: date) -> tuple[date, date]:
    """The 12 full calendar months before the month of `as_of` (23 Nov 2026: 1 Nov 2025 to 31 Oct 2026)."""
    first = as_of.replace(day=1)
    return rules.add_months(first, -12), first - timedelta(days=1)


def _factor(db: Session, item: str, from_uom: str | None, to_uom: str | None) -> Decimal:
    if not from_uom or not to_uom:
        return Decimal(1)
    return factor_to(from_uom, to_uom, benchmarks.conversions(db, item)) or Decimal(1)


def ordered_12m(db: Session, item: str, location: str, uom: str | None, as_of: date) -> Decimal:
    """What was ordered of the item for the location in the 12 full calendar months before this one, in the purchase
    unit: the platform's order lines (not draft, submitted or cancelled) and the old system's orders loaded as history
    (sourcing/order_history.py)."""
    start, end = _window(as_of)
    total = Decimal(0)
    for line_uom, qty in db.execute(
        text(
            "select l.uom, sum(l.qty) from po_line l join po p on p.no = l.po_no where l.item_code = :i "
            "and p.ship_to_location = :loc and p.status not in ('draft', 'submitted', 'cancelled') "
            "and l.status <> 'cancelled' and (p.created_at at time zone 'Asia/Kolkata')::date between :f and :t "
            "group by l.uom"
        ),
        {"i": item, "loc": location, "f": start, "t": end},
    ):
        total += Decimal(qty) * _factor(db, item, line_uom, uom)
    for line_uom, qty in db.execute(
        text(
            "select uom, sum(qty) from order_history where item_code = :i and location_code = :loc "
            "and ordered_on between :f and :t group by uom"
        ),
        {"i": item, "loc": location, "f": start, "t": end},
    ):
        total += Decimal(qty) * _factor(db, item, line_uom, uom)
    return total


def open_need(db: Session, item: str, location: str, start: date, end: date, uom: str | None) -> Decimal:
    """The open material requirements of the item at the location needed inside the period, in the purchase unit."""
    base = db.execute(text("select base_uom from item where code = :i"), {"i": item}).scalar()
    need = db.execute(
        text(
            "select coalesce(sum(base_qty), 0) from material_requirement where item_code = :i and location_code = :loc "
            "and status = 'open' and need_by between :f and :t"
        ),
        {"i": item, "loc": location, "f": start, "t": end},
    ).scalar_one()
    return Decimal(need) * _factor(db, item, base, uom)


def suggest(db: Session, item: str, location: str, start: date, end: date, as_of: date) -> Suggested:
    """The engine's quantity for the period, with both figures in words."""
    row = db.execute(text("select purchase_uom, base_uom from item where code = :i"), {"i": item}).first()
    if row is None:
        raise PeriodError(f"there is no item {item}")
    if problems := rules.check_period(start, end, as_of):
        raise PeriodError(problems[0])
    uom = row[0] or row[1]
    months = rules.months_in(start, end)
    need, ordered = open_need(db, item, location, start, end, uom), ordered_12m(db, item, location, uom, as_of)
    got = rules.suggested(need, ordered, months)
    unit = f" {uom}" if uom else ""
    words = (
        f"Ordered {format_qty(ordered)}{unit} in the last 12 months ({format_qty(got.history_part)}{unit} for "
        f"{count(months, 'month')}); {format_qty(need)}{unit} of open requirements in the period"
    )
    return Suggested(got.qty, need, ordered, months, uom, words)


# ---- making a period RFQ ---------------------------------------------------------------------------------------


def _items(db: Session, lines: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Each line's item, checked: known, active, not capital (bought per approved request), quantity above zero."""
    if not lines:
        raise PeriodError("a period RFQ needs at least one item line")
    out = []
    for n, line in enumerate(lines, start=1):
        code = str(line.get("item_code") or "").strip()
        if not code:
            raise PeriodError(f"line {n} has no item")
        try:
            qty = Decimal(str(line.get("qty")))
        except Exception:  # noqa: BLE001 - whatever it is, it is not a quantity
            raise PeriodError(f"line {n}: the quantity is not a number") from None
        if not qty.is_finite() or qty <= 0:
            raise PeriodError(f"line {n}: the quantity must be above zero")
        item = (
            db.execute(
                text(
                    "select code, name, category_code, coalesce(purchase_uom, base_uom) as uom, active, capex, "
                    "standard_price from item where code = :c"
                ),
                {"c": code},
            )
            .mappings()
            .first()
        )
        if item is None:
            raise PeriodError(f"line {n}: there is no item {code}")
        if item["active"] is False:
            raise PeriodError(f"line {n}: {code} is not active")
        if item["capex"]:
            raise PeriodError(f"line {n}: {code} is a capital item; it is bought on an approved request")
        out.append({**dict(item), "qty": qty})
    if len({i["category_code"] for i in out}) > 1:
        raise PeriodError("one period RFQ per category: make one for each")
    return out


def _clash(db: Session, items: list[str], location: str, start: date, end: date) -> str | None:
    row = db.execute(
        text(
            "select r.no, d.item_code, d.period_from, d.period_to from rfq r join rfq_line l on l.rfq_no = r.no "
            "join period_demand_line d on d.id = l.period_line_id where r.kind = 'period' and r.status = any(:open) "
            "and d.item_code = any(:items) and d.location_code = :loc and d.period_from <= :t and d.period_to >= :f "
            "order by r.no limit 1"
        ),
        {"open": list(OPEN), "items": items, "loc": location, "f": start, "t": end},
    ).first()
    if row is None:
        return None
    return f"{row[0]} already asks for {row[1]} at {location} for {row[2]:%d %b %Y} to {row[3]:%d %b %Y}"


def _contract_notes(db: Session, items: list[str], location: str, start: date, end: date) -> list[str]:
    """Active contract prices for the items here that cover part of the period: noted, never a refusal (Rev s3)."""
    rows = db.execute(
        text(
            "select distinct pr.contract_code, pr.item_code, coalesce(least(pr.valid_to, c.valid_to), "  # noqa: S608
            "pr.valid_to, c.valid_to) from price_record pr join contract c on c.code = pr.contract_code "
            "where pr.item_code = any(:items) and pr.status = 'active' and c.status = 'active' "
            "and pr.valid_from <= :t and (pr.valid_to is null or pr.valid_to >= :f) "
            f"and {regions.price_in_scope('pr')} order by 1, 2"
        ),
        {"items": items, "loc": location, "f": start, "t": end},
    ).all()
    return [
        f"{c} prices {i} here until {u:%d %b %Y}; its obligations stay"
        if u
        else f"{c} prices {i} here; its obligations stay"
        for c, i, u in rows
    ]


def create(
    db: Session,
    tenant: str,
    actor: Actor,
    *,
    location: str,
    period_from: date,
    period_to: date,
    lines: list[dict[str, Any]],
    as_of: date,
    review_task_id: int | None = None,
    note: str | None = None,
) -> Created:
    """Make the period RFQ (a draft) and propose sending it to a buyer. Nothing is written when anything is wrong."""
    del tenant  # the RFQ is in the session's company; kept in the signature like the other sourcing services
    db.execute(text("select pg_advisory_xact_lock(hashtext('sourcing'))"))
    if problems := rules.check_period(period_from, period_to, as_of):
        raise PeriodError(problems[0])
    if db.execute(text("select 1 from location where code = :c"), {"c": location}).first() is None:
        raise PeriodError(f"there is no location {location}")
    items = _items(db, lines)
    codes = [i["code"] for i in items]
    if clash := _clash(db, codes, location, period_from, period_to):
        raise PeriodError(clash)
    if review_task_id is not None:
        price_review.open_task(db, review_task_id)
    category = str(items[0]["category_code"])
    who = actor.id if actor.kind == "user" else actor.identity
    value, group, suggestions = Decimal(0), [], []
    rule = route.rule_for(db, category, as_of)
    from p2p.requests import estimates  # noqa: PLC0415 - requests build on sourcing

    for n, item in enumerate(items, start=1):
        est = estimates.for_item(db, item["code"], item["uom"], item["qty"], as_of, item["standard_price"], location)
        value += item["qty"] * est.price if est else Decimal(0)
        group.append((item, LineOutcome(n, item["code"], item["name"], item["qty"], item["uom"], "rfq", "")))
        suggestions.append(suggest(db, item["code"], location, period_from, period_to, as_of))
    for _, outcome in group:  # the route's band on the period's value (Rev s3: quantity x estimate)
        outcome.min_quotes, outcome.route_rule = quotes_needed(value, rule), rule.version
    cat = db.execute(text("select min_quotes, buyer_user from item_category where code = :c"), {"c": category}).first()
    pairs = [(dict(i), o) for i, o in group]
    min_quotes, min_rule = service.quotation_count(db, category, cat[0] if cat else None, pairs)
    wanted = max(int(service._app_setting(db, "rfq_supplier_count", "3")), min_quotes)
    suppliers, _ = service.eligible_suppliers(db, category, codes, as_of, location)
    invited = suppliers[:wanted]
    notes = _contract_notes(db, codes, location, period_from, period_to)
    if len(invited) < min_quotes:
        notes.insert(
            0,
            f"Only {count(len(invited), 'supplier')} {'is' if len(invited) == 1 else 'are'} approved for {category}; "
            f"at least {min_quotes} quotes are wanted",
        )
    if note and note.strip():
        notes.append(note.strip())
    words = "; ".join(notes) or None
    rfq_no = numbering.next_number(db, "rfq", as_of)
    due = reference.calendar_for(db).add_working_days(
        as_of, int(service._org_setting(db, "default_rfq_response_days", "5"))
    )
    db.execute(
        text(
            "insert into rfq (no, buyer, request_no, category_code, due_date, status, note, min_quotes, min_quotes_rule, "
            "delivery_location, kind, period_from, period_to, review_task_id) values (:no, :buyer, null, :cat, :due, "
            "'draft', :note, :mq, :mqr, :loc, 'period', :f, :t, :rev)"
        ),
        {
            "no": rfq_no, "buyer": cat[1] if cat else None, "cat": category, "due": due, "note": words, "mq": min_quotes,
            "mqr": min_rule, "loc": location, "f": period_from, "t": period_to, "rev": review_task_id,
        },
    )  # fmt: skip
    created = Created(rfq_no, None, [s.code for s in invited], min_quotes, words)
    for n, (item, sug) in enumerate(zip(items, suggestions, strict=True), start=1):
        line_id = db.execute(
            text(
                "insert into period_demand_line (item_code, location_code, period_from, period_to, qty, uom, "
                "suggested_qty, suggestion, review_task_id, created_by) values (:i, :loc, :f, :t, :q, :u, :s, :w, "
                ":rev, :by) returning id"
            ),
            {
                "i": item["code"], "loc": location, "f": period_from, "t": period_to, "q": item["qty"], "u": item["uom"],
                "s": sug.qty, "w": sug.words, "rev": review_task_id, "by": who,
            },
        ).scalar_one()  # fmt: skip
        db.execute(
            text(
                "insert into rfq_line (rfq_no, line_no, request_no, request_line_no, period_line_id, item_code, "
                "description, qty, uom, need_by) values (:r, :n, null, null, :p, :i, :d, :q, :u, :need)"
            ),
            {
                "r": rfq_no, "n": n, "p": line_id, "i": item["code"], "d": item["name"], "q": item["qty"],
                "u": item["uom"], "need": period_from,
            },
        )  # fmt: skip
        created.lines.append(
            {
                "line_no": n,
                "item_code": item["code"],
                "qty": format_qty(item["qty"]),
                "suggested_qty": format_qty(sug.qty),
            }
        )
    for s in invited:
        db.execute(
            text("insert into rfq_supplier (rfq_no, supplier_code, email) values (:r, :s, :e)"),
            {"r": rfq_no, "s": s.code, "e": s.email},
        )
    audit.record(
        db, actor, "rfq.create", "rfq", rfq_no,
        after={
            "kind": "period", "location": location, "period_from": period_from, "period_to": period_to,
            "category": category, "suppliers": created.invited, "due_date": due, "min_quotes": min_quotes,
            "lines": [
                {**ln, "asked": ln["qty"], "suggestion": s.words} for ln, s in zip(created.lines, suggestions, strict=True)
            ],
            "review": review_task_id,
        },
        rule_version=min_rule,
    )  # fmt: skip
    if review_task_id is not None:
        price_review.close_started(db, review_task_id, actor, rfq_no)
    db.commit()
    result = service.SourcingResult(rfq_no)
    service._dispatch(db, rfq_no, value, actor, result)
    created.proposal_id = result.proposals[0] if result.proposals else None
    return created


# ---- from a strategic review ------------------------------------------------------------------------------------


def _review(db: Session, task_id: int) -> dict[str, Any]:
    row = db.execute(
        text("select status, payload from task where id = :i and kind = :k"), {"i": task_id, "k": price_review.KIND}
    ).first()
    if row is None:
        raise PeriodError(f"review {task_id} does not exist", 404)
    if row[0] != "open":
        raise PeriodError(f"review {task_id} is {row[0]}", 409)
    return dict(row[1] or {})


def prefill_from_review(db: Session, task_id: int, as_of: date) -> dict[str, Any]:
    """What a period RFQ started from the review asks: its item (or, for an expiry, the contract's items), the
    locations in scope (the buyer picks one), the period (after the contract ends for 12 months; else the next month
    for 3 months, OP-6) and each line's suggested quantity at the first location."""
    review = _review(db, task_id)
    contract = review.get("contract")
    prices = (
        [
            dict(r)
            for r in db.execute(
                text(
                    "select item_code, location_code, region_code from price_record where contract_code = :c "
                    "and status = 'active' and (cast(:i as text) is null or item_code = :i) order by item_code"
                ),
                {"c": contract, "i": review.get("item") if review.get("trigger") != "expiry" else None},
            ).mappings()
        ]
        if contract
        else []
    )
    items = list(dict.fromkeys([str(review["item"])] if review.get("item") else [p["item_code"] for p in prices]))
    region_of = regions.region_of(db)
    every = sorted(region_of)
    if prices:
        locations = [loc for loc in every if any(contains(regions.price_scope(p), loc, region_of) for p in prices)]
    else:
        locations = every
    if review.get("trigger") == "expiry" and contract:
        ends = db.execute(text("select valid_to from contract where code = :c"), {"c": contract}).scalar()
        start = (ends + timedelta(days=1)) if ends else rules.add_months(as_of.replace(day=1), 1)
        end = rules.last_day(start)
    else:
        start = rules.add_months(as_of.replace(day=1), 1)
        end = rules.add_months(start, 3) - timedelta(days=1)
    where = locations[0] if locations else None
    lines = [
        {"item_code": i, "suggested_qty": format_qty(suggest(db, i, where, start, end, as_of).qty) if where else None}
        for i in items
    ]
    return {
        "task_id": task_id, "locations": locations, "period_from": start.isoformat(), "period_to": end.isoformat(),
        "lines": lines,
    }  # fmt: skip


# ---- cancelling --------------------------------------------------------------------------------------------------


def cancel(db: Session, rfq_no: str, actor: Actor, reason: str) -> None:
    """Cancel a period RFQ not yet awarded: its sending proposal is withdrawn and its demand lines cancelled. Invited
    suppliers get no mail (OP-11)."""
    if not (reason or "").strip():
        raise PeriodError("a reason is needed to cancel")
    row = db.execute(text("select kind, status, proposal_id from rfq where no = :n for update"), {"n": rfq_no}).first()
    if row is None:
        raise PeriodError(f"{rfq_no} does not exist", 404)
    if row[0] != "period":
        raise PeriodError(f"{rfq_no} is bought for a request: its lines are changed on the request", 409)
    if row[1] not in ("draft", "sent"):
        raise PeriodError(f"{rfq_no} is {row[1].replace('_', ' ')}: it can no longer be cancelled", 409)
    db.execute(text("update rfq set status = 'cancelled' where no = :n"), {"n": rfq_no})
    db.execute(
        text(
            "update period_demand_line set status = 'cancelled' where id in "
            "(select period_line_id from rfq_line where rfq_no = :n)"
        ),
        {"n": rfq_no},
    )
    audit.record(db, actor, "rfq.cancel", "rfq", rfq_no, before={"status": row[1]}, reason=reason.strip())
    db.commit()
    if row[2] is not None:
        proposals.withdraw(db, int(row[2]), int(actor.id) if actor.kind == "user" else None, f"{rfq_no} was cancelled")
