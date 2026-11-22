"""The email outbox (task 11a.1, DF-23): a mail is written in the sender's transaction and sent after the commit.

`queue_email` adds an `email_message` row (status `queued`) and a job `email` keyed by its Message-ID, without
committing: the mail is kept or dropped with the work it is about. After the caller commits, `send_now` tries it at once
(so it leaves at the same moment as before) and the job is the safety net. `deliver` is the one sender: it locks the
row, sends only a row still queued, and marks it sent, so a mail is never sent twice. The full body, with any signed
link, is kept encrypted in `sealed` until it is sent; `body_text` and the job payload never hold the secret part.

One-time codes (platform/otp.py) do not come here: the person is waiting for the code, and a lost one is harmless.
"""

import json
import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import Engine, text
from sqlalchemy.orm import Session

from p2p.platform import audit, crypto, files, mail, routing, storage, tenants, worker

log = logging.getLogger(__name__)
JOB = "email"
SWEEP_JOB = "email_sweep"
GRACE = timedelta(minutes=10)  # a job younger than this whose row is missing waits for its sender to commit
GIVE_UP = timedelta(hours=24)  # a mail still unsent after this is failed and a person is told
SWEEP_EVERY = timedelta(minutes=15)


def tenant_of(db: Session) -> str:
    """The company whose database the session is on."""
    slug = tenants.slug_of(str(db.get_bind().engine.url.database or ""))
    if slug is None:
        raise RuntimeError("mail can only be queued in a company's database")
    return slug


def queue_email(
    db: Session,
    *,
    to: str,
    subject: str,
    body: str,
    html: str | None = None,
    secret: str = "",
    record: str | None = None,
    linked_type: str | None = None,
    linked_ref: str | None = None,
    case_id: int | None = None,
    attachments: list[tuple[str, str, bytes]] | None = None,
    reply_to: str | None = None,
    sender: str = mail.DEFAULT_SENDER,
) -> str:
    """Record a mail to send once the caller commits. The mail says `body` + `secret`; `body_text` keeps `record` when
    given (a mail that is all links), else `body`: never the secret part. The caller commits, then calls `send_now` to send it
    at once (else the `email` job sends it). Returns the Message-ID. Does not commit."""
    tenant = tenant_of(db)
    message_id = mail.new_message_id()
    attachment_ids = [
        files.save_file(
            db, tenant, data, filename=name, content_type=ctype, doc_type="email_attachment_out", uploaded_by=None,
            linked_type="email", linked_id=message_id, commit=False,
        ).id
        for name, ctype, data in attachments or []
    ]  # fmt: skip
    db.execute(
        text(
            "insert into email_message (message_id, direction, from_addr, to_addr, subject, body_text, plus_tag, "
            "linked_type, linked_id, case_id, attachment_ids, status, queued_at, sealed, reply_to, sender, job_kind) "
            "values (:m, 'out', :f, :t, :s, :b, :tag, :lt, :li, :c, cast(:a as jsonb), 'queued', now(), :sealed, :rt, "
            ":f, :jk)"
        ),
        {
            "m": message_id, "f": sender, "t": to, "s": subject, "b": (body if record is None else record)[:100_000], "tag": linked_ref,
            "lt": linked_type.lower() if linked_type else None, "li": linked_ref, "c": case_id,
            "a": json.dumps(attachment_ids), "rt": reply_to, "jk": JOB,
            "sealed": crypto.encrypt(json.dumps({"body": body + secret, "html": html})),
        },
    )  # fmt: skip
    audit.record(
        db, audit.SYSTEM, "email.queue", "email", message_id, after={"to": to, "subject": subject, "linked": linked_ref}
    )
    worker.enqueue(tenant, JOB, {"message_id": message_id}, key=message_id)
    return message_id


def send_now(tenant: str, message_ids: list[str], *, engine: Engine | None = None) -> None:
    """After the caller's commit: try each mail at once (so it leaves at the same moment as before the outbox). A failure
    is logged and left to the job; a mail whose transaction was rolled back is simply not found."""
    for message_id in message_ids:
        try:
            with Session(engine or routing.engine_for(tenant)) as db:
                deliver(db, message_id, tenant)
        except Exception:  # noqa: BLE001 - the email job retries it
            log.warning("mail %s not sent now; the job will retry", message_id, exc_info=True)


def deliver(db: Session, message_id: str, tenant: str | None = None) -> str:
    """Send one queued mail: `sent`, or what it already is (`sent`, `cancelled`, `failed`), `busy` (another sender holds
    it) or `absent` (not committed, or rolled back). An SMTP error is recorded on the row and raised."""
    row = (
        db.execute(
            text(
                "select id, status, to_addr, subject, sealed, reply_to, sender, attachment_ids, linked_type, linked_id "
                "from email_message where message_id = :m and direction = 'out' for update skip locked"
            ),
            {"m": message_id},
        )
        .mappings()
        .first()
    )
    if row is None:
        there = db.execute(text("select 1 from email_message where message_id = :m"), {"m": message_id}).first()
        db.rollback()
        return "busy" if there else "absent"
    if row["status"] != "queued":
        db.rollback()
        return str(row["status"])
    sealed = json.loads(crypto.decrypt(str(row["sealed"])))
    try:
        mail.send_email(
            str(row["to_addr"]), str(row["subject"] or ""), str(sealed["body"]), sender=str(row["sender"] or mail.DEFAULT_SENDER),
            reply_to=row["reply_to"], message_id=message_id, attachments=_attachments(db, tenant or tenant_of(db), row),
            html=sealed.get("html"),
        )  # fmt: skip
    except Exception as exc:
        error = f"{type(exc).__name__}: {str(exc)[:300]}"
        db.execute(
            text("update email_message set attempts = attempts + 1, last_error = :e where id = :i"),
            {"e": error, "i": row["id"]},
        )
        if row["linked_type"] == "rfq":  # shown on the RFQ page, as before
            db.execute(
                text("update rfq_supplier set send_error = :e where rfq_no = :n and message_id = :m"),
                {"e": str(exc)[:300], "n": row["linked_id"], "m": message_id},
            )
        db.commit()
        schedule_sweep(tenant or tenant_of(db))
        raise
    db.execute(
        text(
            "update email_message set status = 'sent', sent_at = now(), sealed = null, attempts = attempts + 1, "
            "last_error = null where id = :i"
        ),
        {"i": row["id"]},
    )
    if row["linked_type"] == "rfq":
        db.execute(
            text("update rfq_supplier set send_error = null where rfq_no = :n and message_id = :m"),
            {"n": row["linked_id"], "m": message_id},
        )
    audit.record(
        db, audit.SYSTEM, "email.send", "email", message_id,
        after={"to": row["to_addr"], "subject": row["subject"], "linked": row["linked_id"]},
    )  # fmt: skip
    db.commit()
    return "sent"


def _attachments(db: Session, tenant: str, row: Any) -> list[tuple[str, str, bytes]]:
    ids = list(row["attachment_ids"] or [])
    if not ids:
        return []
    docs = db.execute(
        text("select filename, content_type, storage_key from document where id = any(:i) order by id"), {"i": ids}
    ).all()
    return [(str(d[0]), str(d[1]), storage.get_storage().get(tenant, str(d[2]))) for d in docs]


def cancel(db: Session, message_id: str, reason: str) -> bool:
    """Stop a mail that has not gone yet. Does not commit."""
    done = db.execute(
        text(
            "update email_message set status = 'cancelled', sealed = null, last_error = :r "
            "where message_id = :m and status = 'queued' returning id"
        ),
        {"m": message_id, "r": reason},
    ).first()
    if done:
        audit.record(db, audit.SYSTEM, "email.cancel", "email", message_id, reason=reason)
    return done is not None


@worker.register(JOB)
def _email_job(ctx: worker.JobContext) -> None:
    message_id = str(ctx.payload["message_id"])
    with Session(routing.engine_for(ctx.tenant)) as db:
        got = deliver(db, message_id, ctx.tenant)
    if got == "busy":
        raise RuntimeError("another sender holds this mail; try again")
    if got == "absent" and ctx.now - (ctx.created_at or ctx.now) < GRACE:
        raise RuntimeError("the mail is not committed yet; try again")  # older: its sender rolled back, nothing to send


# ---- the sweep: mail whose job gave up while the mail server was down -------------------------------------------


def schedule_sweep(tenant: str, run_at: datetime | None = None) -> int | None:
    return worker.enqueue_unique(tenant, SWEEP_JOB, {}, run_at or datetime.now(UTC) + SWEEP_EVERY)


def sweep(db: Session, tenant: str, now: datetime) -> dict[str, int]:
    """Queue again each mail waiting longer than the grace time; fail one waiting a day and tell a person. Commits."""
    from p2p.platform import tasks, users  # noqa: PLC0415 - tasks send their own mail through this module

    rows = db.execute(
        text(
            "select message_id, queued_at, to_addr, subject, linked_type, linked_id from email_message "
            "where status = 'queued' and queued_at < :g order by id"
        ),
        {"g": now - GRACE},
    ).all()
    retried = failed = 0
    for m, queued_at, to, subject, linked_type, linked_id in rows:
        if queued_at >= now - GIVE_UP:
            retried += worker.enqueue(tenant, JOB, {"message_id": m}, key=m) is not None
            continue
        db.execute(
            text(
                "update email_message set status = 'failed', sealed = null where message_id = :m and status = 'queued'"
            ),
            {"m": m},
        )
        audit.record(db, audit.SYSTEM, "email.failed", "email", m, after={"to": to, "subject": subject})
        db.commit()
        failed += 1
        roles = {"rfq": ("Buyer",), "po": ("Buyer",), "invoice": ("AP", "Finance")}.get(str(linked_type), ("Admin",))
        tasks.create_task(
            db, tenant, kind="email_failed", title=f"An email could not be sent to {to}: {subject}"[:200],
            assignee=users.first_with_role(db, *roles, "Admin"), sla_hours=8,
            payload={"message_id": m, "to": to, "subject": subject, "linked_type": linked_type, "ref": linked_id},
        )  # fmt: skip
    db.commit()
    return {"retried": retried, "failed": failed}


@worker.register(SWEEP_JOB)
def _sweep_job(ctx: worker.JobContext) -> None:
    with Session(routing.engine_for(ctx.tenant)) as db:
        sweep(db, ctx.tenant, ctx.now)
        left = db.execute(text("select 1 from email_message where status = 'queued' limit 1")).first()
    if left:  # keeps itself going while something waits
        worker.enqueue_unique(ctx.tenant, SWEEP_JOB, {}, ctx.now + SWEEP_EVERY, exclude_id=ctx.id)
