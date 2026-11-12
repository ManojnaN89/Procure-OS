"""Email in and out, linked to the work it is about.

Outgoing mail carries a plus-address in Reply-To, such as rfq+RFQ-0045@our-domain, so the supplier's reply comes back
tagged with the RFQ. Incoming mail is linked by that tag, or by In-Reply-To / References pointing at a message we
sent. A linked message that matches a case adds a case event.
"""

import json
import re
from datetime import UTC, datetime, timedelta
from email.utils import parseaddr

from sqlalchemy import text
from sqlalchemy.orm import Session

from p2p.config import get_settings
from p2p.platform import audit, cases, email_layout, files, mail, worker
from p2p.platform.mailbox import ImapMailbox, IncomingMail, Mailbox, MailpitMailbox
from p2p.platform.sod import Actor

TAG = re.compile(r"^(RFQ|PO|INV|REQ|GRN)-[\w-]+$", re.IGNORECASE)
TYPES = {"RFQ": "rfq", "PO": "po", "INV": "invoice", "REQ": "request", "GRN": "grn"}
MAILER = Actor("service", "mailbox")


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
) -> str:
    """Send an email whose replies will be linked back to the RFQ, PO or invoice. Returns the Message-ID."""
    message_id = mail.new_message_id()
    reply_to = plus_address(linked_type, linked_ref)
    mail.send_email(
        to,
        subject,
        body,
        message_id=message_id,
        reply_to=reply_to,
        attachments=attachments,
        html=email_layout.text_email(subject, body),
    )
    db.execute(
        text(
            "insert into email_message (message_id, direction, from_addr, to_addr, subject, body_text, sent_at, plus_tag, "
            "linked_type, linked_id, case_id) values (:m, 'out', :f, :t, :s, :b, :n, :tag, :lt, :li, :c)"
        ),
        {
            "m": message_id,
            "f": mail.DEFAULT_SENDER,
            "t": to,
            "s": subject,
            "b": body[:100_000],
            "n": datetime.now(UTC),
            "tag": linked_ref,
            "lt": linked_type.lower(),
            "li": linked_ref,
            "c": case_id,
        },
    )
    audit.record(
        db, audit.SYSTEM, "email.send", "email", message_id, after={"to": to, "subject": subject, "linked": linked_ref}
    )
    db.commit()
    return message_id


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
    email_id = int(
        db.execute(
            text(
                "insert into email_message (message_id, in_reply_to, direction, from_addr, to_addr, subject, body_text, sent_at, "
                "plus_tag, linked_type, linked_id, case_id, raw_document_id, attachment_ids) values (:m, :r, 'in', :f, :t, :s, :b, "
                ":n, :tag, :lt, :li, :c, :rd, cast(:a as jsonb)) returning id"
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
    db.commit()
    if linked_type == "rfq":
        worker.enqueue(
            tenant, "read_supplier_reply", {"email_id": email_id}
        )  # a supplier's reply: read it into a draft
    elif linked_type == "po":
        worker.enqueue(
            tenant, "supplier_message", {"email_id": email_id}
        )  # a supplier's mail about an order: the supplier communication agent reads it
    elif linked_type is None and _to_invoice_mailbox(db, incoming.to_addrs):
        worker.enqueue(tenant, "read_invoice_email", {"email_id": email_id})  # a supplier's invoice: read into a draft
    elif linked_type is None and _to_request_mailbox(db, incoming.to_addrs):
        worker.enqueue(
            tenant, "intake_email", {"email_id": email_id}
        )  # a colleague's request: the intake agent drafts it
    return email_id


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
