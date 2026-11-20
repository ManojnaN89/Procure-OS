"""The price types of an item at a location, each in its own section and never merged (D41, task 13.1).

Contract prices in force there, valid confirmed quotes, the last price paid, twelve months of prices paid, the standard
price and the market benchmarks with whether each counts. A market gap is shown per contract price, labelled as
evidence only: no estimate, route, quote ranking or order price ever reads a benchmark.
"""

from datetime import date, timedelta
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from p2p.formulas.scope import Scope
from p2p.formulas.scope import label as scope_label
from p2p.masters import regions
from p2p.orders.quotes import last_paid
from p2p.sourcing import benchmarks, price_review
from p2p.sourcing.service import contract_options


def _money(value: Any) -> str | None:
    return None if value is None else str(value)


def for_item(db: Session, item: str, location: str | None, on: date) -> dict[str, Any] | None:
    found = db.execute(
        text("select code, name, base_uom, standard_price, category_code from item where code = :c"), {"c": item}
    ).mappings().first()  # fmt: skip
    if found is None:
        return None
    words = regions.names(db)
    contracts = []
    for o in contract_options(db, item, found["base_uom"], on, location):
        row = db.execute(
            text(
                "select p.version, p.location_code, p.region_code, coalesce(s.trade_name, s.legal_name) "
                "from price_record p join supplier s on s.code = p.supplier_code where p.id = :i"
            ),
            {"i": o.record_id},
        ).one()
        a = price_review.assess(db, int(o.record_id or 0), on)
        g = a.gap
        contracts.append(
            {
                "supplier_code": o.supplier_code, "supplier": row[3], "contract": o.contract_code, "version": row[0],
                "price": _money(o.price), "min_qty": _money(o.min_qty),
                "valid_to": o.valid_to.isoformat() if o.valid_to else None,
                "where": scope_label(Scope.of([row[1]], [row[2]]), words),
                "market_gap": {
                    "label": "market gap, evidence only", "market": _money(g.market), "gap_inr": _money(g.gap_inr),
                    "gap_pct": _money(g.gap_pct), "saving": _money(g.saving), "relevant_qty": _money(a.qty),
                    "sources": g.sources, "above_thresholds": g.goes_ahead, "why": g.why, "rule": a.rule.version,
                },
            }
        )  # fmt: skip
    quotes = [
        {
            "rfq": r[0],
            "supplier_code": r[1],
            "supplier": r[2],
            "quote": r[3],
            "revision": r[4],
            "price": _money(r[5]),
            "valid_until": r[6].isoformat() if r[6] else None,
        }  # fmt: skip
        for r in db.execute(
            text(
                "select q.rfq_no, q.supplier_code, coalesce(s.trade_name, s.legal_name), q.no, q.revision, ql.unit_price, "
                "q.valid_until from quote q join quote_line ql on ql.quote_no = q.no join rfq_line l on l.rfq_no = q.rfq_no "
                "and l.line_no = ql.rfq_line_no join supplier s on s.code = q.supplier_code where l.item_code = :i "
                "and (q.valid_until is null or q.valid_until >= :d) and q.revision = (select max(x.revision) from quote x "
                "where x.rfq_no = q.rfq_no and x.supplier_code = q.supplier_code) order by q.rfq_no desc, q.supplier_code"
            ),
            {"i": item, "d": on},
        )
    ]
    paid = last_paid(db, [item]).get(item)
    last = None
    if paid:
        when = db.execute(text("select created_at from po where no = :n"), {"n": paid[1]}).scalar()
        last = {"price": _money(paid[0]), "order": paid[1], "date": when.date().isoformat() if when else None}
    history = [
        {
            "month": r[0],
            "orders": int(r[1]),
            "qty": _money(r[2]),
            "avg_price": _money(r[3]),
            "low": _money(r[4]),
            "high": _money(r[5]),
        }  # fmt: skip
        for r in db.execute(
            text(
                "select to_char(po.created_at, 'YYYY-MM'), count(distinct po.no), sum(l.qty), "
                "round(sum(l.qty * l.unit_price) / nullif(sum(l.qty), 0), 2), min(l.unit_price), max(l.unit_price) "
                "from po_line l join po on po.no = l.po_no where l.item_code = :i "
                "and po.status not in ('draft', 'cancelled') and l.status <> 'cancelled' "
                "and po.created_at::date > :since and po.created_at::date <= :d group by 1 order by 1"
            ),
            {"i": item, "since": on - timedelta(days=365), "d": on},
        )
    ]
    rule = price_review.rule_for(db, found["category_code"], on)
    rows = benchmarks.records(db, item=item)
    bench = benchmarks.described(
        db, rows, on=on, max_age_days=rule.evidence_max_age_days, scope=Scope.of([location]),
        uom=found["base_uom"],
    )  # fmt: skip
    return {
        "item": {"code": found["code"], "name": found["name"], "uom": found["base_uom"]},
        "location": location,
        "on": on.isoformat(),
        "contract_prices": contracts,
        "quotes": quotes,
        "last_paid": last,
        "price_history": history,
        "standard_price": _money(found["standard_price"]),
        "budget_price": "not kept per item",
        "benchmarks": bench,
        "rule": rule.version,
    }
