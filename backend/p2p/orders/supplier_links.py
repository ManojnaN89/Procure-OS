"""The supplier's links: a page with no login where a supplier answers an order or records a dispatch.

A link names one order, one supplier and one purpose ('respond': accept, decline or ask for a change; 'dispatch': record
what was shipped). It is signed (platform/signing.py, its own key), expires, and works once: the row in supplier_link is
marked used by the first answer, and a link for a version of the order that has since changed, for an order no longer in
the right state, or for an order of another supplier is refused. A respond link is for one version: every mailing of a
version issues its own, and an older version's link is refused as changed before anything else is said about the order
(DF-07). A dispatch link stays good across versions (OP-14). Opening the page changes nothing.

Answering from a respond link also needs a one-time code (D53): it is emailed only to the address the link was mailed to
(`sent_to`), while that address is still a registered contact of the order's supplier and the supplier is active
(OP-1, OP-19), and it works only on the link it was asked from. A forwarded link alone decides nothing. A link of an
older version can ask for the current version to be emailed to the supplier's contact, never shown on the page (OP-9).
The dispatch link needs no code (OP-3).
"""

import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from p2p.config import get_settings
from p2p.masters import suppliers as supplier_records
from p2p.platform import audit, otp, ratelimit
from p2p.platform.signing import SignatureError, Signer
from p2p.platform.sod import Actor

EXPIRES_DAYS = {"respond": 14, "dispatch": 60}
OPEN_FOR = {"respond": ("sent",), "dispatch": ("sent", "acknowledged", "part_received")}
SIGNER = Signer("supplier-link")
CODES = otp.OneTimeCodes("po_answer")
NO_CODE = "We cannot send a code for this link: please ask the buyer to send the order again"
RESEND_GAP = 600.0  # seconds: the current version is emailed at most once per stale link in this time
_CHECK = (
    "select l.id, l.po_no, l.supplier_code, l.purpose, l.po_version, l.used_at, l.sent_to, p.status, p.version, "
    "p.supplier_code as po_supplier from supplier_link l join po p on p.no = l.po_no where l.id = :i"
)


class LinkError(Exception):
    """The link cannot be used: altered, expired, used, or the order is no longer waiting for this answer."""


class StaleLink(LinkError):
    """A respond link of a version the order has moved on from (block 7 R23): the page says which version is current."""

    def __init__(self, message: str, link_version: int) -> None:
        super().__init__(message)
        self.link_version = link_version


@dataclass(frozen=True)
class Link:
    tenant: str
    id: int
    po_no: str
    supplier_code: str
    purpose: str
    expires: int


def issue(
    db: Session, tenant: str, po_no: str, purpose: str, *, sent_to: str | None = None, now: datetime | None = None
) -> str:
    """Make a link for the order's supplier and return its token. `sent_to` is the address its mail goes to (for a
    respond link, the only address its code goes to). The caller commits."""
    now = now or datetime.now(UTC)
    po = db.execute(text("select supplier_code, version from po where no = :n"), {"n": po_no}).one()
    expires = now + timedelta(days=EXPIRES_DAYS[purpose])
    link_id = db.execute(
        text(
            "insert into supplier_link (po_no, supplier_code, purpose, po_version, issued_at, expires_at, sent_to) "
            "values (:p, :s, :k, :v, :n, :e, :t) returning id"
        ),
        {"p": po_no, "s": po[0], "k": purpose, "v": po[1], "n": now, "e": expires, "t": sent_to},
    ).scalar_one()
    audit.record(
        db, audit.SYSTEM, "supplier_link.issue", "po", po_no,
        after={"link": int(link_id), "purpose": purpose, "version": int(po[1]), "to": sent_to},
    )  # fmt: skip
    return SIGNER.sign([tenant, int(link_id), po_no, str(po[0]), purpose, int(expires.timestamp())])


def url(token: str) -> str:
    return f"{get_settings().public_base_url.rstrip('/')}/supplier/{token}"


def read(token: str, *, now: float | None = None) -> Link:
    """The link a token stands for. Raises LinkError for anything altered, malformed or expired."""
    try:
        tenant, link_id, po_no, supplier, purpose, expires = SIGNER.read(token)
    except SignatureError as exc:
        raise LinkError(str(exc)) from None
    except ValueError:
        raise LinkError("this link is not valid") from None
    if (time.time() if now is None else now) > int(expires):
        raise LinkError("this link has expired: please reply to the order email instead")
    return Link(str(tenant), int(link_id), str(po_no), str(supplier), str(purpose), int(expires))


def check(db: Session, link: Link, *, lock: bool = False) -> dict[str, Any]:
    """The order, if the link may still be used for it. With `lock` the link row is held until the caller commits."""
    row = (
        db.execute(
            text(_CHECK + " for update of l" if lock else _CHECK),
            {"i": link.id},
        )
        .mappings()
        .first()
    )
    if row is None or (row["po_no"], row["supplier_code"], row["purpose"]) != (
        link.po_no,
        link.supplier_code,
        link.purpose,
    ):
        raise LinkError("this link is not valid")
    if row["po_supplier"] != link.supplier_code:
        raise LinkError(f"order {link.po_no} is not placed with you")
    # the version first: after a revision an older version's link always says the order changed (R23), never the state
    # the order is in for us now; a link of that version already used says so too
    if link.purpose == "respond" and row["po_version"] != row["version"]:
        used = " (this link has also been used already: it works once)" if row["used_at"] is not None else ""
        raise StaleLink(
            f"order {link.po_no} has changed since this link was sent{used}: please use the latest email",
            int(row["po_version"]),
        )
    if row["used_at"] is not None:
        raise LinkError("this link has already been used: it works once")
    if row["status"] not in OPEN_FOR[link.purpose]:
        raise LinkError(f"order {link.po_no} is {str(row['status']).replace('_', ' ')}: nothing more can be done here")
    return dict(row)


def use(db: Session, link: Link, what: str) -> None:
    """Mark the link used (the caller commits with the answer it carried)."""
    db.execute(text("update supplier_link set used_at = now(), used_for = :w where id = :i"), {"w": what, "i": link.id})


# ---- the one-time code on the supplier's answer (D53) -----------------------------------------------------------------


def mask(address: str | None) -> str:
    """An address as the page shows it: first letter, ****, @domain (s****@deccancastings.example)."""
    local, at, domain = str(address or "").partition("@")
    return f"{local[:1]}****@{domain}" if at and local else "****"


def code_ref(link: Link) -> str:
    """A code works only on the link it was asked from."""
    return f"{link.po_no}:{link.id}"


def code_address(db: Session, link: Link, row: dict[str, Any]) -> str:
    """The address a code for this link goes to: the one its mail went to, while that is still a registered contact of
    the order's supplier and the supplier is active (OP-1, OP-19)."""
    address = row.get("sent_to")
    status = db.execute(text("select status from supplier where code = :c"), {"c": link.supplier_code}).scalar()
    if not address or status != "active" or not supplier_records.is_contact(db, link.supplier_code, str(address)):
        raise LinkError(NO_CODE)
    return str(address)


def send_code(db: Session, link: Link) -> str:
    """Email a one-time code for this link to the address it was mailed to and return that address. Nothing on the
    order changes."""
    if link.purpose != "respond":
        raise LinkError("this link needs no code")
    address = code_address(db, link, check(db, link))
    db.rollback()
    try:
        return CODES.issue_to(db, code_ref(link), address, subject=f"Your code to answer purchase order {link.po_no}")
    except otp.CodeError as exc:
        raise LinkError(str(exc)) from None


def verify(db: Session, link: Link, code: str | None) -> dict[str, Any]:
    """Let an answer through with the right code. A wrong, expired or locked code is refused (otp commits that and
    nothing else). The link is locked and checked again once the code is accepted; the code is used in the caller's
    transaction, so it commits with the answer. Returns the link's row with `code_id` and `address`."""
    row = check(db, link, lock=True)
    address = code_address(db, link, row)
    try:
        CODES.verify_address(db, code_ref(link), address, code)
    except otp.CodeError as exc:
        db.rollback()
        raise LinkError(str(exc)) from None
    row = check(db, link, lock=True)
    row["code_id"] = db.execute(
        text(
            "select id from one_time_code where purpose = :p and ref = :r and status = 'used' order by id desc limit 1"
        ),
        {"p": CODES.purpose, "r": code_ref(link)},
    ).scalar()
    row["address"] = address
    return row


# ---- a link of an older version (OP-9) --------------------------------------------------------------------------------


def stale_facts(db: Session, link: Link, link_version: int) -> dict[str, Any]:
    """What the page of an older version's link says: the version now, when it was made and emailed, to whom (masked),
    what changed, and whether the current version can be emailed again."""
    from p2p.orders import versions  # noqa: PLC0415 - versions renders documents, which a link check need not load

    po = db.execute(text("select version, status from po where no = :n"), {"n": link.po_no}).one()
    current = db.execute(
        text("select made_at, sent_at, sent_to from po_version where po_no = :p and version = :v"),
        {"p": link.po_no, "v": po[0]},
    ).first()
    try:
        changed: dict[str, Any] | None = versions.compare(db, link.po_no, link_version, int(po[0]))
    except versions.VersionError:
        changed = None
    return {
        "po_no": link.po_no,
        "version": int(po[0]),
        "link_version": link_version,
        "revised_on": current[0] if current else None,
        "sent_on": current[1] if current else None,
        "sent_to": mask(current[2]) if current and current[2] else None,
        "changed": changed,
        "can_resend": po[1] == "sent",
    }


def resend_current(db: Session, tenant: str, link: Link) -> tuple[str, int]:
    """From a stale link: email a new respond link for the current version to the order's contact (never shown on the
    page), at most once per link in ten minutes. Returns the masked address and the version."""
    try:
        check(db, link)
    except StaleLink:
        pass
    else:
        raise LinkError("this link is for the current version of the order: use it to answer")
    status = db.execute(text("select status from po where no = :n"), {"n": link.po_no}).scalar()
    db.rollback()
    if status != "sent":
        raise LinkError(f"order {link.po_no} is {str(status).replace('_', ' ')}: nothing more can be done here")
    key = f"po-current:{tenant}:{link.id}"
    if ratelimit.count(key, RESEND_GAP) >= 1:
        raise LinkError("We emailed the current version a moment ago; please check your mail before asking again.")
    ratelimit.add(key)
    to, version = mail_current(
        db, tenant, link.po_no, why="asked for from a link of an older version", from_link=link.id
    )
    return mask(to), version


def mail_current(
    db: Session, tenant: str, po_no: str, *, why: str, actor: Actor = audit.SYSTEM, from_link: int | None = None
) -> tuple[str, int]:
    """A new respond link for the order's current version, mailed with that version's stored document to the order's
    contact; audit po.link_resend. Returns the address and the version. Commits and sends."""
    from p2p.orders import changes, versions  # noqa: PLC0415 - both build on this module

    po = dict(db.execute(text("select * from po where no = :n"), {"n": po_no}).mappings().one())
    contact = supplier_records.order_contact(db, po["supplier_code"])
    if contact is None:
        raise LinkError(f"the supplier {po['supplier_code']} has no email address on file")
    version = int(po["version"])
    issued = versions.issue_document(db, tenant, po_no)
    token = issue(db, tenant, po_no, "respond", sent_to=contact[0])
    audit.record(
        db, actor, "po.link_resend", "po", po_no,
        after={"version": version, "to": contact[0], "from_link": from_link}, reason=why,
    )  # fmt: skip
    db.commit()
    label = f" (version {version})" if version > 1 else ""
    changes.tell_supplier(
        db, po, f"Purchase order {po_no}{label}",
        f"Here is purchase order {po_no}{label} as it stands (attached). Please accept it, or tell us what you can do, "
        f"here (the link works once and asks for a code we email to you):\n{url(token)}",
        document_ids=[issued.document_id] if issued else None, po_version=version,
    )  # fmt: skip
    return contact[0], version
