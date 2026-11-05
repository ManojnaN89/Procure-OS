"""Builds small synthetic documents for the parsing tests: a text PDF, a scanned PDF, Excel, Word and CSV."""

import io
from pathlib import Path

import openpyxl
from PIL import Image, ImageDraw, ImageFont

QUOTE_LINES = [
    "QUOTATION Q-77",
    "Shree Steel Traders Pvt Ltd",
    "Date: 05 Nov 2026",
    "Item: Ball bearing 6205, quantity 40, rate Rs 125.00",
    "Total Rs 5,000.00 plus GST 18%",
    "Valid till 30 Nov 2026",
]


def text_pdf(lines: list[str] = QUOTE_LINES) -> bytes:
    """A minimal one-page PDF with real text (no image)."""
    stream = (
        "BT /F1 14 Tf 60 760 Td 20 TL "
        + " ".join(f"({ln.replace('(', '[').replace(')', ']')}) Tj T*" for ln in lines)
        + " ET"
    )
    objs = [
        "<< /Type /Catalog /Pages 2 0 R >>",
        "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>",
        f"<< /Length {len(stream)} >>\nstream\n{stream}\nendstream",
        "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for i, body in enumerate(objs, 1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n{body}\nendobj\n".encode("latin-1")
    xref = len(out)
    out += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode()
    for off in offsets:
        out += f"{off:010d} 00000 n \n".encode()
    out += f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    return bytes(out)


def _font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    for candidate in ("C:/Windows/Fonts/arial.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"):
        if Path(candidate).exists():
            return ImageFont.truetype(candidate, size)
    return ImageFont.load_default(size)


def scanned_pdf(lines: list[str] = QUOTE_LINES) -> bytes:
    """A PDF that is only a picture of the text, like a scan."""
    img = Image.new("RGB", (1240, 1000), "white")
    draw = ImageDraw.Draw(img)
    font = _font(40)
    for i, ln in enumerate(lines):
        draw.text((80, 80 + i * 70), ln, fill="black", font=font)
    buf = io.BytesIO()
    img.save(buf, "PDF", resolution=150)
    return buf.getvalue()


def quote_xlsx() -> bytes:
    wb = openpyxl.Workbook()
    ws = wb.active
    assert ws is not None
    ws.title = "Quote"
    ws.append(["Item", "Qty", "Rate", "Amount"])
    ws.append(["Ball bearing 6205", 40, 125, 5000])
    ws.append(["Grease 1kg", 10, 310.5, 3105])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def quote_docx() -> bytes:
    import docx  # python-docx, installed with Docling

    d = docx.Document()
    d.add_paragraph("Quotation Q-78 from Shree Steel Traders")
    t = d.add_table(rows=2, cols=3)
    for c, v in zip(t.rows[0].cells, ["Item", "Qty", "Rate"], strict=True):
        c.text = v
    for c, v in zip(t.rows[1].cells, ["Ball bearing 6205", "40", "125"], strict=True):
        c.text = v
    buf = io.BytesIO()
    d.save(buf)
    return buf.getvalue()


def vendor_csv() -> bytes:
    return (
        b"name,gstin,city\nShree Steel Traders,24AAAPL0000A1Z5,Vadodara\nWestern Jute Packaging,24AAAPL0000B1Z4,Surat\n"
    )
