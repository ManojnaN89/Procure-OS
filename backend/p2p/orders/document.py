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
    company = db.execute(text("select legal_name, registered_address, pan from company limit 1")).first()
    supplier = db.execute(
        text("select coalesce(trade_name, legal_name) as name, gstin, pan from supplier where code = :c"),
        {"c": po["supplier_code"]},
    ).one()
    address = db.execute(
        text(
            "select address, city, pincode from supplier_address where supplier_code = :c "
            "order by (gstin = :g) desc nulls last, id limit 1"
        ),
        {"c": po["supplier_code"], "g": po["supplier_gstin"]},
    ).first()
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

    styles = getSampleStyleSheet()
    body = ParagraphStyle("body", parent=styles["BodyText"], fontSize=9, leading=12)
    small = ParagraphStyle("small", parent=body, fontSize=8, leading=10)
    heading = ParagraphStyle("heading", parent=styles["Title"], fontSize=16, leading=20, alignment=0)

    def p(value: object, style: ParagraphStyle = body) -> Paragraph:
        return Paragraph(escape(str(value)).replace("\n", "<br/>"), style)

    def rich(markup: str, style: ParagraphStyle = body) -> Paragraph:
        """Markup composed here, with every value in it already escaped."""
        return Paragraph(markup, style)

    version = f", version {po['version']}" if int(po["version"]) > 1 else ""
    story: list[Any] = [
        p(company[0] if company else "Our company", heading),
        p((company[1] if company else "") or "", small),
        Spacer(1, 4 * mm),
        p(f"PURCHASE ORDER {po['no']}{version}", ParagraphStyle("h2", parent=heading, fontSize=13)),
        p(f"Date: {po['created_at']:%d %b %Y}", body),
        Spacer(1, 3 * mm),
    ]
    parties = Table(
        [
            [
                rich(
                    "<b>Supplier</b><br/>"
                    + escape(str(supplier[0]))
                    + (f"<br/>GSTIN {escape(str(po['supplier_gstin']))}" if po["supplier_gstin"] else "")
                    + (f"<br/>{escape(', '.join(x for x in address if x))}" if address else "")
                ),
                rich(
                    "<b>Deliver to</b><br/>"
                    + escape(str(ship[0] if ship else po["ship_to_location"]))
                    + (f"<br/>{escape(', '.join(x for x in ship[1:] if x))}" if ship else "")
                    + (f"<br/>Buyer GSTIN {escape(str(po['buyer_gstin']))}" if po["buyer_gstin"] else "")
                ),
            ]
        ],
        colWidths=[90 * mm, 90 * mm],
    )
    parties.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("BOX", (0, 0), (-1, -1), 0.4, colors.grey)]))
    story += [parties, Spacer(1, 4 * mm)]

    rows: list[list[Any]] = [["#", "Item", "HSN/SAC", "Qty", "Rate", "GST %", "Amount", "Needed by"]]
    for n, ln in enumerate(lines, start=1):
        rows.append(
            [
                str(n),
                p(ln["description"] + (f" ({ln['item_code']})" if ln["item_code"] else ""), small),
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
        rows.append(["", p(label, small), "", "", "", format_qty(c["gst_rate"]), _rs(c["amount"]), ""])
    table = Table(rows, colWidths=[8 * mm, 58 * mm, 18 * mm, 20 * mm, 22 * mm, 12 * mm, 24 * mm, 22 * mm], repeatRows=1)
    table.setStyle(
        TableStyle(
            [
                ("FONTSIZE", (0, 0), (-1, -1), 8),
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#e8e8e8")),
                ("GRID", (0, 0), (-1, -1), 0.25, colors.grey),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("ALIGN", (3, 1), (6, -1), "RIGHT"),
            ]
        )
    )
    story += [table, Spacer(1, 3 * mm)]

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
    story += [totals, Spacer(1, 5 * mm)]
    if po["delivery_terms"]:
        story.append(p(f"Delivery terms: {po['delivery_terms']}"))
    story += [p("Terms", ParagraphStyle("t", parent=body, fontName="Helvetica-Bold")), p(terms_text(db, po), small)]
    story += [
        Spacer(1, 6 * mm),
        p(
            "Approved by: " + ", ".join(approved_by)
            if approved_by
            else f"Approved automatically under rule {po['rule_code']}"
            if po["rule_code"]
            else "",
            small,
        ),
    ]
    buffer = BytesIO()
    SimpleDocTemplate(
        buffer, pagesize=A4, leftMargin=15 * mm, rightMargin=15 * mm, topMargin=15 * mm, bottomMargin=15 * mm,
        title=f"Purchase order {po['no']}", author=str(company[0]) if company else "",
    ).build(story)  # fmt: skip
    return buffer.getvalue()
