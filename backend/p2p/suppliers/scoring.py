"""Supplier scores from our own orders, receipts and prices, and the ranking used to pick the best vendor.

Delivery: each goods receipt line is judged against the date the supplier promised, and each sent order against how fast
the supplier acknowledged it. Quality: the share of each delivery that was accepted. Price: how close the supplier's
price is to the lowest on offer for the same item. The weights are company settings; nothing here is a model's opinion.
"""

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from p2p.formulas import scoring as rules
from p2p.formulas.calendar import IST
from p2p.formulas.money import format_qty
from p2p.platform import reference

D = Decimal
TZ_NAME = "Asia/Kolkata"


def _setting(db: Session, key: str, default: str) -> str:
    for table in ("app_setting", "org_setting"):
        value = db.execute(text(f"select value from {table} where key = :k"), {"k": key}).scalar()  # noqa: S608
        if value not in (None, ""):
            return str(value)
    return default


def weights(db: Session) -> rules.Weights:
    return rules.Weights(
        D(_setting(db, "score_weight_price", "40")),
        D(_setting(db, "score_weight_delivery", "40")),
        D(_setting(db, "score_weight_quality", "20")),
    )


@dataclass
class History:
    """What the orders and receipts say about one supplier."""

    receipts: int = 0  # receipt lines that had a promised date to be judged against
    on_time: int = 0
    late_days_total: int = 0
    acks: int = 0  # sent orders that were acknowledged
    quick_acks: int = 0
    receipt_lines: int = 0  # receipt lines with a recorded accepted quantity
    accepted_shares: Decimal = D(0)
    received_qty: Decimal = D(0)
    rejected_qty: Decimal = D(0)
    orders: int = 0


@dataclass
class SupplierScore:
    supplier_code: str
    total: Decimal
    price: Decimal
    delivery: Decimal
    quality: Decimal
    history: History
    price_items: int = 0  # how many items had a competing price to be compared with
    price_gap_pct: Decimal = D(0)  # how far above the lowest price the supplier is, on average
    weights: rules.Weights = field(default_factory=rules.Weights)

    def numbers(self) -> dict[str, Any]:
        h = self.history
        return {
            "score": str(self.total),
            "price": str(self.price.quantize(D("0.001"))),
            "delivery": str(self.delivery.quantize(D("0.001"))),
            "quality": str(self.quality.quantize(D("0.001"))),
            "weights": {
                "price": str(self.weights.price),
                "delivery": str(self.weights.delivery),
                "quality": str(self.weights.quality),
            },
            "orders": h.orders,
            "receipts_judged": h.receipts,
            "on_time": h.on_time,
            "on_time_pct": str((D(100) * h.on_time / h.receipts).quantize(D("0.1"))) if h.receipts else None,
            "late_days_average": str((D(h.late_days_total) / (h.receipts - h.on_time)).quantize(D("0.1")))
            if h.receipts > h.on_time
            else "0",
            "acknowledged": h.acks,
            "acknowledged_quickly": h.quick_acks,
            "receipt_lines": h.receipt_lines,
            "received_qty": format_qty(h.received_qty),
            "rejected_qty": format_qty(h.rejected_qty),
            "accepted_pct": str((D(100) * h.accepted_shares / h.receipt_lines).quantize(D("0.1")))
            if h.receipt_lines
            else None,
            "price_items_compared": self.price_items,
            "price_gap_pct": str(self.price_gap_pct.quantize(D("0.1"))),
        }


def histories(db: Session, as_of: date | None = None) -> dict[str, History]:
    """One pass over the receipts and orders: every supplier's delivery and quality record up to a date."""
    out: dict[str, History] = {}
    cutoff = as_of or date(9999, 12, 31)
    for supplier, on_time, late in db.execute(
        text(
            "select po.supplier_code, (g.gate_entry_at at time zone :tz)::date <= l.promised_date, "
            "greatest((g.gate_entry_at at time zone :tz)::date - l.promised_date, 0) "
            "from gr_line gl join goods_receipt g on g.no = gl.gr_no join po on po.no = gl.po_no "
            "join po_line l on l.po_no = gl.po_no and l.line_no = gl.po_line_no "
            "where l.promised_date is not null and (g.gate_entry_at at time zone :tz)::date <= :c"
        ),
        {"tz": TZ_NAME, "c": cutoff},
    ):
        h = out.setdefault(supplier, History())
        h.receipts += 1
        h.on_time += bool(on_time)
        h.late_days_total += int(late or 0)
    for supplier, received, accepted, rejected in db.execute(
        text(
            "select po.supplier_code, gl.qty_received, gl.qty_accepted, gl.qty_rejected from gr_line gl "
            "join goods_receipt g on g.no = gl.gr_no join po on po.no = gl.po_no "
            "where gl.qty_accepted is not null and (g.gate_entry_at at time zone :tz)::date <= :c"
        ),
        {"tz": TZ_NAME, "c": cutoff},
    ):
        h = out.setdefault(supplier, History())
        h.receipt_lines += 1
        h.received_qty += D(received)
        h.rejected_qty += D(rejected)
        h.accepted_shares += D(accepted) / D(received) if D(received) > 0 else D(1)
    calendar = reference.calendar_for(db)
    ack_days = int(_setting(db, "supplier_ack_due_days", "2"))
    for supplier, sent, acked in db.execute(
        text(
            "select supplier_code, sent_at, acknowledged_at from po where sent_at is not null "
            "and (sent_at at time zone :tz)::date <= :c"
        ),
        {"tz": TZ_NAME, "c": cutoff},
    ):
        h = out.setdefault(supplier, History())
        h.orders += 1
        if acked is not None:
            h.acks += 1
            sent_on = sent.astimezone(IST).date()
            h.quick_acks += acked.astimezone(IST).date() <= calendar.add_working_days(sent_on, ack_days)
    return out


def _price_table(db: Session, as_of: date) -> dict[tuple[str, str | None], dict[str, Decimal]]:
    """For every item and unit, each supplier's base price (the record with the smallest quantity break) in force."""
    rows = db.execute(
        text(
            "select distinct on (p.item_code, p.uom, p.supplier_code) p.item_code, p.uom, p.supplier_code, p.price "
            "from price_record p join supplier s on s.code = p.supplier_code "
            "left join contract c on c.code = p.contract_code "
            "where s.status = 'active' and p.valid_from <= :d and (p.valid_to is null or p.valid_to >= :d) "
            "and (p.contract_code is null or (c.status = 'active' and c.valid_from <= :d and (c.valid_to is null or c.valid_to >= :d))) "
            "order by p.item_code, p.uom, p.supplier_code, coalesce(p.min_qty, 0), p.price"
        ),
        {"d": as_of},
    ).all()
    table: dict[tuple[str, str | None], dict[str, Decimal]] = {}
    for item, uom, supplier, price in rows:
        table.setdefault((item, uom), {})[supplier] = D(price)
    return table


def _price_scores(db: Session, as_of: date) -> dict[str, tuple[Decimal, int, Decimal]]:
    """Per supplier: the average price score over the items it competes on, how many, and the average gap above the best."""
    shares: dict[str, list[Decimal]] = {}
    for prices in _price_table(db, as_of).values():
        if len(prices) < 2:
            continue
        best = min(prices.values())
        for supplier, price in prices.items():
            shares.setdefault(supplier, []).append(rules.price_score(best, price))
    return {s: (sum(v, D(0)) / len(v), len(v), (D(1) - sum(v, D(0)) / len(v)) * 100) for s, v in shares.items()}


def score_all(db: Session, as_of: date) -> dict[str, SupplierScore]:
    """The score of every active supplier."""
    w = weights(db)
    hist = histories(db, as_of)
    prices = _price_scores(db, as_of)
    out: dict[str, SupplierScore] = {}
    for (code,) in db.execute(text("select code from supplier where status = 'active' order by code")):
        h = hist.get(code, History())
        price, items, gap = prices.get(code, (rules.NEUTRAL_PRICE, 0, D(0)))
        delivery = rules.delivery_score(h.on_time, h.receipts, h.quick_acks, h.acks)
        quality = rules.quality_score(h.accepted_shares, h.receipt_lines)
        out[code] = SupplierScore(
            code, rules.combine(w, price, delivery, quality), price, delivery, quality, h, items, gap, w
        )
    return out


def score_supplier(db: Session, code: str, as_of: date) -> SupplierScore:
    """One supplier's score, even one that is not active any more (its history still counts)."""
    all_scores = score_all(db, as_of)
    if code in all_scores:
        return all_scores[code]
    w = weights(db)
    h = histories(db, as_of).get(code, History())
    delivery = rules.delivery_score(h.on_time, h.receipts, h.quick_acks, h.acks)
    quality = rules.quality_score(h.accepted_shares, h.receipt_lines)
    return SupplierScore(
        code,
        rules.combine(w, rules.NEUTRAL_PRICE, delivery, quality),
        rules.NEUTRAL_PRICE,
        delivery,
        quality,
        h,
        0,
        D(0),
        w,
    )


@dataclass
class Ranked:
    supplier_code: str
    price: Decimal
    total: Decimal
    price_score: Decimal
    delivery: Decimal
    quality: Decimal


def rank(db: Session, candidates: list[tuple[str, Decimal]], as_of: date) -> list[Ranked]:
    """Order suppliers that can all supply one item, best first. The price is judged against the others on offer for this
    item (the cheapest scores 1), delivery and quality from each supplier's record. A tie goes to the lower price, then the code."""
    if not candidates:
        return []
    w = weights(db)
    hist = histories(db, as_of)
    best = min(price for _, price in candidates)
    ranked = []
    for code, price in candidates:
        h = hist.get(code, History())
        ps = rules.price_score(best, price)
        delivery = rules.delivery_score(h.on_time, h.receipts, h.quick_acks, h.acks)
        quality = rules.quality_score(h.accepted_shares, h.receipt_lines)
        ranked.append(Ranked(code, price, rules.combine(w, ps, delivery, quality), ps, delivery, quality))
    ranked.sort(key=lambda r: (-r.total, r.price, r.supplier_code))
    return ranked
