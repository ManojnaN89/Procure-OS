"""Email in and out, linked to the work it is about.

Outgoing mail carries a plus-address in Reply-To, such as rfq+RFQ-0045@our-domain, so the supplier's reply comes back
tagged with the RFQ. Incoming mail is linked by that tag, or by In-Reply-To / References pointing at a message we
sent. A linked message that matches a case adds a case event.
"""

import email
import json
import re
from datetime import timedelta
from email.utils import parseaddr
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from p2p.config import get_settings
from p2p.formulas import sender_auth
from p2p.formulas.calendar import business_today
from p2p.platform import audit, cases, email_layout, events, files, outbox, worker
from p2p.platform.mailbox import ImapMailbox, IncomingMail, Mailbox, MailpitMailbox
from p2p.platform.sod import Actor

TAG = re.compile(r"^(RFQ|PO|INV|REQ|GRN)-[\w-]+$", re.IGNORECASE)
TYPES = {"RFQ": "rfq", "PO": "po", "INV": "invoice", "REQ": "request", "GRN": "grn"}
MAILER = Actor("service", "mailbox")
_QUEUED = "linked_mail_queued"  # Message-IDs queued in a session and not yet tried


def plus_address(kind: str, ref: str) -> str:
    """The address replies should go to: rfq+RFQ-0045@domain."""
    return f"{kind.lower()}+{ref}@{get_settings().mail_domain}"


def parse_plus_tag(address: str) -> str | None:
    local = address.split("@", 1)[0]
    _, _, tag = local.partition("+")
    return tag if tag and TAG.match(tag) else None


def _link_from_tag(tag: str | None) -> tuple[str | None, str | None]:
    if not tag:
        return None, None
    return TYPES[tag.split("-", 1)[0].upper()], tag


def send_linked_email(
    db: Session,
    to: str,
    subject: str,
    body: str,
    *,
    linked_type: str,
    linked_ref: str,
    case_id: int | None = None,
    attachments: list[tuple[str, str, bytes]] | None = None,
    document_ids: list[int] | None = None,
) -> str:
    """Queue an email whose replies will be linked back to the RFQ, PO or invoice. Does not commit: the caller commits it
    together with its own change, then calls `send_queued` (or `commit_and_send`) to send it at once; the `email` job
    retries it if the mail server is down (platform/outbox.py). A rollback drops it. Returns the Message-ID."""
    message_id = outbox.queue_email(
        db,
        to=to,
        subject=subject,
        body=body,
        html=email_layout.text_email(subject, body),
        linked_type=linked_type,
        linked_ref=linked_ref,
        case_id=case_id,
        attachments=attachments,
        reply_to=plus_address(linked_type, linked_ref),
        document_ids=document_ids,
    )
    db.info.setdefault(_QUEUED, []).append(message_id)
    return message_id


def send_queued(db: Session) -> list[str]:
    """After the caller's commit: send at once the linked mail this session queued (a mail rolled back is not found)."""
    ids: list[str] = db.info.pop(_QUEUED, [])
    if ids:
        outbox.send_now(outbox.tenant_of(db), ids, engine=db.get_bind().engine)
    return ids


def commit_and_send(db: Session) -> list[str]:
    """Commit the caller's work with the mail it queued, then send that mail."""
    db.commit()
    return send_queued(db)


def sender_check(db: Session, incoming: IncomingMail) -> dict[str, Any]:
    """Our own mail server's sender authentication of the mail (formulas/sender_auth.py), read once at intake."""
    trusted = db.execute(text("select value from org_setting where key = 'mail_authserv_id'")).scalar()
    try:
        headers = [str(h) for h in email.message_from_bytes(incoming.raw).get_all("Authentication-Results") or []]
    except Exception:  # noqa: BLE001 - a raw copy that cannot be read has no trusted result
        headers = []
    return sender_auth.read(headers, str(trusted or ""), incoming.from_addr)


def ingest(db: Session, tenant: str, incoming: IncomingMail) -> int | None:
    """Store one incoming message, its attachments and the raw copy; link it. Returns None if already stored."""
    if db.execute(text("select 1 from email_message where message_id = :m"), {"m": incoming.message_id}).first():
        return None
    tag = next((t for t in map(parse_plus_tag, incoming.to_addrs) if t), None)
    linked_type, linked_id = _link_from_tag(tag)
    if linked_id is None:  # no tag: follow the thread back to a message we sent
        refs = [r for r in [incoming.in_reply_to, *reversed(incoming.references)] if r]
        if refs:
            row = db.execute(
                text(
                    "select linked_type, linked_id, plus_tag from email_message where direction = 'out' and message_id = any(:r)"
                ),
                {"r": refs},
            ).first()
            if row:
                linked_type, linked_id, tag = row[0], row[1], row[2]
    case_id = None
    if linked_id:
        case_id = db.execute(text("select id from purchase_case where ref = :r"), {"r": linked_id}).scalar()
    raw_doc = files.save_file(
        db,
        tenant,
        incoming.raw,
        filename="message.eml",
        content_type="message/rfc822",
        doc_type="email_raw",
        uploaded_by=None,
        linked_type="email",
        linked_id=incoming.message_id,
    )
    attachment_ids = [
        files.save_file(
            db,
            tenant,
            data,
            filename=name,
            content_type=ctype,
            doc_type="email_attachment",
            uploaded_by=None,
            linked_type="email",
            linked_id=incoming.message_id,
        ).id
        for name, ctype, data in incoming.attachments
    ]
    auth = sender_check(db, incoming)
    email_id = int(
        db.execute(
            text(
                "insert into email_message (message_id, in_reply_to, direction, from_addr, to_addr, subject, body_text, sent_at, "
                "plus_tag, linked_type, linked_id, case_id, raw_document_id, attachment_ids, received_on, auth) values (:m, :r, 'in', "
                ":f, :t, :s, :b, :n, :tag, :lt, :li, :c, :rd, cast(:a as jsonb), :on, cast(:auth as jsonb)) returning id"
            ),
            {
                "m": incoming.message_id,
                "r": incoming.in_reply_to,
                "f": incoming.from_addr,
                "t": ", ".join(incoming.to_addrs),
                "s": incoming.subject,
                "b": incoming.body[:100_000],
                "n": incoming.sent_at,
                "tag": tag,
                "lt": linked_type,
                "li": linked_id,
                "c": case_id,
                "rd": raw_doc.id,
                "a": json.dumps(attachment_ids),
                "on": business_today(),  # the day we stored it: a quote's receipt date, never the sender's Date header
                "auth": json.dumps(auth),
            },
        ).scalar_one()
    )
    audit.record(
        db,
        MAILER,
        "email.receive",
        "email",
        incoming.message_id,
        after={
            "from": incoming.from_addr,
            "subject": incoming.subject,
            "linked": linked_id,
            "attachments": len(attachment_ids),
            "auth_verdict": auth["verdict"],
        },
    )
    if case_id:
        cases.log_event(
            db,
            int(case_id),
            "email_received",
            MAILER,
            {"email_id": email_id, "from": incoming.from_addr, "subject": incoming.subject},
        )
    events.emit(
        db, "email.received", incoming.message_id, source="mailbox", payload={"email_id": email_id, "linked_type": linked_type, "to": incoming.to_addrs},
        occurred_at=incoming.sent_at, inline=True,
    )  # fmt: skip
    db.commit()
    return email_id


# The job that reads each kind of incoming mail (by what it is linked to, or the mailbox it came to).
@events.route("email.received")
def _route_email(db: Session, tenant: str, ev: events.EventRow) -> None:
    """A stored mail goes to the one capability that reads it; a mail for nobody stays stored and nothing else."""
    email_id, linked_type, to_addrs = int(ev.payload["email_id"]), ev.payload.get("linked_type"), ev.payload["to"]
    if linked_type == "rfq":
        kind = "read_supplier_reply"  # a supplier's reply: read it into a draft
    elif linked_type == "po":
        kind = "supplier_message"  # a supplier's mail about an order: the supplier communication agent reads it
    elif linked_type is None and _to_invoice_mailbox(db, to_addrs):
        kind = "read_invoice_email"  # a supplier's invoice: read into a draft
    elif linked_type is None and _to_request_mailbox(db, to_addrs):
        kind = "intake_email"  # a colleague's request: the intake agent drafts it
    else:
        return
    worker.enqueue(tenant, kind, {"email_id": email_id}, key=f"email:{email_id}")


def _to_invoice_mailbox(db: Session, to_addrs: list[str]) -> bool:
    from p2p.invoices import reading  # noqa: PLC0415 - reading imports this layer's neighbours

    return reading.is_invoice_mailbox(db, to_addrs)


def _to_request_mailbox(db: Session, to_addrs: list[str]) -> bool:
    box = db.execute(text("select value from org_setting where key = 'request_mailbox'")).scalar()
    if not box:
        return False
    return str(box).strip().lower() in {parseaddr(a)[1].strip().lower() for a in to_addrs}


def poll(db: Session, tenant: str, mailbox: Mailbox) -> int:
    """Read the mailbox and store what is new. Returns how many messages were new."""
    return sum(1 for m in mailbox.fetch_new() if ingest(db, tenant, m) is not None)


def configured_mailbox() -> Mailbox:
    s = get_settings()
    if s.imap_host:
        return ImapMailbox(s.imap_host, s.imap_user, s.imap_password, s.imap_folder)
    return MailpitMailbox(s.mailpit_url)


@worker.register("mailbox_poll")
def _poll_job(ctx: worker.JobContext) -> None:
    """Poll once, then schedule the next poll. A failure is retried by the worker without losing the schedule."""
    from p2p.platform import routing  # noqa: PLC0415

    try:
        with Session(routing.engine_for(ctx.tenant)) as db:
            poll(db, ctx.tenant, configured_mailbox())
    finally:
        every = int(ctx.payload.get("every", 60))
        worker.enqueue_unique(
            ctx.tenant, "mailbox_poll", ctx.payload, ctx.now + timedelta(seconds=every), exclude_id=ctx.id
        )
