"""Sending email through SMTP (Mailpit in development, a real server later)."""

import smtplib
from email.message import EmailMessage
from email.utils import make_msgid

from p2p.config import get_settings

DEFAULT_SENDER = "p2p@cbcpl.example"


def new_message_id() -> str:
    return make_msgid(domain=get_settings().mail_domain)


def send_email(
    to: str,
    subject: str,
    body: str,
    *,
    sender: str = DEFAULT_SENDER,
    reply_to: str | None = None,
    message_id: str | None = None,
    attachments: list[tuple[str, str, bytes]] | None = None,
) -> str:
    """Send a plain-text email, optionally with attachments (filename, content type, bytes). Returns the Message-ID."""
    msg = EmailMessage()
    msg["From"] = sender
    msg["To"] = to
    msg["Subject"] = subject
    msg["Message-ID"] = message_id or new_message_id()
    if reply_to:
        msg["Reply-To"] = reply_to
    msg.set_content(body)
    for name, content_type, data in attachments or []:
        maintype, _, subtype = content_type.partition("/")
        msg.add_attachment(data, maintype=maintype or "application", subtype=subtype or "octet-stream", filename=name)
    s = get_settings()
    with smtplib.SMTP(s.smtp_host, s.smtp_port, timeout=10) as smtp:
        smtp.send_message(msg)
    return str(msg["Message-ID"])
