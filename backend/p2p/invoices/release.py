"""Releasing a blocked invoice: a proposal a person approves (rule 5, block 11).

A price variance is accepted only by the person the `price_variance` approval rule names for its value on the business
date, never by an order maker, the person who entered the invoice, the person who asked, a requester or the person who
received the goods. A quantity or service hold is never released: a receipt, a service confirmation or a corrected
invoice clears it. A release that would put the invoiced quantity above what was accepted fails at execution, with the
reason, and the invoice stays as it was (DF-11, AT-05).
"""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from p2p.approvals import rules as approval_rules
from p2p.approvals.workflow import name_of, org_code_of
from p2p.formulas import matching as rules
from p2p.formulas.calendar import business_today
from p2p.formulas.money import format_inr, format_qty
from p2p.invoices import service as invoices
from p2p.orders import workflow as po_workflow
from p2p.platform import audit, notifications, permissions, proposals
from p2p.platform.sod import Actor, user

ACTION = "invoice.release_block"
D = Decimal


@dataclass(frozen=True)
class Requested:
    proposal_id: int
    kind: str  # price or other
    variance: Decimal
    approver: str | None


def _open_rows(db: Session, no: str) -> list[dict[str, Any]]:
    return [
        dict(r)
        for r in db.execute(
            text(
                "select invoice_line_no, reason_codes, reasons, variance_value from match_result "
                "where invoice_no = :n and result = 'blocked' and resolved_at is null order by invoice_line_no nulls first"
            ),
            {"n": no},
        ).mappings()
    ]


def _reasons(row: dict[str, Any]) -> list[str]:
    return list(row["reasons"] or ([row["reason_codes"]] if row["reason_codes"] else []))


def _open_qty_or_service(db: Session, no: str) -> bool:
    return any(r in (rules.QTY, rules.SERVICE) for row in _open_rows(db, no) for r in _reasons(row))


def _price_lines(db: Session, no: str) -> list[dict[str, Any]]:
    """The blocked price lines of the invoice, with the amounts the summary needs."""
    return [
        dict(r)
        for r in db.execute(
            text(
                "select m.invoice_line_no, coalesce(m.variance_value, 0) as variance_value, "
                "l.qty, l.unit_price, l.discount, l.description, l.po_no, l.po_line_no, pl.unit_price as order_price "
                "from match_result m join invoice_line l on l.invoice_no = m.invoice_no and l.line_no = m.invoice_line_no "
                "join po_line pl on pl.po_no = l.po_no and pl.line_no = l.po_line_no "
                "where m.invoice_no = :n and m.result = 'blocked' and m.resolved_at is null "
                "and :price = any(coalesce(m.reasons, array[m.reason_codes])) order by m.invoice_line_no"
            ),
            {"n": no, "price": rules.PRICE},
        ).mappings()
    ]


def _variance(db: Session, no: str) -> Decimal:
    rows = _price_lines(db, no)
    total = sum((D(r["variance_value"] or 0) for r in rows), D(0))
    if total == 0:  # rows written before the variance column: work it out from the line
        total = sum(
            (
                ((D(r["unit_price"]) - D(r["discount"]) - D(r["order_price"])) * D(r["qty"])).quantize(D("0.01"))
                for r in rows
            ),
            D(0),
        )
    return total.quantize(D("0.01"))


def _po_of(db: Session, no: str) -> str | None:
    return db.execute(
        text("select po_no from invoice_line where invoice_no = :n order by line_no limit 1"), {"n": no}
    ).scalar()


def _refused_people(db: Session, no: str, entered_by: str | None, asker: int, price_only: bool) -> frozenset[str]:
    """The people who may never approve this release (11.5.3), as organisation codes where there is one."""
    people: set[str] = set()
    entered = int(entered_by) if entered_by and entered_by.isdigit() else None
    for code in (org_code_of(db, entered) if entered is not None else None, org_code_of(db, asker)):
        if code:
            people.add(code)
    requester = {
        str(r[0])
        for r in db.execute(
            text(
                "select distinct r.requester from invoice_line il join po_line pl on pl.po_no = il.po_no "
                "and pl.line_no = il.po_line_no join request r on r.no = pl.request_no where il.invoice_no = :n "
                "and r.requester is not null "
                "union select distinct r.on_behalf_of from invoice_line il join po_line pl on pl.po_no = il.po_no "
                "and pl.line_no = il.po_line_no join request r on r.no = pl.request_no "
                "where il.invoice_no = :n and r.on_behalf_of is not null"
            ),
            {"n": no},
        )
        if r[0]
    }
    people |= {c for c in requester if c}
    people |= {
        str(r[0])
        for r in db.execute(
            text(
                "select distinct g.received_by from invoice_line il join gr_line gl on gl.po_no = il.po_no "
                "and gl.po_line_no = il.po_line_no join goods_receipt g on g.no = gl.gr_no where il.invoice_no = :n"
            ),
            {"n": no},
        )
    }
    if price_only:
        po_no = _po_of(db, no)
        if po_no:
            po = dict(db.execute(text("select * from po where no = :n"), {"n": po_no}).mappings().one())
            people |= set(po_workflow.makers(db, po))
    return frozenset(people)


def price_approver(
    db: Session, value: Decimal, *, exclude: frozenset[str], on: date | None = None
) -> approval_rules.Step:
    """The first person the price variance rule names for this value, excluding the people who may not decide it."""
    try:
        rule = approval_rules.find_rule(db, "price_variance", value, on or business_today())
    except approval_rules.ApprovalRuleError:
        rule = None
    steps: list[approval_rules.Step] = []
    if rule is not None:
        try:
            steps = approval_rules.resolve_chain(
                db, rule.roles, requester=None, department=None, on_date=on or business_today(), exclude=exclude
            )
        except approval_rules.ApprovalRuleError:
            steps = []
    if not steps:
        raise invoices.InvoiceError(
            "the price variance rule names nobody who may accept this price; the rule has to name someone else"
        )
    return steps[0]


def _person(db: Session, code: str | None) -> tuple[str, str] | None:
    if not code:
        return None
    row = db.execute(text("select name, designation from org_user where code = :c"), {"c": code}).first()
    return (str(row[0]), str(row[1] or "approver")) if row else None


def release_view(db: Session, no: str) -> dict[str, Any]:
    """What the invoice screen says about releasing it: allowed, why not, which kind, the variance and who decides."""
    inv = (
        db.execute(text("select status, payment_block, entered_by from invoice where no = :n"), {"n": no})
        .mappings()
        .first()
    )
    if inv is None:
        return {"allowed": False, "why_not": "It does not exist", "kind": None, "variance": None, "approver_name": None}
    if inv["status"] != "blocked":
        return {
            "allowed": False,
            "why_not": f"It is {inv['status']}, not blocked",
            "kind": None,
            "variance": None,
            "approver_name": None,
        }
    if inv["payment_block"] == "duplicate":
        return {
            "allowed": False,
            "why_not": "A duplicate is never paid",
            "kind": None,
            "variance": None,
            "approver_name": None,
        }
    reasons = [r for row in _open_rows(db, no) for r in _reasons(row)]
    if rules.QTY in reasons or rules.SERVICE in reasons:
        return {
            "allowed": False,
            "why_not": "Waits for the goods (or the service confirmation); it is matched again by itself",
            "kind": None,
            "variance": None,
            "approver_name": None,
        }
    if rules.PRICE in reasons:
        variance = _variance(db, no)
        try:
            step = price_approver(db, variance, exclude=_refused_people(db, no, inv["entered_by"], 0, True))
        except invoices.InvoiceError as exc:
            return {
                "allowed": False,
                "why_not": str(exc),
                "kind": "price",
                "variance": str(variance),
                "approver_name": None,
            }
        person = _person(db, step.approver)
        return {
            "allowed": True,
            "why_not": None,
            "kind": "price",
            "variance": str(variance),
            "approver_name": person[0] if person else name_of(db, step.approver),
            "approver": step.approver,
            "role": person[1] if person else None,
        }
    return {"allowed": True, "why_not": None, "kind": "other", "variance": None, "approver_name": None}


def _summary(db: Session, no: str, reason: str, price: list[dict[str, Any]], variance: Decimal) -> str:
    if price:
        first = price[0]
        invoice_price = D(first["unit_price"]) - D(first["discount"])
        return (
            f"Accept the price variance of {format_inr(variance)} on {no} ({first['description']}: "
            f"{format_inr(invoice_price)} against {format_inr(first['order_price'])} on {first['po_no']}) and pay it. "
            f"Reason: {reason}"
        )
    reasons = [r for row in _open_rows(db, no) for r in _reasons(row)]
    words = ", ".join(sorted({_reason_word(r) for r in reasons})) or "the block"
    return f"Pay {no} despite the block ({words}). Reason: {reason}"


def _reason_word(reason: str) -> str:
    return {
        rules.GST: "GST differs from the order",
        rules.ARITH: "the invoice does not add up",
        rules.IRN: "no IRN",
        rules.GSTIN: "the GSTIN does not match",
        rules.TAX: "GST is not allowed",
        rules.TAX_HEAD: "the tax heads do not match",
    }.get(reason, reason.replace("_", " "))


def request_release(db: Session, no: str, reason: str, user_id: int, *, tenant: str | None = None) -> int:
    """Ask for a blocked invoice to be paid anyway. Nothing is paid until the right person approves (rule 5)."""
    if not reason.strip():
        raise invoices.InvoiceError("a reason is needed")
    inv = (
        db.execute(
            text(
                "select status, total, payment_block, block_reason, supplier_code, entered_by from invoice where no = :n"
            ),
            {"n": no},
        )
        .mappings()
        .first()
    )
    if inv is None:
        raise invoices.InvoiceError(f"{no} does not exist")
    if inv["status"] != "blocked":
        raise invoices.InvoiceError(f"{no} is {inv['status']}, not blocked")
    if inv["payment_block"] == "duplicate":
        raise invoices.InvoiceError("a duplicate is never paid")
    pending = db.execute(
        text(
            "select 1 from proposal where action = 'invoice.release_block' and status = 'pending' and payload->>'invoice' = :n"
        ),
        {"n": no},
    ).first()
    if pending:
        raise invoices.InvoiceError("a release for this invoice is already waiting for approval")
    # NEEDS block 10: a bill without an order (non-PO) is never three-way matched and its block is never released here
    reasons = [r for row in _open_rows(db, no) for r in _reasons(row)]
    if rules.QTY in reasons or rules.SERVICE in reasons:
        raise invoices.InvoiceError(
            "a quantity or service hold is cleared by a goods receipt, a service confirmation or a corrected invoice; "
            "it is never released"
        )
    price = _price_lines(db, no)
    variance = _variance(db, no) if price else D(0)
    payload: dict[str, Any] = {"invoice": no, "reason": reason.strip(), "asked_by": str(user_id)}
    if tenant:
        payload["tenant"] = tenant
    if price:
        step = price_approver(db, variance, exclude=_refused_people(db, no, inv["entered_by"], user_id, True))
        payload.update(
            {
                "kind": "price",
                "approver": step.approver,
                "delegate": step.delegate,
                "variance": str(variance),
                "lines": [int(r["invoice_line_no"]) for r in price],
            }
        )
    else:
        payload.update({"kind": "other"})
    summary = _summary(db, no, reason.strip(), price, variance)
    try:
        pid = proposals.propose(
            db,
            user(user_id),
            action=ACTION,
            summary=summary,
            payload=payload,
            required_permission="invoice.approve",
            risk="medium",
            value=variance if price else None,
        )
    except Exception as exc:  # the unique open-release index: another asker won the race
        db.rollback()
        if "ux_proposal_open_release" in str(exc) or "duplicate key" in str(exc):
            raise invoices.InvoiceError("a release for this invoice is already waiting for approval") from None
        raise
    if price and tenant:
        who = notifications.login_of(db, tenant, step.delegate or step.approver)
        if who:
            notifications.notify(
                db,
                tenant,
                who,
                "price_variance",
                f"Accept a price variance on {no}: {format_inr(variance)}",
                summary,
                link="/admin/approvals",
            )
    return pid


def _receipt_person(db: Session, no: str, user_id: int) -> bool:
    """Whether this login received (or inspected) the goods on any line of the invoice (rule 7)."""
    me = org_code_of(db, user_id)
    if (
        me
        and db.execute(
            text(
                "select 1 from invoice_line il join gr_line gl on gl.po_no = il.po_no and gl.po_line_no = il.po_line_no "
                "join goods_receipt g on g.no = gl.gr_no where il.invoice_no = :n and g.received_by = :me limit 1"
            ),
            {"n": no, "me": me},
        ).first()
    ):
        return True
    # NEEDS block 9: inspection by another person records the inspector; the audit row is the honest record until then
    return bool(
        db.execute(
            text(
                "select 1 from audit_event a join goods_receipt g on g.no = a.entity_id "
                "join gr_line gl on gl.gr_no = g.no join invoice_line il on il.po_no = gl.po_no and il.po_line_no = gl.po_line_no "
                "where a.action = 'grn.inspect' and a.actor_type = 'user' and a.actor_id = :u and il.invoice_no = :n limit 1"
            ),
            {"n": no, "u": str(user_id)},
        ).first()
    )


@proposals.guard(ACTION)
def _guard(db: Session, payload: dict[str, Any], actor: Actor) -> None:
    """Who may never approve this release (11.5.3): a refused person leaves the proposal waiting."""
    if actor.kind != "user":
        raise permissions.PermissionDenied("only a person approves a release")
    no = str(payload["invoice"])
    inv = db.execute(text("select entered_by from invoice where no = :n"), {"n": no}).first()
    if inv is None:
        raise invoices.InvoiceError(f"{no} does not exist")
    if (inv[0] or "") == actor.id:
        raise permissions.PermissionDenied(
            "segregation of duties: the person who entered the invoice cannot release its block"
        )
    asker = str(payload.get("asked_by") or "")
    if asker and asker == actor.id:
        raise permissions.PermissionDenied(
            "segregation of duties: the person who asked for the release cannot also approve it"
        )
    me = org_code_of(db, int(actor.id))
    if db.execute(
        text(
            "select 1 from invoice_line il join po_line pl on pl.po_no = il.po_no and pl.line_no = il.po_line_no "
            "left join request r on r.no = pl.request_no where il.invoice_no = :n and (:me in (r.requester, r.on_behalf_of) "
            "or exists (select 1 from service_entry e where e.po_no = il.po_no and e.po_line_no = il.po_line_no "
            "and e.accepted_by = :me)) limit 1"
        ),
        {"n": no, "me": me},
    ).first():
        raise permissions.PermissionDenied(
            "segregation of duties: the person who asked for or confirmed what is invoiced cannot also approve the invoice"
        )
    if _receipt_person(db, no, int(actor.id)):
        raise permissions.PermissionDenied(
            "segregation of duties: the person who received or inspected the goods cannot also approve the invoice"
        )
    if payload.get("kind") == "price":
        po_no = _po_of(db, no)
        if po_no:
            po = dict(db.execute(text("select * from po where no = :n"), {"n": po_no}).mappings().one())
            if me and me in po_workflow.makers(db, po):
                raise permissions.PermissionDenied(
                    "segregation of duties: a person who made the order cannot accept its price variance"
                )
        if me not in {str(payload.get("approver") or ""), str(payload.get("delegate") or "")}:
            raise permissions.PermissionDenied("only the person the price variance rule names may accept this price")


def _has_approved_by(db: Session) -> bool:
    """Whether block 10's `invoice.approved_by` column is in this database (it arrives with block 10's migration)."""
    return bool(
        db.execute(
            text(
                "select 1 from information_schema.columns where table_name = 'invoice' and column_name = 'approved_by'"
            )
        ).first()
    )


def _took_goods(db: Session, po_no: str, line_no: int, no: str) -> str | None:
    return db.execute(
        text(
            "select i.no from invoice_line l join invoice i on i.no = l.invoice_no "
            "where l.po_no = :p and l.po_line_no = :l and i.no <> :n and i.type = 'invoice' "
            "and i.status = any(:st) order by i.received_on desc, i.no desc limit 1"
        ),
        {"p": po_no, "l": line_no, "n": no, "st": list(invoices.BOOKED)},
    ).scalar()


@proposals.executor(ACTION)
def _release(db: Session, payload: dict[str, Any], actor: Actor) -> dict[str, Any]:
    """Re-check under the invoice and order-line locks, then release (DF-11). The caller commits the effect."""
    no, reason = str(payload["invoice"]), str(payload.get("reason") or "")
    inv = db.execute(text("select * from invoice where no = :n for update"), {"n": no}).mappings().first()
    if inv is None or inv["status"] != "blocked":
        raise invoices.InvoiceError(f"{no} is no longer blocked")
    invoices.lock_lines(
        db,
        [
            (r[0], r[1])
            for r in db.execute(text("select po_no, po_line_no from invoice_line where invoice_no = :n"), {"n": no})
        ],
    )
    open_reasons = {r for row in _open_rows(db, no) for r in _reasons(row)}
    if rules.QTY in open_reasons or rules.SERVICE in open_reasons:
        raise invoices.InvoiceError(
            f"{no} can no longer be released: a quantity or service hold is cleared only by a receipt, "
            "a service confirmation or a corrected invoice"
        )
    price_lines = {int(r) for r in payload.get("lines") or []}
    for ln in db.execute(
        text(
            "select line_no, po_no, po_line_no, qty, unit_price, discount, description from invoice_line "
            "where invoice_no = :n order by line_no"
        ),
        {"n": no},
    ).mappings():
        available = invoices._available(db, ln["po_no"], ln["po_line_no"], D(
            db.execute(
                text("select received_qty from po_line where po_no = :p and line_no = :l"),
                {"p": ln["po_no"], "l": ln["po_line_no"]},
            ).scalar_one()
        ), no)  # fmt: skip
        if D(ln["qty"]) > available:
            other = _took_goods(db, ln["po_no"], ln["po_line_no"], no)
            detail = f" ({other} took the goods since)" if other else ""
            raise invoices.InvoiceError(
                f"{no} can no longer be released: line {ln['line_no']} (order line {ln['po_line_no']}): "
                f"{format_qty(D(ln['qty']) - available)} more than can be invoiced{detail}; match it again"
            )
    variance = D(payload.get("variance") or 0) if payload.get("kind") == "price" else D(0)
    db.execute(
        text(
            "update invoice set status = 'approved', payment_block = 'none', block_reason = null, "
            "variance_accepted = :v, match_rules = 2 where no = :n"
        ),
        {"v": variance, "n": no},
    )
    if _has_approved_by(db):  # NEEDS block 10: invoice.approved_by (block 10's 0170 column)
        db.execute(text("update invoice set approved_by = :a where no = :n"), {"a": actor.id, "n": no})
    rows = db.execute(
        text(
            "update match_result set resolved_by = :u, resolved_at = now(), resolution = :r, "
            "resolution_kind = case when invoice_line_no = any(:price) then 'price_accepted' else 'released' end "
            "where invoice_no = :n and result = 'blocked' and resolved_at is null returning invoice_line_no"
        ),
        {"u": actor.id, "r": reason, "n": no, "price": list(price_lines) or [-1]},
    ).all()
    from p2p.invoices import payment  # noqa: PLC0415 - payment builds on this module

    payment.book_invoice(db, no)
    invoices._refresh_invoiced(
        db,
        [
            (r[0], r[1])
            for r in db.execute(text("select po_no, po_line_no from invoice_line where invoice_no = :n"), {"n": no})
        ],
    )
    audit.record(
        db, actor, "invoice.release_block", "invoice", no, before={"status": "blocked"},
        after={"status": "approved", "lines": [r[0] for r in rows]}, reason=reason,
    )  # fmt: skip
    if variance > 0:
        audit.record(
            db, actor, "invoice.variance_accepted", "invoice", no,
            after={"variance": str(variance), "lines": sorted(price_lines)},
            rule_version="VAR-01 v1",
        )  # fmt: skip
    return {"invoice": no, "status": "approved", "variance": str(variance)}


@proposals.after_execution(ACTION)
def _after(
    db: Session, proposal: proposals.ProposalRef, execution: str, result: dict[str, Any] | None, actor: Actor
) -> None:
    """Tell the asker what happened: approved, or why it could not be released (11.5.6)."""
    if proposal.proposed_by_type != "user":
        return
    tenant = _tenant(db, proposal)
    if tenant is None:
        return
    no = str(proposal.payload.get("invoice"))
    who = name_of(db, _actor_code(db, int(actor.id))) if str(actor.id).isdigit() else "someone"
    if execution == "ok":
        title = f"{no} was released by {who}"
    else:
        why = (result or {}).get("error", "the re-check failed")
        title = f"{no} could not be released: {why}"
    notifications.notify(db, tenant, int(proposal.proposed_by_id), "invoice_release", title)


@proposals.rejected(ACTION)
def _rejected(db: Session, proposal: proposals.ProposalRef, actor: Actor, reason: str) -> None:
    """Tell the asker a release was rejected, and why."""
    if proposal.proposed_by_type != "user":
        return
    tenant = _tenant(db, proposal)
    if tenant is None:
        return
    no = str(proposal.payload.get("invoice"))
    notifications.notify(db, tenant, int(proposal.proposed_by_id), "invoice_release", f"{no} was rejected: {reason}")


def _actor_code(db: Session, user_id: int) -> str | None:
    return org_code_of(db, user_id)


def _tenant(db: Session, proposal: proposals.ProposalRef) -> str | None:
    slug = proposal.payload.get("tenant")
    if slug:
        return str(slug)
    slug = db.info.get("slug") if hasattr(db, "info") else None
    return str(slug) if slug else None
