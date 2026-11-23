"""The versions of an order (task 0.2, D48): what each version said, the document mailed for it, and the supplier's
acknowledgement of exactly that version.

A row is written when a version is made (the order, each revision, an order brought over at cutover), in the same
transaction, with a snapshot of the order as it then stands (formulas/po_versions.py). A version is issued when it is
mailed to the supplier: its document is rendered once, stored, and that stored file is the mail's attachment, so a
reprint is always the bytes the supplier got. Rows are written only here.
"""

import json
import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from p2p.formulas import po_versions
from p2p.formulas.calendar import IST, business_today
from p2p.orders import approved_versions, document
from p2p.platform import audit, files, storage
from p2p.platform.sod import Actor

log = logging.getLogger(__name__)
SYSTEM = Actor("service", "po-versions")
VIA = ("link", "app", "email")

HEADER_LABELS = {
    "supplier_code": "Supplier",
    "ship_to_location": "Delivery place",
    "buyer_gstin": "Our GSTIN",
    "payment_terms_code": "Payment terms",
    "delivery_terms": "Delivery terms",
    "contract_code": "Contract",
    "taxable": "Total before tax",
    "cgst": "CGST",
    "sgst": "SGST",
    "igst": "IGST",
    "total": "Order total",
}
LINE_LABELS = {
    "item_code": "Item",
    "description": "Description",
    "qty": "Quantity",
    "uom": "Unit",
    "unit_price": "Price",
    "gst_rate": "GST %",
    "taxable": "Taxable",
    "cgst": "CGST",
    "sgst": "SGST",
    "igst": "IGST",
    "need_by": "Needed by",
    "price_record_id": "Contract price record",
    "contract_version": "Contract version",
    "status": "Status",
    "amount": "Amount",
    "added": "Added",
    "dropped": "Dropped",
}


class VersionError(Exception):
    def __init__(self, message: str, status: int = 409) -> None:
        super().__init__(message)
        self.status = status


@dataclass(frozen=True)
class Issued:
    document_id: int
    sha256: str


def made_by(actor: Actor) -> str:
    """How a version's maker is kept: 'user:<id>', 'service:ordering', 'service:supplier-change', 'cutover'."""
    return f"{actor.kind}:{actor.id}"


def _made_at() -> datetime:
    """Now, on the business date (a pinned date in a scenario): when a version was made, mailed or acknowledged, so the
    days counted from it (acknowledgement due, chasers, scoring) are business days."""
    return datetime.combine(business_today(), datetime.now(IST).timetz())


def record(db: Session, po_no: str, *, made_by: str, reason: str | None = None) -> int:
    """Keep the order's current version and what it says. The caller commits. Returns the version."""
    now = approved_versions.snapshot(db, po_no)
    db.execute(
        text(
            "insert into po_version (po_no, version, made_at, made_by, reason, snapshot) "
            "values (:p, :v, :at, :by, :r, cast(:s as jsonb))"
        ),
        {"p": po_no, "v": now.version, "at": _made_at(), "by": made_by, "r": reason, "s": _json(now)},
    )
    return now.version


def _json(snap: po_versions.Snapshot) -> str:
    return json.dumps(snap.as_json())


def _row(db: Session, po_no: str, version: int) -> dict[str, Any] | None:
    row = (
        db.execute(
            text(
                "select v.*, d.sha256 from po_version v left join document d on d.id = v.document_id "
                "where v.po_no = :p and v.version = :v"
            ),
            {"p": po_no, "v": version},
        )
        .mappings()
        .first()
    )
    return dict(row) if row else None


def _ensure(db: Session, po_no: str, version: int) -> None:
    """An order written before versions were kept (or by hand in a test) gets its row the first time it is issued."""
    if _row(db, po_no, version) is None:
        record(db, po_no, made_by="backfill")


def issue_document(db: Session, tenant: str, po_no: str) -> Issued | None:
    """Render the current version's document once and store it (the caller commits). A version already issued keeps
    its document. None when the document could not be made: the transaction is rolled back (the caller locks the
    order again), the failure is audited and the order goes out as text."""
    version = int(db.execute(text("select version from po where no = :n"), {"n": po_no}).scalar_one())
    _ensure(db, po_no, version)
    row = _row(db, po_no, version)
    if row and row["document_id"]:
        return Issued(int(row["document_id"]), str(row["sha256"]))
    try:
        pdf = document.order_document(db, po_no)
    except Exception as exc:  # noqa: BLE001 - the order still goes out as text; a fault in the document must not stop it
        log.warning("the document of %s version %s could not be made", po_no, version, exc_info=True)
        db.rollback()
        audit.record(
            db, SYSTEM, "po.document_failed", "po", po_no, after={"version": version},
            reason=f"the document could not be made ({type(exc).__name__}); the order is sent as text",
        )  # fmt: skip
        return None
    saved = files.save_file(
        db, tenant, pdf, filename=f"{po_no}.pdf" if version == 1 else f"{po_no}-v{version}.pdf",
        content_type="application/pdf", doc_type="po_version",
        uploaded_by=None, linked_type="po", linked_id=po_no, commit=False,
    )  # fmt: skip
    db.execute(
        text("update po_version set document_id = :d where po_no = :p and version = :v"),
        {"d": saved.id, "p": po_no, "v": version},
    )
    audit.record(
        db, SYSTEM, "po.version_issued", "po", po_no,
        after={"version": version, "document": saved.id, "sha256": saved.sha256},
    )  # fmt: skip
    return Issued(saved.id, saved.sha256)


def mark_sent(db: Session, po_no: str, version: int, to: str) -> None:
    """The version went to the supplier at this address, on the business date (the caller commits)."""
    _ensure(db, po_no, version)
    db.execute(
        text("update po_version set sent_at = :at, sent_to = :t where po_no = :p and version = :v"),
        {"at": _made_at(), "t": to, "p": po_no, "v": version},
    )


def issued_on(db: Session, po_no: str, before: int) -> datetime | None:
    """When the order last went to the supplier before version `before` (its business date)."""
    found = db.execute(
        text("select max(sent_at) from po_version where po_no = :p and version < :v"), {"p": po_no, "v": before}
    ).scalar()
    return found if isinstance(found, datetime) else None


def mail_carries(db: Session, message_id: str, version: int) -> None:
    """An outbound order mail carried this version: a reply to it answers this version (OP-12). The caller commits."""
    db.execute(text("update email_message set po_version = :v where message_id = :m"), {"v": version, "m": message_id})


def mark_acknowledged(
    db: Session, po_no: str, version: int, by: Actor, via: str, *, detail: Any = None, note: str | None = None
) -> int:
    """The supplier acknowledged this version, by its link, to a buyer in the app, or by email, with how it came
    (`detail`: dispatch.AckDetail). Only orders/dispatch.acknowledge calls this (the caller commits). Returns the row's
    id."""
    if via not in VIA:
        raise ValueError(f"an acknowledgement comes by {', '.join(VIA)}, not {via}")
    _ensure(db, po_no, version)
    return int(
        db.execute(
            text(
                "update po_version set acknowledged_at = :at, acknowledged_by = :b, ack_via = :w, ack_address = :a, "
                "ack_link_id = :l, ack_code_id = :c, ack_email_id = :e, ack_sender_auth = cast(:auth as jsonb), "
                "ack_change_ids = :ch, ack_note = :n where po_no = :p and version = :v returning id"
            ),
            {
                "b": made_by(by), "w": via, "p": po_no, "v": version, "n": note, "at": _made_at(),
                "a": getattr(detail, "address", None), "l": getattr(detail, "link_id", None),
                "c": getattr(detail, "code_id", None), "e": getattr(detail, "email_id", None),
                "auth": json.dumps(detail.sender_auth) if getattr(detail, "sender_auth", None) else None,
                "ch": list(detail.change_ids) if getattr(detail, "change_ids", None) else None,
            },
        ).scalar_one()
    )  # fmt: skip


def stored_pdf(db: Session, tenant: str, po_no: str, version: int) -> bytes | None:
    """The bytes mailed for this version; None when it was not issued (or has no record)."""
    row = _row(db, po_no, version)
    if row is None or not row["document_id"]:
        return None
    key = db.execute(text("select storage_key from document where id = :i"), {"i": row["document_id"]}).scalar_one()
    return storage.get_storage().get(tenant, str(key))


def current_pdf(db: Session, tenant: str, po_no: str) -> bytes:
    """The order's current version: as it was issued, else drawn now and marked as a draft not issued (OP-12)."""
    version = db.execute(text("select version from po where no = :n"), {"n": po_no}).scalar()
    if version is None:
        raise document.DocumentError(f"{po_no} does not exist")
    return stored_pdf(db, tenant, po_no, int(version)) or document.order_document(db, po_no, draft=True)


# ---- reading them -------------------------------------------------------------------------------------------------


def _approval(row: dict[str, Any], po: dict[str, Any], approved: set[int], revisions: dict[int, Any]) -> str | None:
    version = int(row["version"])
    if version in approved:
        return "approved"
    rev = revisions.get(version)
    if rev is not None and rev["baseline_version"] is not None and not rev["reapproval"]:
        return "carried"
    if version == po["version"] and po["status"] in ("draft", "submitted"):
        return "waiting"
    return None


def listing(db: Session, po_no: str) -> list[dict[str, Any]]:
    """Every recorded version of the order, newest first."""
    po = db.execute(text("select version, status from po where no = :n"), {"n": po_no}).mappings().first()
    if po is None:
        raise VersionError(f"{po_no} does not exist", 404)
    approved = {
        int(r[0]) for r in db.execute(text("select version from po_approved_version where po_no = :n"), {"n": po_no})
    }
    revisions = {
        int(r["version"]): dict(r)
        for r in db.execute(
            text("select version, material, reapproval, baseline_version from po_revision where po_no = :n"),
            {"n": po_no},
        ).mappings()
    }
    out = []
    for r in db.execute(
        text(
            "select v.*, d.sha256 from po_version v left join document d on d.id = v.document_id "
            "where v.po_no = :n order by v.version desc"
        ),
        {"n": po_no},
    ).mappings():
        rev = revisions.get(int(r["version"]))
        out.append(
            {
                "version": r["version"],
                "made_at": r["made_at"].isoformat(),
                "made_by": r["made_by"],
                "made_by_name": _name(db, str(r["made_by"])),
                "reason": r["reason"],
                "material": list(rev["material"] or []) if rev else [],
                "approval": _approval(dict(r), dict(po), approved, revisions),
                "sent_at": r["sent_at"].isoformat() if r["sent_at"] else None,
                "sent_to": r["sent_to"],
                "acknowledged_at": r["acknowledged_at"].isoformat() if r["acknowledged_at"] else None,
                "acknowledged_by": r["acknowledged_by"],
                "ack_via": r["ack_via"],
                "ack_address": r["ack_address"],
                "ack_with_code": r["ack_code_id"] is not None,
                "ack_note": r["ack_note"],
                "ack_email_id": r["ack_email_id"],
                "ack_changes": list(r["ack_change_ids"] or []),
                "ack_lines": _ack_lines(db, po_no, int(r["version"])) if r["acknowledged_at"] else {},
                "document": {"id": r["document_id"], "sha256": r["sha256"]} if r["document_id"] else None,
                "current": int(r["version"]) == int(po["version"]),
            }
        )
    return out


def _ack_lines(db: Session, po_no: str, version: int) -> dict[str, str]:
    """The line dates the acknowledgement of a version recorded."""
    return {
        str(r[0]): r[1].isoformat()
        for r in db.execute(
            text(
                "select c.po_line_no, c.promised_date from po_commitment c join po_version v on v.id = c.acknowledgement_id "
                "where v.po_no = :p and v.version = :v and c.po_line_no is not null order by c.po_line_no"
            ),
            {"p": po_no, "v": version},
        )
    }


def maker_name(db: Session, by: str) -> str:
    """Who made or acknowledged a version, in words."""
    return _name(db, by)


def _name(db: Session, by: str) -> str:
    """A maker in words: the person's name, else the system or the import that made it."""
    kind, _, ident = by.partition(":")
    if kind == "user" and ident.isdigit():
        from p2p.platform import notifications  # noqa: PLC0415 - logins live in the control database

        email = notifications.email_of(int(ident))
        if email:
            name = db.execute(text("select name from org_user where lower(email) = lower(:e)"), {"e": email}).scalar()
            if name:
                return str(name)
        return "a user"
    return {
        "service:ordering": "the system (ordering)",
        "service:supplier-change": "the supplier's change, accepted",
        "cutover": "the old system (cutover)",
        "backfill": "recorded when versions began to be kept",
    }.get(by, by)


def snapshot_of(db: Session, po_no: str, version: int) -> po_versions.Snapshot:
    row = db.execute(
        text("select snapshot from po_version where po_no = :p and version = :v"), {"p": po_no, "v": version}
    ).scalar()
    if row is None:
        raise VersionError(f"version {version} of {po_no} has no record (made before versions were kept)", 404)
    return po_versions.Snapshot.from_json(dict(row), version=version)


def _shown(value: Any) -> Any:
    if value is None or isinstance(value, (int, str)):
        return value
    return value.isoformat() if hasattr(value, "isoformat") else str(value)


def compare(db: Session, po_no: str, a: int, b: int) -> dict[str, Any]:
    """What changed from version a to version b: header fields, lines and charges, changed values only."""
    if db.execute(text("select 1 from po where no = :n"), {"n": po_no}).first() is None:
        raise VersionError(f"{po_no} does not exist", 404)
    if a == b:
        raise VersionError("pick two different versions to compare", 422)
    found = po_versions.diff(snapshot_of(db, po_no, a), snapshot_of(db, po_no, b))
    header = [
        {
            "field": d.field,
            "label": HEADER_LABELS.get(d.field, d.field),
            "before": _shown(d.before),
            "after": _shown(d.after),
        }
        for d in found
        if d.scope == "header"
    ]
    lines: dict[int, dict[str, Any]] = {}
    charges: dict[str, dict[str, Any]] = {}
    for d in found:
        if d.scope == "header":
            continue
        field = {
            "field": d.field,
            "label": LINE_LABELS.get(d.field, d.field),
            "before": _shown(d.before),
            "after": _shown(d.after),
        }
        change = d.field if d.field in ("added", "dropped") else "changed"
        if d.scope == "line" and d.line_no is not None:
            lines.setdefault(d.line_no, {"line_no": d.line_no, "change": change, "fields": []})["fields"].append(field)
        elif d.scope == "charge" and d.charge is not None:
            charges.setdefault(d.charge, {"type": d.charge, "change": change, "fields": []})["fields"].append(field)
    return {"from": a, "to": b, "header": header, "lines": list(lines.values()), "charges": list(charges.values())}
