"""Confirming a service from an email: a signed link that works once (signed with the shared `platform/signing.py`).

At the end of a period the person who asked for a service gets one email listing each service still to confirm, each with
a link to a page that shows the line and asks how much was done. The link names one person and one order line, expires,
and remembers how many confirmations the line had when it was sent: once a confirmation is made (from this link, another
one, or the app) the link stops working. Opening it changes nothing; the confirmation is a separate button, with the same
checks as the app, and the service entry says it came by an emailed link.
"""

import time
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from html import escape
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from p2p.approvals.email_links import EXPIRES_DAYS, LinkError
from p2p.config import get_settings
from p2p.formulas.money import format_inr, format_qty
from p2p.platform import audit, notifications, worker
from p2p.platform.signing import SignatureError, Signer
from p2p.receiving import services

PURPOSE = "service-link"
VIA = "emailed link"
SIGNER = Signer(PURPOSE)


@dataclass(frozen=True)
class ServiceLink:
    tenant: str
    po_no: str
    line_no: int
    user_id: int
    seen: int  # confirmations on the line when the link was made
    expires: int


def _count(db: Session, po_no: str, line_no: int) -> int:
    return int(
        db.execute(
            text("select count(*) from service_entry where po_no = :p and po_line_no = :l"), {"p": po_no, "l": line_no}
        ).scalar_one()
    )


def make(tenant: str, po_no: str, line_no: int, user_id: int, seen: int, *, now: float | None = None) -> str:
    expires = int((time.time() if now is None else now) + EXPIRES_DAYS * 86400)
    try:
        return SIGNER.sign([tenant, po_no, line_no, user_id, seen, expires])
    except SignatureError as exc:
        raise LinkError(str(exc)) from None


def read(token: str, *, now: float | None = None) -> ServiceLink:
    try:
        fields = SIGNER.read(token)
    except SignatureError as exc:
        raise LinkError(str(exc)) from None
    try:
        tenant, po_no, line_no, user_id, seen, expires = fields
        link = ServiceLink(str(tenant), str(po_no), int(line_no), int(user_id), int(seen), int(expires))
    except (TypeError, ValueError):
        raise LinkError("this link is not valid") from None
    if (time.time() if now is None else now) > link.expires:
        raise LinkError("this link has expired: confirm the service in the app")
    return link


def url(tenant: str, po_no: str, line_no: int, user_id: int, seen: int) -> str:
    return f"{get_settings().public_base_url.rstrip('/')}/confirm-service/{make(tenant, po_no, line_no, user_id, seen)}"


def summary(db: Session, link: ServiceLink) -> dict[str, Any]:
    """The line as the page shows it, if the link still works. Changes nothing."""
    if _count(db, link.po_no, link.line_no) != link.seen:
        raise LinkError("this link has been used: the service was confirmed after it was sent (see the app)")
    from p2p.approvals.workflow import org_code_of  # noqa: PLC0415 - approvals pulls in the request workflow

    me = org_code_of(db, link.user_id)
    for line in services.open_lines(db, for_person=me) if me else []:
        if line["po_no"] == link.po_no and line["line_no"] == link.line_no:
            return line
    raise LinkError("this service is no longer waiting for your confirmation")


def confirm(db: Session, link: ServiceLink, basis: str, value: Decimal, remarks: str | None, *, as_of: date) -> str:
    """Confirm for the person the link names, with every check the app makes. Returns the service entry number."""
    summary(db, link)
    try:
        done = services.confirm(
            db,
            services.Confirmation(link.po_no, link.line_no, basis, value, remarks=remarks),
            link.user_id,
            as_of=as_of,
            via=VIA,
        )
    except services.ServiceEntryError as exc:
        db.rollback()
        raise LinkError(str(exc)) from None
    return done.no


def compose(
    tenant: str, lines: list[dict[str, Any]], user_id: int, counts: dict[tuple[str, int], int]
) -> tuple[str, str]:
    """The email: each service still to confirm, with its link. A plain template; no model writes any of it."""
    text_parts = ["Please confirm the services below were done (fully or in part). Each link works once.", ""]
    rows = ""
    for ln in lines:
        what = f"{ln['description']} from {ln['supplier']} on {ln['po_no']}"
        done = f"{format_qty(ln['received_qty'])} of {format_qty(ln['qty'])} {ln['uom'] or ''} confirmed so far".strip()
        try:
            href = url(tenant, ln["po_no"], ln["line_no"], user_id, counts[(ln["po_no"], ln["line_no"])])
        except LinkError:  # no signing secret: confirm in the app
            href = ""
        text_parts += [f"- {what} ({format_inr(ln['amount'])}): {done}", f"  {href or 'Confirm it in the app.'}"]
        link = f"<a href='{escape(href)}'>Confirm</a>" if href else "Confirm it in the app"
        rows += f"<tr><td>{escape(what)}</td><td>{escape(format_inr(ln['amount']))}</td><td>{escape(done)}</td><td>{link}</td></tr>"
    text_parts += ["", f"The links expire in {EXPIRES_DAYS} days. Opening a link confirms nothing."]
    html = (
        "<div style='font-family:system-ui,sans-serif;font-size:14px;color:#1d2733;max-width:640px'>"
        f"<p>{escape(text_parts[0])}</p><table cellpadding='6' border='1' style='border-collapse:collapse'>"
        f"<tr><th>Service</th><th>Order value</th><th>So far</th><th></th></tr>{rows}</table>"
        f"<p style='color:#6b7785;font-size:12px'>{escape(text_parts[-1])}</p></div>"
    )
    return "\n".join(text_parts), html


def ask_requesters(db: Session, tenant: str, *, dry_run: bool = False) -> list[tuple[str, int]]:
    """Email each person who asked for a service still waiting for confirmation (one email each, at the end of a period).
    Returns (person, number of lines) for each person told (or who would be, on a dry run)."""
    by_person: dict[str, list[dict[str, Any]]] = {}
    for line in services.open_lines(db):
        who = line["requester"] or line["on_behalf_of"]
        if who:
            by_person.setdefault(str(who), []).append(line)
    told: list[tuple[str, int]] = []
    for code, lines in sorted(by_person.items()):
        uid = notifications.login_of(db, tenant, code)
        address = notifications.email_of(uid) if uid is not None else None
        if uid is None or not address:
            continue
        told.append((code, len(lines)))
        if dry_run:
            continue
        counts = {(ln["po_no"], ln["line_no"]): _count(db, ln["po_no"], ln["line_no"]) for ln in lines}
        body, html = compose(tenant, lines, uid, counts)
        subject = f"Please confirm {len(lines)} service(s)"
        worker.enqueue(tenant, "send_email", {"to": address, "subject": subject, "body": body, "html": html})
        audit.record(
            db, audit.SYSTEM, "service.confirm_email", "org_user", code,
            after={"to": address, "lines": [f"{ln['po_no']}/{ln['line_no']}" for ln in lines]},
        )  # fmt: skip
        db.commit()
    return told
