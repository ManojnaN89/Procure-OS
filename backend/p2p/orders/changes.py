"""Changes to an order after it was made: a revision (a new version), cancelling it, and closing what is still to come.

* A revision changes the quantity or the need-by date of open lines, never a price (a price comes from a contract or an
  award). Every revision is a new version with its own history row. If the order's total goes up it goes through approval
  again on the new total; a lower total or a date change needs no new approval. A supplier who already has the order gets
  the revised order by email.
* Cancelling is for an order nothing has been received or invoiced against. It gives back the budget commitment and the
  open supply, and either frees the lines to be ordered again or ends the need.
* Closing is for an order that was partly delivered: the part still to come is closed, so it no longer counts as supply or
  as a commitment.
All of it is a person's decision with a reason; every change writes an audit row.
"""

import json
import logging
import smtplib
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from p2p.approvals.workflow import name_of, notify_person
from p2p.formulas.gst import Registration
from p2p.formulas.money import format_qty
from p2p.formulas.orders import LineAmounts, line_amounts, order_totals
from p2p.orders import document, workflow
from p2p.platform import audit, emails, notifications, proposals, tasks
from p2p.platform.sod import user

log = logging.getLogger(__name__)
CANCELLABLE = ("draft", "submitted", "approved", "sent", "acknowledged")
CLOSABLE = ("sent", "acknowledged", "part_received")
REVISABLE = ("draft", "submitted", "approved", "sent", "acknowledged", "part_received")
WITH_SUPPLIER = ("sent", "acknowledged", "part_received")


class ChangeError(Exception):
    pass


@dataclass
class LineChange:
    line_no: int
    qty: Decimal | None = None
    need_by: date | None = None


def _locked(db: Session, po_no: str) -> dict[str, Any]:
    row = db.execute(text("select * from po where no = :n for update"), {"n": po_no}).mappings().first()
    if row is None:
        raise ChangeError(f"{po_no} does not exist")
    return dict(row)


def _reason(reason: str | None) -> str:
    if not reason or not reason.strip():
        raise ChangeError("a reason is needed")
    return reason.strip()


def _tell_supplier(
    db: Session, po: dict[str, Any], subject: str, body: str, attachments: list[tuple[str, str, bytes]] | None = None
) -> bool:
    """Email the supplier's main contact. A mail fault never undoes the change: the page says the supplier was not told."""
    contact = db.execute(
        text(
            "select email, name from supplier_contact where supplier_code = :c and email is not null "
            "order by is_primary desc nulls last, id limit 1"
        ),
        {"c": po["supplier_code"]},
    ).first()
    if contact is None:
        return False
    company = db.execute(text("select coalesce(short_name, legal_name) from company limit 1")).scalar() or "Our company"
    try:
        emails.send_linked_email(
            db,
            contact[0],
            subject,
            f"Dear {contact[1] or 'Sir or Madam'},\n\n{body}\n\nRegards,\nProcurement, {company}\n",
            linked_type="po",
            linked_ref=po["no"],
            attachments=attachments,
        )
    except (OSError, smtplib.SMTPException):
        db.rollback()
        log.warning("could not tell the supplier about %s", po["no"], exc_info=True)
        return False
    return True


def _tell_people(db: Session, tenant: str | None, po_no: str, title: str, body: str, *, buyer: str | None) -> None:
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
    told = False
    if was in WITH_SUPPLIER:
        told = _tell_supplier(
            db,
            po,
            f"Purchase order {po_no} is cancelled",
            f"Please do not supply against order {po_no}. It is cancelled. Reason: {why}",
        )
    _tell_people(
        db,
        tenant,
        po_no,
        f"Order {po_no} was cancelled",
        why if not reorder else f"{why} The lines will be ordered again.",
        buyer=po["buyer"],
    )
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
    told = _tell_supplier(
        db, po, f"Purchase order {po_no}: balance closed",
        f"We are closing the balance of order {po_no}; no further supply is expected on:\n" + "\n".join(closed) + f"\nReason: {why}",
    )  # fmt: skip
    _tell_people(db, tenant, po_no, f"The balance of order {po_no} was closed", why, buyer=po["buyer"])
    return {"status": "closed" if not still_open else po["status"], "closed": closed, "supplier_told": told}


# ---- revise ---------------------------------------------------------------------------------------


def revise(
    db: Session,
    tenant: str | None,
    po_no: str,
    user_id: int,
    reason: str | None,
    changes: list[LineChange],
) -> dict[str, Any]:
    """Make a new version of the order: change the quantity or the need-by date of open lines."""
    why = _reason(reason)
    if not changes:
        raise ChangeError("nothing to change")
    po = _locked(db, po_no)
    if po["status"] not in REVISABLE:
        db.rollback()
        raise ChangeError(f"{po_no} is {po['status']}; an order can be revised until goods arrive")
    lines = {
        r["line_no"]: dict(r)
        for r in db.execute(text("select * from po_line where po_no = :n order by line_no"), {"n": po_no}).mappings()
    }
    supplier = db.execute(
        text("select state_code, gstin, gst_reg_type from supplier where code = :c"), {"c": po["supplier_code"]}
    ).one()
    state = db.execute(text("select state_code from location where code = :c"), {"c": po["ship_to_location"]}).scalar()
    supplier_state = supplier[0] or (str(supplier[1])[:2] if supplier[1] else None)
    place = (str(po["buyer_gstin"])[:2] if po["buyer_gstin"] else None) or state
    if not supplier_state or not place:
        db.rollback()
        raise ChangeError(
            "the state of the supplier or of the delivery place is unknown, so the GST cannot be worked out"
        )
    registration = Registration(supplier[2] or "regular")
    before_total = Decimal(po["total"])
    seen: set[int] = set()
    done: list[dict[str, Any]] = []
    for ch in changes:
        ln = lines.get(ch.line_no)
        if ln is None or ln["status"] not in ("open", "part_received"):
            db.rollback()
            raise ChangeError(f"line {ch.line_no} cannot be changed: it is not an open line of {po_no}")
        if ch.line_no in seen:
            db.rollback()
            raise ChangeError(f"line {ch.line_no} is changed twice")
        seen.add(ch.line_no)
        if ch.qty is None and ch.need_by is None:
            db.rollback()
            raise ChangeError(f"line {ch.line_no}: nothing to change")
        entry: dict[str, Any] = {"line": ch.line_no}
        if ch.qty is not None:
            ask = db.execute(
                text("select qty from request_line where request_no = :n and line_no = :l"),
                {"n": ln["request_no"], "l": ln["request_line_no"]},
            ).scalar()
            if ch.qty <= 0:
                db.rollback()
                raise ChangeError(
                    f"line {ch.line_no}: the quantity must be more than zero (cancel the order to drop it)"
                )
            if ch.qty < Decimal(ln["received_qty"]):
                db.rollback()
                raise ChangeError(f"line {ch.line_no}: {format_qty(ln['received_qty'])} has already been received")
            if ask is not None and ch.qty > Decimal(ask):
                db.rollback()
                raise ChangeError(f"line {ch.line_no}: more than the request asked for ({format_qty(ask)})")
            amounts = line_amounts(ch.qty, ln["unit_price"], ln["gst_rate"], supplier_state, place, registration)
            db.execute(
                text(
                    "update po_line set qty = :q, taxable = :t, cgst = :c, sgst = :s, igst = :i "
                    "where po_no = :n and line_no = :l"
                ),
                {
                    "q": ch.qty,
                    "t": amounts.taxable,
                    "c": amounts.cgst,
                    "s": amounts.sgst,
                    "i": amounts.igst,
                    "n": po_no,
                    "l": ch.line_no,
                },
            )
            entry["qty"] = {"from": str(ln["qty"]), "to": str(ch.qty)}
        if ch.need_by is not None:
            db.execute(
                text("update po_line set need_by = :d where po_no = :n and line_no = :l"),
                {"d": ch.need_by, "n": po_no, "l": ch.line_no},
            )
            entry["need_by"] = {
                "from": ln["need_by"].isoformat() if ln["need_by"] else None,
                "to": ch.need_by.isoformat(),
            }
        done.append(entry)
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
    reapprove = after_total > before_total and po["status"] != "draft"
    db.execute(
        text("update po set taxable = :t, cgst = :c, sgst = :s, igst = :i, total = :tot, version = :v where no = :n"),
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
    db.execute(
        text(
            "insert into po_revision (po_no, version, changed_by, reason, changes, total_before, total_after, reapproval) "
            "values (:n, :v, :u, :r, cast(:c as jsonb), :tb, :ta, :re)"
        ),
        {
            "n": po_no,
            "v": version,
            "u": str(user_id),
            "r": why,
            "c": json.dumps(done, default=str),
            "tb": before_total,
            "ta": after_total,
            "re": reapprove,
        },
    )
    audit.record(
        db, user(user_id), "po.revise", "po", po_no,
        before={"version": version - 1, "total": before_total}, after={"version": version, "total": after_total, "changes": done},
        reason=why,
    )  # fmt: skip
    was = po["status"]
    if reapprove:
        # a higher total goes through approval again on the new amount
        _drop_open_decisions(db, po, user_id, f"{po_no} was revised")  # commits
        db.execute(
            text(
                "update po set status = 'draft', approved_at = null, sent_at = null, acknowledged_at = null where no = :n"
            ),
            {"n": po_no},
        )
        db.commit()
        new_status = workflow.submit(db, tenant or "", po_no, user(user_id))
    else:
        db.commit()
        new_status = was
    told = False
    if was in WITH_SUPPLIER and not reapprove:
        told = _tell_supplier(
            db,
            po,
            f"Purchase order {po_no} (version {version}): revised",
            f"Order {po_no} has been revised (version {version}). The revised order is attached. Reason: {why}",
            [(f"{po_no}-v{version}.pdf", "application/pdf", document.order_document(db, po_no))],
        )
    return {
        "version": version,
        "status": new_status,
        "total": str(after_total),
        "reapproval": reapprove,
        "supplier_told": told,
    }


def history(db: Session, po_no: str) -> list[dict[str, Any]]:
    return [
        {
            "version": r["version"],
            "at": r["changed_at"].isoformat(),
            "by": name_of(db, _code_of_id(db, r["changed_by"])),
            "reason": r["reason"],
            "changes": r["changes"],
            "total_before": str(r["total_before"]),
            "total_after": str(r["total_after"]),
            "reapproval": r["reapproval"],
        }
        for r in db.execute(
            text("select * from po_revision where po_no = :n order by version desc"), {"n": po_no}
        ).mappings()
    ]


def _code_of_id(db: Session, user_id: str | None) -> str | None:
    return _code(db, int(user_id)) if user_id and user_id.isdigit() else None
