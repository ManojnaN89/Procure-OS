"""Stock: how much of every item is at every location, and the load of movement history from a dataset."""

import time
import uuid
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from p2p.formulas import stock as rules
from p2p.formulas.calendar import business_today
from p2p.formulas.material import earmarked, free_stock
from p2p.masters.importer import ImportReport, Problem, read_table
from p2p.masters.schema import Table
from p2p.platform import audit
from p2p.platform.sod import Actor, user

MOVEMENT = Table(
    "stock_movement",
    "stock_movement",
    (
        ("id", "text"),
        ("item_code", "text"),
        ("location_code", "text"),
        ("date", "date"),
        ("type", "text"),
        ("qty", "numeric(18,4)"),
        ("unit_cost", "numeric(18,4)"),
        ("cost_centre_code", "text"),
        ("ref_doc", "text"),
    ),
    ("id",),
    (("item_code", "item", "code"), ("location_code", "location", "code")),
)
TYPES = {"receipt", "issue", "return", "adjust", "transfer_in", "transfer_out"}

_LEVELS = """
    select l.item_code, i.name as item_name, i.base_uom as uom, c.name as category,
           l.location_code, loc.name as location_name,
           l.on_hand, coalesce(o.qty, 0) as on_order, l.last_movement,
           p.reorder_policy as policy, p.reorder_point, p.safety_stock, p.min_qty, p.max_qty, p.order_qty
    from stock_level l
    join item i on i.code = l.item_code
    join item_category c on c.code = i.category_code
    join location loc on loc.code = l.location_code
    left join item_location p on p.item_code = l.item_code and p.location_code = l.location_code
    left join (
        select item_code, loc, sum(qty) as qty from (
            select rl.item_code, rl.delivery_location as loc, rl.base_qty as qty
            from request_line rl join request r on r.no = rl.request_no
            where rl.status in ('open', 'sourcing') and r.status in ('draft', 'submitted', 'approved', 'returned')
            union all
            select pl.item_code, po.ship_to_location, pl.base_qty * greatest(pl.qty - pl.received_qty, 0) / pl.qty
            from po_line pl join po on po.no = pl.po_no
            where pl.status in ('open', 'part_received')
              and po.status in ('draft', 'submitted', 'approved', 'sent', 'acknowledged', 'part_received')
        ) u group by item_code, loc
    ) o on o.item_code = l.item_code and o.loc = l.location_code
"""

# stock set aside for open material requests, per item-location
_RESERVED = (
    "select coalesce(sum(reserved_qty), 0) from material_request_line "
    "where status = 'open' and item_code = :i and location_code = :l"
)


@dataclass
class StockLine:
    item_code: str
    item_name: str
    uom: str | None
    category: str
    location_code: str
    location_name: str
    on_hand: Decimal
    on_order: Decimal
    policy: str | None
    reorder_point: Decimal | None
    safety_stock: Decimal | None
    min_qty: Decimal | None
    max_qty: Decimal | None
    order_qty: Decimal | None
    last_movement: str | None
    status: str  # judged on the free stock: what is set aside for material requests is not there for anything else
    reserved: Decimal = Decimal(0)  # set aside for open material requests
    for_requests: Decimal = Decimal(0)  # of on_order, what is bought for material requests
    quarantine: Decimal = Decimal(0)  # held, waiting for a quality decision (not in on_hand)
    blocked: Decimal = Decimal(0)  # held, judged bad (not in on_hand)
    holds: int = 0  # open holds

    @property
    def free(self) -> Decimal:
        return self.on_hand - self.reserved

    @property
    def available(self) -> Decimal:
        """What may be issued or set aside: usable less set aside less the safety stock (negative when below it)."""
        return free_stock(self.on_hand, self.reserved, self.safety_stock)

    @property
    def free_on_order(self) -> Decimal:
        return self.on_order - self.for_requests


def _material_held(db: Session) -> dict[tuple[str, str], tuple[Decimal, Decimal]]:
    """Per item-location: stock set aside for open material requests, and what is still on order for them."""
    held: dict[tuple[str, str], tuple[Decimal, Decimal]] = {}
    for r in db.execute(
        text(
            "select item_code, location_code, need_qty, issued_qty, reserved_qty, requested_qty "
            "from material_request_cover where status = 'open'"
        )
    ).mappings():
        key = (r["item_code"], r["location_code"])
        res, ear = held.get(key, (Decimal(0), Decimal(0)))
        coming = earmarked(r["need_qty"], r["issued_qty"], r["reserved_qty"], r["requested_qty"])
        held[key] = (res + r["reserved_qty"], ear + coming)
    return held


def _held_quality(db: Session) -> dict[tuple[str, str, str], Decimal]:
    """Per item-location: quarantine and blocked stock, and the number of open holds."""
    out: dict[tuple[str, str, str], Decimal] = {}
    for r in db.execute(
        text("select item_code, location_code, quality, qty from stock_quality_level where quality <> 'usable'")
    ):
        out[(r[0], r[1], r[2])] = Decimal(r[3])
    for r in db.execute(
        text("select item_code, location_code, count(*) from stock_hold where closed_at is null group by 1, 2")
    ):
        out[(r[0], r[1], "holds")] = Decimal(r[2])
    return out


def stock_levels(db: Session, *, location: str | None = None, item: str | None = None) -> list[StockLine]:
    """Every item-location with its status. On order is what open requests still ask for and orders still bring. Stock
    set aside for material requests, and what is on order for them, are shown apart and do not count as free."""
    sql = _LEVELS
    params: dict[str, Any] = {}
    clauses = []
    if location:
        clauses.append("l.location_code = :loc")
        params["loc"] = location
    if item:
        clauses.append("l.item_code = :item")
        params["item"] = item
    if clauses:
        sql += " where " + " and ".join(clauses)
    out = []
    held = _material_held(db)
    quality = _held_quality(db)
    for r in db.execute(text(sql + " order by l.location_code, l.item_code"), params).mappings():
        on_order = r["on_order"]
        reserved, coming = held.get((r["item_code"], r["location_code"]), (Decimal(0), Decimal(0)))
        for_requests = min(coming, on_order)  # what was requested for them and has not arrived is still on order
        out.append(
            StockLine(
                item_code=r["item_code"],
                item_name=r["item_name"],
                uom=r["uom"],
                category=r["category"],
                location_code=r["location_code"],
                location_name=r["location_name"],
                on_hand=r["on_hand"],
                on_order=on_order,
                policy=r["policy"],
                reorder_point=r["reorder_point"],
                safety_stock=r["safety_stock"],
                min_qty=r["min_qty"],
                max_qty=r["max_qty"],
                order_qty=r["order_qty"],
                last_movement=r["last_movement"].isoformat() if r["last_movement"] else None,
                reserved=reserved,
                for_requests=for_requests,
                quarantine=quality.get((r["item_code"], r["location_code"], "quarantine"), Decimal(0)),
                blocked=quality.get((r["item_code"], r["location_code"], "blocked"), Decimal(0)),
                holds=int(quality.get((r["item_code"], r["location_code"], "holds"), 0)),
                status=rules.stock_status(
                    r["on_hand"] - reserved,
                    on_order - for_requests,
                    policy=r["policy"],
                    reorder_point=r["reorder_point"],
                    safety_stock=r["safety_stock"],
                    max_qty=r["max_qty"],
                ),
            )
        )
    return out


def import_movements(db: Session, dataset: Path, *, actor: Actor = audit.SYSTEM, dry_run: bool = False) -> ImportReport:
    """Replace the loaded stock history with history/stock_movement.csv. Movements the platform recorded stay."""
    started = time.monotonic()
    report = ImportReport(dry_run=dry_run)
    items = {r[0] for r in db.execute(text("select code from item"))}
    locations = {r[0] for r in db.execute(text("select code from location"))}
    seen: dict[str, int] = {}
    rows: list[dict[str, Any]] = []
    for line, row in read_table(dataset / "history" / "stock_movement.csv", MOVEMENT, report):
        reason = None
        if row["id"] is None:
            reason = "id must not be empty"
        elif row["id"] in seen:
            reason = f"duplicate of line {seen[row['id']]} (same id)"
        elif row["type"] not in TYPES:
            reason = f"type '{row['type']}' is not one of {', '.join(sorted(TYPES))}"
        elif row["date"] is None or row["qty"] is None:
            reason = "date and qty are required"
        elif row["qty"] < 0 and row["type"] != "adjust":
            reason = f"qty {row['qty']} must not be negative for a {row['type']}"
        elif row["item_code"] not in items:
            reason = f"item_code '{row['item_code']}' not found in item"
        elif row["location_code"] not in locations:
            reason = f"location_code '{row['location_code']}' not found in location"
        if reason:
            report.rejected.append(Problem("stock_movement", line, reason))
            continue
        seen[row["id"]] = line
        rows.append(row)
    report.loaded["stock_movement"] = len(rows)

    if not dry_run:
        db.execute(text("delete from stock_movement where source = 'import'"))
        if rows:
            cols = [c for c, _ in MOVEMENT.columns]
            db.execute(
                text(f"insert into stock_movement ({', '.join(cols)}) values ({', '.join(':' + c for c in cols)})"),  # noqa: S608
                rows,
            )
        held = _import_held(db, dataset, items, locations, report)
        audit.record(
            db,
            actor,
            "stock.import",
            "dataset",
            dataset.name,
            after={"rows_loaded": len(rows), "held_lots": held, "rejected": len(report.rejected)},
        )
        db.commit()
    report.seconds = round(time.monotonic() - started, 2)
    return report


HELD = Table(
    "stock_held",
    "stock_held",
    (
        ("id", "text"),
        ("item_code", "text"),
        ("location_code", "text"),
        ("date", "date"),
        ("quality", "text"),
        ("qty", "numeric(18,4)"),
        ("reason", "text"),
        ("reference", "text"),
    ),
    ("id",),
    (("item_code", "item", "code"), ("location_code", "location", "code")),
)


def _import_held(db: Session, dataset: Path, items: set[str], locations: set[str], report: ImportReport) -> int:
    """history/stock_held.csv: lots in the store held in quarantine or blocked at the cutoff. Each is a receipt of that
    quality and a hold. A hold already decided is kept as it is (only its receipt is loaded again)."""
    path = dataset / "history" / "stock_held.csv"
    if not path.exists():
        return 0
    db.execute(
        text(
            "delete from stock_hold h where h.source = 'import' and h.closed_at is null and h.left_qty = h.qty "
            "and not exists (select 1 from stock_hold c where c.parent_id = h.id)"
        )
    )
    kept = {r[0] for r in db.execute(text("select import_id from stock_hold where source = 'import'"))}
    loaded = 0
    for line, row in read_table(path, HELD, report):
        reason = None
        if not row["id"] or row["quality"] not in ("quarantine", "blocked"):
            reason = "id is needed and quality must be quarantine or blocked"
        elif row["qty"] is None or row["qty"] <= 0 or not row["reason"]:
            reason = "qty must be more than zero and a reason is needed"
        elif row["item_code"] not in items or row["location_code"] not in locations:
            reason = f"{row['item_code']} at {row['location_code']} is not a known item and place"
        if reason:
            report.rejected.append(Problem("stock_held", line, reason))
            continue
        db.execute(
            text(
                "insert into stock_movement (id, item_code, location_code, date, type, qty, ref_doc, source, quality) "
                "values (:id, :i, :l, :d, 'receipt', :q, :r, 'import', :k)"
            ),
            {"id": row["id"], "i": row["item_code"], "l": row["location_code"], "d": row["date"], "q": row["qty"],
             "r": row["reference"], "k": row["quality"]},
        )  # fmt: skip
        if row["id"] not in kept:
            db.execute(
                text(
                    "insert into stock_hold (item_code, location_code, quality, qty, left_qty, reason, reference, "
                    "source, import_id, held_by, held_at) values (:i, :l, :k, :q, :q, :why, :r, 'import', :id, "
                    "'import', cast(:d as date))"
                ),
                {"i": row["item_code"], "l": row["location_code"], "k": row["quality"], "q": row["qty"],
                 "why": row["reason"], "r": row["reference"], "id": row["id"], "d": row["date"]},
            )  # fmt: skip
        loaded += 1
    report.loaded["stock_held"] = loaded
    return loaded


# ---- posting from the store: issues and counts ---------------------------------------------------


class StockError(Exception):
    """The posting makes no sense (unknown item, more than is on the shelf, nothing to change)."""


def _on_hand(db: Session, item: str, location: str) -> Decimal:
    value = db.execute(
        text("select on_hand from stock_level where item_code = :i and location_code = :l"), {"i": item, "l": location}
    ).scalar()
    return Decimal(value) if value is not None else Decimal(0)


def _last_cost(db: Session, item: str, location: str) -> Decimal | None:
    value = db.execute(
        text(
            "select unit_cost from stock_movement where item_code = :i and location_code = :l and unit_cost is not null "
            "order by date desc, id desc limit 1"
        ),
        {"i": item, "l": location},
    ).scalar()
    return Decimal(value) if value is not None else None


def _check_place(db: Session, item: str, location: str) -> None:
    if db.execute(text("select 1 from item where code = :c"), {"c": item}).first() is None:
        raise StockError(f"there is no item {item}")
    if (
        db.execute(text("select 1 from location where code = :c and is_stock_location"), {"c": location}).first()
        is None
    ):
        raise StockError(f"{location} is not a stock location")
    db.execute(text("select pg_advisory_xact_lock(hashtext(:k))"), {"k": f"stock:{item}:{location}"})


def _post(db: Session, kind: str, item: str, location: str, qty: Decimal, on: date, ref: str | None) -> str:
    movement = f"SM-{kind[:3].upper()}-{on:%Y%m%d}-{uuid.uuid4().hex[:6]}"
    db.execute(
        text(
            "insert into stock_movement (id, item_code, location_code, date, type, qty, unit_cost, ref_doc, source) "
            "values (:id, :i, :l, :d, :t, :q, :c, :r, 'platform')"
        ),
        {
            "id": movement,
            "i": item,
            "l": location,
            "d": on,
            "t": kind,
            "q": qty,
            "c": _last_cost(db, item, location),
            "r": ref,
        },
    )
    return movement


def reserved(db: Session, item: str, location: str) -> Decimal:
    """Stock of an item-location set aside for open material requests."""
    return Decimal(db.execute(text(_RESERVED), {"i": item, "l": location}).scalar_one())


def safety(db: Session, item: str, location: str) -> Decimal:
    """The safety stock of an item-location (none recorded is none)."""
    value = db.execute(
        text("select safety_stock from item_location where item_code = :i and location_code = :l"),
        {"i": item, "l": location},
    ).scalar()
    return Decimal(value) if value is not None else Decimal(0)


class SafetyStockError(StockError):
    """The issue would reach into the safety stock: only with the manager's override (C4)."""

    def __init__(self, message: str, available: Decimal, safety_stock: Decimal) -> None:
        super().__init__(message)
        self.available, self.safety_stock = available, safety_stock


def issue_movement(
    db: Session,
    actor: Actor,
    item: str,
    location: str,
    qty: Decimal,
    *,
    reference: str | None,
    today: date,
    keep_safety: bool = True,
    override_proposal: int | None = None,
) -> str:
    """Write an issue and its audit row in the caller's transaction (the caller commits or rolls back).

    It may not take more than is free: usable on hand less what is set aside for material requests, and (for an issue by
    hand, `keep_safety`) less the safety stock unless a manager approved using it (`override_proposal`). An issue for a
    material request releases its own reservation first, so its stock counts as free here; safety stock is not in its
    way. Held stock (quarantine, blocked) is never on hand, so never issued.
    """
    qty = Decimal(qty).quantize(rules.QTY)
    if qty <= 0:
        raise StockError("the quantity must be more than zero")
    _check_place(db, item, location)
    have = _on_hand(db, item, location)
    held = reserved(db, item, location)
    if qty > have - held:
        if held:
            raise StockError(
                f"only {max(have - held, Decimal(0)).normalize():f} is free ({held.normalize():f} of "
                f"{have.normalize():f} is set aside for material requests), so {qty.normalize():f} cannot be issued"
            )
        raise StockError(f"only {have.normalize():f} is on the shelf, so {qty.normalize():f} cannot be issued")
    keep = safety(db, item, location) if keep_safety and override_proposal is None else Decimal(0)
    if keep and qty > have - held - keep:
        free = max(have - held - keep, Decimal(0))
        raise SafetyStockError(
            f"only {free.normalize():f} is free; {keep.normalize():f} is safety stock, which needs the manager's "
            f"override, so {qty.normalize():f} cannot be issued",
            free,
            keep,
        )
    ref = (reference or "").strip() or None
    movement = _post(db, "issue", item, location, qty, today, ref)
    from_safety = max(qty - max(have - held - safety(db, item, location), Decimal(0)), Decimal(0))
    audit.record(
        db, actor, "stock.issue", "stock", f"{item}@{location}",
        after={
            "movement": movement, "qty": str(qty), "on_hand": str(have - qty), "reference": ref,
            **({"from_safety": str(from_safety), "proposal": override_proposal} if override_proposal else {}),
        },
    )  # fmt: skip
    return movement


def post_issue(
    db: Session,
    user_id: int,
    item: str,
    location: str,
    qty: Decimal,
    *,
    reference: str | None = None,
    today: date | None = None,
) -> str:
    """Take stock out for use (to a department, a machine, a job). It may not take more than is on the shelf and not set
    aside for a material request."""
    try:
        movement = issue_movement(
            db, user(user_id), item, location, qty, reference=reference, today=today or business_today()
        )
    except StockError:
        db.rollback()
        raise
    db.commit()
    return movement


def post_count(
    db: Session, user_id: int, item: str, location: str, counted: Decimal, reason: str, *, today: date | None = None
) -> str | None:
    """After a stock count: make the books say what is on the shelf. The difference is worked out here, never typed.
    Returns the movement, or None when the books already agree."""
    today = today or business_today()
    counted = Decimal(counted).quantize(rules.QTY)
    if counted < 0:
        raise StockError("a count cannot be below zero")
    if not reason or not reason.strip():
        raise StockError("say why the books are being corrected")
    _check_place(db, item, location)
    have = _on_hand(db, item, location)
    delta = rules.count_difference(counted, have)
    if delta == 0:
        db.rollback()
        return None
    movement = _post(db, "adjust", item, location, delta, today, "stock count")
    audit.record(
        db, user(user_id), "stock.adjust", "stock", f"{item}@{location}",
        before={"on_hand": str(have)}, after={"movement": movement, "counted": str(counted), "difference": str(delta)},
        reason=reason.strip(),
    )  # fmt: skip
    db.commit()
    return movement
