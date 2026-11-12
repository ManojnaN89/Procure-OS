"""Which order lines are services: confirmed by the requester in a service entry, never received by the store.

A line is a service when its item is of kind service, or (an item without a kind, or a free-text line) its category is.
"""

from sqlalchemy import text
from sqlalchemy.orm import Session

_SERVICE_LINES = """
    select l.line_no from po_line l
    left join item i on i.code = l.item_code
    left join request_line rl on rl.request_no = l.request_no and rl.line_no = l.request_line_no
    left join item_category c on c.code = coalesce(i.category_code, nullif(rl.category_code, ''))
    where l.po_no = :p and coalesce(nullif(i.kind, ''), c.kind) = 'service'
"""


def service_lines(db: Session, po_no: str) -> set[int]:
    """The line numbers of the order that are services."""
    return {int(r[0]) for r in db.execute(text(_SERVICE_LINES), {"p": po_no})}
