"""Receipt without an order: goods that arrived with no purchase order, taken in only as a flagged exception.

The store records what came (supplier, item, quantity, why). Nothing enters stock yet: the goods wait outside stock on this
record, not in a quarantine location, because holding them on one status needs no second stock place, no transfers and no
change to how available stock is counted, and the normal goods receipt then puts them into stock once. The purchase manager
decides through a proposal (rule 5). Approved, the buyer raises the order afterwards (an after-the-fact order linked to
the receipt, through the normal order approval), and once it is sent the store posts the goods receipt on it, so the invoice
is matched as usual. Rejected, the goods go back to the supplier and the store records the return.

Statuses: pending (waiting for the purchase manager), approved (waiting for the buyer's order), rejected (to be returned),
ordered (the order is raised; the receipt is posted on it once it is sent), received, returned.
"""

import json
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from p2p.approvals.workflow import org_code_of
from p2p.formulas import receiving as rules
from p2p.formulas.money import format_qty, round_money
from p2p.formulas.orders import current_gst_rate
from p2p.orders import service as ordering
from p2p.platform import audit, numbering, proposals
from p2p.platform.sod import Actor, user
from p2p.receiving import goods

ACTION = "receipt.unordered"
APPROVE = "grn.unordered_approve"  # the purchase manager's (role Approver)
OPEN = ("pending", "approved", "rejected", "ordered")  # still needs someone to act


class UnorderedError(Exception):
    pass


@dataclass
class UnorderedIn:
    supplier_code: str
    item_code: str
    qty: Decimal
    location_code: str
    reason: str
    challan: str | None = None
    serials: list[str] = field(default_factory=list)


def _row(db: Session, no: str) -> dict[str, Any]:
    row = db.execute(text("select * from unordered_receipt where no = :n for update"), {"n": no}).mappings().first()
    if row is None:
        raise UnorderedError(f"{no} does not exist")
    return dict(row)


def _item(db: Session, code: str) -> dict[str, Any]:
    row = (
        db.execute(
            text(
                "select i.code, i.name, i.serialised, i.category_code, coalesce(i.purchase_uom, i.base_uom) as uom, "
                "i.standard_price, i.gst_rate_before, i.gst_rate_after, i.gst_rate_change_date, "
                "coalesce(i.default_gl, c.default_gl) as gl from item i left join item_category c on c.code = i.category_code "
                "where i.code = :c"
            ),
            {"c": code},
        )
        .mappings()
        .first()
    )
    if row is None:
        raise UnorderedError(f"item {code} does not exist")
    return dict(row)


def record(db: Session, data: UnorderedIn, user_id: int, *, as_of: date) -> str:
    """The store takes the goods in as an exception and asks the purchase manager. Returns the receipt number."""
    if data.qty <= 0:
        raise UnorderedError("the quantity received must be more than zero")
    reason = (data.reason or "").strip()
    if not reason:
        raise UnorderedError("a reason is needed: why did goods come without an order?")
    supplier = db.execute(
        text("select coalesce(trade_name, legal_name) from supplier where code = :c"), {"c": data.supplier_code}
    ).scalar()
    if supplier is None:
        raise UnorderedError(f"supplier {data.supplier_code} does not exist")
    if not db.execute(text("select 1 from location where code = :c"), {"c": data.location_code}).first():
        raise UnorderedError(f"location {data.location_code} does not exist")
    item = _item(db, data.item_code)
    serials: list[str] = []
    if item["serialised"]:
        try:
            serials = rules.check_serials(data.serials, data.qty)
        except ValueError as exc:
            raise UnorderedError(str(exc)) from None
        taken = [
            r[0]
            for r in db.execute(
                text("select serial from serial_number where item_code = :i and serial = any(:s) order by serial"),
                {"i": data.item_code, "s": serials},
            )
        ]
        if taken:
            raise UnorderedError(
                f"{'serial number' if len(taken) == 1 else 'serial numbers'} already received for {data.item_code}: {', '.join(taken)}"
            )
    no = numbering.next_number(db, "unordered_receipt", as_of, location_code=data.location_code)
    me = org_code_of(db, user_id) or f"user:{user_id}"
    db.execute(
        text(
            "insert into unordered_receipt (no, supplier_code, item_code, qty, uom, location_code, reason, challan, serials, "
            "received_by, posted_by, received_on) values (:no, :s, :i, :q, :u, :loc, :why, :dc, cast(:ser as jsonb), :by, :p, :on)"
        ),
        {
            "no": no, "s": data.supplier_code, "i": data.item_code, "q": data.qty, "u": item["uom"], "loc": data.location_code,
            "why": reason, "dc": (data.challan or "").strip() or None, "ser": json.dumps(serials), "by": me,
            "p": str(user_id), "on": as_of,
        },
    )  # fmt: skip
    audit.record(
        db, user(user_id), "unordered.record", "unordered_receipt", no,
        after={"supplier": data.supplier_code, "item": data.item_code, "qty": str(data.qty), "reason": reason},
    )  # fmt: skip
    value = round_money(data.qty * Decimal(item["standard_price"] or 0))
    pid = proposals.propose(
        db,
        user(user_id),
        action=ACTION,
        summary=(
            f"Accept goods received without an order: {format_qty(data.qty)} {item['uom'] or ''} of {item['name']} from "
            f"{supplier} at {data.location_code} ({no}). Reason: {reason}"
        ),
        payload={"receipt": no},
        required_permission=APPROVE,
        risk="medium",
        value=value,
    )
    db.execute(text("update unordered_receipt set proposal_id = :p where no = :n"), {"p": pid, "n": no})
    db.commit()
    return no


@proposals.executor(ACTION)
def _approve(db: Session, payload: dict[str, Any], actor: Actor) -> dict[str, Any]:
    """The purchase manager accepts the goods: the buyer raises the order for them next."""
    no = str(payload["receipt"])
    row = _row(db, no)
    if row["status"] != "pending":
        raise UnorderedError(f"{no} is {row['status']}, not waiting for a decision")
    db.execute(
        text("update unordered_receipt set status = 'approved', decided_by = :u, decided_at = now() where no = :n"),
        {"u": actor.id, "n": no},
    )
    audit.record(
        db,
        actor,
        "unordered.approve",
        "unordered_receipt",
        no,
        before={"status": "pending"},
        after={"status": "approved"},
    )
    db.commit()
    return {"receipt": no, "status": "approved"}


def reject(db: Session, no: str, user_id: int, reason: str) -> None:
    """The purchase manager refuses the goods: they go back to the supplier."""
    row = _row(db, no)
    if row["status"] != "pending" or row["proposal_id"] is None:
        raise UnorderedError(f"{no} is {row['status']}, not waiting for a decision")
    db.commit()  # release the row: rejecting the proposal locks and commits on its own
    proposals.reject(db, int(row["proposal_id"]), user_id, reason)
    db.execute(
        text(
            "update unordered_receipt set status = 'rejected', decided_by = :u, decided_at = now(), decision_note = :r "
            "where no = :n and status = 'pending'"
        ),
        {"u": str(user_id), "r": reason.strip(), "n": no},
    )
    audit.record(
        db, user(user_id), "unordered.reject", "unordered_receipt", no,
        before={"status": "pending"}, after={"status": "rejected"}, reason=reason.strip(),
    )  # fmt: skip
    db.commit()


def mark_returned(
    db: Session, tenant: str | None, no: str, user_id: int, challan: str | None, vehicle: str | None, *, as_of: date
) -> str:
    """The store records that rejected goods went back to the supplier: a dispatched return document (RTN) with its
    challan and vehicle, and the supplier is mailed. Returns the return number."""
    row = _row(db, no)
    refused = (
        row["proposal_id"]
        and db.execute(
            text("select status in ('rejected', 'expired') from proposal where id = :p"), {"p": row["proposal_id"]}
        ).scalar()
    )
    if row["status"] != "rejected" and not (row["status"] == "pending" and refused):
        raise UnorderedError(f"{no} is {row['status']}: only rejected goods are returned")
    from p2p.receiving import returns  # noqa: PLC0415 - returns build on receipts

    db.commit()  # release the row: the return creates and commits its own document
    return_no = returns.unordered_return(db, tenant, user_id, dict(row), challan, vehicle, as_of=as_of)
    db.execute(
        text(
            "update unordered_receipt set status = 'returned', returned_on = :d, return_ref = :r, return_no = :rn "
            "where no = :n"
        ),
        {"d": as_of, "r": (challan or "").strip() or None, "rn": return_no, "n": no},
    )
    audit.record(
        db,
        user(user_id),
        "unordered.return",
        "unordered_receipt",
        no,
        after={"status": "returned", "return": return_no},
        reason=None,
    )
    db.commit()
    return return_no


def raise_order(db: Session, tenant: str, no: str, user_id: int, unit_price: Decimal, *, as_of: date) -> str:
    """The buyer raises the order after the fact: a request marked as this exception (its approval was the purchase
    manager's), and an order from it at the price agreed with the supplier, approved like any other. Returns the order."""
    row = _row(db, no)
    if row["status"] != "approved":
        raise UnorderedError(f"{no} is {row['status']}: an order is raised once the purchase manager has approved")
    if unit_price <= 0:
        raise UnorderedError("the price must be more than zero")
    if org_code_of(db, user_id) == row["received_by"]:
        raise UnorderedError("segregation of duties: the person who received the goods cannot also order them")
    item = _item(db, row["item_code"])
    rate = current_gst_rate(item["gst_rate_before"], item["gst_rate_after"], item["gst_rate_change_date"], as_of)
    if rate is None:
        raise UnorderedError(f"item {item['code']} has no GST rate, so it cannot be ordered")
    qty = Decimal(row["qty"])
    request_no = numbering.next_number(db, "request", as_of, location_code=row["location_code"])
    db.execute(
        text(
            "insert into request (no, ship_to_location, type, channel, source_ref, justification, status, total_value, "
            "decided_at, decision_reason, need_by) values (:no, :loc, 'goods', 'unordered_receipt', :ref, :why, 'approved', "
            ":total, now(), :dec, :need)"
        ),
        {
            "no": request_no, "loc": row["location_code"], "ref": no, "why": row["reason"],
            "total": round_money(qty * unit_price), "need": row["received_on"],
            "dec": f"Approved as an exception: goods received without an order ({no}, proposal {row['proposal_id']})",
        },
    )  # fmt: skip
    db.execute(
        text(
            "insert into request_line (request_no, line_no, item_code, qty, uom, est_price, need_by, delivery_location, "
            "gl_code, category_code, reason, status) values (:r, 1, :i, :q, :u, :p, :need, :loc, :gl, :cat, :why, 'sourcing')"
        ),
        {
            "r": request_no, "i": item["code"], "q": qty, "u": row["uom"], "p": unit_price, "need": row["received_on"],
            "loc": row["location_code"], "gl": item["gl"], "cat": item["category_code"], "why": row["reason"],
        },
    )  # fmt: skip
    audit.record(
        db, user(user_id), "request.create", "request", request_no,
        after={"status": "approved", "channel": "unordered_receipt", "receipt": no},
    )  # fmt: skip
    line = ordering.ReadyLine(
        request_no=request_no, request_line_no=1, item_code=item["code"], name=item["name"], qty=qty, uom=row["uom"],
        base_qty=None, est_price=unit_price, need_by=row["received_on"], gl_code=item["gl"], category=item["category_code"],
        location=row["location_code"], supplier_code=row["supplier_code"], unit_price=unit_price, gst_rate=rate,
        source="direct", source_ref=no,
    )  # fmt: skip
    result = ordering.OrderResult(request_no)
    req = dict(db.execute(text("select * from request where no = :n"), {"n": request_no}).mappings().one())
    ordering.place(db, tenant, req, [line], result, user(user_id), as_of=as_of)
    if not result.pos:
        db.rollback()
        raise UnorderedError("the order could not be made: " + "; ".join(result.skipped))
    po_no = result.pos[0]
    db.execute(
        text("update unordered_receipt set status = 'ordered', po_no = :p where no = :n and status = 'approved'"),
        {"p": po_no, "n": no},
    )
    audit.record(
        db, user(user_id), "unordered.order", "unordered_receipt", no, after={"po": po_no, "request": request_no}
    )
    db.commit()
    return po_no


def receive_on_order(db: Session, tenant: str, no: str, user_id: int, *, as_of: date) -> str:
    """Once the after-the-fact order is sent, the store posts the goods receipt on it: the goods enter stock now and the
    invoice matches as for any order. Returns the goods receipt number."""
    row = _row(db, no)
    if row["status"] != "ordered" or not row["po_no"]:
        raise UnorderedError(f"{no} is {row['status']}: the goods are received once their order is raised")
    status = db.execute(text("select status from po where no = :p"), {"p": row["po_no"]}).scalar_one()
    if status not in goods.RECEIVABLE:
        raise UnorderedError(
            f"{row['po_no']} is {status}: it must be approved and sent before the goods are received on it"
        )
    db.commit()  # release the row: posting the receipt locks the order and commits on its own
    posted = goods.post_receipt(
        db,
        row["po_no"],
        [goods.LineIn(1, Decimal(row["qty"]), serials=list(row["serials"] or []))],
        goods.Header(challan=row["challan"], remarks=f"Received without an order ({no})"),
        user_id,
        as_of=as_of,
        tenant=tenant,
    )
    db.execute(
        text("update unordered_receipt set status = 'received', gr_no = :g where no = :n and status = 'ordered'"),
        {"g": posted.no, "n": no},
    )
    audit.record(db, user(user_id), "unordered.receive", "unordered_receipt", no, after={"receipt": posted.no})
    db.commit()
    return posted.no


def listing(db: Session, *, status: str | None = None, open_only: bool = False) -> list[dict[str, Any]]:
    """Every receipt without an order, newest first: for the store's screen and the exceptions screen (and, later, the
    reconciliation). `open_only` keeps those someone still has to act on."""
    # a proposal rejected on the general approvals screen leaves the row pending: it counts as rejected
    rows = db.execute(
        text(
            "select * from (select u.*, case when u.status = 'pending' and p.status in ('rejected', 'expired') "
            "then 'rejected' else u.status end as state, i.name as item_name, "
            "coalesce(s.trade_name, s.legal_name) as supplier, p.status as proposal_status, o.status as po_status "
            "from unordered_receipt u join item i on i.code = u.item_code join supplier s on s.code = u.supplier_code "
            "left join proposal p on p.id = u.proposal_id left join po o on o.no = u.po_no) x "
            "where (cast(:s as text) is null or x.state = :s) and (not :o or x.state = any(:open)) "
            "order by x.created_at desc, x.no desc limit 500"
        ),
        {"s": status, "o": open_only, "open": list(OPEN)},
    ).mappings()
    return [{**dict(r), "status": r["state"]} for r in rows]
