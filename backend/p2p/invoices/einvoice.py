"""The QR code of an e-invoice, read by code: no model is involved.

A registered supplier's invoice carries a QR code holding a signed token (a JWS) whose payload names the supplier and
buyer GSTINs, the document number, type and date, the total, and the IRN (invoice reference number) the portal issued.
The IRN is a hash of four things on the invoice, so it can be recomputed here: an IRN that does not match its own
invoice has been altered or belongs to another document.

The signature itself is NOT verified: that needs the portal's public key. So the payload is a claim that is checked
against the invoice, the supplier on file and the order, never trusted by itself. `signed` says only that it came in
the signed form.
"""

import base64
import hashlib
import io
import json
import re
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from p2p.formulas.fy import financial_year

QR_PAGES = 3  # an invoice carries its QR code on the first pages
QR_SCALE = 2.5  # rendering scale for reading the code from a page


@dataclass(frozen=True)
class EInvoice:
    irn: str | None
    seller_gstin: str | None
    buyer_gstin: str | None
    doc_no: str | None
    doc_type: str | None  # INV, CRN or DBN
    doc_date: date | None
    total: Decimal | None
    item_count: int | None
    hsn: str | None
    irn_date: date | None
    signed: bool

    @property
    def irn_ok(self) -> bool:
        """The IRN is the one this invoice's own details give."""
        if not (self.irn and self.seller_gstin and self.doc_no and self.doc_date and self.doc_type):
            return False
        return self.irn.upper() == irn_of(self.seller_gstin, self.doc_date, self.doc_type, self.doc_no)

    def as_json(self) -> dict[str, Any]:
        return {
            "irn": self.irn,
            "seller_gstin": self.seller_gstin,
            "buyer_gstin": self.buyer_gstin,
            "doc_no": self.doc_no,
            "doc_type": self.doc_type,
            "doc_date": self.doc_date.isoformat() if self.doc_date else None,
            "total": str(self.total) if self.total is not None else None,
            "item_count": self.item_count,
            "irn_ok": self.irn_ok,
            "signed": self.signed,
        }


def irn_of(seller_gstin: str, doc_date: date, doc_type: str, doc_no: str) -> str:
    """SHA-256 of the supplier's GSTIN, the financial year, the document type and the document number."""
    return hashlib.sha256(f"{seller_gstin}{financial_year(doc_date)}{doc_type}{doc_no}".encode()).hexdigest().upper()


def _date(value: Any) -> date | None:
    text = str(value or "").strip()
    for fmt in ("%d/%m/%Y", "%Y-%m-%d", "%d-%m-%Y"):
        try:
            return datetime.strptime(text[:10], fmt).date()  # noqa: DTZ007 - a calendar date, no time
        except ValueError:
            continue
    return None


def _decimal(value: Any) -> Decimal | None:
    try:
        return Decimal(str(value).replace(",", "")) if value not in (None, "") else None
    except InvalidOperation:
        return None


def _claims(text: str) -> tuple[dict[str, Any], bool] | None:
    """The details inside a QR code's text: a signed token, or plain JSON."""
    text = text.strip()
    parts = text.split(".")
    if len(parts) == 3 and all(parts):
        try:
            padded = parts[1] + "=" * (-len(parts[1]) % 4)
            body = json.loads(base64.urlsafe_b64decode(padded))
        except (ValueError, json.JSONDecodeError):
            return None
        inner = body.get("data", body) if isinstance(body, dict) else None
        if isinstance(inner, str):
            try:
                inner = json.loads(inner)
            except json.JSONDecodeError:
                return None
        return (inner, True) if isinstance(inner, dict) else None
    if text.startswith("{"):
        try:
            body = json.loads(text)
        except json.JSONDecodeError:
            return None
        return (body, False) if isinstance(body, dict) else None
    return None


def parse_qr_text(text: str) -> EInvoice | None:
    """An e-invoice from the text of a QR code, or None if the code is something else."""
    found = _claims(text)
    if found is None:
        return None
    data, signed = found
    if not any(k in data for k in ("Irn", "SellerGstin", "DocNo")):
        return None
    count = _decimal(data.get("ItemCnt"))
    return EInvoice(
        irn=(str(data["Irn"]).strip() if data.get("Irn") else None),
        seller_gstin=(str(data["SellerGstin"]).strip().upper() if data.get("SellerGstin") else None),
        buyer_gstin=(str(data["BuyerGstin"]).strip().upper() if data.get("BuyerGstin") else None),
        doc_no=(str(data["DocNo"]).strip() if data.get("DocNo") else None),
        doc_type=(str(data["DocTyp"]).strip().upper() if data.get("DocTyp") else None),
        doc_date=_date(data.get("DocDt")),
        total=_decimal(data.get("TotInvVal")),
        item_count=int(count) if count is not None else None,
        hsn=(str(data["MainHsnCode"]) if data.get("MainHsnCode") else None),
        irn_date=_date(data.get("IrnDt")),
        signed=signed,
    )


def make_qr_text(
    *, irn: str, seller_gstin: str, buyer_gstin: str, doc_no: str, doc_type: str, doc_date: date, total: Decimal,
    item_count: int, hsn: str = "", signed: bool = True,
) -> str:  # fmt: skip
    """A QR payload in the portal's shape. The signature is a placeholder: only tests and evals build these."""
    claims = {
        "SellerGstin": seller_gstin, "BuyerGstin": buyer_gstin, "DocNo": doc_no, "DocTyp": doc_type,
        "DocDt": doc_date.strftime("%d/%m/%Y"), "TotInvVal": float(total), "ItemCnt": item_count, "MainHsnCode": hsn,
        "Irn": irn, "IrnDt": f"{doc_date:%Y-%m-%d} 10:00:00",
    }  # fmt: skip
    if not signed:
        return json.dumps(claims)

    def b64(raw: bytes) -> str:
        return base64.urlsafe_b64encode(raw).decode().rstrip("=")

    header = b64(json.dumps({"alg": "RS256", "typ": "JWT"}).encode())
    payload = b64(json.dumps({"data": json.dumps(claims), "iss": "NIC"}).encode())
    return f"{header}.{payload}.{b64(b'placeholder-signature')}"


# ---- finding the code in a document -----------------------------------------------------------


def _pages(data: bytes, filename: str) -> list[Any]:
    """The first pages of a PDF as images, or the image itself."""
    from PIL import Image

    if filename.lower().endswith(".pdf"):
        import pypdfium2 as pdfium  # type: ignore[import-untyped]

        pdf = pdfium.PdfDocument(data)
        try:
            return [pdf[i].render(scale=QR_SCALE).to_pil() for i in range(min(len(pdf), QR_PAGES))]
        finally:
            pdf.close()
    return [Image.open(io.BytesIO(data)).convert("RGB")]


def qr_texts(data: bytes, filename: str) -> list[str]:
    """The text of every QR code found on the first pages. A document that cannot be rendered has none."""
    import zxingcpp

    texts: list[str] = []
    try:
        for page in _pages(data, filename):
            texts += [r.text for r in zxingcpp.read_barcodes(page, formats=zxingcpp.barcode_formats_from_str("QRCode"))]
    except Exception:  # noqa: BLE001 - a file that cannot be rendered simply has no readable code
        return texts
    return texts


def find_einvoice(data: bytes, filename: str) -> EInvoice | None:
    for text in qr_texts(data, filename):
        parsed = parse_qr_text(text)
        if parsed is not None:
            return parsed
    return None


def gstin_like(value: str) -> bool:
    return re.fullmatch(r"\d{2}[A-Z]{5}\d{4}[A-Z]\d[A-Z\d][A-Z\d]", value.strip().upper()) is not None
