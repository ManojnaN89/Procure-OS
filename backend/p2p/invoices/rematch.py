"""Matching a quantity or service hold again when the accepted quantity rises (block 11, 3.5).

Block 9 promises no call of its own: this block adds every call to the paths that raise an order line's accepted
quantity (a receipt, an inspection, a service confirmation, an accepted excess, an ERP receipt import). The waiting
invoices are taken oldest received first, then by number (OP-6), each in its own transaction. Only a quantity or a
service hold waits for goods: a price block is never cleared by an arrival.

# NEEDS block 9: `receiving/excess.py` (the `receipt.excess_accept` executor, after its commit) and
# `receiving/erp_receipts.import_receipts` (each receipt) must call `after_accepted` here (H-1).
"""

from collections.abc import Iterable

from sqlalchemy import text
from sqlalchemy.orm import Session

from p2p.formulas import matching as rules
from p2p.invoices import service as invoices
from p2p.platform.sod import Actor

REMATCH = Actor("service", "rematch")


def waiting(db: Session, po_no: str, line_nos: Iterable[int]) -> list[str]:
    """The blocked invoices with an open quantity or service reason on one of these order lines, oldest first."""
    lines = sorted({int(n) for n in line_nos})
    if not lines:
        return []
    return [
        str(r[0])
        for r in db.execute(
            text(
                "select distinct i.no, i.received_on from invoice i join invoice_line l on l.invoice_no = i.no "
                "join match_result m on m.invoice_no = i.no and m.resolved_at is null "
                "where i.status = 'blocked' and i.type = 'invoice' and l.po_no = :p and l.po_line_no = any(:ls) "
                "and exists (select 1 from unnest(coalesce(m.reasons, array[m.reason_codes])) r "
                "where r in (:qty, :service)) order by i.received_on, i.no"
            ),
            {"p": po_no, "ls": lines, "qty": rules.QTY, "service": rules.SERVICE},
        )
    ]


def after_accepted(
    db: Session, po_no: str, line_nos: Iterable[int], actor: Actor = REMATCH, *, grn: str | None = None
) -> list[str]:
    """Match again every invoice whose quantity or service hold one of these lines had; return the numbers approved."""
    approved: list[str] = []
    reason = f"goods accepted on {grn}" if grn else "accepted quantity rose"
    for no in waiting(db, po_no, line_nos):
        done = invoices.match_invoice(db, no, actor, reason=reason)
        if done.status == "approved":
            approved.append(no)
    return approved
