"""Which documents a supplier must have on file, and whether they are good. Plain rules on dates and flags."""

from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import date, timedelta

LABELS = {
    "pan": "PAN card",
    "gst_cert": "GST certificate",
    "udyam": "Udyam certificate",
    "cancelled_cheque": "bank proof (cancelled cheque)",
    "msme_agreement": "MSME agreement",
    "iso": "ISO certificate",
    "licence": "licence",
    "insurance": "insurance",
    "lower_tds_cert": "lower TDS certificate",
}


@dataclass(frozen=True)
class SupplierDocument:
    doc_type: str
    expires_on: date | None
    verified: bool


@dataclass
class DocumentCheck:
    blocking: list[str] = field(default_factory=list)  # a required document is missing, not verified or expired
    warnings: list[str] = field(default_factory=list)  # another document has expired or is about to

    @property
    def ok(self) -> bool:
        return not self.blocking


def required_documents(gst_reg_type: str | None, msme_category: str | None) -> tuple[str, ...]:
    """PAN for everyone, the GST certificate if registered for GST, the Udyam certificate for an MSME. The bank proof
    (cancelled cheque) is not needed to activate, invite or send an order (owner decision 9, 22 Nov 2026): the bank
    account is verified before any payment instead."""
    required = ["pan"]
    if gst_reg_type == "regular":
        required.append("gst_cert")
    if msme_category in ("micro", "small", "medium"):
        required.append("udyam")
    return tuple(required)


def _label(doc_type: str) -> str:
    return LABELS.get(doc_type, doc_type.replace("_", " "))


def check_documents(
    required: Iterable[str], documents: Iterable[SupplierDocument], today: date, warn_days: int = 30
) -> DocumentCheck:
    """A required document counts when one of its type is verified and not expired (on its last day it is still good).

    Any other document that has expired, or expires within `warn_days`, is only a warning.
    """
    by_type: dict[str, list[SupplierDocument]] = {}
    for doc in documents:
        by_type.setdefault(doc.doc_type, []).append(doc)
    result = DocumentCheck()
    required = tuple(required)
    for doc_type in required:
        held = by_type.get(doc_type, [])
        if not held:
            result.blocking.append(f"{_label(doc_type)} is missing")
        elif not any(d.verified for d in held):
            result.blocking.append(f"{_label(doc_type)} is not verified")
        elif not any(d.verified and (d.expires_on is None or d.expires_on >= today) for d in held):
            result.blocking.append(f"{_label(doc_type)} has expired")
    soon = today + timedelta(days=warn_days)
    for doc_type, held in sorted(by_type.items()):
        if doc_type in required or any(d.expires_on is None or d.expires_on > soon for d in held):
            continue  # a required one is already judged; one with a later copy on file is fine
        latest = max(d.expires_on for d in held if d.expires_on)
        if latest < today:
            result.warnings.append(f"{_label(doc_type)} expired on {latest:%d %b %Y}")
        else:
            result.warnings.append(f"{_label(doc_type)} expires on {latest:%d %b %Y}")
    return result
