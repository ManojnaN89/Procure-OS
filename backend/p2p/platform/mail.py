"""Sending email through SMTP (Mailpit in development, a real server later)."""

import smtplib
from email.message import EmailMessage

from p2p.config import get_settings

DEFAULT_SENDER = "p2p@cbcpl.example"


def send_email(to: str, subject: str, body: str, *, sender: str = DEFAULT_SENDER, reply_to: str | None = None) -> None:
    msg = EmailMessage()
    msg["From"] = sender
    msg["To"] = to
    msg["Subject"] = subject
    if reply_to:
        msg["Reply-To"] = reply_to
    msg.set_content(body)
    s = get_settings()
    with smtplib.SMTP(s.smtp_host, s.smtp_port, timeout=10) as smtp:
        smtp.send_message(msg)
