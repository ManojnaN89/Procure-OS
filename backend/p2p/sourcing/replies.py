"""Supplier replies: read an incoming email about an RFQ into a draft that a person confirms.

The email arrives through the mailbox and is linked to its RFQ by the reply address. This module finds out which
supplier sent it (mail from anyone else is never shown to a model), has the AI step read it, and stores a draft.
A person confirms the draft, which enters the quote through the same checks as a quote typed by hand, or dismisses it.
Whether a clean draft may be confirmed without a person is the autonomy gate's decision ('quote.read').
"""

import json
from dataclasses import asdict
from datetime import UTC, date, datetime
from decimal import Decimal
from email.utils import parseaddr
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from p2p.agents import autonomy
from p2p.ai_steps import quote_reading as reading_step
from p2p.ai_steps.gateway import Gateway
from p2p.ai_steps.quote_reading import Proposal, QuoteReading, RfqLine
from p2p.ai_steps.runner import run_step
from p2p.formulas.money import format_qty
from p2p.formulas.orders import current_gst_rate
from p2p.orders import quotes
from p2p.platform import audit, tasks
from p2p.platform.sod import Actor
from p2p.sourcing import service

SERVICE = Actor("service", "quote_reading")
DECISION_TYPE = "quote.read"
MAX_ATTACHMENT_CHARS = 20_000


class ReplyError(Exception):
    pass


def _json(value: Any) -> str:
    return json.dumps(value, default=str)


def _buyer_user(db: Session) -> int:
    """Someone to give the review task to when a reading is not reliable: a buyer, else anyone."""
    found = db.execute(text("select user_id from user_profile where role = 'Buyer' order by user_id limit 1")).scalar()
    if not found:
        found = db.execute(text("select user_id from user_profile order by user_id limit 1")).scalar()
    return int(found or 0)


def _supplier_of(db: Session, rfq_no: str, from_addr: str) -> str | None:
    address = parseaddr(from_addr)[1].strip().lower()
    if not address:
        return None
    row = db.execute(
        text(
            "select rs.supplier_code from rfq_supplier rs where rs.rfq_no = :r and ("
            "lower(rs.email) = :a or exists (select 1 from supplier_contact c where c.supplier_code = rs.supplier_code "
            "and lower(c.email) = :a))"
        ),
        {"r": rfq_no, "a": address},
    ).first()
    return str(row[0]) if row else None


def _contact_name(db: Session, supplier: str, from_addr: str) -> str | None:
    """The name of the person at the supplier who wrote, if we know them."""
    address = parseaddr(from_addr)[1].lower()
    name = db.execute(
        text("select name from supplier_contact where supplier_code = :s and lower(email) = :e limit 1"),
        {"s": supplier, "e": address},
    ).scalar()
    return str(name) if name else None


def _attachment_text(db: Session, tenant: str, ids: list[int], notes: list[str]) -> str:
    """The text of the attachments that can be read (a quotation is often a PDF or a spreadsheet)."""
    from p2p.platform import parsing, storage

    parts: list[str] = []
    for doc_id in ids:
        row = db.execute(text("select filename, storage_key from document where id = :i"), {"i": doc_id}).first()
        if row is None:
            continue
        try:
            parsed = parsing.parse_document(storage.get_storage().get(tenant, row[1]), row[0])
        except Exception as exc:  # noqa: BLE001 - one unreadable attachment must not stop the email being read
            notes.append(f"attachment {row[0]} could not be read: {type(exc).__name__}")
            continue
        table_text = "\n".join(" | ".join(cells) for t in parsed.tables for cells in t.rows)
        parts.append(f"--- attachment {row[0]} ---\n{parsed.text}\n{table_text}")
    return "\n".join(parts)[:MAX_ATTACHMENT_CHARS]


def _rfq_lines(db: Session, rfq_no: str, as_of: date) -> list[RfqLine]:
    rows = db.execute(
        text(
            "select l.line_no, l.item_code, l.description, l.qty, i.gst_rate_before, i.gst_rate_after, i.gst_rate_change_date, "
            "rl.est_price from rfq_line l left join item i on i.code = l.item_code "
            "left join request_line rl on rl.request_no = l.request_no and rl.line_no = l.request_line_no "
            "where l.rfq_no = :n order by l.line_no"
        ),
        {"n": rfq_no},
    ).all()
    return [RfqLine(r[0], r[1], r[2], r[3], current_gst_rate(r[4], r[5], r[6], as_of), r[7]) for r in rows]


def _store(
    db: Session,
    rfq_no: str,
    email_id: int,
    supplier: str | None,
    status: str,
    *,
    intent: str | None = None,
    reading: QuoteReading | None = None,
    proposal: Proposal | None = None,
    problems: list[str] | None = None,
    model: str | None = None,
) -> int:
    return int(
        db.execute(
            text(
                "insert into quote_draft (rfq_no, supplier_code, email_id, intent, status, reading, proposal, problems, "
                "assumptions, confidence, model, prompt_version) values (:r, :s, :e, :i, :st, cast(:rd as jsonb), "
                "cast(:pr as jsonb), cast(:pb as jsonb), cast(:a as jsonb), :c, :m, :pv) returning id"
            ),
            {
                "r": rfq_no,
                "s": supplier,
                "e": email_id,
                "i": intent,
                "st": status,
                "rd": _json(reading.model_dump(mode="json")) if reading else None,
                "pr": _json(asdict(proposal)) if proposal else None,
                "pb": _json([*(problems or []), *(proposal.problems if proposal else [])]),
                "a": _json(proposal.assumptions if proposal else []),
                "c": reading.confidence if reading else None,
                "m": model,
                "pv": reading_step.PROMPT_VERSION,
            },
        ).scalar_one()
    )


def read_reply(db: Session, tenant: str, email_id: int, gateway: Gateway, *, as_of: date) -> int | None:
    """Read one incoming email about an RFQ into a draft. Returns the draft's id, or None if there is nothing to do."""
    mail = (
        db.execute(
            text(
                "select id, from_addr, subject, body_text, linked_type, linked_id, attachment_ids from email_message "
                "where id = :i and direction = 'in'"
            ),
            {"i": email_id},
        )
        .mappings()
        .first()
    )
    if mail is None or mail["linked_type"] != "rfq":
        return None
    if db.execute(text("select 1 from quote_draft where email_id = :i"), {"i": email_id}).first():
        return None  # already read
    rfq_no = str(mail["linked_id"])
    rfq = db.execute(text("select status from rfq where no = :n"), {"n": rfq_no}).first()
    if rfq is None:
        return None
    supplier = _supplier_of(db, rfq_no, mail["from_addr"] or "")
    if supplier is None:
        draft = _store(
            db,
            rfq_no,
            email_id,
            None,
            "needs_person",
            problems=[f"the sender {mail['from_addr']} is not one of the suppliers asked, so the email was not read"],
        )
        db.commit()
        return draft
    if rfq[0] != "sent":
        draft = _store(
            db,
            rfq_no,
            email_id,
            supplier,
            "needs_person",
            problems=[f"{rfq_no} is {rfq[0]}; it is no longer collecting quotes"],
        )
        db.commit()
        return draft

    notes: list[str] = []
    attachment_ids = [int(x) for x in (mail["attachment_ids"] or [])]
    source = f"Subject: {mail['subject']}\n\n{mail['body_text'] or ''}"
    if attachment_ids:
        source += "\n\n" + _attachment_text(db, tenant, attachment_ids, notes)
    lines = _rfq_lines(db, rfq_no, as_of)
    items = "\n".join(
        f"{ln.line_no}. {ln.name}" + (f" ({ln.item_code})" if ln.item_code else "") + f" - {format_qty(ln.qty)}"
        for ln in lines
    )

    outcome = run_step(
        db,
        tenant,
        reading_step.STEP,
        source,
        gateway=gateway,
        review_assignee=_buyer_user(db),
        entity="rfq",
        entity_id=rfq_no,
        inputs={"items": items},
    )
    model = f"{outcome.completion.provider}/{outcome.completion.model}" if outcome.completion else None
    if outcome.status != "ok" or not isinstance(outcome.value, QuoteReading):
        draft = _store(
            db, rfq_no, email_id, supplier, "needs_person", problems=[*outcome.failures, *notes], model=model
        )
        audit.record(
            db, SERVICE, "quote_draft.create", "quote_draft", draft, after={"status": "needs_person", "rfq": rfq_no}
        )
        db.commit()
        return draft

    reading = outcome.value
    proposal = reading_step.build_proposal(reading, lines)
    proposal.assumptions.extend(notes)
    status = "pending" if not proposal.problems else "needs_person"
    draft = _store(
        db, rfq_no, email_id, supplier, status, intent=reading.intent, reading=reading, proposal=proposal, model=model
    )
    audit.record(
        db,
        SERVICE,
        "quote_draft.create",
        "quote_draft",
        draft,
        after={
            "rfq": rfq_no,
            "supplier": supplier,
            "intent": reading.intent,
            "status": status,
            "lines": len(proposal.lines),
        },
        model=model,
        prompt_version=reading_step.PROMPT_VERSION,
    )
    db.commit()

    if reading.intent == "question" and reading.question:
        tasks.create_task(
            db,
            tenant,
            kind="supplier_question",
            title=f"{supplier} asks about {rfq_no}: {reading.question[:100]}",
            assignee=_buyer_user(db),
            sla_hours=24,
            payload={
                "rfq_no": rfq_no,
                "draft": draft,
                "question": reading.question,
                "reply_draft": service.question_reply_draft(
                    db, rfq_no, _contact_name(db, supplier, mail["from_addr"]), reading.question
                ),
            },
        )
    _maybe_apply(db, tenant, draft, reading, proposal, as_of)
    return draft


def _maybe_apply(db: Session, tenant: str, draft: int, reading: QuoteReading, proposal: Proposal, as_of: date) -> None:
    """A clean draft is confirmed without a person only when the gate allows it. Anything in doubt waits."""
    if reading.intent not in ("quote", "decline") or proposal.problems or proposal.assumptions:
        return
    value = sum((ln.unit_price for ln in proposal.lines), Decimal(0))
    if not autonomy.gate(db, DECISION_TYPE, value=value, confidence=reading.confidence).allowed:
        return
    confirm_draft(db, tenant, draft, SERVICE, as_of=as_of)
    autonomy.record_outcome(db, DECISION_TYPE, auto=True, ref=str(draft))


# ---- the person's decision --------------------------------------------------------------------


def _locked(db: Session, draft_id: int) -> dict[str, Any]:
    row = db.execute(text("select * from quote_draft where id = :i for update"), {"i": draft_id}).mappings().first()
    if row is None:
        raise ReplyError(f"draft {draft_id} does not exist")
    return dict(row)


def confirm_draft(
    db: Session,
    tenant: str,
    draft_id: int,
    actor: Actor,
    *,
    as_of: date,
    edits: dict[int, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Turn a pending draft into a quote (or record the supplier's refusal). `edits` lets the person correct line values."""
    draft = _locked(db, draft_id)
    if draft["status"] != "pending":
        raise ReplyError(f"draft {draft_id} is {draft['status']}; only a pending draft can be confirmed")
    rfq_no, supplier = draft["rfq_no"], draft["supplier_code"]
    if not supplier:
        raise ReplyError("the supplier is not known")
    result: dict[str, Any]
    if draft["intent"] == "decline":
        reason = (draft["reading"] or {}).get("decline_reason")
        db.execute(
            text(
                "update rfq_supplier set declined = true, decline_reason = :r where rfq_no = :n and supplier_code = :s"
            ),
            {"r": reason, "n": rfq_no, "s": supplier},
        )
        result = {"declined": supplier}
    elif draft["intent"] == "quote":
        proposal = draft["proposal"] or {}
        lines = []
        for ln in proposal.get("lines", []):
            change = (edits or {}).get(int(ln["rfq_line_no"]), {})
            lines.append(
                quotes.QuoteLineInput(
                    rfq_line_no=int(ln["rfq_line_no"]),
                    unit_price=Decimal(str(change.get("unit_price", ln["unit_price"]))),
                    gst_rate=Decimal(str(change.get("gst_rate", ln["gst_rate"]))),
                    discount_pct=Decimal(str(change.get("discount_pct", ln["discount_pct"] or 0))),
                    delivery_days=(
                        int(change["delivery_days"])
                        if change.get("delivery_days") not in (None, "")
                        else ln["delivery_days"]
                    ),
                )
            )
        try:
            quote_no = quotes.enter_quote(
                db,
                rfq_no,
                quotes.QuoteInput(
                    supplier_code=supplier,
                    lines=lines,
                    valid_until=date.fromisoformat(proposal["valid_until"]) if proposal.get("valid_until") else None,
                    freight=Decimal(str(proposal.get("freight") or 0)),
                    packing_forwarding=Decimal(str(proposal.get("packing_forwarding") or 0)),
                    document_ref=f"email:{draft['email_id']}",
                ),
                actor,
            )
        except quotes.QuoteError as exc:
            db.rollback()
            raise ReplyError(str(exc)) from None
        db.execute(text("update quote_draft set quote_no = :q where id = :i"), {"q": quote_no, "i": draft_id})
        result = {"quote_no": quote_no}
    else:
        raise ReplyError("this reply has nothing to confirm; dismiss it when it has been dealt with")
    db.execute(
        text("update quote_draft set status = 'applied', decided_by = :by, decided_at = :at where id = :i"),
        {"by": f"{actor.kind}:{actor.id}", "at": datetime.now(UTC), "i": draft_id},
    )
    audit.record(
        db,
        actor,
        "quote_draft.confirm",
        "quote_draft",
        draft_id,
        after={**result, "rfq": rfq_no, "edited": bool(edits)},
    )
    db.commit()
    quotes.auto_award(db, tenant, rfq_no, as_of=as_of)  # only if the autonomy gate allows it and nothing is in doubt
    return result


def dismiss_draft(db: Session, draft_id: int, actor: Actor, note: str | None = None) -> None:
    draft = _locked(db, draft_id)
    if draft["status"] in ("applied", "dismissed"):
        raise ReplyError(f"draft {draft_id} is already {draft['status']}")
    db.execute(
        text(
            "update quote_draft set status = 'dismissed', decided_by = :by, decided_at = now(), decision_note = :n where id = :i"
        ),
        {"by": f"{actor.kind}:{actor.id}", "n": note, "i": draft_id},
    )
    audit.record(db, actor, "quote_draft.dismiss", "quote_draft", draft_id, reason=note)
    db.commit()


def drafts_of(db: Session, rfq_no: str) -> list[dict[str, Any]]:
    """The drafts of an RFQ for the screen, newest first."""
    rows = db.execute(
        text(
            "select d.*, coalesce(s.trade_name, s.legal_name) as supplier_name, e.subject, e.from_addr "
            "from quote_draft d left join supplier s on s.code = d.supplier_code "
            "left join email_message e on e.id = d.email_id where d.rfq_no = :n order by d.id desc"
        ),
        {"n": rfq_no},
    ).mappings()
    return [dict(r) for r in rows]
