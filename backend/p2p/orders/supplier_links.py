"""The supplier's links: a page with no login where a supplier answers an order or records a dispatch.

A link names one order, one supplier and one purpose ('respond': accept, decline or ask for a change; 'dispatch': record
what was shipped). It is signed (platform/signing.py, its own key), expires, and works once: the row in supplier_link is
marked used by the first answer, and a link for a version of the order that has since changed, for an order no longer in
the right state, or for an order of another supplier is refused. Opening the page changes nothing.
"""

import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from p2p.config import get_settings
from p2p.platform.signing import SignatureError, Signer

EXPIRES_DAYS = {"respond": 14, "dispatch": 60}
OPEN_FOR = {"respond": ("sent",), "dispatch": ("sent", "acknowledged", "part_received")}
SIGNER = Signer("supplier-link")
_CHECK = (
    "select l.po_no, l.supplier_code, l.purpose, l.po_version, l.used_at, p.status, p.version, "
    "p.supplier_code as po_supplier from supplier_link l join po p on p.no = l.po_no where l.id = :i"
)


class LinkError(Exception):
    """The link cannot be used: altered, expired, used, or the order is no longer waiting for this answer."""


@dataclass(frozen=True)
class Link:
    tenant: str
    id: int
    po_no: str
    supplier_code: str
    purpose: str
    expires: int


def issue(db: Session, tenant: str, po_no: str, purpose: str, *, now: datetime | None = None) -> str:
    """Make a link for the order's supplier and return its token. The caller commits."""
    now = now or datetime.now(UTC)
    po = db.execute(text("select supplier_code, version from po where no = :n"), {"n": po_no}).one()
    expires = now + timedelta(days=EXPIRES_DAYS[purpose])
    link_id = db.execute(
        text(
            "insert into supplier_link (po_no, supplier_code, purpose, po_version, issued_at, expires_at) "
            "values (:p, :s, :k, :v, :n, :e) returning id"
        ),
        {"p": po_no, "s": po[0], "k": purpose, "v": po[1], "n": now, "e": expires},
    ).scalar_one()
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
    if row["used_at"] is not None:
        raise LinkError("this link has already been used: it works once")
    if row["status"] not in OPEN_FOR[link.purpose]:
        raise LinkError(f"order {link.po_no} is {str(row['status']).replace('_', ' ')}: nothing more can be done here")
    if link.purpose == "respond" and row["po_version"] != row["version"]:
        raise LinkError(f"order {link.po_no} has changed since this link was sent: please use the latest email")
    return dict(row)


def use(db: Session, link: Link, what: str) -> None:
    """Mark the link used (the caller commits with the answer it carried)."""
    db.execute(text("update supplier_link set used_at = now(), used_for = :w where id = :i"), {"w": what, "i": link.id})
