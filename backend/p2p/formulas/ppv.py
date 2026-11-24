"""The four price measures (13.2, 13.3, D47, DF-29): accounting PPV, contract variance, market gap, and savings.

Unit prices are to four places, amounts to the paisa (half up). The unit price basis is the taxable price after the
line's discount, GST left out (OP-13). Positive PPV and contract variance are unfavourable; positive savings are money
saved. The caller supplies the reference (standard version, contract price, benchmark, estimate), so every row can be
worked out again (rule 8).
"""

from decimal import ROUND_HALF_UP, Decimal

PRICE = Decimal("0.0001")
PAISE = Decimal("0.01")


def unit(qty: Decimal, amount: Decimal) -> Decimal:
    """A unit price in four places, from an amount and a quantity (a missing quantity is no price)."""
    if qty == 0:
        raise ValueError("a quantity of zero has no unit price")
    return (amount / qty).quantize(PRICE, rounding=ROUND_HALF_UP)


def measure(qty: Decimal, actual: Decimal, reference: Decimal) -> Decimal:
    """(actual price - reference price) x quantity, to the paisa, half up."""
    return (qty * (actual - reference)).quantize(PAISE, rounding=ROUND_HALF_UP)


def ppv(qty: Decimal, actual_unit_price: Decimal, standard_unit_price: Decimal) -> Decimal:
    """Accounting purchase price variance: positive when the actual price is above the standard."""
    return measure(qty, actual_unit_price, standard_unit_price)


def true_up(qty: Decimal, invoice_unit_price: Decimal, po_unit_price: Decimal) -> Decimal:
    """`both` recognition: the difference between the invoice price and the price already taken at receipt."""
    return measure(qty, invoice_unit_price, po_unit_price)


def contract_variance(qty: Decimal, price: Decimal, contract_unit_price: Decimal) -> Decimal:
    """The order or invoice price against the contract price in force; positive is above the contract."""
    return measure(qty, price, contract_unit_price)


def saving(qty: Decimal, price: Decimal, baseline_unit_price: Decimal) -> Decimal:
    """What comparing or negotiating saved against the baseline: positive is saved (DF-29)."""
    return measure(qty, baseline_unit_price, price)
