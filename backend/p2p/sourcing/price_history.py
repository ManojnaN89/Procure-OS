"""The last price paid for an item at a place (block 6, the negotiation brief's first reference).

From our own orders (not draft and not cancelled, lines not cancelled) and the old system's orders loaded as history
(`order_history`, with their unit price), within 365 days before the date, in the unit asked. The latest by the day the
order was made, then by order number, wins. Estimates keep reading `orders/quotes.last_paid` (OP-21).
"""

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import text
from sqlalchemy.orm import Session

WINDOW_DAYS = 365


@dataclass(frozen=True)
class LastPaid:
    price: Decimal
    po_no: str
    on: date


def last_paid_at(db: Session, item: str, location: str | None, uom: str | None, on: date) -> LastPaid | None:
    row = db.execute(
        text(
            "select price, po_no, ordered_on from ("
            "  select l.unit_price as price, p.no as po_no, (p.created_at at time zone 'Asia/Kolkata')::date as ordered_on "
            "  from po_line l join po p on p.no = l.po_no where l.item_code = :i and p.ship_to_location = :loc "
            "  and p.status not in ('draft', 'cancelled') and l.status <> 'cancelled' "
            "  and (cast(:u as text) is null or l.uom is null or l.uom = :u) "
            "  union all "
            "  select h.unit_price, h.po_no, h.ordered_on from order_history h where h.item_code = :i "
            "  and h.location_code = :loc and h.unit_price is not null "
            "  and (cast(:u as text) is null or h.uom is null or h.uom = :u)"
            ") x where ordered_on > :since and ordered_on <= :d order by ordered_on desc, po_no desc limit 1"
        ),
        {"i": item, "loc": location, "u": uom, "since": on - timedelta(days=WINDOW_DAYS), "d": on},
    ).first()
    return LastPaid(Decimal(row[0]), str(row[1]), row[2]) if row else None
