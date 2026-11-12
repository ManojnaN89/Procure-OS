"""Goods receipt: what arrived against a sent order, checked, accepted or rejected, and put into stock.

A receipt is posted by a store person who is not the buyer, an approver or the requester of that order (segregation of
duties). Over-delivery beyond the company's tolerance for the item's category is refused. An item that needs inspection
waits: nothing counts and nothing goes into stock until the inspection says how much was accepted. What is accepted
raises the received quantity of the order line (which the invoice match uses later) and, for stocked items, the stock.
A service line is not received here: the requester confirms it (receiving/services.py). A serialised item needs one serial
number per unit received, and a serial already on file for the item is refused.
"""

import logging
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from p2p.approvals.workflow import org_code_of
from p2p.formulas import receiving as rules
from p2p.formulas.money import format_qty
from p2p.platform import audit, notifications, numbering
from p2p.platform.sod import user
from p2p.receiving.kinds import service_lines

log = logging.getLogger(__name__)
RECEIVABLE = ("sent", "acknowledged", "part_received")


class ReceiptError(Exception):
    pass


@dataclass
class LineIn:
    po_line_no: int
    qty_received: Decimal
    qty_rejected: Decimal | None = None
    reject_reason: str | None = None
    serials: list[str] | None = None  # one per unit received, for a serialised item


@dataclass
class Header:
    challan: str | None = None
    invoice_ref: str | None = None
    eway_bill: str | None = None
    vehicle: str | None = None
    remarks: str | None = None


@dataclass
class Posted:
    no: str
    po_no: str
    po_status: str
    pending_inspection: list[int] = field(default_factory=list)
    accepted: dict[int, Decimal] = field(default_factory=dict)


@dataclass
class Decision:
    po_line_no: int
    qty_accepted: Decimal
    qty_rejected: Decimal
    reason: str | None = None


def over_pct(db: Session, category: str | None) -> Decimal:
    """The over-delivery the company accepts for a category (its own row first, then the one for any category)."""
    for cat in (category, "ANY"):
        if cat is None:
            continue
        value = db.execute(
            text("select value from tolerance where key = 'receipt_over_pct' and category_code = :c"), {"c": cat}
        ).scalar()
        if value not in (None, ""):
            return Decimal(str(value))
    return Decimal(0)


def conflict_of(db: Session, po: dict[str, Any], user_id: int, *, requester_may: bool = False) -> str | None:
    """Why this person may not receive this order, or None. The receiver differs from the buyer, the approvers and the
    requester (`requester_may`: a service is confirmed by the person who asked for it, so that is their duty)."""
    me = org_code_of(db, user_id)
    if me is None:
        return None  # a login with no place in the organisation holds none of those duties
    approvers = {
        r[0]
        for r in db.execute(
            text("select acted_by from po_approval_step where po_no = :p and acted_by is not null"), {"p": po["no"]}
        )
    }
    requester = db.execute(text("select requester from request where no = :r"), {"r": po["request_no"]}).scalar()
    if me == po["buyer"]:
        return "the buyer who ordered cannot also receive the goods"
    if me in approvers:
        return "an approver of the order cannot also receive the goods"
    if me == requester and not requester_may:
        return "the person who asked for the goods cannot also receive them"
    return None


def _counted(db: Session, po_no: str, line_no: int) -> Decimal:
    """Accepted so far, plus what waits for inspection."""
    return Decimal(
        db.execute(
            text(
                "select coalesce(sum(coalesce(g.qty_accepted, g.qty_received)), 0) from gr_line g "
                "where g.po_no = :p and g.po_line_no = :l"
            ),
            {"p": po_no, "l": line_no},
        ).scalar_one()
    )


def _item(db: Session, item_code: str | None) -> dict[str, Any]:
    none = {"stocked": False, "inspection_required": False, "serialised": False, "category_code": None}
    if not item_code:
        return none
    row = (
        db.execute(
            text("select stocked, inspection_required, serialised, category_code from item where code = :c"),
            {"c": item_code},
        )
        .mappings()
        .first()
    )
    return dict(row) if row else none


def count_accepted(
    db: Session, gr_no: str, po: dict[str, Any], line: dict[str, Any], accepted: Decimal, as_of: date
) -> None:
    """What was accepted (or confirmed, for a service) counts against the order line, and stocked goods go into stock."""
    if accepted <= 0:
        return
    total = Decimal(line["received_qty"]) + accepted
    db.execute(
        text("update po_line set received_qty = :t, status = :s where po_no = :p and line_no = :l"),
        {"t": total, "s": rules.line_status(Decimal(line["qty"]), total), "p": po["no"], "l": line["line_no"]},
    )
    item = _item(db, line["item_code"])
    if item["stocked"] and line["item_code"]:
        ratio = Decimal(line["base_qty"]) / Decimal(line["qty"]) if line["base_qty"] else Decimal(1)
        db.execute(
            text(
                "insert into stock_movement (id, item_code, location_code, date, type, qty, unit_cost, ref_doc, source) "
                "values (:id, :i, :loc, :d, 'receipt', :q, :c, :ref, 'platform')"
            ),
            {
                "id": f"SM-{gr_no}-{line['line_no']}",
                "i": line["item_code"],
                "loc": po["ship_to_location"],
                "d": as_of,
                "q": (accepted * ratio).quantize(rules.QTY),
                "c": rules.stock_unit_cost(Decimal(line["unit_price"]), Decimal(line["qty"]), line["base_qty"]),
                "ref": gr_no,
            },
        )


def refresh_order(db: Session, po_no: str) -> str:
    """The order's status from its lines."""
    statuses = [r[0] for r in db.execute(text("select status from po_line where po_no = :p"), {"p": po_no})]
    new = rules.order_status(statuses)
    if new:
        db.execute(text("update po set status = :s where no = :p"), {"s": new, "p": po_no})
        return new
    return str(db.execute(text("select status from po where no = :p"), {"p": po_no}).scalar_one())


def _serials(db: Session, item_code: str, ln: LineIn) -> list[str]:
    """The serial numbers of a serialised item: one per unit received, none already on file for the item."""
    try:
        numbers = rules.check_serials(ln.serials or [], ln.qty_received)
    except ValueError as exc:
        raise ReceiptError(f"line {ln.po_line_no}: {exc}") from None
    taken = sorted(
        r[0]
        for r in db.execute(
            text("select serial from serial_number where item_code = :i and serial = any(:s)"),
            {"i": item_code, "s": numbers},
        )
    )
    if taken:
        raise ReceiptError(
            f"line {ln.po_line_no}: serial number(s) already received for {item_code}: {', '.join(taken)}"
        )
    return numbers


def post_receipt(db: Session, po_no: str, lines: list[LineIn], header: Header, user_id: int, *, as_of: date) -> Posted:
    found = db.execute(text("select * from po where no = :n for update"), {"n": po_no}).mappings().first()
    if found is None:
        raise ReceiptError(f"{po_no} does not exist")
    po = dict(found)
    if po["status"] not in RECEIVABLE:
        raise ReceiptError(f"{po_no} is {po['status']}; goods can only be received against an order that was sent")
    if not lines:
        raise ReceiptError("a receipt needs at least one line")
    if len({ln.po_line_no for ln in lines}) != len(lines):
        raise ReceiptError("a line appears twice in the receipt")
    conflict = conflict_of(db, po, user_id)
    if conflict:
        raise ReceiptError(f"segregation of duties: {conflict}")
    order_lines = {
        r["line_no"]: dict(r)
        for r in db.execute(
            text("select * from po_line where po_no = :n order by line_no for update"), {"n": po_no}
        ).mappings()
    }
    plan: list[tuple[LineIn, dict[str, Any], dict[str, Any], rules.SplitResult | None]] = []
    result: rules.SplitResult | None
    services = service_lines(db, po_no)
    serials: dict[int, list[str]] = {}
    for ln in lines:
        row = order_lines.get(ln.po_line_no)
        if row is None or row["status"] not in ("open", "part_received"):
            raise ReceiptError(f"line {ln.po_line_no} is not open on {po_no}")
        if ln.po_line_no in services:
            raise ReceiptError(
                f"line {ln.po_line_no} ({row['description']}) is a service: the person who asked for it confirms it"
            )
        item = _item(db, row["item_code"])
        if item["serialised"]:
            serials[ln.po_line_no] = _serials(db, row["item_code"], ln)
        room = rules.room_left(
            Decimal(row["qty"]), _counted(db, po_no, ln.po_line_no), over_pct(db, item["category_code"])
        )
        if ln.qty_received > room:
            raise ReceiptError(
                f"line {ln.po_line_no} ({row['description']}): {ln.qty_received} is more than the {room} that may still be received"
            )
        if item["inspection_required"]:
            if ln.qty_rejected:
                raise ReceiptError(f"line {ln.po_line_no} needs inspection: the inspection decides what is rejected")
            if ln.qty_received <= 0:
                raise ReceiptError("the quantity received must be more than zero")
            plan.append((ln, row, item, None))
            continue
        try:
            result = rules.split(ln.qty_received, ln.qty_rejected)
        except ValueError as exc:
            raise ReceiptError(f"line {ln.po_line_no}: {exc}") from None
        if result.rejected > 0 and not (ln.reject_reason or "").strip():
            raise ReceiptError(f"line {ln.po_line_no}: a reason is needed for the rejected quantity")
        plan.append((ln, row, item, result))

    pending = [ln.po_line_no for ln, _, _, res in plan if res is None]
    me = org_code_of(db, user_id) or f"user:{user_id}"
    gr_no = numbering.next_number(db, "goods_receipt", as_of, location_code=po["ship_to_location"])
    db.execute(
        text(
            "insert into goods_receipt (no, po_no, location_code, received_by, delivery_challan_no, supplier_invoice_ref, "
            "eway_bill_no, vehicle_no, inspection_status, accepted_on, remarks, posted_by) values "
            "(:no, :po, :loc, :by, :dc, :inv, :ew, :veh, :ins, :acc, :rem, :u)"
        ),
        {
            "no": gr_no,
            "po": po_no,
            "loc": po["ship_to_location"],
            "by": me,
            "dc": header.challan,
            "inv": header.invoice_ref,
            "ew": header.eway_bill,
            "veh": header.vehicle,
            "ins": "pending" if pending else "not_required",
            "acc": None if pending else as_of,
            "rem": header.remarks,
            "u": str(user_id),
        },
    )
    for line_no, numbers in serials.items():
        for serial in numbers:
            db.execute(
                text(
                    "insert into serial_number (item_code, serial, gr_no, po_no, po_line_no) values (:i, :s, :g, :p, :l)"
                ),
                {"i": order_lines[line_no]["item_code"], "s": serial, "g": gr_no, "p": po_no, "l": line_no},
            )
    accepted_by_line: dict[int, Decimal] = {}
    for ln, row, _, result in plan:
        db.execute(
            text(
                "insert into gr_line (gr_no, po_no, po_line_no, qty_received, qty_accepted, qty_rejected, reject_reason) "
                "values (:g, :p, :l, :r, :a, :x, :why)"
            ),
            {
                "g": gr_no,
                "p": po_no,
                "l": ln.po_line_no,
                "r": ln.qty_received,
                "a": None if result is None else result.accepted,
                "x": None if result is None else result.rejected,
                "why": (ln.reject_reason or "").strip() or None,
            },
        )
        if result is not None:
            accepted_by_line[ln.po_line_no] = result.accepted
            count_accepted(db, gr_no, po, row, result.accepted, as_of)
    status = refresh_order(db, po_no)
    audit.record(
        db,
        user(user_id),
        "grn.post",
        "goods_receipt",
        gr_no,
        after={
            "po": po_no,
            "lines": {ln.po_line_no: str(ln.qty_received) for ln in lines},
            "accepted": {k: str(v) for k, v in accepted_by_line.items()},
            "pending_inspection": pending,
            "po_status": status,
            **({"serials": serials} if serials else {}),
        },
    )
    db.commit()
    return Posted(gr_no, po_no, status, pending, accepted_by_line)


def inspect(db: Session, gr_no: str, decisions: list[Decision], user_id: int, *, as_of: date) -> Posted:
    """Record what the inspection accepted and rejected on the lines that were waiting."""
    gr = db.execute(text("select * from goods_receipt where no = :n for update"), {"n": gr_no}).mappings().first()
    if gr is None:
        raise ReceiptError(f"{gr_no} does not exist")
    if gr["inspection_status"] != "pending":
        raise ReceiptError(f"{gr_no} is not waiting for inspection")
    po = dict(db.execute(text("select * from po where no = :n for update"), {"n": gr["po_no"]}).mappings().one())
    waiting = {
        r["po_line_no"]: dict(r)
        for r in db.execute(
            text("select * from gr_line where gr_no = :g and qty_accepted is null order by po_line_no"), {"g": gr_no}
        ).mappings()
    }
    given = {d.po_line_no: d for d in decisions}
    if set(given) != set(waiting) or len(given) != len(decisions):
        raise ReceiptError(
            "the inspection must decide every line that is waiting, once: " + ", ".join(map(str, sorted(waiting)))
        )
    order_lines = {
        r["line_no"]: dict(r)
        for r in db.execute(text("select * from po_line where po_no = :n for update"), {"n": po["no"]}).mappings()
    }
    for line_no, line in waiting.items():
        d = given[line_no]
        if d.qty_accepted < 0 or d.qty_rejected < 0 or d.qty_accepted + d.qty_rejected != Decimal(line["qty_received"]):
            raise ReceiptError(
                f"line {line_no}: accepted and rejected must add up to the {line['qty_received']} received"
            )
        if d.qty_rejected > 0 and not (d.reason or "").strip():
            raise ReceiptError(f"line {line_no}: a reason is needed for the rejected quantity")
    total_rejected = Decimal(0)
    accepted_by_line: dict[int, Decimal] = {}
    for line_no, d in given.items():
        db.execute(
            text(
                "update gr_line set qty_accepted = :a, qty_rejected = :x, reject_reason = :why where gr_no = :g and po_line_no = :l"
            ),
            {
                "a": d.qty_accepted,
                "x": d.qty_rejected,
                "why": (d.reason or "").strip() or None,
                "g": gr_no,
                "l": line_no,
            },
        )
        total_rejected += d.qty_rejected
        accepted_by_line[line_no] = d.qty_accepted
        count_accepted(db, gr_no, po, order_lines[line_no], d.qty_accepted, as_of)
    db.execute(
        text("update goods_receipt set inspection_status = :s, accepted_on = :d where no = :n"),
        {"s": "failed" if total_rejected > 0 else "passed", "d": as_of, "n": gr_no},
    )
    status = refresh_order(db, po["no"])
    audit.record(
        db,
        user(user_id),
        "grn.inspect",
        "goods_receipt",
        gr_no,
        after={"accepted": {k: str(v) for k, v in accepted_by_line.items()}, "rejected": str(total_rejected)},
    )
    db.commit()
    return Posted(gr_no, po["no"], status, [], accepted_by_line)


def tell_requesters(db: Session, tenant: str, gr_no: str, *, inspected: bool = False) -> int:
    """Tell the people who asked for the goods what arrived on a receipt (or what the inspection decided), and the buyer.
    One notification each, in the app and by email. It never undoes or hides the receipt: a failure is only logged."""
    try:
        gr = db.execute(text("select po_no from goods_receipt where no = :n"), {"n": gr_no}).mappings().first()
        if gr is None:
            return 0
        rows = (
            db.execute(
                text(
                    "select g.po_line_no, l.description, l.uom, l.request_no, g.qty_received, g.qty_accepted, g.qty_rejected "
                    "from gr_line g join po_line l on l.po_no = g.po_no and l.line_no = g.po_line_no "
                    "where g.gr_no = :n order by g.po_line_no"
                ),
                {"n": gr_no},
            )
            .mappings()
            .all()
        )
        po = db.execute(text("select buyer, status from po where no = :p"), {"p": gr["po_no"]}).mappings().one()
        told = 0
        wanted = {r["request_no"] for r in rows}
        people: dict[str, list[Any]] = {}
        for request_no in sorted(wanted):
            who = db.execute(text("select requester from request where no = :r"), {"r": request_no}).scalar()
            people.setdefault(str(who) if who else "", []).append(request_no)
        recipients = [(code, mine) for code, mine in people.items() if code]
        if po["buyer"] and str(po["buyer"]) not in {c for c, _ in recipients}:
            recipients.append((str(po["buyer"]), sorted(wanted)))
        for code, mine in recipients:
            uid = notifications.login_of(db, tenant, code)
            if uid is None:
                continue
            lines = []
            for r in rows:
                if r["request_no"] not in mine:
                    continue
                what = f"{r['description'] or 'Item'} ({r['uom'] or 'units'})"
                if r["qty_accepted"] is None:
                    lines.append(f"- {what}: {format_qty(r['qty_received'])} received, waiting for inspection")
                elif Decimal(r["qty_rejected"] or 0) > 0:
                    lines.append(
                        f"- {what}: {format_qty(r['qty_accepted'])} accepted, {format_qty(r['qty_rejected'])} rejected"
                    )
                else:
                    lines.append(f"- {what}: {format_qty(r['qty_accepted'])} accepted")
            if not lines:
                continue
            title = f"Inspection done for {gr['po_no']}" if inspected else f"Goods arrived for {gr['po_no']}"
            notifications.notify(
                db,
                tenant,
                uid,
                "goods_received",
                title,
                f"{title} (receipt {gr_no}):\n" + "\n".join(lines),
                link=f"#/purchase-orders/{gr['po_no']}",
            )
            told += 1
        return told
    except Exception:  # noqa: BLE001 - telling people must never undo or hide a receipt
        db.rollback()
        log.warning("could not notify about receipt %s", gr_no, exc_info=True)
        return 0
