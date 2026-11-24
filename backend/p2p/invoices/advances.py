"""Advances on an order: request, its own approval, payment, use against invoices and recovery (2.3, D-18, D-23, DF-17).

An advance is never paid because the order's terms mention one: the buyer requests it (up to the percentage the terms
carry), the rule for an `advance` of its gross names a person who is not the requester or a maker of the order, and only
an approved request enters a payment run. Its tax is held back when the advance is paid, never at proposal. An invoice
of an order with an approved or in-run advance waits until that advance is settled; a settled advance is taken off the
order's booked invoices in turn. Cancelling an order with money already advanced needs a debit note for what was not
used, so the money is recovered before the order goes (AT-06).
"""

from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from p2p.approvals import rules as approval_rules
from p2p.approvals.workflow import name_of, org_code_of
from p2p.formulas import payment as rules
from p2p.formulas.calendar import business_today
from p2p.formulas.money import format_inr
from p2p.invoices.service import InvoiceError
from p2p.platform import audit, proposals
from p2p.platform.sod import Actor, user

D = Decimal
LIVE = ("requested", "approved", "in_run", "paid", "recovering")


@dataclass
class Request:
    id: int
    po_no: str
    gross: Decimal
    taxable: Decimal
    pct: Decimal
    status: str
    approver: str | None


def _po(db: Session, po_no: str) -> dict[str, Any]:
    row = (
        db.execute(
            text(
                "select po.*, t.advance_pct, po.total as version_total, po.taxable as version_taxable, "
                "coalesce(po.version, 1) as version "
                "from po left join payment_terms t on t.code = po.payment_terms_code where po.no = :p"
            ),
            {"p": po_no},
        )
        .mappings()
        .first()
    )
    if row is None:
        raise InvoiceError(f"{po_no} does not exist")
    return dict(row)


def request(
    db: Session,
    po_no: str,
    user_id: int,
    *,
    pct: Decimal | None = None,
    reason: str | None = None,
    tenant: str | None = None,
) -> dict[str, Any]:
    """Ask for an advance on an order the supplier has seen (12.6.1). One live request per order."""
    po = _po(db, po_no)
    if po["status"] not in ("sent", "acknowledged", "part_received"):
        raise InvoiceError(f"{po_no} is {po['status']}: an advance is asked for on an order the supplier has seen")
    terms_pct = D(po["advance_pct"] or 0)
    if terms_pct <= 0:
        raise InvoiceError(f"the payment terms of {po_no} carry no advance")
    asked = D(pct) if pct is not None else terms_pct
    if asked <= 0:
        raise InvoiceError("the advance must be more than zero")
    if asked > terms_pct:
        raise InvoiceError(f"the terms allow at most {terms_pct.normalize():f}%")
    live = db.execute(
        text("select id from advance_request where po_no = :p and status = any(:s)"),
        {"p": po_no, "s": list(LIVE)},
    ).scalar()
    if live:
        raise InvoiceError(f"{po_no} already has an advance request")
    gross, taxable = rules.advance_amounts(D(po["version_total"]), D(po["version_taxable"]), asked)
    if gross <= 0:
        raise InvoiceError("the advance works out at nothing")
    requester = org_code_of(db, user_id) or f"user:{user_id}"
    from p2p.orders import workflow as po_workflow  # noqa: PLC0415 - orders builds on invoices, so import late

    makers = po_workflow.makers(db, po)
    today = business_today()
    try:
        rule = approval_rules.find_rule(db, "advance", gross, today)
        steps = approval_rules.resolve_chain(
            db, rule.roles, requester=requester, department=None, on_date=today, exclude=makers
        )
    except approval_rules.ApprovalRuleError as exc:
        raise InvoiceError(str(exc)) from None
    if not steps:
        raise InvoiceError(f"no approval rule names who approves an advance of {format_inr(gross)}")
    first = steps[0]
    request_id = int(
        db.execute(
            text(
                "insert into advance_request (po_no, po_version, pct, gross, taxable, status, reason, requested_by, approver) "
                "values (:p, :v, :pct, :g, :t, 'requested', :why, :by, :ap) returning id"
            ),
            {
                "p": po_no, "v": po["version"], "pct": asked, "g": gross, "t": taxable,
                "why": reason, "by": requester, "ap": first.approver,
            },
        ).scalar_one()
    )  # fmt: skip
    pid = proposals.propose(
        db,
        user(user_id),
        action="advance.approve",
        summary=f"Advance of {format_inr(gross)} on {po_no} ({asked.normalize():f}%)",
        payload={"request_id": request_id},
        required_permission="advance.approve",
        risk="high",
    )
    db.execute(text("update advance_request set proposal_id = :p where id = :i"), {"p": pid, "i": request_id})
    audit.record(
        db, user(user_id), "advance.request", "advance_request", str(request_id),
        after={"po": po_no, "gross": gross, "taxable": taxable, "pct": asked, "approver": first.approver},
    )  # fmt: skip
    db.commit()
    return {
        "id": request_id, "gross": gross, "taxable": taxable, "approver": first.approver,
        "approver_name": name_of(db, first.approver), "proposal": pid,
    }  # fmt: skip


@proposals.guard("advance.approve")
def _only_named(db: Session, payload: dict[str, Any], actor: Actor) -> None:
    row = db.execute(
        text("select approver from advance_request where id = :i"), {"i": int(payload["request_id"])}
    ).first()
    if row is None:
        raise InvoiceError("the advance request is gone")
    me = org_code_of(db, int(actor.id)) if actor.kind == "user" else None
    deputy = approval_rules.deputy_on(db, str(row[0]), business_today())
    if me not in {row[0], deputy}:
        raise InvoiceError(f"this advance waits for {name_of(db, str(deputy or row[0]))}")


@proposals.executor("advance.approve")
def approve(db: Session, payload: dict[str, Any], actor: Actor) -> dict[str, Any]:
    """The named person approved the advance (no code: the run that pays it carries one)."""
    request_id = int(payload["request_id"])
    row = (
        db.execute(text("select * from advance_request where id = :i for update"), {"i": request_id}).mappings().first()
    )
    if row is None or row["status"] != "requested":
        raise InvoiceError(f"advance request {request_id} is not waiting for approval")
    db.execute(
        text("update advance_request set status = 'approved', approved_by = :u, approved_at = now() where id = :i"),
        {"u": org_code_of(db, int(actor.id)) if actor.kind == "user" else actor.id, "i": request_id},
    )
    audit.record(db, actor, "advance.approve", "advance_request", str(request_id),
                 before={"status": "requested"}, after={"status": "approved"})  # fmt: skip
    db.commit()
    return {"request": request_id, "status": "approved"}


def reject(db: Session, request_id: int, user_id: int, reason: str) -> None:
    if not reason.strip():
        raise InvoiceError("a reason is needed to reject an advance")
    row = (
        db.execute(text("select * from advance_request where id = :i for update"), {"i": request_id}).mappings().first()
    )
    if row is None:
        raise InvoiceError(f"advance request {request_id} does not exist")
    if row["status"] != "requested":
        raise InvoiceError(f"advance request {request_id} is {row['status']}, not waiting")
    me = org_code_of(db, user_id)
    if me not in {row["approver"]} and me != approval_rules.deputy_on(db, str(row["approver"]), business_today()):
        raise InvoiceError(f"this advance waits for {name_of(db, str(row['approver']))}")
    db.execute(
        text("update advance_request set status = 'rejected', cancel_reason = :w, approved_by = :u where id = :i"),
        {"w": reason, "u": me, "i": request_id},
    )
    if row["proposal_id"]:
        db.execute(
            text(
                "update proposal set status = 'rejected', decided_by = :u, decided_at = now(), decision_note = :w where id = :p"
            ),
            {"u": user_id, "w": reason, "p": row["proposal_id"]},
        )
    audit.record(db, user(user_id), "advance.reject", "advance_request", str(request_id), reason=reason)
    db.commit()


# ---- on settlement: take it off the order's invoices ------------------------------------------


def use_against_invoices(db: Session, payment_no: str) -> list[str]:
    """A settled advance is taken off the order's booked, unpaid invoices, received order first (12.6.4)."""
    pay = (
        db.execute(
            text("select p.*, a.* from payment p join advance a on a.payment_no = p.no where p.no = :n"),
            {"n": payment_no},
        )
        .mappings()
        .first()
    )
    if pay is None:
        return []
    used_on: list[str] = []
    invoices = [
        str(r[0])
        for r in db.execute(
            text(
                "select i.no from invoice i join invoice_line il on il.invoice_no = i.no "
                "where il.po_no = :p and i.type = 'invoice' and i.status = 'approved' and i.booked_at is not null "
                "and i.in_run is null and i.advance_used < i.total order by i.received_on, i.no"
            ),
            {"p": pay["po_no"]},
        )
    ]
    for invoice_no in invoices:
        left_gross = D(
            db.execute(
                text("select gross - used_gross from advance where payment_no = :p"), {"p": payment_no}
            ).scalar_one()
        )
        left_tax = D(
            db.execute(
                text("select taxable - used_taxable from advance where payment_no = :p"), {"p": payment_no}
            ).scalar_one()
        )
        if left_gross <= 0:
            break
        inv = db.execute(text("select * from invoice where no = :n for update"), {"n": invoice_no}).mappings().one()
        g, t = rules.advance_use(
            left_gross,
            left_tax,
            D(inv["total"]) - D(inv["advance_used"]),
            D(inv["taxable"]) - D(inv["advance_taxable_used"]),
        )
        if g <= 0:
            continue
        db.execute(
            text(
                "update advance set used_gross = used_gross + :g, used_taxable = used_taxable + :t where payment_no = :p"
            ),
            {"g": g, "t": t, "p": payment_no},
        )
        db.execute(
            text(
                "insert into advance_adjustment (advance_payment_no, invoice_no, amount, taxable) values (:p, :i, :g, :t) "
                "on conflict (advance_payment_no, invoice_no) do update set amount = advance_adjustment.amount + :g, "
                "taxable = advance_adjustment.taxable + :t"
            ),
            {"p": payment_no, "i": invoice_no, "g": g, "t": t},
        )
        db.execute(
            text(
                "update invoice set advance_used = advance_used + :g, advance_taxable_used = advance_taxable_used + :t where no = :n"
            ),
            {"g": g, "t": t, "n": invoice_no},
        )
        from p2p.invoices import tds  # noqa: PLC0415 - tds builds on this module

        tds.rebuild(db, invoice_no, audit.SYSTEM, f"advance {payment_no} settled")
        used_on.append(invoice_no)
    _mark_used(db, payment_no)
    audit.record(db, audit.SYSTEM, "advance.use", "payment", payment_no,
                 after={"invoices": used_on})  # fmt: skip
    return used_on


def _mark_used(db: Session, payment_no: str) -> None:
    row = (
        db.execute(text("select request_id, gross, used_gross from advance where payment_no = :p"), {"p": payment_no})
        .mappings()
        .first()
    )
    if row is None:
        return
    if D(row["used_gross"]) >= D(row["gross"]):
        db.execute(text("update advance_request set status = 'used' where id = :i"), {"i": row["request_id"]})


def unused(db: Session, request_id: int) -> Decimal:
    row = db.execute(
        text(
            "select a.gross - a.used_gross, a.taxable - a.used_taxable from advance_request r "
            "left join advance a on a.request_id = r.id where r.id = :i"
        ),
        {"i": request_id},
    ).first()
    return D(row[0] or 0) if row else D(0)


# ---- cancelling the order, and recovery -------------------------------------------------------


def cancel_problem(db: Session, po_no: str) -> str | None:
    """What stops an order being cancelled because of a paid advance (AT-06), in plain words."""
    row = db.execute(
        text(
            "select a.payment_no, a.gross - a.used_gross from advance a join advance_request r on r.id = a.request_id "
            "where r.po_no = :p and r.status in ('paid', 'recovering') and a.gross > a.used_gross "
            "order by a.payment_no limit 1"
        ),
        {"p": po_no},
    ).first()
    if row is None:
        return None
    return f"an advance of {format_inr(D(row[1]))} was paid on {po_no}: recover it before cancelling"


def cancel_with_order(db: Session, po_no: str, actor: Actor, reason: str) -> None:
    """The order is cancelled: a live request not yet paid is cancelled with it (one in a run is taken out first)."""
    rows = db.execute(
        text(
            "select r.id, r.status, r.payment_no, p.run_no from advance_request r "
            "left join payment p on p.no = r.payment_no where r.po_no = :p and r.status = any(:s)"
        ),
        {"p": po_no, "s": ["requested", "approved", "in_run", "used"]},
    ).mappings()
    for r in rows:
        if r["status"] == "in_run" and r["run_no"]:
            from p2p.invoices import runs  # noqa: PLC0415 - the run service builds on this module

            runs._remove_payment(db, str(r["run_no"]), str(r["payment_no"]), f"order cancelled: {reason}", actor)  # noqa: SLF001
        db.execute(
            text("update advance_request set status = 'cancelled', cancel_reason = :w where id = :i"),
            {"w": reason, "i": r["id"]},
        )


def recover(db: Session, request_id: int, user_id: int, reason: str) -> dict[str, Any]:
    """Raise a debit note for an advance paid and not used, so the order can be cancelled and the money recovered."""
    if not reason.strip():
        raise InvoiceError("a reason is needed to recover an advance")
    row = (
        db.execute(text("select * from advance_request where id = :i for update"), {"i": request_id}).mappings().first()
    )
    if row is None:
        raise InvoiceError(f"advance request {request_id} does not exist")
    if row["status"] not in ("paid", "in_run"):
        raise InvoiceError(f"advance request {request_id} is {row['status']}: nothing is paid to recover")
    left = db.execute(
        text("select gross - used_gross, taxable - used_taxable from advance where request_id = :i"), {"i": request_id}
    ).first()
    gross, taxable = (D(left[0] or 0), D(left[1] or 0)) if left else (D(0), D(0))
    if gross <= 0:
        raise InvoiceError("the advance has been used: there is nothing to recover")
    from p2p.invoices import notes  # noqa: PLC0415 - notes builds on this module

    po = db.execute(text("select supplier_code from po where no = :p"), {"p": row["po_no"]}).scalar_one()
    gst_pct = D("18")
    note = notes.raise_debit_note(
        db=db,
        supplier=str(po),
        taxable=taxable,
        gst_pct=gst_pct,
        reason=f"Advance recovered on {row['po_no']}: {reason.strip()}",
        source_ref=f"advance:{row['payment_no']}",
        user_id=user_id,
    )
    db.execute(
        text("update advance_request set status = 'recovering', recovery_note = :n where id = :i"),
        {"n": note, "i": request_id},
    )
    audit.record(
        db, user(user_id), "advance.recover", "advance_request", str(request_id),
        after={"note": note, "gross": gross, "taxable": taxable}, reason=reason,
    )  # fmt: skip
    db.commit()
    return {"note": note, "gross": gross, "taxable": taxable}


def listing(db: Session) -> list[dict[str, Any]]:
    rows = db.execute(
        text(
            "select r.*, po.supplier_code, coalesce(s.trade_name, s.legal_name) as supplier, "
            "a.payment_no as advance_payment, a.gross - a.used_gross as unused "
            "from advance_request r join po on po.no = r.po_no join supplier s on s.code = po.supplier_code "
            "left join advance a on a.request_id = r.id order by r.requested_at desc, r.id desc"
        )
    ).mappings()
    return [dict(r) for r in rows]


def get(db: Session, request_id: int) -> dict[str, Any]:
    row = db.execute(text("select * from advance_request where id = :i"), {"i": request_id}).mappings().first()
    if row is None:
        raise InvoiceError(f"advance request {request_id} does not exist")
    return dict(row)
