"""The quotation request as a printable document (PDF), one per supplier asked: the RFQ number, the lines with their
need-by dates, where to deliver, the terms and the date to quote by. Laid out like the purchase order (orders/document.py)
and read from the RFQ as stored, so the same RFQ always gives the same page."""

from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from p2p.formulas.money import format_qty
from p2p.orders.document import Pdf, supplier_address

INSTRUCTIONS = (
    "1. Quote for each line: the unit price in rupees, any discount, the GST rate, the delivery time in days, and the "
    "most you can supply if it is less than the quantity asked.\n"
    "2. State freight, packing and any other charges separately, how long your prices are valid, and your payment terms.\n"
    "3. Quote the reference {rfq_no} on your reply and send it by {due} to the email address this request came from.\n"
    "4. A quotation is no order: we select suppliers on total cost and terms, and an order follows only in writing."
)


class RfqDocumentError(Exception):
    pass


def rfq_document(db: Session, rfq_no: str, supplier_code: str) -> bytes:
    rfq = db.execute(text("select * from rfq where no = :n"), {"n": rfq_no}).mappings().first()
    if rfq is None:
        raise RfqDocumentError(f"{rfq_no} does not exist")
    supplier = db.execute(
        text(
            "select coalesce(s.trade_name, s.legal_name) as name, s.gstin, t.name as terms from rfq_supplier r "
            "join supplier s on s.code = r.supplier_code left join payment_terms t on t.code = s.payment_terms_code "
            "where r.rfq_no = :n and r.supplier_code = :s"
        ),
        {"n": rfq_no, "s": supplier_code},
    ).first()
    if supplier is None:
        raise RfqDocumentError(f"{supplier_code} was not asked to quote on {rfq_no}")
    ship = db.execute(
        text(
            "select l.name, l.address, l.city, l.pincode, l.gstin from request r "
            "join location l on l.code = r.ship_to_location where r.no = :n"
        ),
        {"n": rfq["request_no"]},
    ).first()
    lines = [
        dict(r)
        for r in db.execute(
            text(
                "select l.*, i.hsn_sac from rfq_line l left join item i on i.code = l.item_code "
                "where l.rfq_no = :n order by l.line_no"
            ),
            {"n": rfq_no},
        ).mappings()
    ]
    due = f"{rfq['due_date']:%d %b %Y}"

    doc = Pdf(db)
    doc.title(f"REQUEST FOR QUOTATION {rfq_no}", f"Date: {rfq['created_at']:%d %b %Y}    Quote by: {due}")
    doc.parties(
        ("To", [str(supplier[0]), f"GSTIN {supplier[1]}" if supplier[1] else "", supplier_address(db, supplier_code)]),
        (
            "Deliver to",
            [
                str(ship[0]) if ship else "as agreed",
                ", ".join(x for x in ship[1:4] if x) if ship else "",
                f"Buyer GSTIN {ship[4]}" if ship and ship[4] else "",
            ],
        ),
    )
    rows: list[list[Any]] = [["#", "Item", "HSN/SAC", "Qty", "Needed by", "Unit price (Rs)", "GST %", "Days"]]
    for ln in lines:
        rows.append(
            [
                str(ln["line_no"]),
                doc.p(ln["description"] + (f" ({ln['item_code']})" if ln["item_code"] else ""), doc.small),
                ln["hsn_sac"] or "",
                f"{format_qty(ln['qty'])} {ln['uom'] or ''}".strip(),
                f"{ln['need_by']:%d %b %Y}" if ln["need_by"] else "",
                "",
                "",
                "",
            ]
        )
    doc.lines(rows, [8, 62, 18, 20, 22, 22, 12, 16], (3, 3))
    doc.add(
        doc.p(f"Delivery terms: {rfq['delivery_terms'] or 'delivered to the address above'}"),
        doc.p(f"Payment terms: {rfq['payment_terms_proposed'] or supplier[2] or 'as agreed'}"),
    )
    doc.space(3)
    doc.add(doc.p("Instructions", doc.bold), doc.p(INSTRUCTIONS.format(rfq_no=rfq_no, due=due), doc.small))
    return doc.build(f"Request for quotation {rfq_no}")
