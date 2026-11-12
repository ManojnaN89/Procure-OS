"""What the books should carry on a date: goods and services received and not invoiced (and invoiced and not received),
per order line and by supplier, and invoices of micro and small suppliers that are late or nearly so. Read only.

A receipt counts what was accepted (goods: the accepted quantity at the order price, on the day it was accepted; a
service: the confirmed amount on the day it was confirmed), so rejected and returned goods never count. An invoice counts
its taxable value on the day it was received; a credit note on the line takes value off, a cancelled invoice counts
nothing. The arithmetic is formulas/reconciliation.py.
"""

from collections import defaultdict
from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from p2p.formulas import payment as pay_rules
from p2p.formulas import reconciliation as rules
from p2p.invoices import payment

D = Decimal
Key = tuple[str, int]


def _events(db: Session, sql: str, params: dict[str, Any]) -> dict[Key, list[rules.Event]]:
    out: dict[Key, list[rules.Event]] = defaultdict(list)
    for r in db.execute(text(sql), params).mappings():
        out[(r["po_no"], int(r["line_no"]))].append(rules.Event(r["day"], D(r["qty"]), D(r["value"])))
    return out


def received_vs_invoiced(db: Session, as_of: date, supplier: str | None = None) -> dict[str, Any]:
    """Every order line whose received and invoiced values differ on the date, the totals by supplier and in all."""
    p = {"d": as_of, "s": supplier}
    receipts = _events(
        db,
        "select l.po_no, l.po_line_no as line_no, g.accepted_on as day, coalesce(l.qty_accepted, 0) as qty, "
        "round(coalesce(l.qty_accepted, 0) * pl.unit_price, 2) as value from gr_line l "
        "join goods_receipt g on g.no = l.gr_no join po_line pl on pl.po_no = l.po_no and pl.line_no = l.po_line_no "
        "join po on po.no = l.po_no where g.accepted_on <= :d and (cast(:s as text) is null or po.supplier_code = :s) "
        "union all select e.po_no, e.po_line_no, e.accepted_on, e.qty_accepted, e.amount_accepted from service_entry e "
        "join po on po.no = e.po_no where e.accepted_on <= :d and (cast(:s as text) is null or po.supplier_code = :s)",
        p,
    )
    invoices = _events(
        db,
        "select l.po_no, l.po_line_no as line_no, i.received_on as day, "
        "case when i.type = 'credit_note' then -l.qty else l.qty end as qty, "
        "case when i.type = 'credit_note' then -l.taxable else l.taxable end as value "
        "from invoice_line l join invoice i on i.no = l.invoice_no where i.status <> 'cancelled' and i.received_on <= :d "
        "and (cast(:s as text) is null or i.supplier_code = :s)",
        p,
    )
    keys = sorted(set(receipts) | set(invoices))
    info = {
        (r["po_no"], int(r["line_no"])): r
        for r in db.execute(
            text(
                "select l.po_no, l.line_no, l.item_code, l.description, l.unit_price, po.supplier_code, "
                "coalesce(s.trade_name, s.legal_name) as supplier from po_line l join po on po.no = l.po_no "
                "join supplier s on s.code = po.supplier_code where l.po_no = any(:p)"
            ),
            {"p": list({k[0] for k in keys})},
        ).mappings()
    }
    lines: list[dict[str, Any]] = []
    suppliers: dict[str, dict[str, Any]] = {}
    for key in keys:
        pos = rules.position(receipts.get(key, []), invoices.get(key, []), as_of)
        if not pos.not_invoiced and not pos.not_received:
            continue
        row = info[key]
        lines.append(
            {
                "po_no": key[0], "line_no": key[1], "supplier_code": row["supplier_code"], "supplier": row["supplier"],
                "item_code": row["item_code"], "description": row["description"], "unit_price": row["unit_price"],
                "received_qty": pos.received_qty, "invoiced_qty": pos.invoiced_qty,
                "received_value": pos.received_value, "invoiced_value": pos.invoiced_value,
                "not_invoiced": pos.not_invoiced, "not_received": pos.not_received,
                "since": pos.since, "age_days": pos.age_days,
            }
        )  # fmt: skip
        s = suppliers.setdefault(
            row["supplier_code"],
            {"supplier_code": row["supplier_code"], "supplier": row["supplier"], "lines": 0,
             "not_invoiced": D(0), "not_received": D(0)},
        )  # fmt: skip
        s["lines"] += 1
        s["not_invoiced"] += pos.not_invoiced
        s["not_received"] += pos.not_received
    return {
        "as_of": as_of,
        "lines": lines,
        "suppliers": sorted(suppliers.values(), key=lambda s: s["supplier_code"]),
        "not_invoiced": sum((x["not_invoiced"] for x in lines), D(0)),
        "not_received": sum((x["not_received"] for x in lines), D(0)),
    }


def msme_due(db: Session, as_of: date, within_days: int) -> list[dict[str, Any]]:
    """Unpaid invoices of micro and small suppliers whose MSMED Act deadline has passed or comes within the days given:
    the days agreed in writing (at most the maximum) or the no-agreement days, from acceptance. Latest first."""
    limits = payment.msme_limits(db)
    rows = db.execute(
        text(
            "select i.no, i.supplier_invoice_no, i.invoice_date, i.received_on, i.total, i.status, i.due_date, "
            "s.code as supplier_code, coalesce(s.trade_name, s.legal_name) as supplier, s.msme_category, "
            "coalesce(s.msme_agreement, false) as agreement, s.agreed_payment_days from invoice i "
            "join supplier s on s.code = i.supplier_code where i.type = 'invoice' "
            "and i.status not in ('paid', 'cancelled') and s.msme_category = any(:c) order by i.no"
        ),
        {"c": list(pay_rules.MSME_CATEGORIES)},
    ).mappings()
    out: list[dict[str, Any]] = []
    for r in rows:
        accepted = payment.accepted_on(db, dict(r))
        deadline = pay_rules.msme_deadline(accepted, r["agreement"], r["agreed_payment_days"], limits)
        late = pay_rules.days_late(deadline, as_of)
        if late < -within_days:
            continue
        out.append(
            {
                "invoice": r["no"], "number": r["supplier_invoice_no"], "supplier_code": r["supplier_code"],
                "supplier": r["supplier"], "msme_category": r["msme_category"], "total": r["total"], "status": r["status"],
                "accepted_on": accepted, "agreement": r["agreement"],
                "limit_days": (deadline - accepted).days, "deadline": deadline,
                "days_late": max(late, 0), "days_left": max(-late, 0),
            }
        )  # fmt: skip
    return sorted(out, key=lambda x: (x["deadline"], x["invoice"]))
