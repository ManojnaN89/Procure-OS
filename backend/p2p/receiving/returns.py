"""Returning goods to a supplier: the return challan (RTN), the replacement or no replacement, and the recovery claim.

A return line comes from a blocked hold (an inspection rejection or a manual hold), an excess hold (goods above the
order the buyer sends back), a gate rejection (made with the receipt, already dispatched) or a receipt without an order.
Dispatch takes the stock out of its quality, reduces the hold, frees the serials, counts a manual hold's goods back off
the order line and its budget, and mails the supplier one plain message. The buyer then decides per line whether the
quantity will be replaced or not. A counted return with no replacement raises a recovery claim when the line was
invoiced beyond what remains received; the claim is a record, the debit note and its settlement are block 12's.

Every write is audited; the numbers are RTN-<loc>-<fy>-<seq> and RCL-<loc>-<fy>-<seq>.
"""

import json
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from p2p.approvals.workflow import org_code_of
from p2p.formulas import returns as rules
from p2p.formulas.gst import Registration
from p2p.formulas.money import format_qty
from p2p.formulas.orders import line_amounts
from p2p.orders import service as ordering
from p2p.platform import audit, notifications, numbering, outbox, reference, tasks
from p2p.platform.sod import user
from p2p.receiving import goods

SLA_HOURS = Decimal(18)


class ReturnError(Exception):
    """The return or the decision makes no sense: `status` is the HTTP answer (404, 409, 422)."""

    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status


@dataclass
class ReturnLineIn:
    hold_id: int
    qty: Decimal
    serials: list[str] | None = None
    gr_no: str | None = None
    po_line_no: int | None = None
    supplier_code: str | None = None


@dataclass
class Created:
    no: str


@dataclass
class Dispatched:
    no: str
    claims: list[str] = field(default_factory=list)


def _me(db: Session, user_id: int) -> str:
    return org_code_of(db, user_id) or f"user:{user_id}"


def _hold(db: Session, hold_id: int) -> dict[str, Any]:
    row = db.execute(text("select * from stock_hold where id = :i for update"), {"i": hold_id}).mappings().first()
    if row is None:
        raise ReturnError(404, f"there is no hold {hold_id}")
    return dict(row)


def _receipt(db: Session, gr_no: str) -> dict[str, Any]:
    row = db.execute(text("select * from goods_receipt where no = :n"), {"n": gr_no}).mappings().first()
    if row is None:
        raise ReturnError(404, f"there is no receipt {gr_no}")
    return dict(row)


def _item_name(db: Session, item_code: str | None) -> tuple[str | None, str | None]:
    if not item_code:
        return None, None
    row = db.execute(
        text("select name, coalesce(purchase_uom, base_uom) from item where code = :c"), {"c": item_code}
    ).first()
    return (str(row[0]), row[1]) if row else (None, None)


def _returned_already(db: Session, gr_no: str, po_no: str, line_no: int) -> Decimal:
    """What was already returned as counted from one receipt line."""
    return Decimal(
        db.execute(
            text(
                "select coalesce(sum(l.qty), 0) from supplier_return_line l "
                "join supplier_return r on r.no = l.return_no where l.gr_no = :g and l.po_no = :p "
                "and l.po_line_no = :l and l.counted and r.status <> 'cancelled'"
            ),
            {"g": gr_no, "p": po_no, "l": line_no},
        ).scalar_one()
    )


def _accepted_on(db: Session, gr_no: str, line_no: int) -> Decimal:
    value = db.execute(
        text(
            "select coalesce(qty_accepted, 0) - coalesce(qty_excess, 0) from gr_line where gr_no = :g and po_line_no = :l"
        ),
        {"g": gr_no, "l": line_no},
    ).scalar()
    return Decimal(value) if value is not None else Decimal(0)


def _from_receipt(db: Session, hold: dict[str, Any]) -> dict[str, Any]:
    """The receipt, order line and supplier a hold of a receipt's stock came from."""
    gr = _receipt(db, str(hold["gr_no"]))
    po = db.execute(text("select * from po where no = :n"), {"n": gr["po_no"]}).mappings().one()
    return {"gr": gr, "po": dict(po), "po_line_no": hold["gr_line_no"], "supplier": po["supplier_code"]}


def _manual_origin(db: Session, hold: dict[str, Any], data: ReturnLineIn) -> dict[str, Any]:
    """A manual hold must say which receipt the goods came from; opening or imported stock may name only a supplier."""
    if hold["source"] == "import":
        if not data.supplier_code:
            raise ReturnError(422, "say which supplier the stock came from")
        supplier = db.execute(text("select code from supplier where code = :c"), {"c": data.supplier_code}).scalar()
        if supplier is None:
            raise ReturnError(422, f"supplier {data.supplier_code} does not exist")
        return {"gr": None, "po": None, "po_line_no": None, "supplier": supplier}
    if not data.gr_no:
        raise ReturnError(422, "say which receipt the goods came from")
    gr = _receipt(db, data.gr_no)
    if gr["location_code"] != hold["location_code"]:
        raise ReturnError(422, f"{data.gr_no} is not at {hold['location_code']}")
    line_no = int(data.po_line_no or hold["gr_line_no"] or 0)
    if not line_no:
        raise ReturnError(422, "say which line of the receipt the goods came from")
    row = db.execute(
        text(
            "select g.po_no, l.item_code from gr_line g join po_line l on l.po_no = g.po_no "
            "and l.line_no = g.po_line_no where g.gr_no = :g and g.po_line_no = :l"
        ),
        {"g": data.gr_no, "l": line_no},
    ).first()
    if row is None or row[1] != hold["item_code"]:
        raise ReturnError(422, f"{hold['item_code']} is not on line {line_no} of {data.gr_no}")
    left = _accepted_on(db, data.gr_no, line_no) - _returned_already(db, data.gr_no, row[0], line_no)
    if Decimal(data.qty) > left:
        raise ReturnError(409, f"only {left.normalize():f} of that receipt line may still be returned")
    po = db.execute(text("select * from po where no = :n"), {"n": row[0]}).mappings().one()
    return {"gr": gr, "po": dict(po), "po_line_no": line_no, "supplier": po["supplier_code"]}


def create(
    db: Session, tenant: str | None, user_id: int, reason: str, lines: list[ReturnLineIn], *, as_of: date
) -> Created:
    """A draft return from open blocked or excess holds. Commits."""
    why = (reason or "").strip()
    if not why:
        raise ReturnError(422, "say why the goods go back")
    if not lines:
        raise ReturnError(422, "a return needs at least one line")
    rows: list[dict[str, Any]] = []
    supplier: str | None = None
    location: str | None = None
    for data in lines:
        if data.qty <= 0:
            raise ReturnError(422, "the quantity must be more than zero")
        hold = _hold(db, data.hold_id)
        if hold["closed_at"] is not None or Decimal(hold["left_qty"]) <= 0:
            raise ReturnError(409, f"hold {data.hold_id} is already closed")
        if hold["quality"] != "blocked" and hold["source"] != "excess":
            raise ReturnError(409, f"hold {data.hold_id} is {hold['quality']}: only blocked or excess goods go back")
        if Decimal(data.qty) > Decimal(hold["left_qty"]):
            raise ReturnError(409, f"only {Decimal(hold['left_qty']).normalize():f} is left on hold {data.hold_id}")
        counted = hold["source"] == "manual"
        if hold["gr_no"]:
            origin = _from_receipt(db, hold)
        elif hold["source"] == "manual":
            origin = _manual_origin(db, hold, data)
        else:
            origin = _manual_origin(db, hold, data)
        if origin["po"] is not None:
            counted = counted and origin["po"] is not None
        else:
            counted = False
        if supplier is None:
            supplier, location = str(origin["supplier"]), str(hold["location_code"])
        elif str(origin["supplier"]) != supplier or str(hold["location_code"]) != location:
            raise ReturnError(422, "one return has one supplier and one place: split the lines")
        name, uom = _item_name(db, hold["item_code"])
        serials = [s.strip().upper() for s in (data.serials or []) if s and s.strip()]
        if serials and hold["gr_no"]:
            allowed = {
                r[0]
                for r in db.execute(
                    text(
                        "select serial from serial_number where gr_no = :g and item_code = :i "
                        "and status in ('received', 'rejected')"
                    ),
                    {"g": hold["gr_no"], "i": hold["item_code"]},
                )
            }
            bad = sorted(set(serials) - allowed)
            if bad:
                raise ReturnError(422, f"not on that receipt: {', '.join(bad)}")
        rows.append(
            {
                "item_code": hold["item_code"], "description": name, "qty": data.qty, "uom": uom,
                "quality": hold["quality"], "hold_id": int(hold["id"]),
                "gr_no": origin["gr"]["no"] if origin["gr"] else None,
                "po_no": origin["po"]["no"] if origin["po"] else None, "po_line_no": origin["po_line_no"],
                "counted": bool(counted and origin["po"] is not None), "serials": serials,
                "unit_cost": None if origin["gr"] is None else _unit_cost(db, origin["gr"]["no"], hold),
            }
        )  # fmt: skip
    if supplier is None or location is None:
        raise ReturnError(422, "a return needs a supplier and a place")
    no = numbering.next_number(db, "supplier_return", as_of, location_code=location)
    db.execute(
        text(
            "insert into supplier_return (no, supplier_code, location_code, source, reason, created_by) "
            "values (:n, :s, :l, 'store', :why, :by)"
        ),
        {"n": no, "s": supplier, "l": location, "why": why, "by": _me(db, user_id)},
    )
    for line_no, row in enumerate(rows, start=1):
        db.execute(
            text(
                "insert into supplier_return_line (return_no, line_no, item_code, description, qty, uom, quality, "
                "hold_id, gr_no, po_no, po_line_no, counted, serials, unit_cost, outcome) values "
                "(:n, :l, :i, :d, :q, :u, :k, :h, :g, :p, :pl, :c, cast(:s as jsonb), :cost, "
                "case when cast(:p as text) is null or cast(:k as text) = 'quarantine' then 'none' else null end)"
            ),
            {
                "n": no, "l": line_no, "i": row["item_code"], "d": row["description"], "q": row["qty"],
                "u": row["uom"], "k": row["quality"], "h": row["hold_id"], "g": row["gr_no"], "p": row["po_no"],
                "pl": row["po_line_no"], "c": row["counted"], "s": json.dumps(row["serials"]),
                "cost": row["unit_cost"],
            },
        )  # fmt: skip
        if row["gr_no"] and row["quality"] == "quarantine":
            db.execute(
                text(
                    "update receipt_excess set status = 'returning', return_no = :r where hold_id = :h "
                    "and status in ('waiting', 'proposed')"
                ),
                {"r": no, "h": row["hold_id"]},
            )
    audit.record(
        db, user(user_id), "return.create", "supplier_return", no,
        after={"supplier": supplier, "lines": len(rows), "source": "store"}, reason=why,
    )  # fmt: skip
    db.commit()
    return Created(no)


def gate_return(
    db: Session,
    tenant: str | None,
    user_id: int,
    po: dict[str, Any],
    gr_no: str,
    rejected: list[dict[str, Any]],
    as_of: date,
) -> str:
    """A receipt's gate rejections go back with the delivery: one RTN, dispatched that day (OP-9). The caller commits."""
    no = numbering.next_number(db, "supplier_return", as_of, location_code=po["ship_to_location"])
    me = _me(db, user_id)
    reason = f"Rejected at the gate on {gr_no}"
    db.execute(
        text(
            "insert into supplier_return (no, supplier_code, location_code, status, source, reason, dispatched_on, "
            "created_by, dispatched_by) values (:n, :s, :l, 'dispatched', 'gate', :why, :d, :by, :by)"
        ),
        {"n": no, "s": po["supplier_code"], "l": po["ship_to_location"], "why": reason, "d": as_of, "by": me},
    )
    for line_no, item in enumerate(rejected, start=1):
        row = item["row"]
        name, uom = _item_name(db, row["item_code"])
        db.execute(
            text(
                "insert into supplier_return_line (return_no, line_no, item_code, description, qty, uom, quality, gr_no, "
                "po_no, po_line_no, serials, unit_cost, outcome) values (:n, :l, :i, :d, :q, :u, 'none', :g, :p, :pl, "
                "cast(:s as jsonb), :cost, 'none')"
            ),
            {
                "n": no, "l": line_no, "i": row["item_code"], "d": name or row["description"], "q": item["qty"],
                "u": uom or row["uom"], "g": gr_no, "p": po["no"], "pl": item["line"],
                "s": json.dumps(item.get("serials") or []),
                "cost": _unit_cost(db, gr_no, {"gr_no": gr_no, "item_code": row["item_code"]}),
            },
        )  # fmt: skip
        if item.get("serials"):
            db.execute(
                text(
                    "update serial_number set status = 'returned', return_no = :r where gr_no = :g and serial = any(:s)"
                ),
                {"r": no, "g": gr_no, "s": item["serials"]},
            )
    audit.record(
        db, user(user_id), "return.create", "supplier_return", no,
        after={"supplier": po["supplier_code"], "source": "gate", "receipt": gr_no}, reason=reason,
    )  # fmt: skip
    audit.record(
        db,
        user(user_id),
        "return.dispatch",
        "supplier_return",
        no,
        after={"status": "dispatched", "gate": True},
    )
    return no


def _unit_cost(db: Session, gr_no: str | None, hold: dict[str, Any]) -> Decimal | None:
    if not gr_no:
        return None
    row = db.execute(
        text(
            "select unit_cost from stock_movement where ref_doc = :g and item_code = :i and unit_cost is not null "
            "order by date, id limit 1"
        ),
        {"g": gr_no, "i": hold["item_code"]},
    ).scalar()
    return Decimal(row) if row is not None else None


def _lines(db: Session, no: str) -> list[dict[str, Any]]:
    return [
        dict(r)
        for r in db.execute(
            text("select * from supplier_return_line where return_no = :n order by line_no"), {"n": no}
        ).mappings()
    ]


def dispatch(db: Session, tenant: str, no: str, user_id: int, challan: str, vehicle: str, *, as_of: date) -> Dispatched:
    """The goods leave the store with the return challan: stock, holds, serials, the counted line, and the supplier's
    mail. Commits."""
    header = db.execute(text("select * from supplier_return where no = :n for update"), {"n": no}).mappings().first()
    if header is None:
        raise ReturnError(404, f"{no} does not exist")
    h = dict(header)
    if h["status"] != "draft":
        raise ReturnError(409, f"{no} is {h['status']}: only a draft return is dispatched")
    if not (challan or "").strip() or not (vehicle or "").strip():
        raise ReturnError(422, "a challan number and a vehicle are needed")
    actor = user(user_id)
    counted_lines: list[dict[str, Any]] = []
    for line in _lines(db, no):
        hold = _hold(db, int(line["hold_id"])) if line["hold_id"] else None
        if hold is not None and (hold["closed_at"] is not None or Decimal(hold["left_qty"]) < Decimal(line["qty"])):
            raise ReturnError(409, f"hold {line['hold_id']} does not have {line['qty']} left")
        if hold is not None and line["quality"] != "none":
            db.execute(
                text(
                    "insert into stock_movement (id, item_code, location_code, date, type, qty, unit_cost, ref_doc, "
                    "source, quality) values (:id, :i, :l, :d, 'return', :q, :c, :r, 'platform', :k)"
                ),
                {
                    "id": f"SM-RTN-{no}-{line['line_no']}", "i": line["item_code"], "l": h["location_code"],
                    "d": as_of, "q": line["qty"], "c": line["unit_cost"], "r": no, "k": line["quality"],
                },
            )  # fmt: skip
            left = Decimal(hold["left_qty"]) - Decimal(line["qty"])
            if left == 0:
                db.execute(
                    text("update stock_hold set left_qty = 0, closed_at = now(), closed_by_doc = :d where id = :i"),
                    {"d": no, "i": hold["id"]},
                )
            else:
                db.execute(text("update stock_hold set left_qty = :l where id = :i"), {"l": left, "i": hold["id"]})
        if line["serials"]:
            db.execute(
                text(
                    "update serial_number set status = 'returned', return_no = :r where item_code = :i "
                    "and serial = any(:s) and status in ('received', 'rejected')"
                ),
                {"r": no, "i": line["item_code"], "s": list(line["serials"])},
            )
        if line["counted"] and line["po_no"]:
            po = dict(db.execute(text("select * from po where no = :n"), {"n": line["po_no"]}).mappings().one())
            po_line = dict(
                db.execute(
                    text("select * from po_line where po_no = :p and line_no = :l"),
                    {"p": line["po_no"], "l": line["po_line_no"]},
                )
                .mappings()
                .one()
            )
            goods.uncount(
                db, po, po_line, Decimal(line["qty"]), as_of, source="return", gr_no=line["gr_no"], actor=actor
            )
            goods.refresh_order(db, po["no"], fallback_to=goods.order_fallback(po))
            counted_lines.append(line)
        if line["quality"] == "quarantine" and line["hold_id"]:
            db.execute(
                text(
                    "update receipt_excess set status = 'returned', decided_by = :u, decided_at = now() "
                    "where return_no = :r and status in ('waiting', 'proposed', 'returning')"
                ),
                {"u": _me(db, user_id), "r": no},
            )
    db.execute(
        text(
            "update supplier_return set status = 'dispatched', challan = :c, vehicle = :v, dispatched_on = :d, "
            "dispatched_by = :by where no = :n"
        ),
        {"c": challan.strip(), "v": vehicle.strip(), "d": as_of, "by": _me(db, user_id), "n": no},
    )
    task_id = _outcome_task(db, tenant, no, h)
    if task_id is not None:
        db.execute(text("update supplier_return set task_id = :t where no = :n"), {"t": task_id, "n": no})
    audit.record(
        db, actor, "return.dispatch", "supplier_return", no,
        after={"status": "dispatched", "challan": challan.strip(), "vehicle": vehicle.strip(),
               "counted": [x["line_no"] for x in counted_lines], "task": task_id},
        reason=h["reason"],
    )  # fmt: skip
    db.commit()
    _mail_supplier(db, tenant, no, as_of)
    return Dispatched(no, [])


def unordered_return(
    db: Session, tenant: str | None, user_id: int, row: dict[str, Any], challan: str | None, vehicle: str | None,
    *, as_of: date,
) -> str:  # fmt: skip
    """Rejected goods that came without an order go back with a return document too (9.9): dispatched, no order, no
    claim. The caller commits."""
    if not (challan or "").strip() or not (vehicle or "").strip():
        raise ReturnError(422, "a challan number and a vehicle are needed to record the return")
    no = numbering.next_number(db, "supplier_return", as_of, location_code=row["location_code"])
    me = _me(db, user_id)
    reason = f"Goods received without an order ({row['no']}) rejected: {row['reason']}"
    db.execute(
        text(
            "insert into supplier_return (no, supplier_code, location_code, status, source, reason, challan, vehicle, "
            "dispatched_on, created_by, dispatched_by) values (:n, :s, :l, 'dispatched', 'unordered', :why, :c, :v, "
            ":d, :by, :by)"
        ),
        {
            "n": no, "s": row["supplier_code"], "l": row["location_code"], "why": reason,
            "c": (challan or "").strip(), "v": (vehicle or "").strip(), "d": as_of, "by": me,
        },
    )  # fmt: skip
    name, uom = _item_name(db, row["item_code"])
    db.execute(
        text(
            "insert into supplier_return_line (return_no, line_no, item_code, description, qty, uom, quality, "
            "unordered_no, serials, outcome) values (:n, 1, :i, :d, :q, :u, 'none', :un, cast(:s as jsonb), 'none')"
        ),
        {
            "n": no, "i": row["item_code"], "d": name, "q": row["qty"], "u": uom or row["uom"],
            "un": row["no"], "s": json.dumps(list(row["serials"] or [])),
        },
    )  # fmt: skip
    if row["serials"]:
        db.execute(
            text(
                "update serial_number set status = 'returned', return_no = :r where item_code = :i and serial = any(:s)"
            ),
            {"r": no, "i": row["item_code"], "s": list(row["serials"])},
        )
    audit.record(
        db, user(user_id), "return.create", "supplier_return", no,
        after={"supplier": row["supplier_code"], "source": "unordered", "receipt": row["no"]}, reason=reason,
    )  # fmt: skip
    audit.record(db, user(user_id), "return.dispatch", "supplier_return", no, after={"status": "dispatched"})
    db.commit()
    _mail_supplier(db, tenant or "", no, as_of)
    return no


def _outcome_task(db: Session, tenant: str, no: str, header: dict[str, Any]) -> int | None:
    """One task for the buyer when a dispatched return has a line whose replacement is not decided."""
    open_lines = [line for line in _lines(db, no) if line["po_no"] and line["po_line_no"] and line["outcome"] is None]
    if not open_lines:
        return None
    first = open_lines[0]
    po = db.execute(text("select * from po where no = :n"), {"n": first["po_no"]}).mappings().one()
    assignee = notifications.login_of(db, tenant, str(po["buyer"])) if po["buyer"] else None
    if assignee is None:
        return None
    supplier = db.execute(
        text("select coalesce(trade_name, legal_name) from supplier where code = :c"), {"c": header["supplier_code"]}
    ).scalar()
    qty = sum((Decimal(line["qty"]) for line in open_lines), Decimal(0))
    title = f"Agree with {supplier} whether the {format_qty(qty)} returned on {no} will be replaced"
    manager = None
    for (code,) in db.execute(
        text("select code from org_user where active and lower(designation) = 'purchase manager' order by code")
    ).all():
        manager = notifications.login_of(db, tenant, str(code))
        if manager:
            break
    return tasks.create_task(
        db,
        tenant,
        kind="return_outcome",
        title=title,
        assignee=assignee,
        sla_hours=SLA_HOURS,
        escalation_chain=[x for x in [manager] if x],
        payload={
            "return_no": no,
            "po_no": first["po_no"],
            "calendar_location": header["location_code"],
            "link": "#/returns",
        },
        email_note="Replace or no replacement: say which on the return",
        calendar=reference.calendar_for_location(db, header["location_code"]),
    )


def _mail_supplier(db: Session, tenant: str, no: str, as_of: date) -> bool:
    """One plain mail to the supplier's order contact with the returned lines; no model writes any of it."""
    header = db.execute(text("select * from supplier_return where no = :n"), {"n": no}).mappings().one()
    from p2p.masters import suppliers  # noqa: PLC0415 - supplier records build on masters

    found = suppliers.order_contact(db, str(header["supplier_code"]))
    address = found[0] if found else None
    if not address:
        return False
    lines = _lines(db, no)
    body_lines = []
    for line in lines:
        words = (
            f"{format_qty(line['qty'])} {line['uom'] or ''} {line['description'] or line['item_code'] or ''}".strip()
        )
        receipt = f", our receipt {line['gr_no']}" if line["gr_no"] else ""
        one = f"- {words} (your challan {header['challan']}){receipt}"
        body_lines.append(one)
    body = (
        f"We have returned the goods below on {as_of:%d %b %Y}, return challan {no}. Reason: {header['reason']}\n"
        + "\n".join(body_lines)
    )
    subject = f"Goods returned on return challan {no}"
    outbox.queue_email(
        db,
        to=address,
        subject=subject,
        body=body,
        record=body,
        linked_type="supplier_return",
        linked_ref=no,
    )
    audit.record(db, audit.SYSTEM, "return.mail", "supplier_return", no, after={"to": address})
    db.commit()
    return True


def outcome(db: Session, tenant: str, no: str, user_id: int, outcomes: dict[int, str], *, as_of: date) -> list[str]:
    """The buyer's decision per order line: replace, or no replacement (which may raise a recovery claim). Commits.
    Returns the claims raised."""
    header = db.execute(text("select * from supplier_return where no = :n for update"), {"n": no}).mappings().first()
    if header is None:
        raise ReturnError(404, f"{no} does not exist")
    h = dict(header)
    if h["status"] != "dispatched":
        raise ReturnError(409, f"{no} is {h['status']}: an outcome is decided after dispatch")
    lines = {line["line_no"]: line for line in _lines(db, no)}
    claims: list[str] = []
    for line_no, decision in outcomes.items():
        line = lines.get(int(line_no))
        if line is None:
            raise ReturnError(422, f"{no} has no line {line_no}")
        if decision not in ("replace", "no_replacement"):
            raise ReturnError(422, "the outcome must be replace or no_replacement")
        if not line["po_no"] or not line["po_line_no"]:
            raise ReturnError(409, f"line {line_no} has no order line: its outcome is none")
        if line["outcome"] not in (None, decision):
            raise ReturnError(409, f"line {line_no} is already decided {line['outcome']}")
        status = db.execute(
            text("select status from po_line where po_no = :p and line_no = :l"),
            {"p": line["po_no"], "l": line["po_line_no"]},
        ).scalar()
        if decision == "replace" and status in ("closed", "cancelled"):
            raise ReturnError(409, f"line {line['po_line_no']} is {status}: its replacement cannot be received")
        db.execute(
            text(
                "update supplier_return_line set outcome = :o, outcome_by = :by, outcome_at = now() "
                "where return_no = :n and line_no = :l"
            ),
            {"o": decision, "by": _me(db, user_id), "n": no, "l": line_no},
        )
        audit.record(
            db,
            user(user_id),
            "return.outcome",
            "supplier_return",
            no,
            after={"line": int(line_no), "outcome": decision},
            reason=None,
        )
        if decision == "no_replacement" and line["counted"]:
            claim = _raise_claim(db, tenant, h, line, user_id, as_of)
            if claim:
                claims.append(claim)
    remaining = [line for line in _lines(db, no) if line["po_no"] and line["po_line_no"] and line["outcome"] is None]
    if not remaining and h["task_id"]:
        tasks.complete_task(db, int(h["task_id"]), user_id)
    db.commit()
    return claims


def _raise_claim(
    db: Session, tenant: str, header: dict[str, Any], line: dict[str, Any], user_id: int, as_of: date
) -> str | None:
    """A counted return with no replacement, invoiced beyond what remains received: a recovery claim (OP-10)."""
    from p2p.invoices.service import BOOKED  # noqa: PLC0415 - invoices build on receiving

    po = db.execute(text("select * from po where no = :n"), {"n": line["po_no"]}).mappings().one()
    order_line = dict(
        db.execute(
            text("select * from po_line where po_no = :p and line_no = :l"),
            {"p": line["po_no"], "l": line["po_line_no"]},
        )
        .mappings()
        .one()
    )
    qty = rules.claim_qty(
        Decimal(line["qty"]), Decimal(order_line["invoiced_qty"]), Decimal(order_line["received_qty"])
    )
    if qty <= 0:
        return None
    invoices = [
        r[0]
        for r in db.execute(
            text(
                "select distinct i.no from invoice i join invoice_line il on il.invoice_no = i.no "
                "where il.po_no = :p and il.po_line_no = :l and i.status = any(:st) order by i.no"
            ),
            {"p": line["po_no"], "l": line["po_line_no"], "st": list(BOOKED)},
        )
    ]
    supplier = db.execute(
        text("select state_code, gstin, gst_reg_type from supplier where code = :c"), {"c": po["supplier_code"]}
    ).one()
    supplier_state = supplier[0] or (str(supplier[1])[:2] if supplier[1] else None)
    _, place = ordering.place_of_supply(db, po["ship_to_location"])
    if not supplier_state or not place:
        raise ReturnError(
            409, "the state of the supplier or the place is unknown, so the claim's GST cannot be worked out"
        )
    amounts = line_amounts(
        qty, Decimal(order_line["unit_price"]), order_line["gst_rate"], supplier_state, place,
        Registration(supplier[2] or "regular"),
    )  # fmt: skip
    no = numbering.next_number(db, "recovery_claim", as_of, location_code=header["location_code"])
    db.execute(
        text(
            "insert into recovery_claim (no, supplier_code, po_no, po_line_no, return_no, return_line_no, qty, "
            "unit_price, taxable, cgst, sgst, igst, total, invoices, reason, raised_by) values "
            "(:no, :s, :p, :pl, :r, :rl, :q, :price, :t, :c, :sg, :i, :tot, :inv, :why, :by)"
        ),
        {
            "no": no, "s": po["supplier_code"], "p": line["po_no"], "pl": line["po_line_no"], "r": header["no"],
            "rl": line["line_no"], "q": qty, "price": order_line["unit_price"], "t": amounts.taxable,
            "c": amounts.cgst, "sg": amounts.sgst, "i": amounts.igst, "tot": amounts.total, "inv": invoices,
            "why": header["reason"], "by": _me(db, user_id),
        },
    )  # fmt: skip
    db.execute(
        text("update supplier_return_line set claim_no = :c where return_no = :n and line_no = :l"),
        {"c": no, "n": header["no"], "l": line["line_no"]},
    )
    audit.record(
        db, user(user_id), "claim.raise", "recovery_claim", no,
        after={"supplier": po["supplier_code"], "po": line["po_no"], "line": line["po_line_no"], "qty": str(qty),
               "total": str(amounts.total), "invoices": invoices, "return": header["no"]},
    )  # fmt: skip
    supplier_name = db.execute(
        text("select coalesce(trade_name, legal_name) from supplier where code = :c"), {"c": po["supplier_code"]}
    ).scalar_one()
    told = {str(po["buyer"]) if po["buyer"] else ""}
    ap_logins = [
        int(r[0])
        for r in db.execute(
            text(
                "select distinct ur.user_id from user_role ur join role_permission rp on rp.role = ur.role "
                "where rp.action = 'invoice.book' order by 1"
            )
        )
    ]
    for ap_uid in ap_logins:
        notifications.notify(
            db,
            tenant,
            ap_uid,
            "recovery_claim",
            f"Recovery claim {no}: {amounts.total} from {supplier_name}",
            f"Recovery claim {no} on {line['po_no']} line {line['po_line_no']} for {format_qty(qty)} returned "
            f"with no replacement.",
            link="#/invoices",
        )
    for code in sorted(c for c in told if c):
        uid = notifications.login_of(db, tenant, code)
        if uid is None:
            continue
        notifications.notify(
            db,
            tenant,
            uid,
            "recovery_claim",
            f"Recovery claim {no}: {amounts.total} from {supplier_name}",
            f"Recovery claim {no} on {line['po_no']} line {line['po_line_no']} for {format_qty(qty)} returned "
            f"with no replacement.",
            link="#/purchase-orders/{line['po_no']}",
        )
    return no


def cancel(db: Session, no: str, user_id: int, reason: str) -> None:
    """A draft return is cancelled; its holds are left as they are. A dispatched return is final. Commits."""
    why = (reason or "").strip()
    if not why:
        raise ReturnError(422, "say why the return is cancelled")
    header = db.execute(text("select * from supplier_return where no = :n for update"), {"n": no}).mappings().first()
    if header is None:
        raise ReturnError(404, f"{no} does not exist")
    if header["status"] != "draft":
        raise ReturnError(409, f"{no} is {header['status']}: a dispatched return is final")
    db.execute(
        text(
            "update receipt_excess set status = 'waiting', return_no = null where return_no = :r and status = 'returning'"
        ),
        {"r": no},
    )
    db.execute(
        text("update supplier_return set status = 'cancelled', reason = :why where no = :n"), {"why": why, "n": no}
    )
    audit.record(db, user(user_id), "return.cancel", "supplier_return", no, after={"status": "cancelled"}, reason=why)
    db.commit()


def listing(db: Session, *, status: str | None = None) -> list[dict[str, Any]]:
    return [
        dict(r)
        for r in db.execute(
            text(
                "select r.*, coalesce(s.trade_name, s.legal_name) as supplier, "
                "(select count(*) from supplier_return_line l where l.return_no = r.no) as lines, "
                "(select coalesce(sum(l.qty), 0) from supplier_return_line l where l.return_no = r.no) as qty "
                "from supplier_return r join supplier s on s.code = r.supplier_code "
                "where (cast(:s as text) is null or r.status = :s) order by r.created_at desc, r.no desc limit 500"
            ),
            {"s": status},
        ).mappings()
    ]


def detail(db: Session, no: str) -> dict[str, Any]:
    header = (
        db.execute(
            text(
                "select r.*, coalesce(s.trade_name, s.legal_name) as supplier, l.name as location_name "
                "from supplier_return r join supplier s on s.code = r.supplier_code "
                "join location l on l.code = r.location_code where r.no = :n"
            ),
            {"n": no},
        )
        .mappings()
        .first()
    )
    if header is None:
        raise ReturnError(404, f"{no} does not exist")
    return {**dict(header), "lines": _lines(db, no)}


def claims(db: Session, *, supplier: str | None = None) -> list[dict[str, Any]]:
    return [
        dict(r)
        for r in db.execute(
            text(
                "select c.*, coalesce(s.trade_name, s.legal_name) as supplier from recovery_claim c "
                "join supplier s on s.code = c.supplier_code "
                "where (cast(:s as text) is null or c.supplier_code = :s) order by c.raised_at desc, c.no desc"
            ),
            {"s": supplier},
        ).mappings()
    ]
