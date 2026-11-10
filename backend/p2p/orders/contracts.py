"""What has been ordered against each contract. The ceiling is before tax; cancelled orders do not count."""

from decimal import Decimal

from sqlalchemy import text
from sqlalchemy.orm import Session

from p2p.formulas.contracts import ContractUse


def usage(db: Session, code: str) -> ContractUse:
    row = db.execute(text("select max_value from contract where code = :c"), {"c": code}).first()
    used = db.execute(
        text(
            "select coalesce(sum(l.taxable), 0) from po_line l join po on po.no = l.po_no "
            "where l.source = 'contract' and l.source_ref = :c and po.status <> 'cancelled' and l.status <> 'cancelled'"
        ),
        {"c": code},
    ).scalar_one()
    return ContractUse(None if row is None or row[0] is None else Decimal(row[0]), Decimal(used))
