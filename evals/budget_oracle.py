"""What the budget actuals must add up to under D-10 (block 3), worked out from the documents, never from budget_actual:
a stocked item is spent when it is accepted at receipt (accepted quantity x order price, paise half up, per line); anything
else when its invoice is paid (the line's taxable value); an invoice's freight and packing less a discount taken when it
is paid. Shared by E21, E26 and E30."""

from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import text
from sqlalchemy.orm import Session

PAISE = Decimal("0.01")
_STOCKED = "coalesce((select i.stocked from item i where i.code = pl.item_code), false)"


def at_receipt(db: Session) -> Decimal:
    """Accepted stocked goods at their order price, for receipts the platform posted (history loaded into the tables by
    the replay, `posted_by` import, never went through a receipt and spent nothing)."""
    total = Decimal(0)
    for qty, price in db.execute(
        text(
            f"select g.qty_accepted, pl.unit_price from gr_line g join po_line pl on pl.po_no = g.po_no "  # noqa: S608
            f"and pl.line_no = g.po_line_no join goods_receipt r on r.no = g.gr_no "
            f"where coalesce(g.qty_accepted, 0) > 0 and r.posted_by <> 'import' and {_STOCKED}"
        )
    ):
        total += (Decimal(qty) * Decimal(price)).quantize(PAISE, rounding=ROUND_HALF_UP)
    return total


def at_payment(db: Session, invoice_no: str | None = None) -> Decimal:
    """Paid invoices: lines that are not stocked items at their taxable value, plus charges less a discount taken."""
    one = "and i.no = :n" if invoice_no else ""
    lines = db.execute(
        text(
            "select coalesce(sum(il.taxable), 0) from invoice_line il join invoice i on i.no = il.invoice_no "  # noqa: S608
            "join po_line pl on pl.po_no = il.po_no and pl.line_no = il.po_line_no "
            f"where i.status = 'paid' and i.type = 'invoice' and not {_STOCKED} {one}"
        ),
        {"n": invoice_no},
    ).scalar_one()
    charges = db.execute(
        text(
            "select coalesce(sum((select coalesce(sum(c.amount), 0) from invoice_charge c where c.invoice_no = i.no) "  # noqa: S608
            f"- i.discount_taken), 0) from invoice i where i.status = 'paid' and i.type = 'invoice' {one}"
        ),
        {"n": invoice_no},
    ).scalar_one()
    return Decimal(lines) + Decimal(charges)
