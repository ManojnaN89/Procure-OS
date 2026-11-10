"""The Excel form attached to a quotation request: one row per item, with the columns a quote needs left for the supplier.

A supplier who fills it in and sends it back gives the quote reader a clean table; one who prefers a PDF or an email
still can. The sheet carries no formulas and no macros.
"""

import io
from typing import Any

import openpyxl
from openpyxl.styles import Font, PatternFill

from p2p.formulas.money import format_qty

CONTENT_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
HEADERS = [
    "Line",
    "Item",
    "Quantity",
    "Unit",
    "Needed by",
    "Unit price (Rs)",
    "Discount %",
    "GST %",
    "Delivery days",
    "Minimum order",
    "Remarks",
]
FOR_SUPPLIER = HEADERS[5:]  # the columns the supplier fills in


def build(rfq: dict[str, Any], lines: list[dict[str, Any]], company: str) -> bytes:
    wb = openpyxl.Workbook()
    ws = wb.active
    assert ws is not None
    ws.title = "Quotation"
    ws.append([f"Quotation for {rfq['no']} - {company}"])
    ws["A1"].font = Font(bold=True, size=12)
    ws.append(
        [f"Please reply by {rfq['due_date']:%d %b %Y}. Fill in the yellow columns and send this sheet back by email."]
    )
    ws.append([])
    ws.append(HEADERS)
    for cell in ws[ws.max_row]:
        cell.font = Font(bold=True)
    yellow = PatternFill("solid", fgColor="FFF2CC")
    for ln in lines:
        ws.append(
            [
                ln["line_no"],
                ln["description"] + (f" ({ln['item_code']})" if ln["item_code"] else ""),
                format_qty(ln["qty"]),
                ln["uom"] or "",
                f"{ln['need_by']:%d %b %Y}" if ln["need_by"] else "",
                None,
                None,
                None,
                None,
                None,
                None,
            ]
        )
        for cell in ws[ws.max_row][len(HEADERS) - len(FOR_SUPPLIER) :]:
            cell.fill = yellow
    ws.append([])
    for label in ("Freight (Rs)", "Packing and forwarding (Rs)", "Payment terms", "Price valid until"):
        ws.append([label])
        ws.cell(row=ws.max_row, column=2).fill = yellow
    for col, width in zip("ABCDEFGHIJK", (6, 38, 10, 8, 13, 16, 12, 8, 14, 14, 24), strict=True):
        ws.column_dimensions[col].width = width
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
