"""Service entry: the person who asked for a service confirms that it was done, fully or in part.

The confirmation is the service's receipt (a service entry sheet, numbered SRN-<location>-<FY>-<seq>). It is made by
quantity, by a percentage of the ordered quantity, or by amount for a lump-sum line, in the app or from an emailed link
(receiving/service_links.py). What is confirmed counts as received on the order line, so the supplier's invoice is matched
against the order and the confirmation (a two-way match: there is no goods receipt). More than the order plus the
company's over-delivery tolerance (`receipt_over_pct`) is refused, as for goods.

Confirming is the requester's own duty, so the requester may do it; the buyer and the order's approvers may not, and the
person who confirmed may not later release the invoice's block (invoices/service.py).
"""

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from p2p.approvals.workflow import name_of, org_code_of
from p2p.formulas import matching
from p2p.formulas import receiving as rules
from p2p.platform import audit, numbering
from p2p.platform.sod import Actor, user
from p2p.receiving import goods
from p2p.receiving.kinds import service_lines

REMATCH = Actor("service", "service_entry")


class ServiceEntryError(Exception):
    pass


@dataclass
class Confirmation:
    po_no: str
    po_line_no: int
    basis: str  # qty, pct or amount
    value: Decimal
    period_from: date | None = None
    period_to: date | None = None
    remarks: str | None = None


@dataclass
class Confirmed:
    no: str
    po_no: str
    po_line_no: int
    qty: Decimal
    amount: Decimal
    po_status: str
    rematched: list[str] = field(default_factory=list)  # blocked invoices matched again after the confirmation


_LINE = """
    select l.*, r.requester, r.on_behalf_of, coalesce(i.category_code, nullif(rl.category_code, '')) as category_code
    from po_line l left join request r on r.no = l.request_no
    left join request_line rl on rl.request_no = l.request_no and rl.line_no = l.request_line_no
    left join item i on i.code = l.item_code
    where l.po_no = :p and l.line_no = :l
"""


def asked_by(line: dict[str, Any]) -> set[str]:
    """Who may confirm the line: the person who asked for it, and the person it was asked for."""
    return {str(c) for c in (line["requester"], line["on_behalf_of"]) if c}


def confirm(db: Session, c: Confirmation, user_id: int, *, as_of: date, via: str = "app") -> Confirmed:
    """Record that the service on an order line was done (fully or in part) and count it as received."""
    found = db.execute(text("select * from po where no = :n for update"), {"n": c.po_no}).mappings().first()
    if found is None:
        raise ServiceEntryError(f"{c.po_no} does not exist")
    po = dict(found)
    if po["status"] not in goods.RECEIVABLE:
        raise ServiceEntryError(f"{c.po_no} is {po['status']}: a service is confirmed on an order that was sent")
    row = db.execute(text(_LINE + " for update of l"), {"p": c.po_no, "l": c.po_line_no}).mappings().first()
    if row is None or row["status"] not in ("open", "part_received"):
        raise ServiceEntryError(f"line {c.po_line_no} is not open on {c.po_no}")
    line = dict(row)
    if c.po_line_no not in service_lines(db, c.po_no):
        raise ServiceEntryError(f"line {c.po_line_no} ({line['description']}) is goods: the store receives it")
    me = org_code_of(db, user_id)
    if me is None or me not in asked_by(line):
        who = " or ".join(name_of(db, x) for x in sorted(asked_by(line))) or "nobody (the line has no requester)"
        raise ServiceEntryError(f"only the person who asked for the service confirms it: {who}")
    conflict = goods.conflict_of(db, po, user_id, requester_may=True)
    if conflict:
        raise ServiceEntryError(
            f"segregation of duties: {conflict.replace('receive the goods', 'confirm the service')}"
        )
    if c.period_from and c.period_to and c.period_to < c.period_from:
        raise ServiceEntryError("the period ends before it starts")
    ordered, price = Decimal(line["qty"]), Decimal(line["unit_price"])
    room = rules.room_left(ordered, Decimal(line["received_qty"]), goods.over_pct(db, line["category_code"]))
    try:
        done = rules.confirm_service(c.basis, c.value, ordered, price, room)
    except ValueError as exc:
        raise ServiceEntryError(f"line {c.po_line_no} ({line['description']}): {exc}") from None

    no = numbering.next_number(db, "service_receipt", as_of, location_code=po["ship_to_location"])
    db.execute(
        text(
            "insert into service_entry (no, po_no, po_line_no, period_from, period_to, basis, value, qty_accepted, "
            "amount_accepted, accepted_by, accepted_on, via, remarks, posted_by) values "
            "(:no, :p, :l, :f, :t, :b, :v, :q, :a, :by, :on, :via, :rem, :u)"
        ),
        {
            "no": no, "p": c.po_no, "l": c.po_line_no, "f": c.period_from, "t": c.period_to, "b": c.basis,
            "v": c.value, "q": done.qty, "a": done.amount, "by": me, "on": as_of, "via": via,
            "rem": (c.remarks or "").strip() or None, "u": str(user_id),
        },
    )  # fmt: skip
    goods.count_accepted(db, no, po, line, done.qty, as_of)
    status = goods.refresh_order(db, c.po_no)
    audit.record(
        db,
        user(user_id),
        "service.confirm",
        "service_entry",
        no,
        after={
            "po": c.po_no, "line": c.po_line_no, "basis": c.basis, "value": str(c.value), "qty": str(done.qty),
            "amount": str(done.amount), "via": via, "po_status": status,
        },
    )  # fmt: skip
    db.commit()
    return Confirmed(no, c.po_no, c.po_line_no, done.qty, done.amount, status, _rematch(db, c.po_no, c.po_line_no))


def _rematch(db: Session, po_no: str, line_no: int) -> list[str]:
    """Invoices held because the service was not confirmed are matched again now (the same rules, nothing overridden)."""
    from p2p.invoices import service as invoices  # noqa: PLC0415 - invoices builds on receiving

    held = [
        r[0]
        for r in db.execute(
            text(
                "select distinct i.no from invoice i join invoice_line l on l.invoice_no = i.no "
                "join match_result m on m.invoice_no = i.no and m.resolved_at is null and m.reason_codes = :why "
                "where i.status = 'blocked' and l.po_no = :p and l.po_line_no = :l order by i.no"
            ),
            {"why": matching.SERVICE, "p": po_no, "l": line_no},
        )
    ]
    return [no for no in held if invoices.match_invoice(db, no, REMATCH).status == "approved"]


_OPEN = """
    select l.po_no, l.line_no, l.description, l.item_code, l.qty, l.uom, l.unit_price, l.received_qty, l.need_by,
           p.status as po_status, p.ship_to_location, coalesce(s.trade_name, s.legal_name) as supplier,
           r.requester, r.on_behalf_of
    from po_line l join po p on p.no = l.po_no join supplier s on s.code = p.supplier_code
    left join request r on r.no = l.request_no
    where p.status = any(:st) and l.status in ('open', 'part_received')
    order by p.sent_at nulls last, l.po_no, l.line_no
"""


def open_lines(db: Session, *, for_person: str | None = None) -> list[dict[str, Any]]:
    """Service lines on sent orders that still wait for confirmation; only those a person asked for, when one is named."""
    out: list[dict[str, Any]] = []
    services: dict[str, set[int]] = {}
    for r in db.execute(text(_OPEN), {"st": list(goods.RECEIVABLE)}).mappings():
        if for_person is not None and for_person not in asked_by(dict(r)):
            continue
        if r["po_no"] not in services:
            services[r["po_no"]] = service_lines(db, r["po_no"])
        if r["line_no"] not in services[r["po_no"]]:
            continue
        price, ordered, done = Decimal(r["unit_price"]), Decimal(r["qty"]), Decimal(r["received_qty"])
        out.append(
            {
                **dict(r),
                "amount": (ordered * price).quantize(Decimal("0.01")),
                "confirmed_amount": (done * price).quantize(Decimal("0.01")),
                "left": max(ordered - done, Decimal(0)),
            }
        )
    return out


def entries(db: Session, *, po_no: str | None = None, limit: int = 200) -> list[dict[str, Any]]:
    """Service entries, newest first (all of them, or one order's). Reconciliation counts them as receipts."""
    return [
        dict(r)
        for r in db.execute(
            text(
                "select e.*, l.description, l.uom, coalesce(s.trade_name, s.legal_name) as supplier, u.name as accepted_by_name "
                "from service_entry e join po_line l on l.po_no = e.po_no and l.line_no = e.po_line_no "
                "join po p on p.no = e.po_no join supplier s on s.code = p.supplier_code "
                "left join org_user u on u.code = e.accepted_by where (cast(:p as text) is null or e.po_no = :p) "
                "order by e.created_at desc, e.no desc limit :n"
            ),
            {"p": po_no, "n": limit},
        ).mappings()
    ]
