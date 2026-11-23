"""The supplier communication agent (AG02): reads what suppliers write about orders, acts on what is small, hands over the rest.

An email linked to a purchase order (or an RFQ) from one of the supplier's known addresses is read by an AI step that
only classifies it and copies the dates, quantities and prices it states (ai_steps/supplier_message.py). Code does
the rest:

- an acknowledgement is recorded, and a later promised date is taken only within the company's tolerance;
- a change of delivery date or a delay within 'supplier_date_days' moves the promised date, and a quantity within
  'supplier_change_pct' is noted as a commitment: the order itself, and so its value, is never changed here;
- a price change, a substitution, a rejection, a question, a larger change, a request to change bank details: a task
  for the right person with a drafted reply, and nothing applied. A bank change is never applied by anything;
- a date, quantity or price change and a refusal that are not applied also become proposed changes, old value to new,
  that the buyer accepts or rejects on the order page (orders/confirmation.py): only an accepted one changes the order;
- a dispatch (all of it, or part) is stored as a dispatch note for the store (orders/dispatch_notes.py), checked against
  what is still to ship.

Applying a small change and sending a reminder go through the autonomy gate ('supplier.change', 'supplier.remind'); when
the gate says no, the same thing becomes a task for a buyer. Mail from anyone but a known contact of the supplier is not
shown to a model. A daily job chases suppliers that have not acknowledged an order and the balance of part deliveries.
"""

import re
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from email.utils import parseaddr
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from p2p.agents import autonomy
from p2p.ai_steps import supplier_message as step
from p2p.ai_steps.runner import run_step
from p2p.formulas import supplier_changes as rules
from p2p.formulas.calendar import IST, business_today
from p2p.formulas.money import format_inr, format_qty
from p2p.invoices import tolerances
from p2p.orders import confirmation, dispatch, dispatch_notes
from p2p.platform import audit, emails, routing, tasks, users, worker
from p2p.platform.sod import Actor

AGENT = Actor("agent", "supplier_comms")
DECISION_CHANGE = "supplier.change"
DECISION_REMIND = "supplier.remind"
JOB = "supplier_chase"
RUN_AT = time(9, 45)  # India time
ACTIVE = ("sent", "acknowledged", "part_received")


@dataclass
class Handled:
    status: str  # applied, task, noted, ignored, needs_person, handled_elsewhere
    kind: str | None
    action: str
    task_id: int | None = None
    reply_draft: str | None = None
    notes: list[str] = field(default_factory=list)


# ---- settings and people ----------------------------------------------------------------------


def _tolerance(db: Session) -> tuple[int, Decimal]:
    """The supplier change limits on the day the change is read, from the tolerance version in force then."""
    tol = tolerances.in_force(db, business_today())
    return int(tol.decimal("supplier_date_days", "3")), tol.decimal("supplier_change_pct", "5")


def _setting(db: Session, key: str, default: str) -> str:
    value = db.execute(text("select value from org_setting where key = :k"), {"k": key}).scalar()
    return str(value) if value not in (None, "") else default


def _task(
    db: Session, tenant: str, kind: str, title: str, roles: tuple[str, ...], sla: int, payload: dict[str, Any]
) -> int:
    return tasks.create_task(
        db,
        tenant,
        kind=kind,
        title=title[:200],
        assignee=users.first_with_role(db, *roles),
        sla_hours=sla,
        payload=payload,
        actor=AGENT,
    )


# ---- the order the email is about -------------------------------------------------------------


def _order(db: Session, po_no: str) -> dict[str, Any] | None:
    po = db.execute(text("select * from po where no = :n"), {"n": po_no}).mappings().first()
    if po is None:
        return None
    out = dict(po)
    out["lines"] = [
        dict(r)
        for r in db.execute(
            text("select * from po_line where po_no = :n and status <> 'cancelled' order by line_no"), {"n": po_no}
        ).mappings()
    ]
    return out


def _current_promised(order: dict[str, Any]) -> date | None:
    open_lines = [ln for ln in order["lines"] if ln["status"] in ("open", "part_received")]
    promised: list[date] = [ln["promised_date"] for ln in open_lines if ln["promised_date"]]
    if promised:
        return max(promised)
    needed: list[date] = [ln["need_by"] for ln in open_lines if ln["need_by"]]
    return max(needed) if needed else None


def _norm(value: str) -> str:
    return " ".join(re.sub(r"[^\w\s]", " ", value.casefold()).split())


def _line_for(order: dict[str, Any], item: str | None) -> dict[str, Any] | None:
    """The order line a message is about: the only open line, or the one whose name or code the message mentions."""
    open_lines = [ln for ln in order["lines"] if ln["status"] in ("open", "part_received")]
    if len(open_lines) == 1:
        only: dict[str, Any] = open_lines[0]
        return only
    if item:
        wanted = _norm(item)
        for ln in open_lines:
            name, code = _norm(ln["description"] or ""), _norm(ln["item_code"] or "")
            if wanted and (wanted in name or name in wanted or (code and code == wanted)):
                found: dict[str, Any] = ln
                return found
    return None


def _supplier_of(db: Session, linked_type: str, ref: str, from_addr: str) -> tuple[str | None, str | None]:
    """The supplier whose known address the email came from (and that supplier's name), or None for anyone else."""
    address = parseaddr(from_addr)[1].strip().lower()
    if not address:
        return None, None
    if linked_type == "po":
        row = db.execute(
            text(
                "select po.supplier_code, coalesce(s.trade_name, s.legal_name) from po join supplier s on s.code = po.supplier_code "
                "where po.no = :r and (lower(coalesce(po.sent_to, '')) = :a or exists (select 1 from supplier_contact c "
                "where c.supplier_code = po.supplier_code and lower(c.email) = :a))"
            ),
            {"r": ref, "a": address},
        ).first()
    else:
        row = db.execute(
            text(
                "select rs.supplier_code, coalesce(s.trade_name, s.legal_name) from rfq_supplier rs join supplier s on s.code = rs.supplier_code "
                "where rs.rfq_no = :r and (lower(coalesce(rs.email, '')) = :a or exists (select 1 from supplier_contact c "
                "where c.supplier_code = rs.supplier_code and lower(c.email) = :a))"
            ),
            {"r": ref, "a": address},
        ).first()
    return (str(row[0]), str(row[1])) if row else (None, None)


def _contact(db: Session, supplier: str) -> tuple[str, str | None] | None:
    row = db.execute(
        text(
            "select email, name from supplier_contact where supplier_code = :c and email is not null "
            "order by is_primary desc nulls last, id limit 1"
        ),
        {"c": supplier},
    ).first()
    return (str(row[0]), row[1]) if row else None


# ---- the words of the replies -----------------------------------------------------------------


def _company(db: Session) -> str:
    return str(
        db.execute(text("select coalesce(short_name, legal_name) from company limit 1")).scalar() or "Our company"
    )


def _greeting(name: str | None) -> str:
    return f"Dear {name or 'Sir or Madam'},\n\n"


def _sign(db: Session) -> str:
    return f"\nRegards,\nProcurement, {_company(db)}\n"


def _draft_date_noted(db: Session, po_no: str, contact: str | None, requested: date) -> str:
    return (
        _greeting(contact)
        + f"Thank you. We have noted the delivery date of {requested:%d %b %Y} for purchase order {po_no}."
        + "\nPlease keep to it and tell us at once if it changes again.\n"
        + _sign(db)
    )


def _draft_date_refused(
    db: Session, po_no: str, contact: str | None, requested: date | None, latest: date | None
) -> str:
    asked = f"the delivery date of {requested:%d %b %Y}" if requested else "the new delivery date"
    limit = f" The latest we can accept without discussing it is {latest:%d %b %Y}." if latest else ""
    return (
        _greeting(contact)
        + f"We have received your request to move purchase order {po_no} to {asked}.{limit}"
        + " Please tell us whether you can deliver by then, or call us so that we can agree the way forward.\n"
        + _sign(db)
    )


def _draft_price(db: Session, po_no: str, contact: str | None, old: Decimal | None, new: Decimal | None) -> str:
    change = f" from {format_inr(old)} to {format_inr(new)}" if old is not None and new is not None else ""
    return (
        _greeting(contact)
        + f"We have received your request to change the price on purchase order {po_no}{change}. A change of price needs approval "
        + "here and cannot be accepted by email. We will come back to you after review; until then the order stands as placed.\n"
        + _sign(db)
    )


def _draft_substitution(db: Session, po_no: str, contact: str | None, item: str | None) -> str:
    what = f" of {item}" if item else ""
    return (
        _greeting(contact)
        + f"Thank you for offering an alternative{what} against purchase order {po_no}. A different make or item needs approval "
        + "from our side; please do not dispatch it until you hear from us.\n"
        + _sign(db)
    )


def _draft_reject(db: Session, po_no: str, contact: str | None) -> str:
    return (
        _greeting(contact)
        + f"We note that you are unable to accept purchase order {po_no}. Please confirm the reason in writing and whether any part "
        + "of the order can be supplied, so that we can decide how to proceed.\n"
        + _sign(db)
    )


def _draft_qty(db: Session, po_no: str, contact: str | None, qty: Decimal | None, ordered: Decimal | None) -> str:
    return (
        _greeting(contact)
        + f"We have received your message about the quantity on purchase order {po_no}"
        + (
            f" ({format_qty(qty)} against {format_qty(ordered)} ordered)"
            if qty is not None and ordered is not None
            else ""
        )
        + ". Please let us know by when the balance will follow; we will confirm whether we accept the change.\n"
        + _sign(db)
    )


# ---- the agent --------------------------------------------------------------------------------


def handle_email(db: Session, tenant: str, email_id: int, gateway: Any, *, as_of: date | None = None) -> Handled | None:
    """Read one email about an order or an RFQ and do what is small; hand over the rest. None if there is nothing to do."""
    as_of = as_of or business_today()
    mail = (
        db.execute(
            text(
                "select id, from_addr, subject, body_text, linked_type, linked_id from email_message "
                "where id = :i and direction = 'in'"
            ),
            {"i": email_id},
        )
        .mappings()
        .first()
    )
    if mail is None or mail["linked_type"] not in ("po", "rfq") or not mail["linked_id"]:
        return None
    if db.execute(text("select 1 from supplier_message where email_id = :i"), {"i": email_id}).first():
        return None  # already read
    linked_type, ref = str(mail["linked_type"]), str(mail["linked_id"])
    exists = db.execute(
        text("select 1 from po where no = :r" if linked_type == "po" else "select 1 from rfq where no = :r"), {"r": ref}
    ).first()
    if exists is None:
        return None
    supplier, supplier_name = _supplier_of(db, linked_type, ref, mail["from_addr"] or "")
    if supplier is None:
        # an email from someone who is not a known contact of the supplier is never shown to a model
        _store(
            db,
            email_id,
            linked_type,
            ref,
            None,
            None,
            "ignored",
            action="the sender is not a known contact of the supplier",
        )
        audit.record(
            db,
            AGENT,
            "supplier_message.ignored",
            "email",
            email_id,
            reason=f"sender {mail['from_addr']} is not a contact for {ref}",
        )
        db.commit()
        return Handled("ignored", None, "the sender is not a known contact of the supplier, so the email was not read")

    source = f"Subject: {mail['subject'] or ''}\n\n{mail['body_text'] or ''}"
    outcome = run_step(
        db,
        tenant,
        step.STEP,
        source,
        gateway=gateway,
        review_assignee=users.first_with_role(db, "Buyer"),
        entity=linked_type,
        entity_id=ref,
        inputs={
            "ref": f"{'order' if linked_type == 'po' else 'RFQ'} {ref}",
            "supplier": supplier_name,
            "today": as_of.isoformat(),
        },
    )
    model = f"{outcome.completion.provider}/{outcome.completion.model}" if outcome.completion else None
    if outcome.status != "ok" or not isinstance(outcome.value, step.SupplierMessage):
        _store(
            db,
            email_id,
            linked_type,
            ref,
            supplier,
            None,
            "needs_person",
            action="could not be read reliably: " + "; ".join(outcome.failures)[:300],
            task_id=outcome.task_id,
            model=model,
        )
        audit.record(
            db,
            AGENT,
            "supplier_message.unread",
            "email",
            email_id,
            after={"ref": ref},
            reason="; ".join(outcome.failures)[:300],
        )
        db.commit()
        return Handled(
            "needs_person", None, "the reading was not reliable; a person was asked to look at it", outcome.task_id
        )

    reading: step.SupplierMessage = outcome.value
    if linked_type == "po":
        handled = _on_order(db, tenant, ref, supplier, mail, reading, as_of)
    else:
        handled = _on_rfq(db, tenant, ref, supplier, mail, reading)
    if reading.has_embedded_instruction:
        handled.notes.append("the email contained instructions to the system, which were ignored")
        audit.record(
            db,
            AGENT,
            "supplier_message.instruction_ignored",
            "email",
            email_id,
            reason=f"email about {ref} carried instructions to a system",
        )
        _task(
            db,
            tenant,
            "supplier_email_review",
            f"{supplier_name}'s email about {ref} contained instructions to the system (ignored)",
            ("Buyer",),
            48,
            {"email_id": email_id, "ref": ref},
        )
    _store(
        db,
        email_id,
        linked_type,
        ref,
        supplier,
        reading.type,
        handled.status,
        reading=reading,
        action=handled.action,
        task_id=handled.task_id,
        reply_draft=handled.reply_draft,
        model=model,
    )
    audit.record(
        db, AGENT, "supplier_message.read", "email", email_id,
        after={"ref": ref, "supplier": supplier, "kind": reading.type, "status": handled.status, "action": handled.action},
        model=model, prompt_version=step.PROMPT_VERSION,
    )  # fmt: skip
    emails.commit_and_send(db)
    return handled


def _store(
    db: Session, email_id: int, linked_type: str, ref: str, supplier: str | None, kind: str | None, status: str, *,
    reading: step.SupplierMessage | None = None, action: str | None = None, task_id: int | None = None,
    reply_draft: str | None = None, model: str | None = None,
) -> None:  # fmt: skip
    import json  # noqa: PLC0415

    db.execute(
        text(
            "insert into supplier_message (email_id, linked_type, linked_ref, supplier_code, kind, status, reading, action, task_id, "
            "reply_draft, confidence, model, prompt_version) values (:e, :lt, :r, :s, :k, :st, cast(:rd as jsonb), :a, :t, :d, :c, :m, :pv)"
        ),
        {
            "e": email_id, "lt": linked_type, "r": ref, "s": supplier, "k": kind, "st": status,
            "rd": json.dumps(reading.model_dump(mode="json")) if reading else None, "a": action, "t": task_id, "d": reply_draft,
            "c": reading.confidence if reading else None, "m": model, "pv": step.PROMPT_VERSION,
        },
    )  # fmt: skip


def _on_rfq(db: Session, tenant: str, rfq_no: str, supplier: str, mail: Any, reading: step.SupplierMessage) -> Handled:
    """Quotes, refusals and questions about an RFQ are handled where the quote is read. A request for more time and a
    request to change bank details are for a person."""
    if reading.type == "extension_request":
        task = _task(
            db, tenant, "supplier_extension", f"{supplier} asks for more time to quote on {rfq_no}", ("Buyer",), 24,
            {"rfq_no": rfq_no, "email_id": int(mail["id"]), "reason": reading.reason},
        )  # fmt: skip
        return Handled("task", reading.type, "a buyer decides whether to give more time", task)
    if reading.type == "bank_change":
        return _bank_change(db, tenant, rfq_no, supplier, mail)
    return Handled(
        "handled_elsewhere", reading.type, "quotes, refusals and questions on an RFQ are handled with the quote"
    )


def _bank_change(db: Session, tenant: str, ref: str, supplier: str, mail: Any) -> Handled:
    task = _task(
        db, tenant, "bank_change_verification",
        f"VERIFY BY PHONE: {supplier} asks to change its bank account ({ref}). Do NOT change anything from the email",
        ("Finance", "Approver", "Buyer"), 4, {"ref": ref, "email_id": int(mail["id"]), "risk": "fraud pattern"},
    )  # fmt: skip
    return Handled(
        "task",
        "bank_change",
        "a person verifies with the supplier on a number already on file; nothing is changed",
        task,
    )


_CONDITION = re.compile(
    r"\b(only if|only when|only after|provided that|provided you|subject to|on (?:the )?condition|unless|in advance|"
    r"advance payment|prepaid|prepayment|against payment|payment terms?|revised (?:price|rate)|price (?:increase|revision))\b",
    re.I,
)


def _on_order(
    db: Session, tenant: str, po_no: str, supplier: str, mail: Any, reading: step.SupplierMessage, as_of: date
) -> Handled:
    order = _order(db, po_no)
    assert order is not None
    contact = _contact(db, supplier)
    contact_name = contact[1] if contact else None
    kind = reading.type
    if kind == "bank_change":
        return _bank_change(db, tenant, po_no, supplier, mail)
    if kind in ("price_change", "substitution", "reject", "question", "other"):
        return _for_a_person(db, tenant, order, contact_name, reading, mail)
    if kind in ("payment_followup", "invoice_cover"):
        what = "asks about payment" if kind == "payment_followup" else "sent an invoice by email"
        task = _task(
            db, tenant, "supplier_payment_query" if kind == "payment_followup" else "supplier_invoice_email",
            f"{supplier} {what} ({po_no}): {mail['subject'] or ''}"[:200], ("AP", "Finance"), 24,
            {"po_no": po_no, "email_id": int(mail["id"])},
        )  # fmt: skip
        return Handled("task", kind, "accounts deals with it", task)
    if order["status"] not in ACTIVE:
        task = _task(
            db, tenant, "supplier_change", f"{supplier} wrote about {po_no}, which is {order['status']}: {kind}", ("Buyer",), 24,
            {"po_no": po_no, "email_id": int(mail["id"])},
        )  # fmt: skip
        return Handled("task", kind, f"the order is {order['status']}, so nothing was changed", task)
    if kind in ("ack", "date_change", "delay", "partial_dispatch", "qty_change", "dispatch") and _CONDITION.search(
        f"{mail['subject'] or ''} {mail['body_text'] or ''}"
    ):
        # "Confirmed, but only if payment is made in advance" is not an acknowledgement: it changes the terms. Code decides this, not the model.
        task = _task(
            db, tenant, "supplier_change", f"{supplier} set a condition on {po_no}: {mail['subject'] or ''}"[:200], ("Buyer",), 24,
            {"po_no": po_no, "email_id": int(mail["id"]), "kind": kind},
        )  # fmt: skip
        return Handled(
            "task", kind, "the email sets a condition, so a buyer decides; nothing on the order was changed", task
        )
    if kind == "ack":
        return _ack(db, tenant, order, supplier, reading, mail, as_of)
    if kind == "dispatch":
        return _dispatched(db, tenant, order, supplier, reading, mail, as_of)
    if kind in ("date_change", "delay", "partial_dispatch"):
        handled = _date(db, tenant, order, supplier, contact_name, reading, mail)
        if kind == "partial_dispatch" and reading.qty is not None and _line_for(order, reading.item) is not None:
            handled.notes.append(_note_dispatch(db, tenant, order, reading, mail, as_of, whole=False)[1])
        return handled
    if kind == "qty_change":
        return _quantity(db, tenant, order, supplier, contact_name, reading, mail)
    return Handled("noted", kind, "nothing to do")


def _for_a_person(
    db: Session, tenant: str, order: dict[str, Any], contact: str | None, reading: step.SupplierMessage, mail: Any
) -> Handled:
    po_no = order["no"]
    line = _line_for(order, reading.item)
    draft: str | None = None
    detail = ""
    sla = 24
    if reading.type == "price_change":
        change = rules.price_change_pct(reading.old_price, reading.new_price)
        detail = f" by {change}%" if change is not None else ""
        draft = _draft_price(db, po_no, contact, reading.old_price, reading.new_price)
        title = f"price change asked on {po_no}{detail}"
    elif reading.type == "substitution":
        draft = _draft_substitution(db, po_no, contact, reading.item)
        title = f"supplier offers a substitute on {po_no}"
    elif reading.type == "reject":
        draft, sla = _draft_reject(db, po_no, contact), 4
        title = f"supplier cannot accept {po_no}"
    elif reading.type == "question":
        title = f"supplier question on {po_no}"
    else:
        title = f"supplier email on {po_no} needs a look"
    task = _task(
        db, tenant, "supplier_change" if reading.type != "question" else "supplier_question", f"{title}: {mail['subject'] or ''}"[:200],
        ("Buyer",), sla,
        {
            "po_no": po_no, "email_id": int(mail["id"]), "kind": reading.type, "reply_draft": draft, "reason": reading.reason,
            "old_price": reading.old_price, "new_price": reading.new_price, "line": line["line_no"] if line else None,
        },
    )  # fmt: skip
    if reading.type == "price_change" and line is not None and reading.new_price is not None:
        asked = [confirmation.Proposed("price", line["line_no"], str(reading.new_price))]
        _propose(db, tenant, order, asked, reading, mail, task)
    elif reading.type == "reject":
        _propose(db, tenant, order, [confirmation.Proposed("decline")], reading, mail, task)
    return Handled("task", reading.type, "a buyer decides; nothing on the order was changed", task, draft)


def _propose(
    db: Session, tenant: str, order: dict[str, Any], asked: list[confirmation.Proposed], reading: step.SupplierMessage,
    mail: Any, task: int,
) -> None:  # fmt: skip
    """What the email asks for also goes to the buyer as proposed changes, old value to new, on the buyer's task. The
    values are the ones the evidence check found in the email; the buyer decides."""
    if not asked:
        return
    try:
        confirmation.propose(
            db, tenant, order["no"], asked, source="email", reason=reading.reason, actor=AGENT, email_id=int(mail["id"]),
            task_id=task,
        )  # fmt: skip
    except confirmation.ConfirmationError:
        db.rollback()  # nothing to propose (the same value, or a line no longer open): the task alone stays


def _gate(db: Session, decision: str, order: dict[str, Any], reading: step.SupplierMessage) -> autonomy.GateDecision:
    return autonomy.gate(db, decision, value=Decimal(order["total"]), confidence=reading.confidence)


def _ack(
    db: Session,
    tenant: str,
    order: dict[str, Any],
    supplier: str,
    reading: step.SupplierMessage,
    mail: Any,
    as_of: date,
) -> Handled:
    po_no = order["no"]
    if order["status"] != "sent":
        return Handled("noted", "ack", f"{po_no} is already {order['status']}")
    allowed = _gate(db, DECISION_CHANGE, order, reading)
    if not allowed.allowed:
        task = _task(
            db, tenant, "supplier_change", f"{supplier} acknowledged {po_no}: record it", ("Buyer",), 24,
            {"po_no": po_no, "email_id": int(mail["id"]), "promised": reading.new_date, "why_not_automatic": allowed.reason},
        )  # fmt: skip
        return Handled("task", "ack", f"not recorded automatically: {allowed.reason}", task)
    warnings = dispatch.acknowledge(
        db, po_no, AGENT, promised=reading.new_date, note="acknowledged by email", today=as_of
    )
    autonomy.record_outcome(db, DECISION_CHANGE, auto=True, ref=f"po:{po_no}")
    if warnings:
        task = _task(
            db, tenant, "supplier_change", f"{supplier} acknowledged {po_no} with a late date: {warnings[0]}"[:200], ("Buyer",), 24,
            {"po_no": po_no, "warnings": warnings},
        )  # fmt: skip
        return Handled("applied", "ack", "acknowledgement recorded; the promised date is later than needed", task)
    return Handled("applied", "ack", "acknowledgement recorded")


def _date(
    db: Session,
    tenant: str,
    order: dict[str, Any],
    supplier: str,
    contact: str | None,
    reading: step.SupplierMessage,
    mail: Any,
) -> Handled:
    po_no = order["no"]
    date_days, _ = _tolerance(db)
    current = _current_promised(order)
    requested = rules.requested_date(current, reading.new_date or reading.balance_date, reading.delay_days)
    verdict = rules.judge_date(current, requested, date_days)
    latest = current + timedelta(days=date_days) if current else None
    if verdict.ok and requested is not None:
        allowed = _gate(db, DECISION_CHANGE, order, reading)
        if allowed.allowed:
            return _apply_date(db, order, supplier, contact, requested, current, verdict.detail, mail)
        why = f"within tolerance, but not applied automatically: {allowed.reason}"
    else:
        why = verdict.detail
    draft = (
        _draft_date_noted(db, po_no, contact, requested)
        if verdict.ok and requested
        else _draft_date_refused(db, po_no, contact, requested, latest)
    )
    task = _task(
        db, tenant, "supplier_change", f"{supplier} asks to move the delivery of {po_no}: {why}"[:200], ("Buyer",), 24,
        {
            "po_no": po_no, "email_id": int(mail["id"]), "current": current, "requested": requested, "kind": reading.type,
            "within_tolerance": verdict.ok, "reply_draft": draft,
        },
    )  # fmt: skip
    if requested is not None:
        line = _line_for(order, reading.item)
        lines = [line] if line else [ln for ln in order["lines"] if ln["status"] in ("open", "part_received")]
        moved = [ln for ln in lines if (ln["promised_date"] or ln["need_by"]) != requested]
        asked = [confirmation.Proposed("date", ln["line_no"], requested.isoformat()) for ln in moved]
        _propose(db, tenant, order, asked, reading, mail, task)
    return Handled("task", reading.type, f"not applied: {why}", task, draft)


def _apply_date(
    db: Session,
    order: dict[str, Any],
    supplier: str,
    contact: str | None,
    requested: date,
    current: date | None,
    detail: str,
    mail: Any,
) -> Handled:
    po_no = order["no"]
    db.execute(
        text("update po_line set promised_date = :d where po_no = :p and status in ('open', 'part_received')"),
        {"d": requested, "p": po_no},
    )
    db.execute(
        text("insert into po_commitment (po_no, promised_date, email_id, note) values (:p, :d, :e, :n)"),
        {"p": po_no, "d": requested, "e": int(mail["id"]), "n": detail},
    )
    audit.record(
        db, AGENT, "po.promised_date_change", "po", po_no,
        before={"promised_date": current}, after={"promised_date": requested}, reason=detail,
    )  # fmt: skip
    autonomy.record_outcome(db, DECISION_CHANGE, auto=True, ref=f"po:{po_no}")
    from p2p.requests import material  # noqa: PLC0415 - material requests sit above orders

    material.after_event(db, None, po_no=po_no, why=f"{supplier} moved the delivery of {po_no}")
    draft = _draft_date_noted(db, po_no, contact, requested)
    _send(db, supplier, po_no, f"Delivery date noted: purchase order {po_no}", draft)
    return Handled(
        "applied", "date_change", f"promised date moved to {requested:%d %b %Y} ({detail})", reply_draft=draft
    )


def _quantity(
    db: Session,
    tenant: str,
    order: dict[str, Any],
    supplier: str,
    contact: str | None,
    reading: step.SupplierMessage,
    mail: Any,
) -> Handled:
    po_no = order["no"]
    _, pct = _tolerance(db)
    line = _line_for(order, reading.item)
    if line is None or reading.qty is None:
        why = "the message does not say which line it is about" if line is None else "the message gives no quantity"
        verdict = rules.Verdict(rules.OUT, why)
    else:
        verdict = rules.judge_quantity(Decimal(line["qty"]), reading.qty, pct)
    if verdict.ok and line is not None:
        allowed = _gate(db, DECISION_CHANGE, order, reading)
        if allowed.allowed:
            db.execute(
                text(
                    "insert into po_commitment (po_no, po_line_no, promised_qty, promised_date, email_id, note) values (:p, :l, :q, :d, :e, :n)"
                ),
                {
                    "p": po_no,
                    "l": line["line_no"],
                    "q": reading.qty,
                    "d": reading.balance_date or reading.new_date,
                    "e": int(mail["id"]),
                    "n": verdict.detail,
                },
            )
            audit.record(
                db, AGENT, "po.commitment", "po", po_no,
                after={"line": line["line_no"], "promised_qty": reading.qty, "ordered": line["qty"]}, reason=verdict.detail,
            )  # fmt: skip
            autonomy.record_outcome(db, DECISION_CHANGE, auto=True, ref=f"po:{po_no}")
            return Handled(
                "applied", "qty_change", f"quantity noted as a commitment, the order is unchanged ({verdict.detail})"
            )
        why = f"within tolerance, but not applied automatically: {allowed.reason}"
    else:
        why = verdict.detail
    draft = _draft_qty(db, po_no, contact, reading.qty, Decimal(line["qty"]) if line else None)
    task = _task(
        db, tenant, "supplier_change", f"{supplier} can supply a different quantity on {po_no}: {why}"[:200], ("Buyer",), 24,
        {"po_no": po_no, "email_id": int(mail["id"]), "qty": reading.qty, "line": line["line_no"] if line else None, "reply_draft": draft},
    )  # fmt: skip
    if line is not None and reading.qty is not None:
        asked = [confirmation.Proposed("qty", line["line_no"], str(reading.qty))]
        _propose(db, tenant, order, asked, reading, mail, task)
    return Handled("task", "qty_change", f"not applied: {why}", task, draft)


def _note_dispatch(
    db: Session,
    tenant: str,
    order: dict[str, Any],
    reading: step.SupplierMessage,
    mail: Any,
    as_of: date,
    *,
    whole: bool,
) -> tuple[bool, str]:
    """Store what the email says was shipped as a dispatch note. With `whole` and no quantity written, the email says the
    order went out: everything still to ship. Returns whether it was stored, and what happened in words."""
    po_no = order["no"]
    line = _line_for(order, reading.item)
    if reading.qty is not None:
        if line is None:
            return False, "the email gives a quantity shipped but not which line"
        quantities = {int(line["line_no"]): reading.qty}
    elif whole:
        quantities = {n: ln["left"] for n, ln in dispatch_notes.to_ship(db, po_no).items() if ln["left"] > 0}
    else:
        return False, "the email gives no quantity shipped"
    shipped = reading.shipped_on or as_of
    expected = reading.new_date if reading.new_date and reading.new_date >= shipped else None
    try:
        recorded = dispatch_notes.record(
            db, tenant, po_no, quantities, shipped_on=shipped, actor=AGENT, source="email", expected_on=expected,
            vehicle=reading.vehicle, lr_no=reading.lr_no, invoice_no=reading.invoice_no, email_id=int(mail["id"]),
        )  # fmt: skip
    except dispatch_notes.DispatchNoteError as exc:
        db.rollback()
        return False, f"the dispatch was not recorded: {exc}"
    return True, f"dispatch note {recorded.note_id} recorded for the store" + (" (part)" if recorded.partial else "")


def _dispatched(
    db: Session,
    tenant: str,
    order: dict[str, Any],
    supplier: str,
    reading: step.SupplierMessage,
    mail: Any,
    as_of: date,
) -> Handled:
    stored, said = _note_dispatch(db, tenant, order, reading, mail, as_of, whole=True)
    if stored:
        return Handled("applied", "dispatch", said)
    task = _task(
        db, tenant, "supplier_dispatch", f"{supplier} says it shipped against {order['no']}: {said}"[:200], ("Buyer",), 24,
        {"po_no": order["no"], "email_id": int(mail["id"]), "qty": reading.qty, "why": said},
    )  # fmt: skip
    return Handled("task", "dispatch", said, task)


def _send(db: Session, supplier: str, po_no: str, subject: str, body: str) -> bool:
    contact = _contact(db, supplier)
    if contact is None:
        return False
    emails.send_linked_email(db, contact[0], subject, body, linked_type="po", linked_ref=po_no)  # sent after the commit
    return True


# ---- reminders and chasers --------------------------------------------------------------------


@dataclass
class ChaseResult:
    emailed: list[tuple[str, str]] = field(default_factory=list)  # (po_no, kind)
    tasks: list[tuple[str, str]] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)


def _last_chase(db: Session, po_no: str, kind: str) -> tuple[datetime | None, int]:
    row = db.execute(
        text("select max(sent_at), count(*) from supplier_chase where po_no = :p and kind = :k"),
        {"p": po_no, "k": kind},
    ).one()
    return row[0], int(row[1])


def _balance_lines(db: Session, po_no: str) -> list[tuple[str, Decimal, Decimal]]:
    rows = db.execute(
        text(
            "select description, qty, received_qty from po_line where po_no = :p and status in ('open', 'part_received') "
            "and received_qty < qty order by line_no"
        ),
        {"p": po_no},
    ).all()
    return [(r[0], Decimal(r[1]), Decimal(r[2])) for r in rows]


def chase_run(db: Session, tenant: str, *, as_of: date, now: datetime | None = None) -> ChaseResult:
    """Chase suppliers that have not acknowledged an order, and for the balance of part deliveries that are late.

    Each chase is an email through the autonomy gate ('supplier.remind'); when the gate says no, a buyer gets a task to
    send it. A chaser is not repeated before 'supplier_chase_days' and there are at most 'supplier_chase_max' of each kind.
    """
    now = now or datetime.now(UTC)
    result = ChaseResult()
    every = timedelta(days=int(_setting(db, "supplier_chase_days", "3")))
    most = int(_setting(db, "supplier_chase_max", "3"))
    for po in db.execute(
        text(
            "select no, supplier_code, status, sent_at, total from po where status in ('sent', 'part_received') order by no"
        )
    ).mappings():
        po_no, supplier = str(po["no"]), str(po["supplier_code"])
        if po["status"] == "sent":
            kind = "ack"
            due = po["sent_at"] is not None and dispatch.ack_overdue(db, po["sent_at"], as_of)
        else:
            kind = "balance"
            order = _order(db, po_no)
            promised = _current_promised(order) if order else None
            due = bool(_balance_lines(db, po_no)) and (promised is None or promised < as_of)
        if not due:
            continue
        last, count = _last_chase(db, po_no, kind)
        if count >= most or (last is not None and now - last < every):
            result.skipped.append(f"{po_no}:{kind}")
            continue
        contact = _contact(db, supplier)
        body = _chaser_text(db, po_no, kind, contact[1] if contact else None, po["sent_at"])
        subject = (
            f"Reminder: please acknowledge purchase order {po_no}"
            if kind == "ack"
            else f"Balance of purchase order {po_no}"
        )
        allowed = autonomy.gate(db, DECISION_REMIND, value=Decimal(po["total"]), confidence=1.0)
        if allowed.allowed and contact is not None and _send(db, supplier, po_no, subject, body):
            db.execute(
                text("insert into supplier_chase (po_no, kind, how, sent_at) values (:p, :k, 'email', :n)"),
                {"p": po_no, "k": kind, "n": now},
            )
            audit.record(
                db, AGENT, "supplier.chase", "po", po_no, after={"kind": kind, "how": "email", "number": count + 1}
            )
            autonomy.record_outcome(db, DECISION_REMIND, auto=True, ref=f"{po_no}:{kind}")
            result.emailed.append((po_no, kind))
            continue
        why = allowed.reason if not allowed.allowed else "the supplier has no email address on file"
        task = _task(
            db, tenant, "supplier_chase", f"Chase {supplier} about {po_no}: {'no acknowledgement yet' if kind == 'ack' else 'the balance is late'}",
            ("Buyer",), 24, {"po_no": po_no, "kind": kind, "draft": body, "why_not_automatic": why},
        )  # fmt: skip
        db.execute(
            text("insert into supplier_chase (po_no, kind, how, sent_at, task_id) values (:p, :k, 'task', :n, :t)"),
            {"p": po_no, "k": kind, "n": now, "t": task},
        )
        audit.record(
            db, AGENT, "supplier.chase", "po", po_no, after={"kind": kind, "how": "task", "task": task}, reason=why
        )
        result.tasks.append((po_no, kind))
    emails.commit_and_send(db)
    return result


def _chaser_text(db: Session, po_no: str, kind: str, contact: str | None, sent_at: datetime | None) -> str:
    if kind == "ack":
        when = f" on {sent_at.astimezone(IST):%d %b %Y}" if sent_at else ""
        return (
            _greeting(contact)
            + f"We sent you purchase order {po_no}{when} and have not received your acknowledgement. Please reply to this email, "
            + "keeping the subject line, with the date you will deliver.\n"
            + _sign(db)
        )
    lines = "\n".join(
        f"  {n}. {name}: {format_qty(qty)} ordered, {format_qty(got)} received, {format_qty(qty - got)} to come"
        for n, (name, qty, got) in enumerate(_balance_lines(db, po_no), start=1)
    )
    return (
        _greeting(contact)
        + f"The delivery against purchase order {po_no} is not complete:\n{lines}\n\nPlease confirm the date by which the balance will "
        + "be delivered.\n"
        + _sign(db)
    )


def next_run(now: datetime) -> datetime:
    tomorrow = now.astimezone(IST).date() + timedelta(days=1)
    return datetime.combine(tomorrow, RUN_AT, tzinfo=IST).astimezone(UTC)


def schedule(tenant: str, run_at: datetime | None = None) -> int | None:
    """Queue the daily chase for a company unless one is already waiting."""
    return worker.enqueue_unique(tenant, JOB, {}, run_at or datetime.now(UTC))


@worker.register(JOB)
def _chase_job(ctx: worker.JobContext) -> None:
    with Session(routing.engine_for(ctx.tenant)) as db:
        chase_run(db, ctx.tenant, as_of=ctx.now.astimezone(IST).date(), now=ctx.now)
    worker.enqueue_unique(ctx.tenant, JOB, {}, next_run(ctx.now), exclude_id=ctx.id)


@worker.register("supplier_message")
def _message_job(ctx: worker.JobContext) -> None:
    from p2p.agents import capabilities  # noqa: PLC0415 - the switch imports the agents
    from p2p.ai_steps.gateway import Gateway  # noqa: PLC0415

    with Session(routing.engine_for(ctx.tenant)) as db:
        if capabilities.job_allowed(db, ctx.tenant, "supplier_message", ctx.payload):  # else handed to a person
            handle_email(db, ctx.tenant, int(ctx.payload["email_id"]), Gateway(), as_of=ctx.now.astimezone(IST).date())
    schedule(ctx.tenant)  # the daily chase keeps itself going once it has started
