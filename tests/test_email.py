import time
import uuid
from collections.abc import Iterator
from datetime import timedelta
from email.message import EmailMessage

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

from p2p.platform import cases, emails, mail, routing, tenants, worker
from p2p.platform.mailbox import ImapMailbox, MailpitMailbox, parse_rfc822
from p2p.platform.sod import Actor


def _mailpit_up() -> bool:
    try:
        return httpx.get("http://localhost:8025/api/v1/info", timeout=2).status_code == 200
    except httpx.HTTPError:
        return False


needs_mailpit = pytest.mark.skipif(not _mailpit_up(), reason="Mailpit is not running (docker compose up -d)")


@pytest.fixture
def db(control_db: str) -> Iterator[Session]:
    slug = "t" + uuid.uuid4().hex[:8]
    tenants.create_tenant(slug, load_reference=False)
    with Session(routing.engine_for(slug)) as session:
        session.info["slug"] = slug
        yield session
    routing.clear_engine_cache()


def ref(prefix: str = "RFQ") -> str:
    return f"{prefix}-{uuid.uuid4().hex[:6].upper()}"


def wait_for(subject: str) -> None:
    """Mailpit accepts mail instantly, but give it a moment to index."""
    deadline = time.time() + 10
    while time.time() < deadline:
        found = httpx.get(
            "http://localhost:8025/api/v1/search", params={"query": f"subject:{subject}"}, timeout=5
        ).json()
        if found["messages"]:
            return
        time.sleep(0.2)
    raise AssertionError("mail did not arrive")


# ---- plus-addressing and parsing -------------------------------------------------------------


def test_plus_addresses_are_built_and_read_back() -> None:
    address = emails.plus_address("rfq", "RFQ-0045")
    assert address == "rfq+RFQ-0045@cbcpl.example"
    assert emails.parse_plus_tag(address) == "RFQ-0045"
    assert emails.parse_plus_tag("rfq@cbcpl.example") is None
    assert emails.parse_plus_tag("rfq+random@cbcpl.example") is None  # not a reference we issue


def test_a_raw_message_is_parsed_with_body_threading_and_attachment() -> None:
    msg = EmailMessage()
    msg["From"] = "Sales <sales@steel.example>"
    msg["To"] = "rfq+RFQ-1@cbcpl.example, other@cbcpl.example"
    msg["Subject"] = "Re: RFQ-1"
    msg["Message-ID"] = "<abc@steel.example>"
    msg["In-Reply-To"] = "<ours@cbcpl.example>"
    msg["References"] = "<first@cbcpl.example> <ours@cbcpl.example>"
    msg["Date"] = "Thu, 05 Nov 2026 10:00:00 +0530"
    msg.set_content("Our quote is attached.")
    msg.add_attachment(b"%PDF fake", maintype="application", subtype="pdf", filename="quote.pdf")

    m = parse_rfc822(msg.as_bytes())

    assert (m.message_id, m.from_addr, m.subject) == ("<abc@steel.example>", "sales@steel.example", "Re: RFQ-1")
    assert m.to_addrs == ["rfq+RFQ-1@cbcpl.example", "other@cbcpl.example"]
    assert m.in_reply_to == "<ours@cbcpl.example>" and m.references == ["<first@cbcpl.example>", "<ours@cbcpl.example>"]
    assert m.body.strip() == "Our quote is attached." and m.attachments == [
        ("quote.pdf", "application/pdf", b"%PDF fake")
    ]
    assert m.sent_at.utcoffset() == timedelta(hours=5, minutes=30)


# ---- the round trip through Mailpit ----------------------------------------------------------


@needs_mailpit
def test_a_reply_to_an_rfq_is_stored_linked_to_the_rfq_and_adds_a_case_event(db: Session) -> None:
    rfq = ref()
    case_id = cases.create_case(db, rfq, Actor("service", "t"), title="Bearings RFQ")
    subject = f"Reply {rfq}"
    mail.send_email(
        f"rfq+{rfq}@cbcpl.example",
        subject,
        "We can supply at Rs 120.",
        sender="sales@steel.example",
        attachments=[("quote.pdf", "application/pdf", b"%PDF-1.4 quote")],
    )
    wait_for(subject)

    stored = emails.poll(db, db.info["slug"], MailpitMailbox())
    assert stored >= 1

    row = db.execute(
        text(
            "select direction, from_addr, linked_type, linked_id, case_id, jsonb_array_length(attachment_ids) "
            "from email_message where subject = :s"
        ),
        {"s": subject},
    ).one()
    assert tuple(row) == ("in", "sales@steel.example", "rfq", rfq, case_id, 1)
    event = db.execute(
        text("select event, detail->>'subject' from case_event where case_id = :c and event = 'email_received'"),
        {"c": case_id},
    ).one()
    assert tuple(event) == ("email_received", subject)
    # the attachment and the raw message are stored as documents
    docs = db.execute(
        text(
            "select doc_type, filename from document where linked_type = 'email' and linked_id = "
            "(select message_id from email_message where subject = :s) order by id"
        ),
        {"s": subject},
    ).all()
    assert [tuple(d) for d in docs] == [("email_raw", "message.eml"), ("email_attachment", "quote.pdf")]


@needs_mailpit
def test_reading_the_same_mail_twice_stores_it_once(db: Session) -> None:
    rfq = ref()
    subject = f"Once {rfq}"
    mail.send_email(f"rfq+{rfq}@cbcpl.example", subject, "hello")
    wait_for(subject)

    emails.poll(db, db.info["slug"], MailpitMailbox())
    again = emails.poll(db, db.info["slug"], MailpitMailbox())

    assert again == 0
    assert db.execute(text("select count(*) from email_message where subject = :s"), {"s": subject}).scalar_one() == 1


@needs_mailpit
def test_our_outgoing_mail_carries_the_reply_address_and_a_reply_without_a_tag_is_linked_by_thread(db: Session) -> None:
    rfq = ref()
    out_id = emails.send_linked_email(
        db, "sales@steel.example", f"Please quote {rfq}", "Details attached.", linked_type="rfq", linked_ref=rfq
    )
    wait_for(f"Please quote {rfq}")
    sent = httpx.get("http://localhost:8025/api/v1/search", params={"query": f"subject:{rfq}"}, timeout=5).json()[
        "messages"
    ][0]
    assert sent["ReplyTo"][0]["Address"] == f"rfq+{rfq}@cbcpl.example"

    # the supplier replies to a plain address, with no tag, but with In-Reply-To pointing at our message
    reply = f"Re: Please quote {rfq}"
    mail_msg = EmailMessage()
    mail_msg["From"] = "sales@steel.example"
    mail_msg["To"] = "p2p@cbcpl.example"
    mail_msg["Subject"] = reply
    mail_msg["In-Reply-To"] = out_id
    mail_msg["Message-ID"] = mail.new_message_id()
    mail_msg.set_content("Quote follows.")
    import smtplib

    with smtplib.SMTP("localhost", 1025) as smtp:
        smtp.send_message(mail_msg)
    wait_for(reply)
    emails.poll(db, db.info["slug"], MailpitMailbox())

    row = db.execute(
        text("select direction, linked_type, linked_id from email_message where subject = :s"), {"s": reply}
    ).one()
    assert tuple(row) == ("in", "rfq", rfq)


@needs_mailpit
def test_the_poll_job_stores_mail_and_schedules_the_next_poll(db: Session) -> None:
    engine = tenants.control_engine()
    with engine.begin() as c:
        c.execute(text("delete from job"))
    rfq = ref()
    subject = f"Job {rfq}"
    mail.send_email(f"rfq+{rfq}@cbcpl.example", subject, "hello")
    wait_for(subject)

    first = worker.enqueue(db.info["slug"], "mailbox_poll", {"every": 30})
    worker.run_once(now=worker.datetime.now(worker.UTC) + timedelta(seconds=1))

    assert db.execute(text("select count(*) from email_message where subject = :s"), {"s": subject}).scalar_one() == 1
    with engine.begin() as c:
        rows = c.execute(text("select id, status from job where kind = 'mailbox_poll' order by id")).all()
    engine.dispose()
    assert [r[1] for r in rows] == ["done", "queued"] and rows[0][0] == first  # exactly one follow-up poll is waiting


# ---- a real mailbox over IMAP (with a fake server) -------------------------------------------


class FakeImap:
    def __init__(self, raw: bytes) -> None:
        self.raw, self.seen, self.calls = raw, False, []

    def login(self, user: str, password: str) -> None:
        self.calls.append(("login", user))

    def select(self, folder: str) -> None:
        self.calls.append(("select", folder))

    def search(self, charset: object, criterion: str) -> tuple[str, list[bytes]]:
        assert criterion == "UNSEEN"
        return "OK", [b"" if self.seen else b"1"]

    def fetch(self, num: bytes, what: str) -> tuple[str, list[object]]:
        assert what == "(BODY.PEEK[])"
        return "OK", [(b"1 (BODY[] {n}", self.raw), b")"]

    def store(self, num: bytes, mode: str, flag: str) -> None:
        self.seen = True

    def logout(self) -> None:
        self.calls.append(("logout", ""))


def test_a_real_mailbox_is_read_over_imap_and_marked_seen() -> None:
    msg = EmailMessage()
    msg["From"] = "a@steel.example"
    msg["To"] = "rfq+RFQ-9@cbcpl.example"
    msg["Subject"] = "Quote"
    msg["Message-ID"] = "<imap-1@steel.example>"
    msg.set_content("body")
    server = FakeImap(msg.as_bytes())
    box = ImapMailbox("imap.example", "user", "app-password", connect=lambda host: server)

    first = box.fetch_new()
    second = box.fetch_new()

    assert [m.message_id for m in first] == ["<imap-1@steel.example>"] and second == []
    assert ("login", "user") in server.calls and ("select", "INBOX") in server.calls
