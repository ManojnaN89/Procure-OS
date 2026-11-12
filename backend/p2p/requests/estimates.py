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
    db: Session, item_code: str, unit: str | None, qty: Decimal, as_of: date, standard: Decimal | None
) -> Estimate | None:
    """The estimate of `qty` of an item bought in `unit` on a date. None when the item has no price at all."""
    contract = pick_contract_price(contract_options(db, item_code, unit, as_of), qty)
    return estimated_price(
        (contract.price, contract.contract_code) if contract else None,
        last_paid(db, [item_code]).get(item_code),
        standard,
    )
