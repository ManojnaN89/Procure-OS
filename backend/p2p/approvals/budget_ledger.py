"""What is actually spent against a budget (`budget_actual`), the one place it is written (task 3.7, D-10).

A stocked item is spent when its goods are accepted at receipt (at once, or when inspection accepts them), at the order
price; a rejected quantity is never spent. Everything else (services, goods not kept in stock, free-text lines) is spent
when its invoice is paid, at the invoice's taxable value, in the month the invoice was received; freight and packing on
an invoice are spent then too, less a discount taken. A price difference on a stocked line's invoice is not posted here
(that is purchase price variance, task 13.2).

Each row is booked in the month the event happened unless that month is locked; then in the first open month after it,
keeping the event's month (`event_period`), with an audit row `budget.actual_moved`. A receipt reversal (task 3.6) will
post a negative amount through `post_receipt`.
"""

from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from p2p.formulas.budget import post_period, receipt_actual
from p2p.platform import audit
from p2p.platform.sod import Actor

D = Decimal


def locked_periods(db: Session) -> set[str]:
    return {str(r[0]) for r in db.execute(text("select period from budget_period_lock"))}


def _month(d: date) -> str:
    return f"{d.year}-{d.month:02d}"


def _insert(db: Session, actor: Actor, row: dict[str, Any], current: str, locked: set[str]) -> dict[str, Any]:
    """Write one row in the month it may be booked in; say so in the audit log when it was moved."""
    row = {**row, "period": post_period(row["event_period"], locked, current)}
    new_id = db.execute(
        text(
            "insert into budget_actual (invoice_no, source, gr_no, po_no, po_line_no, request_no, request_line_no, "
            "cost_centre_code, gl_code, location_code, period, event_period, amount) values (:invoice_no, :source, "
            ":gr_no, :po_no, :po_line_no, :request_no, :request_line_no, :cost_centre_code, :gl_code, :location_code, "
            ":period, :event_period, :amount) returning id"
        ),
        row,
    ).scalar_one()
    if row["period"] != row["event_period"]:
        audit.record(
            db, actor, "budget.actual_moved", "budget_actual", new_id,
            before={"period": row["event_period"]}, after={"period": row["period"]},
            reason=f"{row['event_period']} is locked",
        )  # fmt: skip
    return {**row, "id": new_id, "amount": str(row["amount"])}


def _line(db: Session, po_no: str, line_no: int) -> dict[str, Any] | None:
    found = (
        db.execute(
            text(
                "select pl.po_no, pl.line_no, pl.unit_price, pl.item_code, pl.request_no, pl.request_line_no, "
                "coalesce(rl.gl_code, pl.gl_code) as gl_code, rl.cost_centre_code, p.ship_to_location, "
                "coalesce(i.stocked, false) as stocked from po_line pl join po p on p.no = pl.po_no "
                "left join request_line rl on rl.request_no = pl.request_no and rl.line_no = pl.request_line_no "
                "left join item i on i.code = pl.item_code where pl.po_no = :p and pl.line_no = :l"
            ),
            {"p": po_no, "l": line_no},
        )
        .mappings()
        .first()
    )
    return dict(found) if found else None


def post_receipt(
    db: Session, gr_no: str, po_no: str, line_no: int, accepted: Decimal, as_of: date, actor: Actor
) -> dict[str, Any] | None:
    """A stocked line's goods were accepted: what they cost at the order price is spent, in the month of acceptance.
    Nothing for a line that is not a stocked item. The caller commits."""
    ln = _line(db, po_no, line_no)
    if ln is None or not ln["stocked"]:
        return None
    amount = receipt_actual(accepted, D(ln["unit_price"]))
    if amount is None:
        return None
    row = _insert(
        db,
        actor,
        {
            "invoice_no": None,
            "source": "receipt",
            "gr_no": gr_no,
            "po_no": po_no,
            "po_line_no": line_no,
            "request_no": ln["request_no"],
            "request_line_no": ln["request_line_no"],
            "cost_centre_code": ln["cost_centre_code"],
            "gl_code": ln["gl_code"],
            "location_code": ln["ship_to_location"],
            "event_period": _month(as_of),
            "amount": amount,
        },  # fmt: skip
        _month(as_of),
        locked_periods(db),
    )
    audit.record(db, actor, "budget.actual", "goods_receipt", gr_no, after={"rows": [row]})
    return row


def _bill_lines(db: Session, invoice_no: str, actor: Actor, paid_on: date, received_on: date) -> list[dict[str, Any]]:
    """A bill without an order was paid: each line is spent from its own cost centre, GL and location, at its taxable
    value, in the month the bill was received (a locked month moves it to the next open one)."""
    rows: list[dict[str, Any]] = []
    locked, current, event = locked_periods(db), _month(paid_on), _month(received_on)
    for ln in db.execute(
        text(
            "select line_no, taxable, cost_centre_code, gl_code, location_code from invoice_line "
            "where invoice_no = :n and gl_code is not null order by line_no"
        ),
        {"n": invoice_no},
    ).mappings():
        rows.append(
            _insert(
                db,
                actor,
                {
                    "invoice_no": invoice_no,
                    "source": "payment",
                    "gr_no": None,
                    "po_no": None,
                    "po_line_no": ln["line_no"],
                    "request_no": None,
                    "request_line_no": None,
                    "cost_centre_code": ln["cost_centre_code"],
                    "gl_code": ln["gl_code"],
                    "location_code": ln["location_code"],
                    "event_period": event,
                    "amount": D(ln["taxable"]),
                },
                current,
                locked,
            )
        )
    if rows:
        audit.record(db, actor, "budget.actual", "invoice", invoice_no, after={"rows": rows})
    return rows


def post_payment(db: Session, invoice_no: str, actor: Actor, paid_on: date) -> list[dict[str, Any]]:
    """An invoice was paid: its lines that are not stocked items are spent at their taxable value, and its freight and
    packing less a discount taken, in the month the invoice was received. A bill without an order posts each line from
    its own cost centre, GL and location. The caller commits."""
    inv = db.execute(
        text("select received_on, type, discount_taken, kind from invoice where no = :n"), {"n": invoice_no}
    ).first()
    if inv is None or inv[1] != "invoice":
        return []
    if inv[3] == "non_po":
        return _bill_lines(db, invoice_no, actor, paid_on, inv[0])
    lines = [
        dict(r)
        for r in db.execute(
            text(
                "select il.po_no, il.po_line_no, sum(il.taxable) as amount from invoice_line il where il.invoice_no = :n "
                "group by 1, 2 order by min(il.line_no)"
            ),
            {"n": invoice_no},
        ).mappings()
    ]
    rows: list[dict[str, Any]] = []
    locked, current, event = locked_periods(db), _month(paid_on), _month(inv[0])
    first: dict[str, Any] | None = None
    for il in lines:
        ln = _line(db, il["po_no"], il["po_line_no"])
        if ln is None:
            continue
        first = first or ln
        if ln["stocked"]:
            continue  # stock was spent when it was accepted (D-10)
        rows.append(
            _insert(
                db,
                actor,
                {
                    "invoice_no": invoice_no,
                    "source": "payment",
                    "gr_no": None,
                    "po_no": ln["po_no"],
                    "po_line_no": ln["line_no"],
                    "request_no": ln["request_no"],
                    "request_line_no": ln["request_line_no"],
                    "cost_centre_code": ln["cost_centre_code"],
                    "gl_code": ln["gl_code"],
                    "location_code": ln["ship_to_location"],
                    "event_period": event,
                    "amount": D(il["amount"]),
                },
                current,
                locked,
            )  # fmt: skip
        )
    charges = D(
        db.execute(
            text("select coalesce(sum(amount), 0) from invoice_charge where invoice_no = :n"), {"n": invoice_no}
        ).scalar_one()
    ) - D(inv[2] or 0)
    if charges and first is not None:  # freight and packing are spent too; a discount is spent less
        rows.append(
            _insert(
                db,
                actor,
                {
                    "invoice_no": invoice_no,
                    "source": "payment",
                    "gr_no": None,
                    "po_no": first["po_no"],
                    "po_line_no": None,
                    "request_no": first["request_no"],
                    "request_line_no": first["request_line_no"],
                    "cost_centre_code": first["cost_centre_code"],
                    "gl_code": first["gl_code"],
                    "location_code": first["ship_to_location"],
                    "event_period": event,
                    "amount": charges,
                },
                current,
                locked,
            )  # fmt: skip
        )
    if rows:
        audit.record(db, actor, "budget.actual", "invoice", invoice_no, after={"rows": rows})
    return rows
