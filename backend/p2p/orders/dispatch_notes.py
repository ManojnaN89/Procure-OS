"""Dispatch notes: what the supplier says it has shipped against an order, so the store expects it.

A supplier records a dispatch from the link it gets once it has accepted the order, or says so in a reply that the
supplier-mail agent reads (a model copies the quantities and numbers; code checks them against the order). A note has the
quantity per line, the date shipped, the vehicle or LR/AWB number, the supplier's invoice number if there is one, and
when it should arrive. A note may be for part of what is still to come: then the rest stays open and the supplier gets a
link for the next one. A note never receives anything: the store counts the goods when they arrive.
"""

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from p2p.formulas.calendar import business_today
from p2p.formulas.money import format_qty
from p2p.orders import supplier_links
from p2p.platform import audit, notifications
from p2p.platform.sod import Actor

OPEN_STATUSES = ("sent", "acknowledged", "part_received")


class DispatchNoteError(Exception):
    pass


@dataclass
class Recorded:
    note_id: int
    still_to_ship: dict[int, Decimal] = field(default_factory=dict)  # line -> quantity not yet shipped or received

    @property
    def partial(self) -> bool:
        return any(q > 0 for q in self.still_to_ship.values())


def to_ship(db: Session, po_no: str) -> dict[int, dict[str, Any]]:
    """Open lines with what is still to ship: ordered less the larger of what was received and what notes say was sent."""
    rows = db.execute(
        text(
            "select l.line_no, l.description, l.uom, l.qty, l.received_qty, l.short_closed_qty, "
            "coalesce((select sum(d.qty) from dispatch_note_line d join dispatch_note n on n.id = d.note_id "
            "where n.po_no = l.po_no and d.po_line_no = l.line_no), 0) as shipped "
            "from po_line l where l.po_no = :p and l.status in ('open', 'part_received') order by l.line_no"
        ),
        {"p": po_no},
    ).mappings()
    out: dict[int, dict[str, Any]] = {}
    for r in rows:
        left = (
            Decimal(r["qty"]) - Decimal(r["short_closed_qty"]) - max(Decimal(r["received_qty"]), Decimal(r["shipped"]))
        )
        out[int(r["line_no"])] = {**dict(r), "left": max(left, Decimal(0))}
    return out


def record(
    db: Session,
    tenant: str,
    po_no: str,
    quantities: dict[int, Decimal],
    *,
    shipped_on: date,
    actor: Actor,
    source: str,
    expected_on: date | None = None,
    vehicle: str | None = None,
    lr_no: str | None = None,
    invoice_no: str | None = None,
    email_id: int | None = None,
    link: supplier_links.Link | None = None,
) -> Recorded:
    """Store a dispatch note against the order and tell the store and the buyer. With `link`, the link is used up."""
    po = db.execute(text("select status, buyer from po where no = :n for update"), {"n": po_no}).first()
    if po is None:
        raise DispatchNoteError(f"{po_no} does not exist")
    if po[0] not in OPEN_STATUSES:
        db.rollback()
        raise DispatchNoteError(f"{po_no} is {po[0]}: nothing is waiting to be shipped")
    if link is not None:
        try:
            supplier_links.check(db, link, lock=True)
        except supplier_links.LinkError:
            db.rollback()
            raise
    open_lines = to_ship(db, po_no)
    wanted = {n: q for n, q in quantities.items() if q is not None and q != 0}
    problem = _problem(wanted, open_lines, shipped_on, expected_on)
    if problem:
        db.rollback()
        raise DispatchNoteError(problem)
    note_id = int(
        db.execute(
            text(
                "insert into dispatch_note (po_no, shipped_on, expected_on, vehicle_no, lr_no, invoice_no, source, email_id, "
                "recorded_by) values (:p, :s, :e, :v, :lr, :inv, :src, :em, :by) returning id"
            ),
            {
                "p": po_no, "s": shipped_on, "e": expected_on, "v": _clean(vehicle), "lr": _clean(lr_no),
                "inv": _clean(invoice_no), "src": source, "em": email_id, "by": actor.id,
            },
        ).scalar_one()
    )  # fmt: skip
    for line_no, qty in wanted.items():
        db.execute(
            text("insert into dispatch_note_line (note_id, po_line_no, qty) values (:n, :l, :q)"),
            {"n": note_id, "l": line_no, "q": qty},
        )
    still = {n: ln["left"] - wanted.get(n, Decimal(0)) for n, ln in open_lines.items()}
    if link is not None:
        supplier_links.use(db, link, "dispatch")
    audit.record(
        db, actor, "po.dispatch_note", "po", po_no,
        after={
            "note": note_id, "lines": {str(n): q for n, q in wanted.items()}, "shipped_on": shipped_on,
            "expected_on": expected_on, "vehicle": vehicle, "lr_no": lr_no, "invoice_no": invoice_no, "source": source,
            "still_to_ship": {str(n): q for n, q in still.items()},
        },
    )  # fmt: skip
    db.commit()
    _tell_store(db, tenant, po_no, open_lines, wanted, shipped_on, expected_on)
    return Recorded(note_id, still)


def _clean(value: str | None) -> str | None:
    return (value or "").strip()[:60] or None


def _problem(
    wanted: dict[int, Decimal], open_lines: dict[int, dict[str, Any]], shipped_on: date, expected_on: date | None
) -> str | None:
    if not wanted:
        return "say how much was shipped on at least one line"
    for line_no, qty in wanted.items():
        line = open_lines.get(line_no)
        if line is None:
            return f"line {line_no} is not an open line of the order"
        if qty <= 0:
            return f"line {line_no}: the quantity shipped must be more than zero"
        if qty > line["left"]:
            return f"line {line_no}: {format_qty(qty)} is more than the {format_qty(line['left'])} still to ship"
    if shipped_on > business_today():
        return "the date shipped cannot be in the future"
    if expected_on is not None and expected_on < shipped_on:
        return "the goods cannot arrive before they are shipped"
    return None


def _tell_store(
    db: Session,
    tenant: str,
    po_no: str,
    open_lines: dict[int, dict[str, Any]],
    wanted: dict[int, Decimal],
    shipped_on: date,
    expected_on: date | None,
) -> None:
    """Everyone who receives goods sees it in the app (no email: it is on their receiving page too)."""
    what = ", ".join(
        f"{format_qty(q)} {open_lines[n]['uom'] or ''} {open_lines[n]['description']}".replace("  ", " ")
        for n, q in wanted.items()
    )
    when = f", expected {expected_on:%d %b %Y}" if expected_on else ""
    receivers = [
        int(r[0])
        for r in db.execute(
            text(
                "select distinct ur.user_id from user_role ur join role_permission rp on rp.role = ur.role "
                "where rp.action = 'grn.create' order by 1"
            )
        )
    ]
    for user_id in receivers:
        notifications.notify(
            db, tenant, user_id, "order", f"On the way: {po_no}", f"Shipped {shipped_on:%d %b %Y}: {what}{when}.",
            link="#/receiving", email=False,
        )  # fmt: skip


def notes(db: Session, po_no: str) -> list[dict[str, Any]]:
    """The dispatch notes of an order, newest first, with their lines, for the order page and the store."""
    out: list[dict[str, Any]] = []
    for n in db.execute(
        text("select * from dispatch_note where po_no = :p order by shipped_on desc, id desc"), {"p": po_no}
    ).mappings():
        lines = db.execute(
            text(
                "select d.po_line_no, d.qty, l.description, l.uom from dispatch_note_line d "
                "join po_line l on l.po_no = :p and l.line_no = d.po_line_no where d.note_id = :n order by d.po_line_no"
            ),
            {"p": po_no, "n": n["id"]},
        ).mappings()
        out.append(
            {
                "id": n["id"],
                "shipped_on": n["shipped_on"].isoformat(),
                "expected_on": n["expected_on"].isoformat() if n["expected_on"] else None,
                "vehicle": n["vehicle_no"],
                "lr_no": n["lr_no"],
                "invoice_no": n["invoice_no"],
                "source": n["source"],
                "lines": [
                    {"line_no": r["po_line_no"], "name": r["description"], "qty": format_qty(r["qty"]), "uom": r["uom"]}
                    for r in lines
                ],
            }
        )
    return out
