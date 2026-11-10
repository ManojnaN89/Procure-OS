"""Synthetic supplier invoices as documents: a text PDF (optionally with the e-invoice QR code drawn on it), a scanned
PDF, or a picture. Used by the invoice reading tests and by evals/e27. Everything here is made up; GSTINs carry 0000."""

import io
import zlib
from dataclasses import dataclass, field
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

import numpy as np
from PIL import Image, ImageDraw

from p2p.invoices import einvoice

D = Decimal
PAISE = D("0.01")


def money(x: Decimal) -> str:
    return f"{x.quantize(PAISE, rounding=ROUND_HALF_UP):,.2f}"


@dataclass
class Row:
    description: str
    qty: Decimal
    price: Decimal
    rate: Decimal = D(18)
    po_ref: str | None = None  # printed in front of the description, like 'PO-2627-000001/1'


@dataclass
class Spec:
    supplier_name: str = "Alpha Traders"
    gstin: str = "27AAAAA0000A1Z5"
    buyer_gstin: str = "29BBBBB0000B1Z5"
    number: str = "AT/2627/0101"
    on: date = date(2026, 11, 6)
    po_no: str | None = None
    rows: list[Row] = field(default_factory=lambda: [Row("Bearing", D(10), D(100))])
    freight: Decimal = D(0)
    credit_note: bool = False
    ref_invoice: str | None = None
    irn: bool = False  # print the IRN (and put the QR code on it when drawn)
    wrong_total: Decimal | None = None  # print a total that is not the sum

    @property
    def interstate(self) -> bool:
        return self.gstin[:2] != self.buyer_gstin[:2]

    def amounts(self) -> tuple[Decimal, Decimal, Decimal, Decimal]:
        """(taxable, cgst, sgst, igst) the way the platform works them out: tax per line, half each when in-state."""
        taxable = cgst = sgst = igst = D(0)
        parts = [(r.qty * r.price, r.rate) for r in self.rows]
        if self.freight:
            parts.append((self.freight, max(r.rate for r in self.rows)))
        for base, rate in parts:
            base = base.quantize(PAISE, rounding=ROUND_HALF_UP)
            tax = (base * rate / 100).quantize(PAISE, rounding=ROUND_HALF_UP)
            taxable += base
            if self.interstate:
                igst += tax
            else:
                half = (base * rate / 200).quantize(PAISE, rounding=ROUND_HALF_UP)
                cgst += half
                sgst += half
        return taxable, cgst, sgst, igst

    @property
    def total(self) -> Decimal:
        t, c, s, i = self.amounts()
        return t + c + s + i

    @property
    def doc_type(self) -> str:
        return "CRN" if self.credit_note else "INV"

    @property
    def irn_value(self) -> str:
        return einvoice.irn_of(self.gstin, self.on, self.doc_type, self.number)


def text_lines(spec: Spec, *, irn: str | None = None) -> list[str]:
    taxable, cgst, sgst, igst = spec.amounts()
    out = [
        "CREDIT NOTE" if spec.credit_note else "TAX INVOICE",
        spec.supplier_name,
        f"GSTIN: {spec.gstin}",
        f"{'Credit note' if spec.credit_note else 'Invoice'} No: {spec.number}",
        f"Date: {spec.on:%d/%m/%Y}",
        f"Bill to GSTIN: {spec.buyer_gstin}",
    ]
    if spec.po_no:
        out.append(f"Purchase order: {spec.po_no}")
    if spec.ref_invoice:
        out.append(f"Against invoice: {spec.ref_invoice}")
    out.append("Sl  Description  Qty  Rate  Amount  GST %")
    for i, r in enumerate(spec.rows, 1):
        ref = f"{r.po_ref} " if r.po_ref else ""
        out.append(f"{i} {ref}{r.description}   {r.qty:f}   {money(r.price)}   {money(r.qty * r.price)}   {r.rate:f}")
    if spec.freight:
        out.append(f"Freight {money(spec.freight)}")
    out.append(f"Taxable value {money(taxable)}")
    if spec.interstate:
        out.append(f"IGST {money(igst)}")
    else:
        out += [f"CGST {money(cgst)}", f"SGST {money(sgst)}"]
    out.append(f"Total {money(spec.wrong_total if spec.wrong_total is not None else spec.total)}")
    if irn:
        out.append(f"IRN: {irn}")
    return out


def qr_array(text: str) -> np.ndarray:
    import zxingcpp

    return np.array(
        zxingcpp.write_barcode_to_image(zxingcpp.create_barcode(text, zxingcpp.BarcodeFormat.QRCode), scale=4)
    )


def qr_text(spec: Spec, *, irn: str | None = None, **over: object) -> str:
    kw: dict[str, object] = {
        "irn": irn or spec.irn_value, "seller_gstin": spec.gstin, "buyer_gstin": spec.buyer_gstin, "doc_no": spec.number,
        "doc_type": spec.doc_type, "doc_date": spec.on, "total": spec.total, "item_count": len(spec.rows), "hsn": "8482",
    }  # fmt: skip
    kw.update(over)
    return einvoice.make_qr_text(**kw)  # type: ignore[arg-type]


def pdf(lines: list[str], qr: np.ndarray | None = None) -> bytes:
    """A one-page PDF with real text and, if given, a QR code picture on it."""
    stream = (
        "BT /F1 11 Tf 40 800 Td 16 TL "
        + " ".join(f"({ln.replace('(', '[').replace(')', ']')}) Tj T*" for ln in lines)
        + " ET"
    )
    if qr is not None:
        stream += " q 150 0 0 150 400 640 cm /Im1 Do Q"
    objs: list[bytes] = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >>"
        + (b" /XObject << /Im1 6 0 R >>" if qr is not None else b"")
        + b" >> >>",
        f"<< /Length {len(stream)} >>\nstream\n{stream}\nendstream".encode("latin-1"),
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    if qr is not None:
        h, w = qr.shape
        packed = zlib.compress(qr.tobytes())
        objs.append(
            f"<< /Type /XObject /Subtype /Image /Width {w} /Height {h} /ColorSpace /DeviceGray /BitsPerComponent 8 "
            f"/Filter /FlateDecode /Length {len(packed)} >>\nstream\n".encode()
            + packed
            + b"\nendstream"
        )
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for i, body in enumerate(objs, 1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n".encode() + body + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode()
    for off in offsets:
        out += f"{off:010d} 00000 n \n".encode()
    out += f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    return bytes(out)


def picture(lines: list[str], qr: np.ndarray | None = None, *, fmt: str = "PNG") -> bytes:
    """The invoice as a picture (a scan or a phone photo), with the QR code if given."""
    from sample_documents import _font

    img = Image.new("RGB", (1240, 1000), "white")
    draw = ImageDraw.Draw(img)
    font = _font(30)
    for i, ln in enumerate(lines):
        draw.text((60, 40 + i * 44), ln, fill="black", font=font)
    if qr is not None:
        img.paste(Image.fromarray(qr).convert("RGB").resize((300, 300)), (880, 40))
    buf = io.BytesIO()
    img.save(buf, fmt, resolution=150)
    return buf.getvalue()


def make(spec: Spec, *, qr: bool = False, kind: str = "pdf") -> bytes:
    """The invoice document. With qr=True it carries the e-invoice QR code (and prints the IRN)."""
    lines = text_lines(spec, irn=spec.irn_value if qr else None)
    code = qr_array(qr_text(spec)) if qr else None
    return pdf(lines, code) if kind == "pdf" else picture(lines, code)
