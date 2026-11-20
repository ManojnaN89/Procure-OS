"""The estimate of a request line, read from the company's records: the contract price in force for the quantity, the
last price paid, the item's standard price. The choice between them is `formulas/estimate.py`; every way a request is
raised (the form, the plain-words intake, replenishment, the store check) asks here, so all are estimated alike."""

from datetime import date
from decimal import Decimal

from sqlalchemy.orm import Session

from p2p.formulas.estimate import Estimate, estimated_price
from p2p.formulas.sourcing import pick_contract_price
from p2p.orders.quotes import last_paid
from p2p.sourcing.service import contract_options


def for_item(
    db: Session,
    item_code: str,
    unit: str | None,
    qty: Decimal,
    as_of: date,
    standard: Decimal | None,
    location: str | None = None,
) -> Estimate | None:
    """The estimate of `qty` of an item bought in `unit` on a date for a delivery location (a regional contract price
    only inside its scope). None when the item has no price at all. A market benchmark is never an estimate."""
    contract = pick_contract_price(contract_options(db, item_code, unit, as_of, location), qty)
    return estimated_price(
        (contract.price, contract.contract_code) if contract else None,
        last_paid(db, [item_code]).get(item_code),
        standard,
    )
