"""Reading the mail a test made: the outbox keeps the full text (with any signed link) sealed until it is sent, so a test
that checks the mail itself opens the queued rows, as the sender would."""

import json
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from p2p.platform import crypto


def queued_mails(db: Session) -> list[dict[str, Any]]:
    """Each mail still waiting to be sent, oldest first: to, subject, body and html as they will go out."""
    db.rollback()
    out = []
    for to, subject, sealed in db.execute(
        text(
            "select to_addr, subject, sealed from email_message where status = 'queued' and sealed is not null order by id"
        )
    ):
        full = json.loads(crypto.decrypt(str(sealed)))
        out.append({"to": to, "subject": subject, "body": full["body"], "html": full.get("html") or ""})
    return out
