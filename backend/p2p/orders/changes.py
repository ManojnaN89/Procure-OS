"""Changes to an order after it was made: a revision (a new version), cancelling it, and closing what is still to come.

* A revision changes the quantity or the need-by date of open lines, the delivery place, the payment terms or the
  delivery terms, or moves a contract line to the contract price in force. A buyer never types a price (a price comes
  from a contract or an award); only a price the supplier asked for and a person accepted (orders/confirmation.py) does.
  The supplier and the item are never changed (cancel and order again); tax is worked out again by code when the place
  changes. Every revision is a new version (orders/versions.py) with its own history row. It is measured from the last
  approved version (orders/approved_versions.py), so small changes add up: a material change (formulas/po_versions.py,
  Q19) goes through approval again on the new total; any other carries the approval. A supplier who already has the
  order gets the new version's document by email and acknowledges that version; a touchless order a person changed is
  no longer touchless (Rev s5).
* Cancelling is for an order nothing has been received or invoiced against. It gives back the budget commitment and the
  open supply, and either frees the lines to be ordered again or ends the need.
* Closing is for an order that was partly delivered: the part still to come is closed, so it no longer counts as supply or
  as a commitment.
All of it is a person's decision with a reason; every change writes an audit row.
"""

import json
import logging
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from p2p.approvals.workflow import name_of, notify_person
from p2p.formulas import po_versions
from p2p.formulas.calendar import IST, business_today
from p2p.formulas.gst import Registration
from p2p.formulas.money import format_inr, format_qty
from p2p.formulas.orders import LineAmounts, line_amounts, order_totals
from p2p.formulas.sourcing import pick_contract_price
from p2p.formulas.touchless import PRICE_LIST
from p2p.invoices import tolerances
from p2p.masters import regions
from p2p.masters import suppliers as supplier_records
from p2p.orders import approved_versions, dispatch, document, service, supplier_links, versions, workflow
from p2p.platform import audit, emails, notifications, outbox, proposals, tasks
from p2p.platform.sod import Actor, user
from p2p.sourcing import service as sourcing

log = logging.getLogger(__name__)
CANCELLABLE = ("draft", "submitted", "approved", "sent", "acknowledged")
CLOSABLE = ("sent", "acknowledged", "part_received")
REVISABLE = ("draft", "submitted", "approved", "sent", "acknowledged", "part_received")
WITH_SUPPLIER = ("sent", "acknowledged", "part_received")


class ChangeError(Exception):
    pass


class InputError(ChangeError):
    """The change asked for is wrong in itself (nothing to change, a value it already has, text too long): 422."""


@dataclass
class LineChange:
    line_no: int
    qty: Decimal | None = None
    need_by: date | None = None
    unit_price: Decimal | None = None  # only a supplier's change that a person accepted
    promised_date: date | None = None  # the supplier's new date, written with the need-by date it asked for
    reprice: bool = False  # move a contract line to the contract price in force today


@dataclass
class HeaderChange:
    """The header fields a buyer may change (OP-1): delivery place, payment terms (which carry any advance) and
    delivery terms. Supplier and item are never changed on an order (OP-3); tax follows the place (OP-5); the budget
    line stays as the request was approved (OP-4)."""

    ship_to_location: str | None = None
    payment_terms_code: str | None = None
    delivery_terms: str | None = None

    def empty(self) -> bool:
        return self.ship_to_location is None and self.payment_terms_code is None and self.delivery_terms is None


def _locked(db: Session, po_no: str) -> dict[str, Any]:
    row = db.execute(text("select * from po where no = :n for update"), {"n": po_no}).mappings().first()
    if row is None:
        raise ChangeError(f"{po_no} does not exist")
    return dict(row)


def _reason(reason: str | None) -> str:
    if not reason or not reason.strip():
        raise ChangeError("a reason is needed")
    return reason.strip()


def supplier_contact(db: Session, supplier_code: str) -> tuple[str, str | None] | None:
    """The supplier's order contact: address and name (masters/suppliers.order_contact)."""
    return supplier_records.order_contact(db, supplier_code)


def tell_supplier(
    db: Session,
    po: dict[str, Any],
    subject: str,
    body: str,
    attachments: list[tuple[str, str, bytes]] | None = None,
    *,
    document_ids: list[int] | None = None,
    po_version: int | None = None,
) -> bool:
    """Email the supplier's main contact: commits the mail and sends it (the change itself is already committed). False
    when there is no address; a refused mail stays queued for the email job and never undoes the change. `document_ids`
    are stored files attached as they are (a version's document); `po_version` is the version the mail carries, so a
    reply to it answers that version (OP-12)."""
    contact = supplier_contact(db, po["supplier_code"])
    if contact is None:
        return False
    company = db.execute(text("select coalesce(short_name, legal_name) from company limit 1")).scalar() or "Our company"
    message_id = emails.send_linked_email(
        db,
        contact[0],
        subject,
        f"Dear {contact[1] or 'Sir or Madam'},\n\n{body}\n\nRegards,\nProcurement, {company}\n",
        linked_type="po",
        linked_ref=po["no"],
        attachments=attachments,
        document_ids=document_ids,
    )
    if po_version is not None:
        versions.mail_carries(db, message_id, po_version)
    emails.commit_and_send(db)
    return True


def tell_people(db: Session, tenant: str | None, po_no: str, title: str, body: str, *, buyer: str | None) -> None:
    """The people who asked for the goods and the buyer hear about it (in the app and by email)."""
    codes = {
        r[0]
        for r in db.execute(
            text(
                "select distinct r.requester from po_line l join request r on r.no = l.request_no "
                "where l.po_no = :p and r.requester is not null"
            ),
            {"p": po_no},
        )
    }
    if buyer:
        codes.add(buyer)
    for code in sorted(codes):
        notify_person(db, tenant, code, "order", title, body, link=f"#/purchase-orders/{po_no}")


def _drop_open_decisions(db: Session, po: dict[str, Any], who: int, note: str) -> None:
    """An order that is cancelled no longer waits for anybody: its approval steps, their tasks and a hold proposal go."""
    steps = db.execute(
        text(
            "update po_approval_step set status = 'cancelled' where po_no = :p and status in ('pending', 'waiting') "
            "returning task_id"
        ),
        {"p": po["no"]},
    ).all()
    db.commit()
    for (task_id,) in steps:
        if task_id:
            tasks.cancel_task(db, int(task_id), who, reason=note)
    if po["hold_proposal_id"]:
        proposals.withdraw(db, int(po["hold_proposal_id"]), who, note)


# ---- cancel ----------------------------------------------------------------------------------


def cancel(
    db: Session, tenant: str | None, po_no: str, user_id: int, reason: str | None, *, reorder: bool = False
) -> dict[str, Any]:
    """Cancel an order nothing has been received or invoiced against.

    With `reorder` the lines are ready to be ordered again (a different supplier or price is wanted); without it the need
    itself is dropped and the request lines are cancelled. Either way the order's commitment and open supply are gone."""
    why = _reason(reason)
    po = _locked(db, po_no)
    if po["status"] == "part_received":
        db.rollback()
        raise ChangeError(f"goods have been received against {po_no}: close what is still to come instead")
    if po["status"] not in CANCELLABLE:
        db.rollback()
        raise ChangeError(
            f"{po_no} is {po['status']}; only an order that is not yet received or closed can be cancelled"
        )
    received = db.execute(
        text("select coalesce(sum(received_qty), 0) from po_line where po_no = :n and status <> 'cancelled'"),
        {"n": po_no},
    ).scalar_one()
    if received > 0:
        db.rollback()
        raise ChangeError(f"goods have been received against {po_no}: close what is still to come instead")
    billed = db.execute(
        text(
            "select count(*) from invoice_line il join invoice i on i.no = il.invoice_no "
            "where il.po_no = :n and i.status <> 'cancelled'"
        ),
        {"n": po_no},
    ).scalar_one()
    if billed:
        db.rollback()
        raise ChangeError(f"an invoice has been entered against {po_no}: it cannot be cancelled")
    was = po["status"]
    lines = db.execute(
        text("select request_no, request_line_no from po_line where po_no = :n and status <> 'cancelled'"),
        {"n": po_no},
    ).all()
    workflow.cancel_order(db, po_no, f"Cancelled by {name_of(db, _code(db, user_id))}: {why}")
    if not reorder:
        for request_no, line_no in lines:
            db.execute(
                text("update request_line set status = 'cancelled' where request_no = :n and line_no = :l"),
                {"n": request_no, "l": line_no},
            )
        for request_no in sorted({r[0] for r in lines}):
            open_left = db.execute(
                text("select count(*) from request_line where request_no = :n and status in ('open', 'sourcing')"),
                {"n": request_no},
            ).scalar_one()
            if open_left == 0:
                db.execute(text("update request set status = 'closed' where no = :n"), {"n": request_no})
    audit.record(
        db, user(user_id), "po.cancel", "po", po_no,
        before={"status": was}, after={"status": "cancelled", "reorder": reorder}, reason=why,
    )  # fmt: skip
    db.commit()
    _drop_open_decisions(db, po, user_id, f"{po_no} was cancelled")
    dispatch.close_ack_tasks(db, po_no, "cancelled")
    told = False
    if was in WITH_SUPPLIER:
        told = tell_supplier(
            db,
            po,
            f"Purchase order {po_no} is cancelled",
            f"Please do not supply against order {po_no}. It is cancelled. Reason: {why}",
        )
    tell_people(
        db,
        tenant,
        po_no,
        f"Order {po_no} was cancelled",
        why if not reorder else f"{why} The lines will be ordered again.",
        buyer=po["buyer"],
    )
    _check_again(db, tenant, po_no, f"{po_no} was cancelled")
    return {"status": "cancelled", "supplier_told": told}


def _code(db: Session, user_id: int) -> str | None:
    """The organisation code of a login, for names in messages."""
    email = notifications.email_of(user_id)
    row = (
        db.execute(text("select code from org_user where lower(email) = lower(:e)"), {"e": email}).first()
        if email
        else None
    )
    return str(row[0]) if row else None


# ---- close the balance -------------------------------------------------------------------------


def close_balance(db: Session, tenant: str | None, po_no: str, user_id: int, reason: str | None) -> dict[str, Any]:
    """The supplier will not deliver the rest: close what is still to come. Something must already have been received."""
    why = _reason(reason)
    po = _locked(db, po_no)
    if po["status"] not in CLOSABLE:
        db.rollback()
        raise ChangeError(
            f"{po_no} is {po['status']}; only an order that is with the supplier can have its balance closed"
        )
    open_lines = [
        dict(r)
        for r in db.execute(
            text("select * from po_line where po_no = :n and status in ('open', 'part_received') order by line_no"),
            {"n": po_no},
        ).mappings()
    ]
    if (
        db.execute(
            text("select coalesce(sum(received_qty), 0) from po_line where po_no = :n"), {"n": po_no}
        ).scalar_one()
        <= 0
    ):
        db.rollback()
        raise ChangeError(f"nothing has been received against {po_no}: cancel it instead")
    closed: list[str] = []
    for ln in open_lines:
        balance = Decimal(ln["qty"]) - Decimal(ln["received_qty"])
        if balance <= 0:
            continue
        db.execute(
            text("update po_line set status = 'closed', short_closed_qty = :b where po_no = :n and line_no = :l"),
            {"b": balance, "n": po_no, "l": ln["line_no"]},
        )
        closed.append(f"{ln['description']}: {format_qty(balance)} {ln['uom'] or ''}".strip())
    if not closed:
        db.rollback()
        raise ChangeError(f"nothing is still to come on {po_no}")
    still_open = db.execute(
        text("select count(*) from po_line where po_no = :n and status in ('open', 'part_received')"), {"n": po_no}
    ).scalar_one()
    db.execute(text("update po set closed_reason = :r, closed_at = now() where no = :n"), {"r": why, "n": po_no})
    if not still_open:
        db.execute(text("update po set status = 'closed' where no = :n"), {"n": po_no})
    audit.record(
        db, user(user_id), "po.close_balance", "po", po_no,
        before={"status": po["status"]}, after={"closed": closed}, reason=why,
    )  # fmt: skip
    db.commit()
    told = tell_supplier(
        db, po, f"Purchase order {po_no}: balance closed",
        f"We are closing the balance of order {po_no}; no further supply is expected on:\n" + "\n".join(closed) + f"\nReason: {why}",
    )  # fmt: skip
    tell_people(db, tenant, po_no, f"The balance of order {po_no} was closed", why, buyer=po["buyer"])
    _check_again(db, tenant, po_no, f"the balance of {po_no} was closed")
    return {"status": "closed" if not still_open else po["status"], "closed": closed, "supplier_told": told}


def _check_again(db: Session, tenant: str | None, po_no: str, why: str) -> None:
    """Cover dropped or moved: the store check runs again for what the order bought for material requests (task 6.5)."""
    from p2p.requests import material  # noqa: PLC0415 - material requests sit above orders

    material.after_event(db, tenant, po_no=po_no, why=why)


# ---- revise ---------------------------------------------------------------------------------------


def _not_same(field: str, now: Any, new: Any) -> None:
    if now == new:
        raise InputError(f"{field} is already {new}")


def _place_name(db: Session, code: str) -> str:
    return document.delivery_place(db, code)[0]


@dataclass(frozen=True)
class _Tax:
    supplier_state: str
    place: str
    registration: Registration

    def amounts(self, qty: Decimal, price: Decimal, rate: Any) -> LineAmounts:
        return line_amounts(qty, price, rate, self.supplier_state, self.place, self.registration)


def _check_header(db: Session, po: dict[str, Any], header: HeaderChange) -> dict[str, Any]:
    """The new header values, checked before anything is written (R12-R14)."""
    po_no = po["no"]
    sets: dict[str, Any] = {}
    if header.delivery_terms is not None:
        terms = header.delivery_terms.strip()
        if not 1 <= len(terms) <= 200:
            raise InputError("delivery terms must be 1 to 200 characters")
        _not_same("delivery_terms", po["delivery_terms"], terms)
        sets["delivery_terms"] = terms
    if header.payment_terms_code is not None:
        code = header.payment_terms_code.strip()
        _not_same("payment_terms_code", po["payment_terms_code"], code)
        if db.execute(text("select 1 from payment_terms where code = :c"), {"c": code}).first() is None:
            raise InputError(f"{code} is not one of the company's payment terms")
        invoiced = db.execute(
            text(
                "select 1 from invoice_line il join invoice i on i.no = il.invoice_no "
                "where il.po_no = :n and i.status <> 'cancelled' limit 1"
            ),
            {"n": po_no},
        ).first()
        if invoiced:
            raise ChangeError(f"an invoice has been entered against {po_no}: its payment terms are fixed")
        if db.execute(text("select 1 from advance where po_no = :n limit 1"), {"n": po_no}).first():
            raise ChangeError(f"an advance has been paid against {po_no}: its payment terms are fixed")
        sets["payment_terms_code"] = code
    if header.ship_to_location is not None:
        code = header.ship_to_location.strip()
        _not_same("ship_to_location", po["ship_to_location"], code)
        if db.execute(text("select 1 from location where code = :c"), {"c": code}).first() is None:
            raise InputError(f"{code} is not one of the company's delivery places")
        gstin, state = service.place_of_supply(db, code)
        if not state:
            raise InputError(f"the state of {code} is unknown, so the GST cannot be worked out")
        old = _place_name(db, po["ship_to_location"])
        received = db.execute(
            text("select coalesce(sum(received_qty), 0) from po_line where po_no = :n and status <> 'cancelled'"),
            {"n": po_no},
        ).scalar_one()
        if received > 0 or po["status"] == "part_received":
            raise ChangeError(f"goods have been received at {old}: the delivery place cannot change")
        if db.execute(text("select 1 from dispatch_note where po_no = :n limit 1"), {"n": po_no}).first():
            raise ChangeError(f"the supplier has recorded a dispatch to {old}: agree the new place with it first")
        words = regions.names(db)
        for line_no, contract, loc, region in db.execute(
            text(
                "select l.line_no, pr.contract_code, pr.location_code, pr.region_code from po_line l "  # noqa: S608
                "join price_record pr on pr.id = l.price_record_id where l.po_no = :n and l.status <> 'cancelled' "
                f"and not {regions.price_in_scope('pr')} order by l.line_no"
            ),
            {"n": po_no, "loc": code},
        ):
            where = loc or region
            raise ChangeError(f"line {line_no}: {contract} applies in {words.get(where, where)} only")
        sets["ship_to_location"] = code
        sets["buyer_gstin"] = gstin
    return sets


def _contract_version(db: Session, record_id: int | None) -> int | None:
    if record_id is None:
        return None
    return db.execute(text("select version from price_record where id = :i"), {"i": record_id}).scalar()


def _reprice(
    db: Session, po: dict[str, Any], ln: dict[str, Any], qty: Decimal, ship_to: str
) -> tuple[Decimal, int, dict[str, Any]]:
    """The contract price in force today at the delivery place for a contract line (R15): its price, its record and the
    version it moves from and to."""
    n = ln["line_no"]
    contract = ln["source_ref"] if ln["source"] == "contract" and ln["source_ref"] != PRICE_LIST else None
    if not contract or not ln["item_code"]:
        raise ChangeError(f"line {n} is not bought on a contract")
    if Decimal(ln["received_qty"]) > 0 or Decimal(ln["invoiced_qty"]) > 0:
        raise ChangeError(f"line {n}: goods have been received or invoiced against it, so its price stays")
    today = business_today()
    options = [
        o
        for o in sourcing.contract_options(db, ln["item_code"], ln["uom"], today, ship_to)
        if o.contract_code == contract and o.supplier_code == po["supplier_code"]
    ]
    best = pick_contract_price(options, qty)  # one supplier: the highest break the quantity reaches
    if best is None or best.record_id is None:
        raise ChangeError(
            f"no price of {contract} is in force for {ln['item_code']} at {_place_name(db, ship_to)} on {today:%d %b %Y}"
        )
    was = _contract_version(db, ln["price_record_id"])
    if best.record_id == ln["price_record_id"]:
        raise ChangeError(f"line {n} is already at the contract price in force ({contract} version {was})")
    return best.price, best.record_id, {"from": was, "to": _contract_version(db, best.record_id)}


def _touchless_lost(
    db: Session, po: dict[str, Any], user_id: int, base: po_versions.Snapshot | None, version: int, material: bool
) -> str:
    """A person changed an order the system issued without a buyer: it is no longer touchless (Rev s5)."""
    when = versions.issued_on(db, po["no"], version) or po["sent_at"] or po["approved_at"] or po["created_at"]
    said = f"Changed by {name_of(db, _code(db, user_id))} after it was issued on {when.astimezone(IST):%d %b %Y}: "
    said += "no longer touchless (Rev s5). "
    if material or base is None:
        return said + f"The change is material, so a person approves version {version}."
    return (
        said + f"The approval by rule {base.rule_code or po['rule_code']} of version {base.version} carries: "
        "nothing material changed."
    )


def _retax(db: Session, po_no: str, tax: _Tax) -> None:
    """Every line and charge of the order taxed again at the place of supply (the delivery place changed)."""
    for line_no, qty, price, rate in db.execute(
        text("select line_no, qty, unit_price, gst_rate from po_line where po_no = :n and status <> 'cancelled'"),
        {"n": po_no},
    ).all():
        a = tax.amounts(Decimal(qty), Decimal(price), rate)
        db.execute(
            text("update po_line set taxable = :t, cgst = :c, sgst = :s, igst = :i where po_no = :n and line_no = :l"),
            {"t": a.taxable, "c": a.cgst, "s": a.sgst, "i": a.igst, "n": po_no, "l": line_no},
        )
    for charge_id, amount, rate in db.execute(
        text("select id, amount, gst_rate from po_charge where po_no = :n"), {"n": po_no}
    ).all():
        a = tax.amounts(Decimal(1), Decimal(amount), rate)
        db.execute(
            text("update po_charge set cgst = :c, sgst = :s, igst = :i where id = :id"),
            {"c": a.cgst, "s": a.sgst, "i": a.igst, "id": charge_id},
        )


def _line_edit(
    db: Session, po: dict[str, Any], ln: dict[str, Any], ch: LineChange, tax: _Tax, ship_to: str, approved_change: bool
) -> dict[str, Any]:
    """Apply one line's change and return its history entry."""
    n = ch.line_no
    if ch.unit_price is not None and not approved_change:
        raise InputError(f"line {n}: a price is not changed by a revision")
    if ch.reprice and ch.unit_price is not None:
        raise InputError(f"line {n}: a price comes from the contract, not typed")
    if ch.qty is None and ch.need_by is None and ch.unit_price is None and not ch.reprice:
        raise InputError(f"line {n}: nothing to change")
    entry: dict[str, Any] = {"line": n}
    if ch.qty is not None:
        ask = db.execute(
            text("select qty from request_line where request_no = :n and line_no = :l"),
            {"n": ln["request_no"], "l": ln["request_line_no"]},
        ).scalar()
        if ch.qty <= 0:
            raise ChangeError(f"line {n}: the quantity must be more than zero (cancel the order to drop it)")
        if ch.qty < Decimal(ln["received_qty"]):
            raise ChangeError(f"line {n}: {format_qty(ln['received_qty'])} has already been received")
        if ask is not None and ch.qty > Decimal(ask):
            raise ChangeError(f"line {n}: more than the request asked for ({format_qty(ask)})")
        entry["qty"] = {"from": str(ln["qty"]), "to": str(ch.qty)}
    qty = ch.qty if ch.qty is not None else Decimal(ln["qty"])
    price = Decimal(ln["unit_price"])
    record = ln["price_record_id"]
    if ch.unit_price is not None:
        if ch.unit_price <= 0:
            raise ChangeError(f"line {n}: the price must be more than zero")
        price, record = ch.unit_price, None  # the supplier's price, no longer the contract's (OP-7)
        if ln["price_record_id"] is not None:
            entry["contract_version"] = {"from": _contract_version(db, ln["price_record_id"]), "to": None}
    if ch.reprice:
        price, record, entry["contract_version"] = _reprice(db, po, ln, qty, ship_to)
    if price != Decimal(ln["unit_price"]):
        entry["unit_price"] = {"from": str(ln["unit_price"]), "to": str(price)}
    if ch.qty is not None or ch.unit_price is not None or ch.reprice:
        a = tax.amounts(qty, price, ln["gst_rate"])
        db.execute(
            text(
                "update po_line set qty = :q, unit_price = :p, price_record_id = :r, taxable = :t, cgst = :c, sgst = :s, "
                "igst = :i where po_no = :n and line_no = :l"
            ),
            {
                "q": qty, "p": price, "r": record, "t": a.taxable, "c": a.cgst, "s": a.sgst, "i": a.igst,
                "n": po["no"], "l": n,
            },
        )  # fmt: skip
    if ch.need_by is not None:
        db.execute(
            text(
                "update po_line set need_by = :d, promised_date = coalesce(:p, promised_date) "
                "where po_no = :n and line_no = :l"
            ),
            {"d": ch.need_by, "p": ch.promised_date, "n": po["no"], "l": n},
        )
        entry["need_by"] = {"from": ln["need_by"].isoformat() if ln["need_by"] else None, "to": ch.need_by.isoformat()}
    return entry


def apply_excess(
    db: Session,
    tenant: str | None,
    po_no: str,
    line_no: int,
    qty: Decimal,
    *,
    approved_by: Actor,
    rule_code: str | None,
    rule_version: int | None,
    band: Decimal | None,
    reason: str,
) -> dict[str, Any]:
    """Accept goods above the order by revising the line to what it counts plus the excess (9.3.2).

    The approval carries: the new version is recorded as approved by the proposal's approver, so the order is not sent
    back to draft and no second round starts. The caller commits, then mails the version to the supplier.
    """
    why = _reason(reason)
    po = _locked(db, po_no)
    if po["status"] not in WITH_SUPPLIER + ("received",):
        raise ChangeError(f"{po_no} is {po['status']}: goods above it cannot be accepted")
    if (
        po["status"] == "received"
        and not db.execute(
            text("select 1 from po_line where po_no = :n and line_no = :l and status = 'received'"),
            {"n": po_no, "l": line_no},
        ).first()
    ):
        raise ChangeError(f"{po_no} is received: line {line_no} cannot take more")
    ln = dict(
        db.execute(
            text("select * from po_line where po_no = :n and line_no = :l for update"), {"n": po_no, "l": line_no}
        )
        .mappings()
        .one()
    )
    if ln["status"] not in ("open", "part_received", "received"):
        raise ChangeError(f"line {line_no} is {ln['status']}: the excess cannot be accepted on it")
    if qty <= Decimal(ln["received_qty"]):
        raise ChangeError(f"line {line_no}: {format_qty(ln['received_qty'])} is already received; nothing to add")
    supplier = db.execute(
        text("select state_code, gstin, gst_reg_type from supplier where code = :c"), {"c": po["supplier_code"]}
    ).one()
    supplier_state = supplier[0] or (str(supplier[1])[:2] if supplier[1] else None)
    gstin, place = service.place_of_supply(db, po["ship_to_location"])
    if not supplier_state or not place:
        raise ChangeError(
            "the state of the supplier or of the delivery place is unknown, so the GST cannot be worked out"
        )
    del gstin
    tax = _Tax(supplier_state, place, Registration(supplier[2] or "regular"))
    amounts = tax.amounts(qty, Decimal(ln["unit_price"]), ln["gst_rate"])
    db.execute(
        text(
            "update po_line set qty = :q, taxable = :t, cgst = :c, sgst = :s, igst = :i, "
            "status = case when :q <= received_qty then 'received' else 'part_received' end "
            "where po_no = :n and line_no = :l"
        ),
        {
            "q": qty, "t": amounts.taxable, "c": amounts.cgst, "s": amounts.sgst, "i": amounts.igst,
            "n": po_no, "l": line_no,
        },
    )  # fmt: skip
    parts = [
        LineAmounts(r[0], r[1], r[2], r[3])
        for r in db.execute(
            text("select taxable, cgst, sgst, igst from po_line where po_no = :n and status <> 'cancelled'"),
            {"n": po_no},
        )
    ] + [
        LineAmounts(r[0], r[1], r[2], r[3])
        for r in db.execute(text("select amount, cgst, sgst, igst from po_charge where po_no = :n"), {"n": po_no})
    ]
    totals = order_totals(parts)
    version = int(po["version"]) + 1
    db.execute(
        text(
            "update po set taxable = :t, cgst = :c, sgst = :s, igst = :i, total = :tot, version = :v, "
            "acknowledged_version = null, acknowledged_at = null where no = :n"
        ),
        {
            "t": totals.taxable, "c": totals.cgst, "s": totals.sgst, "i": totals.igst, "tot": totals.total,
            "v": version, "n": po_no,
        },
    )  # fmt: skip
    base = approved_versions.baseline(db, po_no)
    db.execute(
        text(
            "insert into po_revision (po_no, version, changed_by, reason, changes, total_before, total_after, "
            "reapproval, baseline_version, material) values (:n, :v, :u, :r, cast(:c as jsonb), :tb, :ta, false, :bv, "
            "'[]')"
        ),
        {
            "n": po_no, "v": version, "u": approved_by.identity, "r": why,
            "c": json.dumps([{"line": line_no, "qty": {"from": str(ln["qty"]), "to": str(qty)}},
                             {"excess": "accepted by revision"}], default=str),
            "tb": Decimal(po["total"]), "ta": totals.total, "bv": base.version if base else None,
        },
    )  # fmt: skip
    versions.record(db, po_no, made_by="proposal:receipt.excess_accept", reason=why)
    approved_versions.record(
        db, po_no, approved_by=approved_by.identity, rule_code=rule_code, rule_version=rule_version, band=band
    )
    audit.record(
        db, approved_by, "po.revise", "po", po_no,
        before={"version": version - 1, "total": po["total"]},
        after={"version": version, "total": totals.total, "excess": True, "line": line_no,
               "qty": {"from": str(ln["qty"]), "to": str(qty)}, "approval_carried": True,
               "rule": f"{rule_code}:v{rule_version}" if rule_code else None},
        reason=why,
    )  # fmt: skip
    return {"version": version, "total": totals.total, "po": po}


def mail_version(
    db: Session, tenant: str | None, po: dict[str, Any], version: int, why: str, supplier_asked: bool = False
) -> bool:
    """Mail a new version with its respond link, as any carried revision (block 8's acknowledgement rules)."""
    return _mail_version(db, tenant, po, version, why, supplier_asked)


def revise(
    db: Session,
    tenant: str | None,
    po_no: str,
    user_id: int,
    reason: str | None,
    changes: list[LineChange],
    *,
    header: HeaderChange | None = None,
    seen_version: int | None = None,
    approved_change: bool = False,
    supplier_asked: bool = False,
    change_id: int | None = None,
    excess_id: int | None = None,
    approved_by: Actor | None = None,
    rule_code: str | None = None,
    rule_version: int | None = None,
    band: Decimal | None = None,
) -> dict[str, Any]:
    """Make a new version of the order: the quantity or need-by date of open lines, a contract line moved to the
    contract price in force, the delivery place, payment terms or delivery terms. `seen_version` is the version the
    person saw (a later one is refused). `approved_change` is a supplier's change a person has already accepted: it may
    change the price. A material change against the last approved version (Q19) goes through approval again; any other
    carries the approval, and if the supplier already has the order clears its acknowledgement and mails the new
    version's document with a link to answer it.

    With `excess_id` it accepts goods above the order (9.3.2): the line becomes what it counts plus the excess, and the
    version is recorded as approved by `approved_by`, so the approval carries and nothing goes back to draft.
    """
    if excess_id is not None:
        if approved_by is None:
            raise ChangeError("accepting goods above the order needs the person who approved it")
        excess = db.execute(text("select * from receipt_excess where id = :i"), {"i": excess_id}).mappings().first()
        if excess is None:
            raise ChangeError(f"there is no excess {excess_id}")
        line = db.execute(
            text("select received_qty from po_line where po_no = :p and line_no = :l"),
            {"p": excess["po_no"], "l": excess["po_line_no"]},
        ).first()
        if line is None:
            raise ChangeError(f"line {excess['po_line_no']} is not on {excess['po_no']}")
        made = apply_excess(
            db, tenant, po_no, int(excess["po_line_no"]), Decimal(line[0]) + Decimal(excess["qty"]),
            approved_by=approved_by, rule_code=rule_code, rule_version=rule_version, band=band,
            reason=reason or "",
        )  # fmt: skip
        return {
            "version": made["version"], "status": str(db.execute(text("select status from po where no = :n"), {"n": po_no}).scalar_one()),
            "total": str(made["total"]), "reapproval": False, "material": [], "approval_carried": True,
            "header_changes": [], "touchless_lost": False, "supplier_told": False, "po": made["po"],
        }  # fmt: skip
    why = _reason(reason)
    header = header if header is not None and not header.empty() else None
    if not changes and header is None:
        raise InputError("nothing to change")
    po = _locked(db, po_no)
    try:
        if po["status"] not in REVISABLE:
            raise ChangeError(f"{po_no} is {po['status']}; an order can be revised until goods arrive")
        if seen_version is not None and seen_version != int(po["version"]):
            raise ChangeError(f"{po_no} has changed since you opened it: it is now version {po['version']}")
        made = _revise(db, po, user_id, why, changes, header, approved_change, change_id)
    except ChangeError:
        db.rollback()
        raise
    return _after_revision(db, tenant, po, user_id, why, made, approved_change, supplier_asked, change_id, seen_version)


def _revise(
    db: Session,
    po: dict[str, Any],
    user_id: int,
    why: str,
    changes: list[LineChange],
    header: HeaderChange | None,
    approved_change: bool,
    change_id: int | None,
) -> dict[str, Any]:
    """The writes of a revision, all in one transaction (not committed here)."""
    po_no = po["no"]
    lines = {
        r["line_no"]: dict(r)
        for r in db.execute(text("select * from po_line where po_no = :n order by line_no"), {"n": po_no}).mappings()
    }
    sets = _check_header(db, po, header) if header else {}
    ship_to = sets.get("ship_to_location", po["ship_to_location"])
    supplier = db.execute(
        text("select state_code, gstin, gst_reg_type from supplier where code = :c"), {"c": po["supplier_code"]}
    ).one()
    supplier_state = supplier[0] or (str(supplier[1])[:2] if supplier[1] else None)
    if "ship_to_location" in sets:
        place = service.place_of_supply(db, ship_to)[1]
    else:
        state = db.execute(text("select state_code from location where code = :c"), {"c": ship_to}).scalar()
        place = (str(po["buyer_gstin"])[:2] if po["buyer_gstin"] else None) or state
    if not supplier_state or not place:
        raise ChangeError(
            "the state of the supplier or of the delivery place is unknown, so the GST cannot be worked out"
        )
    tax = _Tax(supplier_state, place, Registration(supplier[2] or "regular"))
    before_total = Decimal(po["total"])
    # an order approved with no snapshot kept (made before block 4, or written outside the workflow) is measured
    # from the version it is now, as the migration's backfill would have kept it
    unsnapped = approved_versions.snapshot(db, po_no) if po["approved_at"] is not None else None
    seen: set[int] = set()
    done: list[dict[str, Any]] = []
    for ch in changes:
        ln = lines.get(ch.line_no)
        if ln is None or ln["status"] not in ("open", "part_received"):
            raise ChangeError(f"line {ch.line_no} cannot be changed: it is not an open line of {po_no}")
        if ch.line_no in seen:
            raise InputError(f"line {ch.line_no} is changed twice")
        seen.add(ch.line_no)
        done.append(_line_edit(db, po, ln, ch, tax, ship_to, approved_change))
    header_changes: list[dict[str, Any]] = []
    if sets:
        for field in ("ship_to_location", "payment_terms_code", "delivery_terms"):
            if field in sets:
                header_changes.append({"header": field, "from": po[field], "to": sets[field]})
        if "ship_to_location" in sets:
            _retax(db, po_no, tax)
            header_changes[0]["gst"] = "CGST and SGST" if supplier_state == place else "IGST"
        db.execute(
            text(
                "update po set " + ", ".join(f"{f} = :{f}" for f in sets) + " where no = :n"  # noqa: S608 - fixed names
            ),
            {**sets, "n": po_no},
        )
    parts = [
        LineAmounts(r[0], r[1], r[2], r[3])
        for r in db.execute(
            text("select taxable, cgst, sgst, igst from po_line where po_no = :n and status <> 'cancelled'"),
            {"n": po_no},
        )
    ] + [
        LineAmounts(r[0], r[1], r[2], r[3])
        for r in db.execute(text("select amount, cgst, sgst, igst from po_charge where po_no = :n"), {"n": po_no})
    ]
    totals = order_totals(parts)
    after_total = totals.total
    version = int(po["version"]) + 1
    # a new version voids the acknowledgement of the one before (DF-07): it stays on that version's own row
    db.execute(
        text(
            "update po set taxable = :t, cgst = :c, sgst = :s, igst = :i, total = :tot, version = :v, "
            "acknowledged_version = null, acknowledged_at = null where no = :n"
        ),
        {
            "t": totals.taxable,
            "c": totals.cgst,
            "s": totals.sgst,
            "i": totals.igst,
            "tot": after_total,
            "v": version,
            "n": po_no,
        },
    )
    # measured from the last approved version, so small changes add up (DF-07, DF-08); never approved: nothing to carry
    base = approved_versions.baseline(db, po_no) or unsnapped
    material = (
        po_versions.material_reasons(base, approved_versions.snapshot(db, po_no), _date_tolerance(db)) if base else []
    )
    # a material change goes for approval again; so does any change while approval is under way, so that the approval
    # is of this version and never waits for someone who has just become one of the order's makers (C1)
    reapprove = (bool(material) and po["status"] != "draft") or po["status"] == "submitted"
    db.execute(
        text(
            "insert into po_revision (po_no, version, changed_by, reason, changes, total_before, total_after, reapproval, "
            "baseline_version, material) values (:n, :v, :u, :r, cast(:c as jsonb), :tb, :ta, :re, :bv, cast(:m as jsonb))"
        ),
        {
            "n": po_no,
            "v": version,
            "u": str(user_id),
            "r": why,
            "c": json.dumps(header_changes + done, default=str),
            "tb": before_total,
            "ta": after_total,
            "re": reapprove,
            "bv": base.version if base else None,
            "m": json.dumps(material),
        },
    )
    # an accepted supplier price goes on as the system: accepting it does not make that person a maker (C1)
    maker = "service:supplier-change" if approved_change else f"user:{user_id}"
    versions.record(db, po_no, made_by=maker, reason=why)
    lost = bool(po["touchless"])
    if lost:  # a person changed it, so it is no longer touchless (Rev s5); a material change is approved again too
        note = _touchless_lost(db, po, user_id, base, version, reapprove)
        db.execute(text("update po set touchless = false, touchless_note = :t where no = :n"), {"t": note, "n": po_no})
        audit.record(db, user(user_id), "po.touchless_lost", "po", po_no, after={"version": version}, reason=note)
    return {
        "version": version,
        "before_total": before_total,
        "after_total": after_total,
        "done": done,
        "header_changes": header_changes,
        "base": base,
        "material": material,
        "reapprove": reapprove,
        "touchless_lost": lost,
    }


def _after_revision(
    db: Session,
    tenant: str | None,
    po: dict[str, Any],
    user_id: int,
    why: str,
    made: dict[str, Any],
    approved_change: bool,
    supplier_asked: bool,
    change_id: int | None,
    seen_version: int | None,
) -> dict[str, Any]:
    """Audit the revision, then approval again, or the new version to the supplier."""
    po_no, version, base, reapprove = po["no"], made["version"], made["base"], made["reapprove"]
    carried = base is not None and not reapprove
    audit.record(
        db, user(user_id), "po.revise", "po", po_no,
        before={"version": version - 1, "total": made["before_total"]},
        after={
            "version": version, "total": made["after_total"], "changes": made["done"],
            "header_changes": made["header_changes"], "material": made["material"],
            "baseline_version": base.version if base else None, "approval_carried": carried,
            "seen_version": seen_version,
        },
        reason=why,
    )  # fmt: skip
    was = po["status"]
    if reapprove:
        # approval again on the new version and total; approved_version keeps the old one until then
        _drop_open_decisions(db, po, user_id, f"{po_no} was revised")  # commits
        db.execute(
            text("update po set status = 'draft', approved_at = null, sent_at = null where no = :n"), {"n": po_no}
        )
        if approved_change and change_id is not None:  # the price approver's acceptance, not their making (C1)
            db.execute(text("update po_change set version_made = :v where id = :i"), {"v": version, "i": change_id})
        db.commit()
        by = Actor("service", "supplier-change") if approved_change else user(user_id)
        new_status = workflow.submit(db, tenant or "", po_no, by)
    else:
        if was in WITH_SUPPLIER:  # the supplier acknowledged another version: it answers this one again (DF-07)
            db.execute(
                text(
                    "update po set status = case when status = 'acknowledged' then 'sent' else status end where no = :n"
                ),
                {"n": po_no},
            )
        db.commit()
        new_status = str(db.execute(text("select status from po where no = :n"), {"n": po_no}).scalar_one())
    told = False
    if was in WITH_SUPPLIER and not reapprove:
        told = _mail_version(db, tenant, po, version, why, supplier_asked)
    _check_again(db, tenant, po_no, f"{po_no} was revised")
    dispatch.close_ack_tasks(db, po_no, f"revised to version {version}")
    return {
        "version": version,
        "status": new_status,
        "total": str(made["after_total"]),
        "reapproval": reapprove,
        "material": made["material"],
        "approval_carried": carried,
        "header_changes": made["header_changes"],
        "touchless_lost": made["touchless_lost"],
        "supplier_told": told,
    }


def _mail_version(
    db: Session, tenant: str | None, po: dict[str, Any], version: int, why: str, supplier_asked: bool
) -> bool:
    """The new version to the supplier: its document stored and attached as stored, with a link to answer this version
    (none for a change the supplier itself asked for: accepting it settles the order)."""
    po_no = po["no"]
    issued = versions.issue_document(db, tenant or outbox.tenant_of(db), po_no)
    contact = supplier_contact(db, po["supplier_code"])
    again = ""
    if tenant and not supplier_asked and contact:
        token = supplier_links.issue(db, tenant, po_no, "respond", sent_to=contact[0])
        again = (
            "\n\nPlease accept this version of the order, or tell us what you can do, here (the link works once and "
            f"asks for a code we email to you):\n{supplier_links.url(token)}"
        )
    db.commit()
    told = tell_supplier(
        db,
        po,
        f"Purchase order {po_no} (version {version}): revised",
        f"Order {po_no} has been revised (version {version}). The revised order is attached. Reason: {why}{again}",
        document_ids=[issued.document_id] if issued else None,
        po_version=version,
    )
    if told and contact:
        versions.mark_sent(db, po_no, version, contact[0])
        db.commit()
    return told


def _date_tolerance(db: Session) -> int:
    """The date slip accepted on the day the change is decided, from the tolerance version in force then."""
    return int(tolerances.in_force(db, business_today()).decimal("supplier_date_days", "3"))


_HEADER_WORDS = {
    "ship_to_location": "delivery place",
    "payment_terms_code": "payment terms",
    "delivery_terms": "delivery terms",
}


def _day_said(value: str | None) -> str:
    return f"{date.fromisoformat(value):%d %b %Y}" if value else "none"


def said(entry: dict[str, Any]) -> list[str]:
    """One history entry of a revision in words: 'delivery place PLT-CKN to PLT-SND (GST now IGST)'."""
    if "header" in entry:
        words = f"{_HEADER_WORDS.get(entry['header'], entry['header'])} {entry['from'] or 'none'} to {entry['to']}"
        return [words + (f" (GST now {entry['gst']})" if entry.get("gst") else "")]
    n = entry.get("line")
    out = []
    if "qty" in entry:
        out.append(
            f"line {n}: quantity {format_qty(Decimal(entry['qty']['from']))} to {format_qty(Decimal(entry['qty']['to']))}"
        )
    if "need_by" in entry:
        out.append(f"line {n}: needed by {_day_said(entry['need_by']['from'])} to {_day_said(entry['need_by']['to'])}")
    if "unit_price" in entry:
        out.append(
            f"line {n}: price {format_inr(Decimal(entry['unit_price']['from']))} to "
            f"{format_inr(Decimal(entry['unit_price']['to']))}"
        )
    if "contract_version" in entry:
        cv = entry["contract_version"]
        out.append(
            f"line {n}: contract price version {cv['from']} to {cv['to']}"
            if cv["to"] is not None
            else f"line {n}: no longer at the contract price (version {cv['from']})"
        )
    return out


def _outcome(r: Any) -> str:
    """What a revision did to the approval, in words."""
    if r["reapproval"]:
        reasons = list(r["material"] or [])
        return "approved again" + (f": {'; '.join(reasons)}" if reasons else ": changed while waiting for approval")
    if r["baseline_version"] is not None:
        return f"approval of version {r['baseline_version']} carries: nothing material changed"
    return "not approved yet"


_VIA = {"link": "by link", "app": "in the app", "email": "by email"}


def _acknowledged(db: Session, po_no: str, version: int) -> str:
    """The supplier's answer to the version before and to this one (R5): 'Version 1 acknowledged by link on 23 Nov
    2026; version 2 not yet acknowledged'."""
    rows = {
        int(r[0]): (r[1], r[2])
        for r in db.execute(
            text("select version, acknowledged_at, ack_via from po_version where po_no = :n and version in (:a, :b)"),
            {"n": po_no, "a": version - 1, "b": version},
        )
    }
    words = []
    for v in (version - 1, version):
        if v not in rows:
            continue
        at, via = rows[v]
        words.append(
            f"version {v} acknowledged {_VIA.get(str(via), '')} on {at.astimezone(IST):%d %b %Y}".replace("  ", " ")
            if at
            else f"version {v} not yet acknowledged"
        )
    said = "; ".join(words)
    return said[:1].upper() + said[1:]


def history(db: Session, po_no: str) -> list[dict[str, Any]]:
    return [
        {
            "version": r["version"],
            "said": [w for e in r["changes"] or [] for w in said(e)],
            "outcome": _outcome(r),
            "acknowledged": _acknowledged(db, po_no, int(r["version"])),
            "at": r["changed_at"].isoformat(),
            "by": name_of(db, _code_of_id(db, r["changed_by"])),
            "reason": r["reason"],
            "changes": r["changes"],
            "total_before": str(r["total_before"]),
            "total_after": str(r["total_after"]),
            "reapproval": r["reapproval"],
            "material": list(r["material"] or []),
            "baseline_version": r["baseline_version"],
            "approval_carried": r["baseline_version"] is not None and not r["reapproval"],
        }
        for r in db.execute(
            text("select * from po_revision where po_no = :n order by version desc"), {"n": po_no}
        ).mappings()
    ]


def _code_of_id(db: Session, user_id: str | None) -> str | None:
    return _code(db, int(user_id)) if user_id and user_id.isdigit() else None
