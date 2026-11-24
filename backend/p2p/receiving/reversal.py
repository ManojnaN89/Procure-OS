"""Reversing a receipt, or a service entry: the goods came in on the wrong order, or the confirmation was wrong (3.6).

A store person who is not the order's buyer, an approver or its requester reverses a receipt inside the correction window
(`grn_edit_window_days` working days on the receiving place's calendar, OP-14). Later it is a proposal that a person with
`grn.unordered_approve` who did not propose it approves. The whole receipt is reversed (OP-13), in one transaction: stock
out of its qualities, holds closed, reservations given back, the order and its budget reduced, the serials marked, and
the order back to sent (or the version the supplier acknowledged) when nothing is received any more. It is refused when
an invoice would be left unsupported, when later acts already used the goods, or when the order is closed or cancelled
(OP-15, OP-16). A service entry is reversed by the person who confirmed it or asked for it, with the same window and the
same invoice check (OP-21).
"""

from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from p2p.approvals.workflow import org_code_of
from p2p.formulas.calendar import IST, business_today
from p2p.formulas.money import format_qty
from p2p.formulas.words import count
from p2p.orders import document as po_document
from p2p.platform import audit, outbox, proposals, reference, settings
from p2p.platform.sod import Actor, user
from p2p.receiving import goods
from p2p.receiving import stock as stock_mod

ACTION = "receipt.reverse"
DEFAULT_WINDOW = "3"


class ReversalError(Exception):
    """The reversal makes no sense for this receipt: `status` is the HTTP answer (404, 409, 422)."""

    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status


def window_days(db: Session) -> int:
    return int(settings.number(db, "grn_edit_window_days", DEFAULT_WINDOW))


def window_end(db: Session, posted_on: date, location: str) -> date:
    """The last day a receipt may be reversed at once: its posting day plus the setting's working days, on the place's
    own working days (PLT-CKN works Saturdays, so a Thursday receipt has until Monday)."""
    return reference.calendar_for_location(db, location).add_working_days(posted_on, window_days(db))


def _posted_on(gr: dict[str, Any]) -> date:
    stamp = gr["gate_entry_at"]
    return stamp.astimezone(IST).date() if isinstance(stamp, datetime) else date.fromisoformat(str(stamp))


def _receipt(db: Session, no: str) -> dict[str, Any]:
    row = db.execute(text("select * from goods_receipt where no = :n for update"), {"n": no}).mappings().first()
    if row is None:
        raise ReversalError(404, f"{no} does not exist")
    return dict(row)


def _invoices_of(db: Session, po_no: str, line_no: int) -> list[str]:
    return [
        r[0]
        for r in db.execute(
            text(
                "select distinct i.no from invoice i join invoice_line il on il.invoice_no = i.no "
                "where il.po_no = :p and il.po_line_no = :l and i.status in ('approved', 'part_paid', 'paid') "
                "order by i.no"
            ),
            {"p": po_no, "l": line_no},
        )
    ]


def _refuse_used(db: Session, gr: dict[str, Any]) -> None:
    """Goods already returned, scrapped or decided by a person, or an excess decided: the reversal is refused (OP-16)."""
    used = db.execute(
        text(
            "select 1 from stock_hold h where h.gr_no = :g and h.closed_by_doc is not null "
            "and h.closed_by_doc <> :g limit 1"
        ),
        {"g": gr["no"]},
    ).first()
    if used:
        raise ReversalError(
            409,
            f"goods of {gr['no']} were already returned or scrapped: reverse is no longer possible; correct with a return",
        )
    decided = db.execute(
        text(
            "select 1 from stock_hold h where h.gr_no = :g and h.source = 'decision' and exists "
            "(select 1 from stock_movement m where m.ref_doc = 'HOLD-' || h.id) limit 1"
        ),
        {"g": gr["no"]},
    ).first()
    if decided:
        raise ReversalError(
            409, f"the goods of {gr['no']} were decided by a person after the inspection: correct with a return"
        )
    excess = db.execute(
        text(
            "select 1 from receipt_excess where gr_no = :g and status in ('proposed', 'accepted', 'returned') limit 1"
        ),
        {"g": gr["no"]},
    ).first()
    if excess:
        raise ReversalError(409, f"goods above {gr['po_no']} on {gr['no']} were already decided: correct with a return")


def _lines(db: Session, gr_no: str) -> list[dict[str, Any]]:
    return [
        dict(r)
        for r in db.execute(text("select * from gr_line where gr_no = :g order by po_line_no"), {"g": gr_no}).mappings()
    ]


def _check_invoices(db: Session, po: dict[str, Any], gr: dict[str, Any], lines: list[dict[str, Any]]) -> None:
    order_lines = {
        r["line_no"]: dict(r)
        for r in db.execute(
            text("select * from po_line where po_no = :n order by line_no for update"), {"n": po["no"]}
        ).mappings()
    }
    for line in lines:
        counted = max(Decimal(line["qty_accepted"] or 0) - Decimal(line["qty_excess"] or 0), Decimal(0))
        left = max(Decimal(order_lines[line["po_line_no"]]["received_qty"]) - counted, Decimal(0))
        invoiced = Decimal(order_lines[line["po_line_no"]]["invoiced_qty"])
        if invoiced > left:
            invoices = _invoices_of(db, po["no"], int(line["po_line_no"]))
            raise ReversalError(
                409,
                f"line {line['po_line_no']}: {format_qty(invoiced)} is invoiced ({', '.join(invoices) or 'an invoice'}); "
                f"reversing would leave {format_qty(left)} received",
            )


def _reverse(
    db: Session,
    tenant: str | None,
    gr: dict[str, Any],
    po: dict[str, Any],
    actor: Actor,
    reason: str,
    as_of: date,
    *,
    stock: bool = True,
) -> None:
    """The writes of a reversal, all in one transaction (the caller commits)."""
    lines = _lines(db, gr["no"])
    _check_invoices(db, po, gr, lines)
    order_lines = {
        r["line_no"]: dict(r)
        for r in db.execute(
            text("select * from po_line where po_no = :n order by line_no for update"), {"n": po["no"]}
        ).mappings()
    }
    places: set[tuple[str, str]] = set()
    for line in lines:
        row = order_lines[line["po_line_no"]]
        item = (
            db.execute(
                text(
                    "select i.code, i.stocked, coalesce(i.base_uom, i.purchase_uom), i.name from item i where i.code = :c"
                ),
                {"c": row["item_code"]},
            )
            .mappings()
            .first()
        )
        ratio = Decimal(row["base_qty"]) / Decimal(row["qty"]) if row["base_qty"] else Decimal(1)
        counted = max(Decimal(line["qty_accepted"] or 0) - Decimal(line["qty_excess"] or 0), Decimal(0))
        if stock and item is not None and item["stocked"] and counted > 0:
            units = (counted * ratio).quantize(Decimal("0.0001"))
            have = (
                db.execute(
                    text("select coalesce(on_hand, 0) from stock_level where item_code = :i and location_code = :l"),
                    {"i": item["code"], "l": gr["location_code"]},
                ).scalar()
                or 0
            )
            if Decimal(have) < units:
                name = po_document.delivery_place(db, gr["location_code"])[0]
                raise ReversalError(
                    409,
                    f"{item['code']}: only {format_qty(have)} usable is at {name}, so {format_qty(units)} cannot be taken back",
                )
            db.execute(
                text(
                    "insert into stock_movement (id, item_code, location_code, date, type, qty, unit_cost, ref_doc, "
                    "source, quality) values (:id, :i, :l, :d, 'receipt_reversal', :q, :c, :r, 'platform', 'usable')"
                ),
                {
                    "id": f"SM-REV-{gr['no']}-{line['po_line_no']}", "i": item["code"], "l": gr["location_code"],
                    "d": as_of, "q": units,
                    "c": Decimal(row["unit_price"]) / ratio if ratio else Decimal(row["unit_price"]), "r": gr["no"],
                },
            )  # fmt: skip
            places.add((str(item["code"]), str(gr["location_code"])))
        excess = Decimal(line["qty_excess"] or 0)
        waiting = Decimal(line["qty_received"]) if line["qty_accepted"] is None else Decimal(0)
        for qty, quality in ((excess, "quarantine"), (waiting, "quarantine")):
            if stock and qty > 0 and item is not None and item["stocked"]:
                db.execute(
                    text(
                        "insert into stock_movement (id, item_code, location_code, date, type, qty, unit_cost, ref_doc, "
                        "source, quality) values (:id, :i, :l, :d, 'receipt_reversal', :q, NULL, :r, 'platform', :k)"
                    ),
                    {
                        "id": f"SM-REV-{gr['no']}-{line['po_line_no']}-{quality[:1].upper()}{'' if waiting == 0 else 'W'}",
                        "i": item["code"], "l": gr["location_code"], "d": as_of, "q": (qty * ratio).quantize(
                            Decimal("0.0001")), "r": gr["no"], "k": quality,
                    },
                )  # fmt: skip
                places.add((str(item["code"]), str(gr["location_code"])))
        if counted > 0:
            goods.uncount(db, po, row, counted, as_of, source="receipt_reversal", gr_no=gr["no"], actor=actor)
    goods.refresh_order(db, po["no"], fallback_to=goods.order_fallback(po))
    for place_item, place_location in sorted(places):
        on_hand = (
            db.execute(
                text("select coalesce(on_hand, 0) from stock_level where item_code = :i and location_code = :l"),
                {"i": place_item, "l": place_location},
            ).scalar()
            or 0
        )
        from p2p.requests import reservation  # noqa: PLC0415 - stock leaving gives reservations back

        held = stock_mod.reserved(db, place_item, place_location)
        over = held - Decimal(on_hand)
        if over > 0:
            reservation.release_latest(
                db, actor, place_item, place_location, over,
                f"{format_qty(over)} set aside released by the reversal of {gr['no']}",
            )  # fmt: skip
    db.execute(
        text("update serial_number set status = 'reversed' where gr_no = :g and status in ('received', 'rejected')"),
        {"g": gr["no"]},
    )
    db.execute(
        text(
            "update stock_hold set left_qty = 0, closed_at = now(), closed_by_doc = :g where gr_no = :g and closed_at is null"
        ),
        {"g": gr["no"]},
    )
    db.execute(
        text("update receipt_excess set status = 'reversed' where gr_no = :g and status = 'waiting'"),
        {"g": gr["no"]},
    )
    db.execute(
        text("update unordered_receipt set status = 'ordered', gr_no = null where gr_no = :g and status = 'received'"),
        {"g": gr["no"]},
    )
    db.execute(
        text(
            "update goods_receipt set status = 'reversed', reversed_on = :d, reversed_by = :by, reversal_reason = :why "
            "where no = :n"
        ),
        {"d": as_of, "by": org_code_of(db, int(actor.id)) if str(actor.id).isdigit() else actor.identity,
         "why": reason, "n": gr["no"]},
    )  # fmt: skip
    audit.record(
        db, actor if actor.kind == "user" else audit.SYSTEM, "grn.reverse", "goods_receipt", gr["no"],
        before={"status": gr["status"], "po_status": po["status"]},
        after={"status": "reversed", "lines": [line["po_line_no"] for line in lines],
               "serial": "reversed"},
        reason=reason,
    )  # fmt: skip


def reverse_receipt(
    db: Session, tenant: str | None, no: str, user_id: int, reason: str, *, as_of: date, force: bool = False,
    erp: bool = False,
) -> dict[str, Any]:  # fmt: skip
    """Reverse a receipt: at once inside the window, else as a proposal (`force` skips the window for an ERP import's
    `reverses` row, `erp` lets the ERP reverse its own receipt). Commits. Returns {"status": "reversed"} or
    {"proposal_id": id}."""
    why = (reason or "").strip()
    if not why:
        raise ReversalError(422, "say why the receipt is reversed")
    gr = _receipt(db, no)
    if gr["status"] == "reversed":
        raise ReversalError(409, f"{no} is already reversed")
    if gr["source"] != "platform" and not erp:
        raise ReversalError(409, f"{no} came from the ERP: reverse it there; the reversal comes with the next import")
    po = dict(db.execute(text("select * from po where no = :n"), {"n": gr["po_no"]}).mappings().one())
    if po["status"] in ("closed", "cancelled"):
        raise ReversalError(409, f"{po['no']} is {po['status']}: its receipt cannot be reversed")
    conflict = goods.conflict_of(db, po, user_id)
    if conflict:
        raise ReversalError(
            409, f"segregation of duties: {conflict.replace('receive the goods', 'reverse the receipt')}"
        )
    _refuse_used(db, gr)
    lines = _lines(db, no)
    _check_invoices(db, po, gr, lines)
    posted = _posted_on(gr)
    last = window_end(db, posted, gr["location_code"])
    if not force and as_of > last:
        db.rollback()
        pid = proposals.propose(
            db,
            user(user_id),
            action=ACTION,
            summary=(
                f"Reverse receipt {no} of {gr['po_no']} posted on {posted:%d %b %Y}, after the "
                f"{count(window_days(db), 'working day')} allowed. Reason: {why}"
            ),
            payload={"receipt": no, "reason": why, "posted_on": posted.isoformat(), "window_end": last.isoformat()},
            required_permission="grn.unordered_approve",
            risk="medium",
        )
        db.execute(text("update goods_receipt set reversal_proposal_id = :p where no = :n"), {"p": pid, "n": no})
        db.commit()
        return {"proposal_id": pid}
    actor = user(user_id)
    places = {
        (str(r[0]), gr["location_code"])
        for r in db.execute(
            text(
                "select distinct l.item_code from gr_line g join po_line l on l.po_no = g.po_no "
                "and l.line_no = g.po_line_no join item i on i.code = l.item_code "
                "where g.gr_no = :n and i.stocked"
            ),
            {"n": no},
        )
    }
    _reverse(db, tenant, gr, po, actor, why, as_of, stock=not erp)
    db.commit()
    if tenant:
        goods.tell_reversed(db, tenant, no, why)
        from p2p.requests import material  # noqa: PLC0415 - the store check runs for the place

        try:
            material.check_after(db, tenant, sorted(places), why=f"reversal of {no}")
        except Exception:  # noqa: BLE001 - a store check never undoes the reversal
            db.rollback()
    return {"status": "reversed"}


def _reverse_by_proposal(db: Session, payload: dict[str, Any], actor: Actor) -> dict[str, Any]:
    no = str(payload["receipt"])
    gr = _receipt(db, no)
    if gr["status"] == "reversed":
        raise ReversalError(409, f"{no} is already reversed")
    if gr["source"] != "platform":
        raise ReversalError(409, f"{no} came from the ERP: reverse it there")
    po = dict(db.execute(text("select * from po where no = :n"), {"n": gr["po_no"]}).mappings().one())
    if po["status"] in ("closed", "cancelled"):
        raise ReversalError(409, f"{po['no']} is {po['status']}: its receipt cannot be reversed")
    _refuse_used(db, gr)
    _check_invoices(db, po, gr, _lines(db, no))
    reason = str(payload.get("reason") or "").strip()
    _reverse(db, outbox.tenant_of(db), gr, po, actor, reason, business_today())
    db.commit()
    return {"status": "reversed", "receipt": no}


@proposals.executor(ACTION)
def _executor(db: Session, payload: dict[str, Any], actor: Actor) -> dict[str, Any]:
    if payload.get("service"):
        return _reverse_service_by_proposal(db, payload, actor)
    return _reverse_by_proposal(db, payload, actor)


def reverse_service_entry(
    db: Session, tenant: str | None, no: str, user_id: int, reason: str, *, as_of: date
) -> dict[str, Any]:
    """Reverse a service entry: the person who confirmed it or who asked for the service, same window and invoice
    check, no stock or budget effect (services are spent at payment)."""
    why = (reason or "").strip()
    if not why:
        raise ReversalError(422, "say why the service confirmation is reversed")
    entry = db.execute(text("select * from service_entry where no = :n for update"), {"n": no}).mappings().first()
    if entry is None:
        raise ReversalError(404, f"{no} does not exist")
    e = dict(entry)
    if e["status"] == "reversed":
        raise ReversalError(409, f"{no} is already reversed")
    po = dict(db.execute(text("select * from po where no = :n"), {"n": e["po_no"]}).mappings().one())
    line = dict(
        db.execute(
            text(
                "select l.*, r.requester, r.on_behalf_of from po_line l left join request r on r.no = l.request_no "
                "where l.po_no = :p and l.line_no = :l"
            ),
            {"p": e["po_no"], "l": e["po_line_no"]},
        )
        .mappings()
        .one()
    )
    me = org_code_of(db, user_id)
    allowed = {str(c) for c in (line["requester"], line["on_behalf_of"]) if c} | {str(e["accepted_by"])}
    if me is None or me not in allowed:
        raise ReversalError(403, "only the person who confirmed the service, or who asked for it, reverses it")
    left = max(Decimal(line["received_qty"]) - Decimal(e["qty_accepted"]), Decimal(0))
    if Decimal(line["invoiced_qty"]) > left:
        invoices = _invoices_of(db, e["po_no"], e["po_line_no"])
        raise ReversalError(
            409,
            f"line {e['po_line_no']}: {format_qty(line['invoiced_qty'])} is invoiced "
            f"({', '.join(invoices) or 'an invoice'}); reversing would leave {format_qty(left)} received",
        )
    posted = e["accepted_on"]
    last = window_end(db, posted, po["ship_to_location"])
    if as_of > last:
        db.rollback()
        pid = proposals.propose(
            db, user(user_id), action=ACTION,
            summary=f"Reverse service entry {no} of {e['po_no']} confirmed on {posted:%d %b %Y}, after the "
                    f"{count(window_days(db), 'working day')} allowed. Reason: {why}",
            payload={"service": no, "reason": why}, required_permission="grn.unordered_approve", risk="medium",
        )  # fmt: skip
        db.execute(text("update service_entry set reversal_proposal_id = :p where no = :n"), {"p": pid, "n": no})
        db.commit()
        return {"proposal_id": pid}
    _reverse_service(db, e, po, line, user(user_id), why, as_of)
    db.commit()
    return {"status": "reversed"}


def _reverse_service(
    db: Session, entry: dict[str, Any], po: dict[str, Any], line: dict[str, Any], actor: Actor, reason: str, as_of: date
) -> None:
    qty = Decimal(entry["qty_accepted"])
    left = max(Decimal(line["received_qty"]) - qty, Decimal(0))
    from p2p.formulas import receiving as rules

    db.execute(
        text("update po_line set received_qty = :t, status = :s where po_no = :p and line_no = :l"),
        {"t": left, "s": rules.line_status(Decimal(line["qty"]), left), "p": po["no"], "l": line["line_no"]},
    )
    goods.refresh_order(db, po["no"], fallback_to=goods.order_fallback(po))
    db.execute(
        text(
            "update service_entry set status = 'reversed', reversed_on = :d, reversed_by = :by, reversal_reason = :why "
            "where no = :n"
        ),
        {"d": as_of, "by": org_code_of(db, int(actor.id)) if str(actor.id).isdigit() else actor.identity,
         "why": reason, "n": entry["no"]},
    )  # fmt: skip
    audit.record(
        db, actor if actor.kind == "user" else audit.SYSTEM, "service.reverse", "service_entry", entry["no"],
        before={"status": entry["status"]}, after={"status": "reversed", "qty": str(qty), "po": po["no"]},
        reason=reason,
    )  # fmt: skip


def _reverse_service_by_proposal(db: Session, payload: dict[str, Any], actor: Actor) -> dict[str, Any]:
    no = str(payload["service"])
    entry = db.execute(text("select * from service_entry where no = :n for update"), {"n": no}).mappings().first()
    if entry is None:
        raise ReversalError(404, f"{no} does not exist")
    e = dict(entry)
    if e["status"] == "reversed":
        raise ReversalError(409, f"{no} is already reversed")
    po = dict(db.execute(text("select * from po where no = :n"), {"n": e["po_no"]}).mappings().one())
    line = dict(
        db.execute(
            text("select * from po_line where po_no = :p and line_no = :l"),
            {"p": e["po_no"], "l": e["po_line_no"]},
        )
        .mappings()
        .one()
    )
    left = max(Decimal(line["received_qty"]) - Decimal(e["qty_accepted"]), Decimal(0))
    if Decimal(line["invoiced_qty"]) > left:
        invoices = _invoices_of(db, e["po_no"], e["po_line_no"])
        raise ReversalError(
            409,
            f"line {e['po_line_no']}: {format_qty(line['invoiced_qty'])} is invoiced ({', '.join(invoices) or 'an invoice'}); "
            f"reversing would leave {format_qty(left)} received",
        )
    _reverse_service(db, e, po, line, actor, str(payload.get("reason") or ""), business_today())
    db.commit()
    return {"status": "reversed", "entry": no}
