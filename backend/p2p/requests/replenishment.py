"""Automatic replenishment: turn stock that has fallen to its reorder point into draft purchase requests.

A deterministic service. It reads the stock levels, works out how much of each item to buy with the formulas in
formulas/replenishment.py, and writes draft requests grouped by location, category and urgency (so each one has a
single buyer and a single ship-to). Nothing is sent anywhere: a draft waits for the approval step.

Running it twice does not double anything. An item-location that already has an open request line is skipped, and the
run holds a database lock so two runs at the same moment cannot both create requests.
"""

import json
import uuid
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from p2p.formulas.money import format_qty, round_money
from p2p.formulas.replenishment import purchase_quantity, shortfall
from p2p.formulas.stock import BELOW_SAFETY, COVERED, NEGATIVE, REORDER, STOCKOUT
from p2p.platform import audit, numbering, reference
from p2p.platform.sod import Actor
from p2p.receiving.stock import StockLine, stock_levels

NEEDS_ORDER = {STOCKOUT, BELOW_SAFETY, REORDER}
URGENT = {STOCKOUT, BELOW_SAFETY}


@dataclass
class DraftLine:
    item_code: str
    item_name: str
    qty: Decimal
    uom: str
    base_qty: Decimal
    est_price: Decimal
    value: Decimal
    need_by: date
    gl_code: str | None
    reason: str
    stock_status: str


@dataclass
class DraftRequest:
    location_code: str
    category_code: str
    priority: str
    lines: list[DraftLine]
    no: str | None = None  # a dry run does not take a number

    @property
    def total_value(self) -> Decimal:
        return sum((line.value for line in self.lines), Decimal(0))


@dataclass
class Skipped:
    item_code: str
    location_code: str
    reason: str


@dataclass
class RunResult:
    as_of: date
    dry_run: bool
    run_id: str
    requests: list[DraftRequest] = field(default_factory=list)
    skipped: list[Skipped] = field(default_factory=list)

    @property
    def line_count(self) -> int:
        return sum(len(r.lines) for r in self.requests)

    @property
    def total_value(self) -> Decimal:
        return sum((r.total_value for r in self.requests), Decimal(0))


_ITEMS = """
    select i.code, i.active, i.stocked, i.moq, i.purchase_uom, i.base_uom, i.pack_size, i.standard_price,
           i.default_lead_days, i.category_code, coalesce(i.default_gl, c.default_gl) as gl,
           (select u.factor from uom_conversion u
             where u.item_code = i.code and u.from_uom = i.purchase_uom and u.to_uom = i.base_uom) as factor
    from item i join item_category c on c.code = i.category_code
    where i.code = any(:codes)
"""


def _open_requests(db: Session) -> dict[tuple[str, str], str]:
    rows = db.execute(
        text(
            "select rl.item_code, rl.delivery_location, min(rl.request_no) from request_line rl "
            "join request r on r.no = rl.request_no "
            "where rl.status in ('open', 'sourcing') and r.status in ('draft', 'submitted', 'approved', 'returned') "
            "group by rl.item_code, rl.delivery_location"
        )
    )
    return {(r[0], r[1]): r[2] for r in rows}


def _reason(line: StockLine) -> str:
    plan = (
        f"up to the maximum {format_qty(line.max_qty)}"
        if line.policy == "minmax"
        else f"the standard lot {format_qty(line.order_qty)}"
    )
    return (
        f"On hand {format_qty(line.on_hand)} {line.uom} is at or below the reorder point "
        f"{format_qty(line.reorder_point)}; order {plan}"
    )


def plan(db: Session, as_of: date, *, location: str | None = None) -> tuple[list[DraftRequest], list[Skipped]]:
    """Work out the draft requests without writing anything."""
    calendar = reference.calendar_for(db)
    levels = stock_levels(db, location=location)
    open_requests = _open_requests(db)
    skipped: list[Skipped] = []
    candidates: list[StockLine] = []
    for line in levels:
        key = (line.item_code, line.location_code)
        if line.status == NEGATIVE:
            skipped.append(Skipped(*key, "Books show negative stock: check the postings before ordering"))
        elif line.status == COVERED or (line.status in NEEDS_ORDER and key in open_requests):
            skipped.append(Skipped(*key, f"Already requested ({open_requests.get(key, 'open request')})"))
        elif line.status in NEEDS_ORDER:
            candidates.append(line)
    if not candidates:
        return [], skipped

    info = {
        r["code"]: r for r in db.execute(text(_ITEMS), {"codes": sorted({c.item_code for c in candidates})}).mappings()
    }
    lead_override = {
        (r[0], r[1]): r[2]
        for r in db.execute(
            text(
                "select item_code, location_code, lead_days_override from item_location where lead_days_override is not null"
            )
        )
    }
    groups: dict[tuple[str, str, str], list[DraftLine]] = {}
    for line in candidates:
        key = (line.item_code, line.location_code)
        item = info[line.item_code]
        if not item["active"]:
            skipped.append(Skipped(*key, "Item is inactive"))
            continue
        if not item["standard_price"] or item["standard_price"] <= 0:
            skipped.append(Skipped(*key, "No standard price to estimate the request with"))
            continue
        purchase_uom = item["purchase_uom"] or item["base_uom"]
        factor = item["factor"] or (Decimal(item["pack_size"] or 1) if purchase_uom != item["base_uom"] else Decimal(1))
        need = shortfall(line.policy, line.on_hand + line.on_order, max_qty=line.max_qty, order_qty=line.order_qty)
        qty = purchase_quantity(need, base_per_purchase_unit=factor, moq=Decimal(item["moq"] or 0))
        if qty == 0:
            skipped.append(Skipped(*key, "Nothing to order"))
            continue
        lead = lead_override.get(key) or item["default_lead_days"] or 0
        priority = "urgent" if line.status in URGENT else "normal"
        groups.setdefault((line.location_code, item["category_code"], priority), []).append(
            DraftLine(
                item_code=line.item_code,
                item_name=line.item_name,
                qty=qty,
                uom=purchase_uom,
                base_qty=qty * factor,
                est_price=item["standard_price"],
                value=round_money(qty * item["standard_price"]),
                need_by=calendar.add_working_days(as_of, int(lead)),
                gl_code=str(item["gl"]) if item["gl"] is not None else None,
                reason=_reason(line),
                stock_status=line.status,
            )
        )
    requests = [
        DraftRequest(loc, cat, prio, sorted(lines, key=lambda x: x.item_code))
        for (loc, cat, prio), lines in sorted(groups.items())
    ]
    return requests, skipped


def run(
    db: Session,
    *,
    as_of: date,
    actor: Actor = audit.SYSTEM,
    dry_run: bool = False,
    location: str | None = None,
) -> RunResult:
    """Create the draft requests (or, for a dry run, only report what would be created)."""
    if not dry_run:
        # one run at a time per company; the lock is released when this transaction ends
        db.execute(text("select pg_advisory_xact_lock(hashtext('replenishment'))"))
    requests, skipped = plan(db, as_of, location=location)
    result = RunResult(as_of=as_of, dry_run=dry_run, run_id=uuid.uuid4().hex[:8], requests=requests, skipped=skipped)
    if dry_run:
        return result

    for req in requests:
        req.no = numbering.next_number(db, "request", as_of, location_code=req.location_code)
        db.execute(
            text(
                "insert into request (no, ship_to_location, type, priority, need_by, channel, source_ref, justification, "
                "status, total_value) values (:no, :loc, 'goods', :prio, :need, 'planning', :src, :why, 'draft', :total)"
            ),
            {
                "no": req.no,
                "loc": req.location_code,
                "prio": req.priority,
                "need": min(line.need_by for line in req.lines),
                "src": f"replenishment:{as_of}:{result.run_id}",
                "why": f"Automatic replenishment: {len(req.lines)} item(s) at or below their reorder point",
                "total": req.total_value,
            },
        )
        for n, line in enumerate(req.lines, start=1):
            db.execute(
                text(
                    "insert into request_line (request_no, line_no, item_code, qty, uom, base_qty, est_price, need_by, "
                    "delivery_location, gl_code, category_code, reason, status) values (:no, :n, :item, :qty, :uom, "
                    ":base, :price, :need, :loc, :gl, :cat, :why, 'open')"
                ),
                {
                    "no": req.no,
                    "n": n,
                    "item": line.item_code,
                    "qty": line.qty,
                    "uom": line.uom,
                    "base": line.base_qty,
                    "price": line.est_price,
                    "need": line.need_by,
                    "loc": req.location_code,
                    "gl": line.gl_code,
                    "cat": req.category_code,
                    "why": line.reason,
                },
            )
        audit.record(
            db,
            actor,
            "request.create",
            "request",
            req.no,
            after={
                "status": "draft",
                "channel": "planning",
                "location": req.location_code,
                "category": req.category_code,
                "priority": req.priority,
                "lines": len(req.lines),
                "total_value": str(req.total_value),
                "run": result.run_id,
            },
        )
    audit.record(
        db,
        actor,
        "replenishment.run",
        "replenishment",
        result.run_id,
        after=json.loads(
            json.dumps(
                {
                    "as_of": as_of,
                    "requests": [r.no for r in requests],
                    "lines": result.line_count,
                    "total_value": result.total_value,
                    "skipped": len(skipped),
                },
                default=str,
            )
        ),
    )
    db.commit()
    return result


def summary(result: RunResult) -> dict[str, Any]:
    """The run as plain data for the screen and the command line."""
    reasons: dict[str, int] = {}
    for s in result.skipped:
        key = s.reason.split(" (")[0]
        reasons[key] = reasons.get(key, 0) + 1
    return {
        "dry_run": result.dry_run,
        "as_of": result.as_of.isoformat(),
        "requests": [
            {
                "no": r.no,
                "location": r.location_code,
                "category": r.category_code,
                "priority": r.priority,
                "lines": len(r.lines),
                "total_value": str(r.total_value),
            }
            for r in result.requests
        ],
        "line_count": result.line_count,
        "total_value": str(result.total_value),
        "skipped": reasons,
    }
