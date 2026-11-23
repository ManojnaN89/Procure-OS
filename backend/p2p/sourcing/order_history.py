"""The purchase orders of the old system, kept as history (block 5): what was ordered of each item for each place, and
when, at what unit price. A period RFQ's suggested quantity counts the orders of the 12 months before the month it is made (spec 5.2); a
company that starts on the platform has made none of them here, so the old system's are loaded with its stock history
(`history/po.csv` and `history/po_line.csv` of a dataset). Only orders that count are kept: not draft, submitted or
cancelled, lines not cancelled. Besides the period quantities, only the negotiation brief's last price paid reads them
(sourcing/price_history.py): no budget, estimate or contract use comes from this table.
"""

import csv
from datetime import date
from decimal import Decimal
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.orm import Session

from p2p.platform import audit
from p2p.platform.sod import Actor

NOT_COUNTED = ("draft", "submitted", "cancelled")


def _rows(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def import_orders(db: Session, dataset: Path, *, actor: Actor = audit.SYSTEM) -> int:
    """Replace the loaded order history with the dataset's. Returns how many order lines were kept. Does not commit."""
    folder = dataset / "history"
    if not (folder / "po.csv").is_file() or not (folder / "po_line.csv").is_file():
        return 0
    orders = {r["no"]: r for r in _rows(folder / "po.csv") if r["status"] not in NOT_COUNTED}
    items = {str(r[0]) for r in db.execute(text("select code from item"))}
    kept = []
    for r in _rows(folder / "po_line.csv"):
        po = orders.get(r["po_no"])
        if po is None or r["status"] == "cancelled" or r["item_code"] not in items:
            continue
        kept.append(
            {
                "po": r["po_no"], "line": int(r["line_no"]), "item": r["item_code"], "loc": po["ship_to_location"],
                "on": date.fromisoformat(po["created_at"][:10]), "qty": Decimal(r["qty"]), "uom": r["uom"] or None,
                "price": Decimal(r["unit_price"]) if r.get("unit_price") else None,
            }
        )  # fmt: skip
    db.execute(text("delete from order_history"))
    if kept:
        db.execute(
            text(
                "insert into order_history (po_no, line_no, item_code, location_code, ordered_on, qty, uom, unit_price) "
                "values (:po, :line, :item, :loc, :on, :qty, :uom, :price)"
            ),
            kept,
        )
    audit.record(db, actor, "order_history.import", "order_history", str(dataset.name), after={"lines": len(kept)})
    return len(kept)
