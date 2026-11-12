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
from p2p.platform import audit, reference
from p2p.platform.sod import Actor
from p2p.receiving.stock import StockLine, stock_levels
from p2p.requests import combining, drafts, estimates
from p2p.requests.drafts import DraftLine, DraftRequest

NEEDS_ORDER = {STOCKOUT, BELOW_SAFETY, REORDER}
URGENT = {STOCKOUT, BELOW_SAFETY}

__all__ = ["DraftLine", "DraftRequest", "RunResult", "Skipped", "plan", "run", "summary"]


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
    combined: dict[str, str] = field(default_factory=dict)  # item@location -> the draft request whose line it joined

    @property
    def line_count(self) -> int:
        return sum(len(r.lines) for r in self.requests)

    @property
    def total_value(self) -> Decimal:
        return sum((r.total_value for r in self.requests), Decimal(0))


def _open_requests(db: Session) -> dict[tuple[str, str], str]:
    """Item-locations that already have something on the way for stock: an open request line, or an order not yet fully
    received.

    An order that does not bring the position above the reorder point still counts: nothing more is asked for until it
    has been received or cancelled, so repeating the run never buys the same shortfall twice. A request line counts only
    by its stock part: one that buys only for material requests (each linked with its own quantity) does not stop the
    restock, which judges the stock position without what is on order for them.
    """
    found: dict[tuple[str, str], str] = {}
    for sql in (
        "select rl.item_code, rl.delivery_location, min(rl.request_no) from request_line rl "
        "join request r on r.no = rl.request_no "
        "where rl.status in ('open', 'sourcing') and r.status in ('draft', 'submitted', 'approved', 'returned') "
        "and coalesce(rl.stock_qty, coalesce(rl.base_qty, rl.qty) - (select coalesce(sum(b.qty), 0) "
        "from material_request_buy b join material_request_line m on m.id = b.mr_line_id where m.status <> 'cancelled' "
        "and b.request_no = rl.request_no and b.request_line_no = rl.line_no)) > 0 "
        "group by rl.item_code, rl.delivery_location",
        "select pl.item_code, po.ship_to_location, min(po.no) from po_line pl join po on po.no = pl.po_no "
        "where pl.status in ('open', 'part_received') "
        "and po.status in ('draft', 'submitted', 'approved', 'sent', 'acknowledged', 'part_received') "
        "group by pl.item_code, po.ship_to_location",
    ):
        found.update({(r[0], r[1]): r[2] for r in db.execute(text(sql))})
    return found


def _reason(line: StockLine) -> str:
    plan = (
        f"up to the maximum {format_qty(line.max_qty)}"
        if line.policy == "minmax"
        else f"the standard lot {format_qty(line.order_qty)}"
    )
    held = f" ({format_qty(line.reserved)} of {format_qty(line.on_hand)} set aside)" if line.reserved else ""
    return (
        f"On hand {format_qty(line.free)} {line.uom}{held} is at or below the reorder point "
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
            skipped.append(Skipped(*key, f"Already requested or on order ({open_requests.get(key, 'open request')})"))
        elif line.status in NEEDS_ORDER:
            candidates.append(line)
    if not candidates:
        return [], skipped

    info = drafts.item_info(db, [c.item_code for c in candidates])
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
        purchase_uom, factor = drafts.purchase_unit(item)
        # stock set aside for material requests is not free, and what is on order for them is not on order for stock
        need = shortfall(line.policy, line.free + line.free_on_order, max_qty=line.max_qty, order_qty=line.order_qty)
        qty = purchase_quantity(need, base_per_purchase_unit=factor, moq=Decimal(item["moq"] or 0))
        if qty == 0:
            skipped.append(Skipped(*key, "Nothing to order"))
            continue
        estimate = estimates.for_item(db, line.item_code, purchase_uom, qty, as_of, item["standard_price"])
        if estimate is None:
            skipped.append(
                Skipped(*key, "No price to estimate the request with (no contract, no order, no standard price)")
            )
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
                est_price=estimate.price,
                value=round_money(qty * estimate.price),
                need_by=calendar.add_working_days(as_of, int(lead)),
                gl_code=str(item["gl"]) if item["gl"] is not None else None,
                reason=_reason(line),
                stock_status=line.status,
                stock_qty=need,
                est_source=estimate.source,
            )
        )
    department = drafts.planning_department(db)
    requests = [
        DraftRequest(loc, cat, prio, sorted(lines, key=lambda x: x.item_code), department=department)
        for (loc, cat, prio), lines in sorted(groups.items())
    ]
    for req in requests:
        drafts.route(db, req, as_of)
    return requests, skipped


def _combine(db: Session, actor: Actor, req: DraftRequest, window: int, as_of: date, combined: dict[str, str]) -> None:
    """Restock lines that an open draft planning line can take (a shortfall of the same item and place needed within the
    window) join it as its stock part; the rest stay on the request."""
    kept = []
    for line in req.lines:
        target = combining.open_line(db, line.item_code, req.location_code, line.need_by, window)
        if target is None:
            kept.append(line)
            continue
        note = f"restock {format_qty(line.stock_qty)}"
        combining.join(db, actor, target, need_by=line.need_by, note=note, stock_qty=line.stock_qty)
        combined[f"{line.item_code}@{req.location_code}"] = target["request_no"]
    if len(kept) != len(req.lines):
        req.lines = kept
        if kept:
            drafts.route(db, req, as_of)


def run(
    db: Session,
    *,
    as_of: date,
    actor: Actor = audit.SYSTEM,
    dry_run: bool = False,
    location: str | None = None,
    tenant: str | None = None,
    submit: bool = False,
) -> RunResult:
    """Create the draft requests (or, for a dry run, only report what would be created).

    With submit, each new request is then sent through approval: the rules approve small ones on the spot and hand the
    rest to the people in their chain. A request that cannot be routed stays a draft, with the reason in the result.
    """
    if submit and not tenant and not dry_run:
        raise ValueError("the company is needed to submit requests")
    if not dry_run:
        # one run at a time per company; the lock is released when this transaction ends
        db.execute(text("select pg_advisory_xact_lock(hashtext('replenishment'))"))
    requests, skipped = plan(db, as_of, location=location)
    result = RunResult(as_of=as_of, dry_run=dry_run, run_id=uuid.uuid4().hex[:8], requests=requests, skipped=skipped)
    if dry_run:
        return result

    window = combining.window_days(db)
    for req in requests:
        _combine(db, actor, req, window, as_of, result.combined)
    result.requests = requests = [r for r in requests if r.lines]
    for req in requests:
        drafts.write(
            db,
            actor,
            req,
            as_of=as_of,
            source_ref=f"replenishment:{as_of}:{result.run_id}",
            justification=f"Automatic replenishment: {len(req.lines)} item(s) at or below their reorder point",
            audit_extra={"run": result.run_id},
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
                    "combined": result.combined,
                    "total_value": result.total_value,
                    "skipped": len(skipped),
                },
                default=str,
            )
        ),
    )
    db.commit()
    if submit and tenant:
        drafts.submit(db, tenant, requests, actor, as_of)
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
                "route": r.route,
                "route_kind": r.route_kind,
                "outcome": r.outcome,
                "submit_error": r.submit_error,
            }
            for r in result.requests
        ],
        "auto_approved": sum(1 for r in result.requests if r.route_kind == "auto"),
        "needs_approval": sum(1 for r in result.requests if r.route_kind == "approval"),
        "blocked": sum(1 for r in result.requests if r.route_kind == "blocked"),
        "line_count": result.line_count,
        "combined": result.combined,
        "total_value": str(result.total_value),
        "skipped": reasons,
    }
