"""The purchase order as a document (PDF) that goes to the supplier with the email.

The layout is fixed; the terms text comes from a template the company can change (the setting `po_terms_template`, a
Jinja text with the order's own values). Everything on the page is read from the order as it is stored, so the same order
and version always give the same document.
"""

import logging
from io import BytesIO
from typing import Any
from xml.sax.saxutils import escape

from jinja2.sandbox import SandboxedEnvironment
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
from sqlalchemy import text
from sqlalchemy.orm import Session

from p2p.formulas.money import format_inr, format_qty

log = logging.getLogger(__name__)
DEFAULT_TERMS = (
    "1. Payment terms: {{ payment_terms }}.\n"
    "2. Goods must match the description, quantity and price above. Extra or short supply is not accepted without our "
    "written approval.\n"
    "3. Quote the order number {{ po_no }} on every delivery note and invoice.\n"
    "4. Please acknowledge this order within {{ ack_days }} working days with the date you will deliver."
)
_ENV = SandboxedEnvironment(autoescape=False)


class DocumentError(Exception):
    pass


def _rs(value: Any) -> str:
    return format_inr(value).replace("₹", "Rs ")


def _setting(db: Session, key: str, default: str) -> str:
    value = db.execute(text("select value from org_setting where key = :k"), {"k": key}).scalar()
    return str(value) if value not in (None, "") else default


def terms_text(db: Session, po: dict[str, Any]) -> str:
    """The company's terms template filled with this order's values. A template that does not render falls back to the
    standard text, so a typo in the setting never stops an order from going out."""
    values = {
        "po_no": po["no"],
        "payment_terms": po["payment_terms_code"] or "as agreed",
        "delivery_terms": po["delivery_terms"] or "as agreed",
        "ack_days": _setting(db, "supplier_ack_due_days", "2"),
    }
    for template in (_setting(db, "po_terms_template", DEFAULT_TERMS), DEFAULT_TERMS):
        try:
            return _ENV.from_string(template).render(**values)
        except Exception:  # noqa: BLE001 - any template fault means the standard text
            log.warning("the purchase order terms template did not render; the standard text is used", exc_info=True)
    return ""  # unreachable: the standard text always renders


class Pdf:
    """The layout our documents share (the order, the quotation request): the company heading, a box with the two
    parties, a table of lines and plain paragraphs, built to A4 bytes. Values put on the page are escaped here."""

    def __init__(self, db: Session) -> None:
        self.company = db.execute(text("select legal_name, registered_address, pan from company limit 1")).first()
        styles = getSampleStyleSheet()
        self.body = ParagraphStyle("body", parent=styles["BodyText"], fontSize=9, leading=12)
        self.small = ParagraphStyle("small", parent=self.body, fontSize=8, leading=10)
        self.heading = ParagraphStyle("heading", parent=styles["Title"], fontSize=16, leading=20, alignment=0)
        self.bold = ParagraphStyle("bold", parent=self.body, fontName="Helvetica-Bold")
        self.story: list[Any] = []

    def p(self, value: object, style: ParagraphStyle | None = None) -> Paragraph:
        return Paragraph(escape(str(value)).replace("\n", "<br/>"), style or self.body)

    def add(self, *items: Any) -> None:
        self.story.extend(items)

    def space(self, height_mm: float) -> None:
        self.story.append(Spacer(1, height_mm * mm))

    def title(self, title: str, dated: str) -> None:
        """The company's name and address, then the document's title and date."""
        self.add(self.p(self.company[0] if self.company else "Our company", self.heading))
        self.add(self.p((self.company[1] if self.company else "") or "", self.small))
        self.space(4)
        self.add(self.p(title, ParagraphStyle("h2", parent=self.heading, fontSize=13)), self.p(dated))
        self.space(3)

    def parties(self, left: tuple[str, list[str]], right: tuple[str, list[str]]) -> None:
        """Two boxes side by side, each a bold label over its lines (empty lines are left out)."""

        def box(label: str, rows: list[str]) -> Paragraph:
            return Paragraph(f"<b>{escape(label)}</b><br/>" + "<br/>".join(escape(r) for r in rows if r), self.body)

        table = Table([[box(*left), box(*right)]], colWidths=[90 * mm, 90 * mm])
        table.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("BOX", (0, 0), (-1, -1), 0.4, colors.grey)]))
        self.add(table)
        self.space(4)

    def lines(self, rows: list[list[Any]], widths_mm: list[float], right_cols: tuple[int, int]) -> None:
        """A table with a grey header row; the columns between right_cols are numbers, aligned right."""
        table = Table(rows, colWidths=[w * mm for w in widths_mm], repeatRows=1)
        table.setStyle(
            TableStyle(
                [
                    ("FONTSIZE", (0, 0), (-1, -1), 8),
                    ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#e8e8e8")),
                    ("GRID", (0, 0), (-1, -1), 0.25, colors.grey),
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("ALIGN", (right_cols[0], 1), (right_cols[1], -1), "RIGHT"),
                ]
            )
        )
        self.add(table)
        self.space(3)

    def build(self, title: str) -> bytes:
        buffer = BytesIO()
        SimpleDocTemplate(
            buffer, pagesize=A4, leftMargin=15 * mm, rightMargin=15 * mm, topMargin=15 * mm, bottomMargin=15 * mm,
            title=title, author=str(self.company[0]) if self.company else "",
        ).build(self.story)  # fmt: skip
        return buffer.getvalue()


def order_document(db: Session, po_no: str) -> bytes:
    found = db.execute(text("select * from po where no = :n"), {"n": po_no}).mappings().first()
    if found is None:
        raise DocumentError(f"{po_no} does not exist")
    po = dict(found)
    lines = [
        dict(r)
        for r in db.execute(
            text(
                "select l.*, i.hsn_sac from po_line l left join item i on i.code = l.item_code "
                "where l.po_no = :n and l.status <> 'cancelled' order by l.line_no"
            ),
            {"n": po_no},
        ).mappings()
    ]
    charges = [
        dict(r)
        for r in db.execute(text("select * from po_charge where po_no = :n order by id"), {"n": po_no}).mappings()
    ]
    address = supplier_address(db, po["supplier_code"], po["supplier_gstin"])
    supplier = db.execute(
        text("select coalesce(trade_name, legal_name) from supplier where code = :c"), {"c": po["supplier_code"]}
    ).scalar_one()
    ship = db.execute(
        text("select name, address, city, pincode from location where code = :c"), {"c": po["ship_to_location"]}
    ).first()
    approved_by = [
        r[0]
        for r in db.execute(
            text(
                "select u.name from po_approval_step s join org_user u on u.code = coalesce(s.acted_by, s.approver) "
                "where s.po_no = :n and s.round = :r and s.status = 'approved' order by s.step"
            ),
            {"n": po_no, "r": po["approval_round"]},
        )
    ]

    doc = Pdf(db)
    version = f", version {po['version']}" if int(po["version"]) > 1 else ""
    doc.title(f"PURCHASE ORDER {po['no']}{version}", f"Date: {po['created_at']:%d %b %Y}")
    doc.parties(
        ("Supplier", [str(supplier), f"GSTIN {po['supplier_gstin']}" if po["supplier_gstin"] else "", address]),
        (
            "Deliver to",
            [
                str(ship[0] if ship else po["ship_to_location"]),
                ", ".join(x for x in ship[1:] if x) if ship else "",
                f"Buyer GSTIN {po['buyer_gstin']}" if po["buyer_gstin"] else "",
            ],
        ),
    )
    rows: list[list[Any]] = [["#", "Item", "HSN/SAC", "Qty", "Rate", "GST %", "Amount", "Needed by"]]
    for n, ln in enumerate(lines, start=1):
        rows.append(
            [
                str(n),
                doc.p(ln["description"] + (f" ({ln['item_code']})" if ln["item_code"] else ""), doc.small),
                ln["hsn_sac"] or "",
                f"{format_qty(ln['qty'])} {ln['uom'] or ''}".strip(),
                _rs(ln["unit_price"]),
                format_qty(ln["gst_rate"]),
                _rs(ln["taxable"]),
                f"{ln['need_by']:%d %b %Y}" if ln["need_by"] else "",
            ]
        )
    for c in charges:
        label = "Freight" if c["type"] == "freight" else "Packing and forwarding"
        rows.append(["", doc.p(label, doc.small), "", "", "", format_qty(c["gst_rate"]), _rs(c["amount"]), ""])
    doc.lines(rows, [8, 58, 18, 20, 22, 12, 24, 22], (3, 6))

    tax_rows: list[list[Any]] = [["Total before tax", _rs(po["taxable"])]]
    if po["igst"]:
        tax_rows.append(["IGST", _rs(po["igst"])])
    if po["cgst"] or po["sgst"]:
        tax_rows += [["CGST", _rs(po["cgst"])], ["SGST", _rs(po["sgst"])]]
    tax_rows.append(["Order total", _rs(po["total"])])
    totals = Table(tax_rows, colWidths=[40 * mm, 34 * mm], hAlign="RIGHT")
    totals.setStyle(
        TableStyle(
            [
                ("FONTSIZE", (0, 0), (-1, -1), 9),
                ("ALIGN", (1, 0), (1, -1), "RIGHT"),
                ("FONTNAME", (0, -1), (-1, -1), "Helvetica-Bold"),
            ]
        )
    )
    doc.add(totals)
    doc.space(5)
    if po["delivery_terms"]:
        doc.add(doc.p(f"Delivery terms: {po['delivery_terms']}"))
    doc.add(doc.p("Terms", doc.bold), doc.p(terms_text(db, po), doc.small))
    doc.space(6)
    doc.add(
        doc.p(
            "Approved by: " + ", ".join(approved_by)
            if approved_by
            else f"Approved automatically under rule {po['rule_code']}"
            if po["rule_code"]
            else "",
            doc.small,
        )
    )
    return doc.build(f"Purchase order {po['no']}")


def supplier_address(db: Session, supplier_code: str, gstin: str | None = None) -> str:
    """The supplier's address as one line: the one registered under the GSTIN on the document first."""
    row = db.execute(
        text(
            "select address, city, pincode from supplier_address where supplier_code = :c "
            "order by (gstin = :g) desc nulls last, id limit 1"
        ),
        {"c": supplier_code, "g": gstin},
    ).first()
    return ", ".join(x for x in row if x) if row else ""
