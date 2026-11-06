"""Stock: how much of every item is at every location, and the load of movement history from a dataset."""

import time
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from p2p.formulas import stock as rules
from p2p.masters.importer import ImportReport, Problem, read_table
from p2p.masters.schema import Table
from p2p.platform import audit
from p2p.platform.sod import Actor

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
           l.on_hand, l.last_movement,
           p.reorder_policy as policy, p.reorder_point, p.safety_stock, p.min_qty, p.max_qty, p.order_qty
    from stock_level l
    join item i on i.code = l.item_code
    join item_category c on c.code = i.category_code
    join location loc on loc.code = l.location_code
    left join item_location p on p.item_code = l.item_code and p.location_code = l.location_code
"""


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
    status: str


def stock_levels(db: Session, *, location: str | None = None, item: str | None = None) -> list[StockLine]:
    """Every item-location with its status. Orders are not placed yet, so nothing is on order."""
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
    for r in db.execute(text(sql + " order by l.location_code, l.item_code"), params).mappings():
        on_order = Decimal(0)
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
                status=rules.stock_status(
                    r["on_hand"],
                    on_order,
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
        audit.record(
            db,
            actor,
            "stock.import",
            "dataset",
            dataset.name,
            after={"rows_loaded": len(rows), "rejected": len(report.rejected)},
        )
        db.commit()
    report.seconds = round(time.monotonic() - started, 2)
    return report
