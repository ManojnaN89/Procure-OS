"""The supplier communication agent (AG02): reads what suppliers write about orders, acts on what is small, hands over the rest.

An email linked to a purchase order (or an RFQ) from a registered contact of the supplier (D53: the address the order
went to identifies nobody by itself) is read by an AI step that only classifies it and copies the dates, quantities and
prices it states (ai_steps/supplier_message.py). A mail whose sender authentication failed is not read: the buyer gets
a task. Code does the rest:

- an acknowledgement answers the version of the order mail it replies to (OP-12); it is recorded for that version only
  when the sender was authenticated and the gate allows it (OP-4 interim, `EMAIL_ACK`); otherwise a buyer records it
  with one click. A reply to an older version is answered with the current version's document;
- a change of delivery date or a delay is judged against the due date of the one line it is about (DF-27): within
  'supplier_date_days', with the gate's leave and no stock effect (it would not come after a material request's need,
  rule Q17), only that line's promised date moves. A date naming no line on an order of several lines is never applied;
  each line is proposed to the buyer. A quantity within 'supplier_change_pct' is noted as a commitment: the order
  itself, and so its value, is never changed here;
- a price change, a substitution, a rejection, a question, a larger change, a request to change bank details: a task
  for the right person with a drafted reply, and nothing applied. A bank change is never applied by anything;
- a date, quantity or price change and a refusal that are not applied also become proposed changes, old value to new,
  that the buyer accepts or rejects on the order page (orders/confirmation.py): only an accepted one changes the order;
- a dispatch (all of it, or part) is stored as a dispatch note for the store (orders/dispatch_notes.py), checked against
  what is still to ship.

Applying a small change and sending a reminder go through the autonomy gate ('supplier.change', 'supplier.remind'); when
the gate says no, the same thing becomes a task for a buyer. Mail from anyone but a registered contact of the supplier
is not shown to a model. A daily job chases suppliers that have not acknowledged the current version of an order (on the
working days of the place it delivers to, with a fresh link each time, at most 'supplier_chase_max' per version, then a
task for the buyer) and the balance of part deliveries.
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
from p2p.formulas import deliveries, sender_auth
from p2p.formulas import material as material_rules
from p2p.formulas import supplier_changes as rules
from p2p.formulas.calendar import IST, business_today
from p2p.formulas.money import format_inr, format_qty
from p2p.formulas.words import count
from p2p.invoices import tolerances
from p2p.masters import suppliers as supplier_records
from p2p.orders import confirmation, dispatch, dispatch_notes, supplier_links, versions
from p2p.platform import audit, emails, reference, routing, settings, tasks, users, worker
from p2p.platform.sod import Actor

AGENT = Actor("agent", "supplier_comms")
DECISION_CHANGE = "supplier.change"
DECISION_REMIND = "supplier.remind"
JOB = "supplier_chase"
RUN_AT = time(9, 45)  # India time
ACTIVE = ("sent", "acknowledged", "part_received")
SENDER_TASK = "supplier_sender_check"
ACK_TASK = "supplier_ack_confirm"
OVERDUE_TASK = "po_ack_overdue"
OVERDUE_SLA_HOURS = 18  # two working days
# OP-4 (NEEDS OWNER; interim): what an emailed acknowledgement does, by the sender authentication of the mail. 'record':
# the agent records it when the gate allows; 'confirm': a buyer records it with one click; 'not_read': never read.
EMAIL_ACK = {sender_auth.PASS: "record", sender_auth.NONE: "confirm", sender_auth.FAIL: "not_read"}


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


def _open_lines(order: dict[str, Any]) -> list[dict[str, Any]]:
    return [ln for ln in order["lines"] if ln["status"] in ("open", "part_received")]


def _due(line: dict[str, Any]) -> date | None:
    return deliveries.line_due(line["promised_date"], line["need_by"])[0]


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
    """The supplier the email is from (and its name): the From address must be a registered contact of the order's
    supplier, or of a supplier invited to the RFQ (D53; masters/suppliers.registered_sender). None for anyone else."""
    address = parseaddr(from_addr)[1].strip().lower()
    if not address:
        return None, None
    if linked_type == "po":
        codes = [str(c) for c in db.execute(text("select supplier_code from po where no = :r"), {"r": ref}).scalars()]
    else:
        codes = [
            str(c)
            for c in db.execute(text("select supplier_code from rfq_supplier where rfq_no = :r"), {"r": ref}).scalars()
        ]
    found = supplier_records.registered_sender(db, address, codes)
    if len(found) != 1:  # nobody, or an address registered for two suppliers: neither
        return None, None
    name = db.execute(
        text("select coalesce(trade_name, legal_name) from supplier where code = :c"), {"c": found[0][0]}
    ).scalar()
    return found[0][0], str(name or found[0][0])


def _contact(db: Session, supplier: str) -> tuple[str, str | None] | None:
    return supplier_records.order_contact(db, supplier)


def reply_version(db: Session, mail: Any, order: dict[str, Any]) -> int | None:
    """The version an emailed reply answers (OP-12): the one of our mail it threads to; with no thread, version 1 if the
    order never had another; else unknown (None)."""
    if mail.get("in_reply_to"):
        found = db.execute(
            text(
                "select po_version from email_message where direction = 'out' and message_id = :m "
                "and linked_id = :p and po_version is not null"
            ),
            {"m": mail["in_reply_to"], "p": order["no"]},
        ).scalar()
        if found is not None:
            return int(found)
    return 1 if int(order["version"]) == 1 else None


def _task_po(
    db: Session, tenant: str, kind: str, title: str, po: dict[str, Any], sla: int, payload: dict[str, Any]
) -> int:
    """A task about an order for its buyer (else the first buyer), with a link to the order."""
    from p2p.platform import notifications  # noqa: PLC0415 - logins live in the control database

    who = notifications.login_of(db, tenant, po.get("buyer")) if po.get("buyer") else None
    return tasks.create_task(
        db, tenant, kind=kind, title=title[:200], assignee=who or users.first_with_role(db, "Buyer"), sla_hours=sla,
        payload={"po_no": po["no"], "link": f"#/purchase-orders/{po['no']}", **payload}, actor=AGENT,
    )  # fmt: skip


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
                "select id, from_addr, subject, body_text, linked_type, linked_id, in_reply_to, auth from email_message "
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
    verdict = str((mail["auth"] or {}).get("verdict") or sender_auth.NONE)
    if supplier is None or (linked_type == "po" and EMAIL_ACK.get(verdict) == "not_read"):
        return _not_read(db, tenant, email_id, linked_type, ref, supplier, dict(mail), verdict)

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


def _not_read(
    db: Session, tenant: str, email_id: int, linked_type: str, ref: str, supplier: str | None, mail: dict[str, Any],
    verdict: str,
) -> Handled:  # fmt: skip
    """An email from someone who is not a registered contact of the supplier, or whose sender authentication failed, is
    never shown to a model (D53); about an order, the buyer gets a task to sort it out."""
    address = parseaddr(mail["from_addr"] or "")[1].strip().lower() or str(mail["from_addr"])
    if supplier is None:
        what = "order" if linked_type == "po" else "RFQ"
        problem = (
            f"the sender {address} is not a registered contact of the supplier of {what} {ref}: the email was not read"
        )
    else:
        failed = (mail.get("auth") or {}).get("failed") or "fail"
        problem = f"sender authentication failed ({failed}) for {address}: the email was not read"
    _store(db, email_id, linked_type, ref, supplier, None, "ignored", action=problem)
    audit.record(db, AGENT, "supplier_message.ignored", "email", email_id, after={"verdict": verdict}, reason=problem)
    db.commit()
    if linked_type == "po":
        po = dict(db.execute(text("select * from po where no = :n"), {"n": ref}).mappings().one())
        _task_po(
            db, tenant, SENDER_TASK, f"{ref}: a supplier email was not read ({problem})", po, 24,
            {"email_id": email_id, "problem": problem,
             "next": "Ask the supplier to write from its registered address, or register the contact through supplier "
             "records; nothing is read again automatically"},
        )  # fmt: skip
    return Handled("ignored", None, problem)


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
    """An acknowledgement by email: for the version the reply answers; recorded by the agent only from an authenticated
    registered sender with the gate's leave (EMAIL_ACK, OP-4), else a buyer records it with one click."""
    po_no = order["no"]
    if order["status"] != "sent":
        return Handled("noted", "ack", f"{po_no} is already {order['status']}")
    answers, now = reply_version(db, mail, order), int(order["version"])
    if answers is not None and answers < now:
        return _old_version(db, tenant, order, supplier, answers)
    address = parseaddr(mail["from_addr"] or "")[1].strip().lower()
    verdict = str((mail["auth"] or {}).get("verdict") or sender_auth.NONE)
    dates, why = _ack_dates(order, reading)
    if answers is None:
        why = f"the email does not say which version it answers (the order is version {now})"
    elif why is None and EMAIL_ACK.get(verdict) != "record":
        why = "the sender was not authenticated by our mail server"
    elif why is None:
        allowed = _gate(db, DECISION_CHANGE, order, reading)
        why = None if allowed.allowed else f"not recorded automatically: {allowed.reason}"
    if why is None:
        try:
            warnings = dispatch.acknowledge(
                db, po_no, AGENT, version=answers, via="email", promised=dates, note="acknowledged by email",
                today=as_of, tenant=tenant,
                detail=dispatch.AckDetail(address=address, email_id=int(mail["id"]), sender_auth=dict(mail["auth"] or {})),
            )  # fmt: skip
        except dispatch.DispatchError as exc:
            db.rollback()
            why = str(exc)
        else:
            autonomy.record_outcome(db, DECISION_CHANGE, auto=True, ref=f"po:{po_no}")
            if warnings:
                _task(
                    db, tenant, "supplier_change", f"{supplier} acknowledged {po_no} with a later date: {warnings[0]}",
                    ("Buyer",), 24, {"po_no": po_no, "warnings": warnings},
                )  # fmt: skip
            return Handled("applied", "ack", f"acknowledgement of version {answers} recorded")
    shown = answers or now
    task = _task_po(
        db, tenant, ACK_TASK,
        f"Record {_supplier_name(db, supplier)}'s acknowledgement of {po_no} version {shown} (email from {address})",
        order, 24,
        {"version": answers, "email_id": int(mail["id"]), "from": address, "verdict": verdict, "why": why,
         "lines": {str(k): v for k, v in (dates or {}).items()},
         "next": "Read the email and record the acknowledgement of that version with one click, or ask the supplier to "
         "accept from the link"},
    )  # fmt: skip
    return Handled("task", "ack", f"not recorded: {why}; a buyer records it", task)


def _ack_dates(
    order: dict[str, Any], reading: step.SupplierMessage
) -> tuple[dict[int, date | None] | None, str | None]:
    """The line dates an emailed acknowledgement gives: a date for the line it names (or the only line); no date, the
    order's own dates. One date for no line in particular on an order of several lines is not spread over them (DF-27):
    a person looks unless it is on or before every line's due date."""
    if reading.new_date is None:
        return None, None
    line = _line_for(order, reading.item)
    if line is not None:
        return {int(line["line_no"]): reading.new_date}, None
    if all(_due(ln) is None or reading.new_date <= _due(ln) for ln in _open_lines(order)):  # type: ignore[operator]
        return None, None
    return None, f"the email gives {reading.new_date:%d %b %Y} for no line in particular"


def _old_version(db: Session, tenant: str, order: dict[str, Any], supplier: str, answers: int) -> Handled:
    """A reply to an older version records nothing: the supplier is sent the current version's document."""
    po_no, now = order["no"], int(order["version"])
    sent = db.execute(
        text("select sent_at from po_version where po_no = :p and version = :v"), {"p": po_no, "v": now}
    ).scalar()
    when = f", sent on {sent.astimezone(IST):%d %b %Y}" if sent else ""
    body = (
        _greeting(_contact_name(db, supplier))
        + f"Order {po_no} has been revised (version {now}{when}). Please accept that version from the link in our email.\n"
        + _sign(db)
    )
    stored = versions.issue_document(db, tenant, po_no) if sent else None
    _send(db, supplier, po_no, f"Purchase order {po_no} (version {now})", body,
          document_ids=[stored.document_id] if stored else None, po_version=now)  # fmt: skip
    return Handled("noted", "ack", f"answers version {answers}; the order is now version {now}")


def _supplier_name(db: Session, code: str) -> str:
    name = db.execute(
        text("select coalesce(trade_name, legal_name) from supplier where code = :c"), {"c": code}
    ).scalar()
    return str(name or code)


def _contact_name(db: Session, supplier: str) -> str | None:
    contact = _contact(db, supplier)
    return contact[1] if contact else None


def _date(
    db: Session,
    tenant: str,
    order: dict[str, Any],
    supplier: str,
    contact: str | None,
    reading: step.SupplierMessage,
    mail: Any,
) -> Handled:
    """A new delivery date, judged against the due date of the one line it is about (DF-27)."""
    po_no = order["no"]
    date_days, _ = _tolerance(db)
    open_lines = _open_lines(order)
    line = _line_for(order, reading.item)
    asked = reading.new_date or reading.balance_date
    current = _due(line) if line else None
    requested = rules.requested_date(current, asked, reading.delay_days) if line else asked
    if line is None:
        verdict = rules.Verdict(
            rules.OUT, f"the email names no line of the {count(len(open_lines), 'line')} on the order"
        )
    else:
        verdict = rules.judge_date(current, requested, date_days)
    why = verdict.detail
    if verdict.ok and line is not None and requested is not None:
        late = _stock_effect(db, order, line, requested)
        allowed = _gate(db, DECISION_CHANGE, order, reading)
        if late is None and allowed.allowed:
            return _apply_date(db, order, supplier, contact, line, requested, current, verdict.detail, mail)
        why = (
            f"within tolerance, but {late}" if late
            else f"within tolerance, but not applied automatically: {allowed.reason}"
        )  # fmt: skip
    latest = current + timedelta(days=date_days) if current else None
    draft = (
        _draft_date_noted(db, po_no, contact, requested)
        if verdict.ok and requested
        else _draft_date_refused(db, po_no, contact, requested, latest)
    )
    task = _task(
        db, tenant, "supplier_change", f"{supplier} asks to move the delivery of {po_no}: {why}"[:200], ("Buyer",), 24,
        {
            "po_no": po_no, "email_id": int(mail["id"]), "line": line["line_no"] if line else None, "current": current,
            "requested": requested, "kind": reading.type, "within_tolerance": verdict.ok, "reply_draft": draft,
        },
    )  # fmt: skip
    proposed = []
    for ln in [line] if line else open_lines:
        new = requested if line else rules.requested_date(_due(ln), asked, reading.delay_days)
        if new is not None and new != _due(ln):
            proposed.append(confirmation.Proposed("date", ln["line_no"], new.isoformat()))
    _propose(db, tenant, order, proposed, reading, mail, task)
    return Handled("task", reading.type, f"not applied: {why}", task, draft)


def _stock_effect(db: Session, order: dict[str, Any], line: dict[str, Any], new: date) -> str | None:
    """Why the new date would hurt stock: it comes after the latest arrival of an open material requirement the line
    buys for (rule Q17); None when it would not."""
    from p2p.requests import supply_dates  # noqa: PLC0415 - material requests sit above orders

    buffer = supply_dates.buffer_days(db)
    for mr, need_by, place in db.execute(
        text(
            "select m.mr_no, m.need_by, m.location_code from material_request_line m "
            "join material_request_buy b on b.mr_line_id = m.id where m.status = 'open' "
            "and b.request_no = :r and b.request_line_no = :l order by m.need_by"
        ),
        {"r": line["request_no"], "l": line["request_line_no"]},
    ):
        latest = material_rules.latest_arrival(need_by, buffer, reference.calendar_for_location(db, place))
        if new > latest:
            return f"it would come after {mr} needs it ({need_by:%d %b %Y})"
    return None


def _apply_date(
    db: Session,
    order: dict[str, Any],
    supplier: str,
    contact: str | None,
    line: dict[str, Any],
    requested: date,
    current: date | None,
    detail: str,
    mail: Any,
) -> Handled:
    """Move the promised date of that one line, and keep it as a commitment of the order's version."""
    po_no, line_no = order["no"], int(line["line_no"])
    db.execute(
        text("update po_line set promised_date = :d where po_no = :p and line_no = :l"),
        {"d": requested, "p": po_no, "l": line_no},
    )
    db.execute(
        text(
            "insert into po_commitment (po_no, po_line_no, promised_date, email_id, note, po_version, source) "
            "values (:p, :l, :d, :e, :n, :v, 'agent_date')"
        ),
        {"p": po_no, "l": line_no, "d": requested, "e": int(mail["id"]), "n": detail, "v": order["version"]},
    )
    audit.record(
        db, AGENT, "po.promised_date_change", "po", po_no,
        before={"line": line_no, "promised_date": current},
        after={"line": line_no, "promised_date": requested, "version": order["version"]}, reason=detail,
    )  # fmt: skip
    autonomy.record_outcome(db, DECISION_CHANGE, auto=True, ref=f"po:{po_no}")
    from p2p.requests import material  # noqa: PLC0415 - material requests sit above orders

    material.after_event(db, None, po_no=po_no, why=f"{supplier} moved the delivery of {po_no}")
    draft = _draft_date_noted(db, po_no, contact, requested)
    _send(db, supplier, po_no, f"Delivery date noted: purchase order {po_no}", draft)
    return Handled(
        "applied", "date_change",
        f"line {line_no}: promised date moved to {requested:%d %b %Y} ({detail})", reply_draft=draft,
    )  # fmt: skip


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
                    "insert into po_commitment (po_no, po_line_no, promised_qty, promised_date, email_id, note, "
                    "po_version, source) values (:p, :l, :q, :d, :e, :n, :v, 'agent_qty')"
                ),
                {
                    "p": po_no,
                    "l": line["line_no"],
                    "q": reading.qty,
                    "d": reading.balance_date or reading.new_date,
                    "e": int(mail["id"]),
                    "n": verdict.detail,
                    "v": order["version"],
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


def _send(
    db: Session, supplier: str, po_no: str, subject: str, body: str, *, document_ids: list[int] | None = None,
    po_version: int | None = None,
) -> bool:  # fmt: skip
    contact = _contact(db, supplier)
    if contact is None:
        return False
    message_id = emails.send_linked_email(
        db, contact[0], subject, body, linked_type="po", linked_ref=po_no, document_ids=document_ids
    )  # sent after the commit
    if po_version is not None:
        versions.mail_carries(db, message_id, po_version)
    return True


# ---- reminders and chasers --------------------------------------------------------------------


@dataclass
class ChaseResult:
    emailed: list[tuple[str, str]] = field(default_factory=list)  # (po_no, kind)
    tasks: list[tuple[str, str]] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    overdue: list[str] = field(default_factory=list)  # orders handed to the buyer after the last chaser


def _chasers(db: Session, po_no: str, kind: str, version: int | None) -> list[date]:
    """The days the chasers of this kind went (of this version, for an acknowledgement), oldest first."""
    rows = db.execute(
        text(
            "select sent_at from supplier_chase where po_no = :p and kind = :k "
            "and (cast(:v as integer) is null or po_version = :v) order by sent_at"
        ),
        {"p": po_no, "k": kind, "v": version},
    ).scalars()
    return [r.astimezone(IST).date() for r in rows]


def _balance_lines(db: Session, po_no: str) -> list[tuple[str, Decimal, Decimal]]:
    rows = db.execute(
        text(
            "select description, qty, received_qty from po_line where po_no = :p and status in ('open', 'part_received') "
            "and received_qty < qty order by line_no"
        ),
        {"p": po_no},
    ).all()
    return [(r[0], Decimal(r[1]), Decimal(r[2])) for r in rows]


def _balance_late(db: Session, po_no: str, as_of: date) -> bool:
    """A part delivery whose balance is past its due date (or has none)."""
    dues = [
        deliveries.line_due(r[0], r[1])[0]
        for r in db.execute(
            text(
                "select promised_date, need_by from po_line where po_no = :p and status in ('open', 'part_received') "
                "and received_qty < qty"
            ),
            {"p": po_no},
        )
    ]
    return bool(dues) and any(d is None or d < as_of for d in dues)


def chase_run(db: Session, tenant: str, *, as_of: date, now: datetime | None = None) -> ChaseResult:
    """Chase suppliers that have not acknowledged the current version of an order, and for the balance of part
    deliveries that are late, on the working days of the place each order delivers to.

    The first chaser goes once the acknowledgement is overdue, then one every 'supplier_chase_days' working days, at most
    'supplier_chase_max' per version (a new version starts again). On the day the next would be due after the last, the
    order's buyer gets one task (`po_ack_overdue`); nothing else happens by itself. Each chase is an email through the
    autonomy gate ('supplier.remind'); when the gate says no, a buyer gets a task to send it.
    """
    now = datetime.combine(as_of, (now or datetime.now(UTC)).astimezone(IST).timetz())  # on the business date
    result = ChaseResult()
    every = int(settings.number(db, "supplier_chase_days", "3"))
    most = int(settings.number(db, "supplier_chase_max", "3"))
    for row in db.execute(
        text(
            "select no, supplier_code, status, version, acknowledged_version, ship_to_location, total, buyer from po "
            "where status in ('sent', 'part_received') order by no"
        )
    ).mappings():
        po = dict(row)
        po_no = str(po["no"])
        calendar = reference.calendar_for_location(db, po["ship_to_location"])
        if not calendar.is_working_day(as_of):
            continue
        if po["status"] == "sent":
            kind, version = "ack", int(po["version"])
            due_on = dispatch.ack_due_on(db, po)
            if not dispatch.waiting_ack(po) or due_on is None or as_of <= due_on:
                continue
        else:
            kind, version = "balance", int(po["version"])
            if not _balance_late(db, po_no, as_of):
                continue
        sent = _chasers(db, po_no, kind, version if kind == "ack" else None)
        if sent and as_of < calendar.add_working_days(sent[-1], every):
            result.skipped.append(f"{po_no}:{kind}")
            continue
        if len(sent) >= most:
            if kind == "ack" and _overdue_task(db, tenant, po, len(sent)):
                result.overdue.append(po_no)
            else:
                result.skipped.append(f"{po_no}:{kind}")
            continue
        _chase(db, tenant, po, kind, version, len(sent) + 1, now, result)
    emails.commit_and_send(db)
    return result


def _chase(
    db: Session, tenant: str, po: dict[str, Any], kind: str, version: int, number: int, now: datetime,
    result: ChaseResult,
) -> None:  # fmt: skip
    """One chaser: an email with a fresh link and the version's document, or a task when the gate says no."""
    po_no, supplier = str(po["no"]), str(po["supplier_code"])
    contact = _contact(db, supplier)
    allowed = autonomy.gate(db, DECISION_REMIND, value=Decimal(po["total"]), confidence=1.0)
    link = None
    if allowed.allowed and contact is not None and kind == "ack":
        link = supplier_links.url(supplier_links.issue(db, tenant, po_no, "respond", sent_to=contact[0]))
    body = _chaser_text(db, po_no, kind, contact[1] if contact else None, version, link)
    subject = (
        f"Reminder: please accept purchase order {po_no}" + (f" (version {version})" if version > 1 else "")
        if kind == "ack"
        else f"Balance of purchase order {po_no}"
    )
    if allowed.allowed and contact is not None:
        stored = versions.issue_document(db, tenant, po_no) if kind == "ack" else None
        _send(db, supplier, po_no, subject, body, document_ids=[stored.document_id] if stored else None,
              po_version=version)  # fmt: skip
        db.execute(
            text("insert into supplier_chase (po_no, kind, how, sent_at, po_version) values (:p, :k, 'email', :n, :v)"),
            {"p": po_no, "k": kind, "n": now, "v": version},
        )
        audit.record(
            db, AGENT, "supplier.chase", "po", po_no,
            after={"kind": kind, "how": "email", "number": number, "version": version, "to": contact[0]},
        )  # fmt: skip
        autonomy.record_outcome(db, DECISION_REMIND, auto=True, ref=f"{po_no}:{kind}")
        result.emailed.append((po_no, kind))
        return
    why = allowed.reason if not allowed.allowed else "the supplier has no email address on file"
    task = _task(
        db, tenant, "supplier_chase", f"Chase {supplier} about {po_no}: {'no acknowledgement yet' if kind == 'ack' else 'the balance is late'}",
        ("Buyer",), 24, {"po_no": po_no, "kind": kind, "version": version, "draft": body, "why_not_automatic": why},
    )  # fmt: skip
    db.execute(
        text(
            "insert into supplier_chase (po_no, kind, how, sent_at, task_id, po_version) "
            "values (:p, :k, 'task', :n, :t, :v)"
        ),
        {"p": po_no, "k": kind, "n": now, "t": task, "v": version},
    )
    audit.record(
        db, AGENT, "supplier.chase", "po", po_no,
        after={"kind": kind, "how": "task", "task": task, "number": number, "version": version}, reason=why,
    )  # fmt: skip
    result.tasks.append((po_no, kind))


def _overdue_task(db: Session, tenant: str, po: dict[str, Any], chasers: int) -> bool:
    """After the last chaser: one task for the order's buyer, escalating to the first active purchase manager, once per
    version (OP-14). True when it was made now."""
    po_no, version = str(po["no"]), int(po["version"])
    if db.execute(
        text("select 1 from task where kind = :k and payload->>'po_no' = :p and (payload->>'version')::int = :v"),
        {"k": OVERDUE_TASK, "p": po_no, "v": version},
    ).first():
        return False
    supplier = _supplier_name(db, str(po["supplier_code"]))
    sent = db.execute(
        text("select sent_at from po_version where po_no = :p and version = :v"), {"p": po_no, "v": version}
    ).scalar()
    from p2p.platform import notifications  # noqa: PLC0415 - logins live in the control database

    manager_code = db.execute(
        text("select code from org_user where designation = 'Purchase Manager' and active order by code limit 1")
    ).scalar()
    manager = notifications.login_of(db, tenant, str(manager_code)) if manager_code else None
    buyer = notifications.login_of(db, tenant, po.get("buyer")) if po.get("buyer") else None
    audit.record(
        db, AGENT, "po.ack_overdue", "po", po_no, after={"version": version, "chasers": chasers},
        reason=f"{supplier} has not acknowledged version {version} after {count(chasers, 'reminder')}",
    )  # fmt: skip
    tasks.create_task(
        db, tenant, kind=OVERDUE_TASK,
        title=f"{supplier} has not acknowledged {po_no} version {version} after {count(chasers, 'reminder')}"[:200],
        assignee=buyer or users.first_with_role(db, "Buyer"), sla_hours=OVERDUE_SLA_HOURS,
        escalation_chain=[int(manager)] if manager else None,
        calendar=reference.calendar_for_location(db, po["ship_to_location"]),
        payload={
            "po_no": po_no, "version": version, "sent_on": sent.astimezone(IST).date() if sent else None,
            "chasers": chasers, "link": f"#/purchase-orders/{po_no}",
            "next": "Call the supplier, record its acknowledgement with a note, send the order again, or cancel it",
        },
        actor=AGENT,
    )  # fmt: skip
    return True


def _chaser_text(db: Session, po_no: str, kind: str, contact: str | None, version: int, link: str | None) -> str:
    if kind == "ack":
        sent = db.execute(
            text("select sent_at from po_version where po_no = :p and version = :v"), {"p": po_no, "v": version}
        ).scalar()
        when = f" on {sent.astimezone(IST):%d %b %Y}" if sent else ""
        here = (
            f", here (the link works once and asks for a code we email to you): {link}"
            if link
            else " by replying to this email"
        )
        return (
            _greeting(contact)
            + f"We sent you purchase order {po_no} (version {version}){when} and have not received your acceptance. "
            + f"Please accept it, or tell us what you can do{here}\n"
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
