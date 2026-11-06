"""Reading mail behind one interface.

Development reads Mailpit through its REST API (Mailpit has no IMAP). A real mailbox such as Gmail or Workspace is
read with imaplib and an app password. Both hand back the same IncomingMail, parsed from the raw message.
"""

import imaplib
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from email import message_from_bytes, policy
from email.message import EmailMessage
from email.utils import getaddresses, parsedate_to_datetime
from typing import Any, Protocol

import httpx2


@dataclass
class IncomingMail:
    message_id: str
    from_addr: str
    to_addrs: list[str]
    subject: str
    body: str
    sent_at: datetime
    in_reply_to: str | None = None
    references: list[str] = field(default_factory=list)
    attachments: list[tuple[str, str, bytes]] = field(default_factory=list)  # filename, content type, bytes
    raw: bytes = b""


class Mailbox(Protocol):
    def fetch_new(self) -> list[IncomingMail]: ...


def _ids(value: str | None) -> list[str]:
    return [p for p in (value or "").replace("\n", " ").split() if p.startswith("<")]


def parse_rfc822(raw: bytes) -> IncomingMail:
    msg = message_from_bytes(raw, policy=policy.default)
    assert isinstance(msg, EmailMessage)
    body_part = msg.get_body(preferencelist=("plain", "html"))
    body = body_part.get_content() if body_part else ""
    attachments: list[tuple[str, str, bytes]] = []
    for part in msg.iter_attachments():
        data = part.get_payload(decode=True)
        attachments.append(
            (part.get_filename() or "attachment", part.get_content_type(), data if isinstance(data, bytes) else b"")
        )
    to_addrs = [addr for _, addr in getaddresses([*msg.get_all("To", []), *msg.get_all("Cc", [])]) if addr]
    try:
        sent = parsedate_to_datetime(str(msg["Date"])) if msg["Date"] else datetime.now(UTC)
    except (TypeError, ValueError):
        sent = datetime.now(UTC)
    return IncomingMail(
        message_id=str(msg["Message-ID"] or f"<missing-{abs(hash(raw))}@local>"),
        from_addr=getaddresses([str(msg["From"] or "")])[0][1] if msg["From"] else "",
        to_addrs=to_addrs,
        subject=str(msg["Subject"] or ""),
        body=str(body),
        sent_at=sent if sent.tzinfo else sent.replace(tzinfo=UTC),
        in_reply_to=str(msg["In-Reply-To"]).strip() if msg["In-Reply-To"] else None,
        references=_ids(str(msg["References"]) if msg["References"] else None),
        attachments=attachments,
        raw=raw,
    )


class MailpitMailbox:
    """Reads every message in Mailpit. Messages already stored are skipped by the caller (by Message-ID)."""

    def __init__(self, base_url: str = "http://127.0.0.1:8025", client: httpx2.Client | None = None) -> None:
        self.base = base_url.rstrip("/")
        self.client = client or httpx2.Client(timeout=10)

    def fetch_new(self) -> list[IncomingMail]:
        listing = self.client.get(f"{self.base}/api/v1/messages", params={"limit": 200}).json()
        out = []
        for item in listing.get("messages", []):
            raw = self.client.get(f"{self.base}/api/v1/message/{item['ID']}/raw").content
            out.append(parse_rfc822(raw))
        return out


class ImapMailbox:
    """Reads unseen mail from an IMAP folder and marks it seen. The connection factory can be replaced in tests."""

    def __init__(
        self,
        host: str,
        user: str,
        password: str,
        folder: str = "INBOX",
        connect: Callable[[str], Any] | None = None,
    ) -> None:
        self.host, self.user, self.password, self.folder = host, user, password, folder
        self.connect = connect or (lambda h: imaplib.IMAP4_SSL(h))

    def fetch_new(self) -> list[IncomingMail]:
        conn = self.connect(self.host)
        try:
            conn.login(self.user, self.password)
            conn.select(self.folder)
            _, data = conn.search(None, "UNSEEN")
            out = []
            for num in data[0].split():
                _, parts = conn.fetch(num, "(BODY.PEEK[])")
                raw = parts[0][1] if isinstance(parts[0], tuple) else b""
                out.append(parse_rfc822(bytes(raw)))
                conn.store(num, "+FLAGS", "\\Seen")
            return out
        finally:
            try:
                conn.logout()
            except (imaplib.IMAP4.error, OSError):
                pass
