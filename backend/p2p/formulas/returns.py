"""Rules for returning goods to a supplier and the recovery claim a counted return may raise (4.3, D-21).

Plain arithmetic on quantities and prices that come from the return, the order line and its invoices. A model never
works any of it out (rule 2).
"""

from decimal import ROUND_HALF_UP, Decimal

QTY = Decimal("0.0001")


def claim_qty(returned: Decimal, invoiced: Decimal, received_after: Decimal) -> Decimal:
    """What may be claimed back: the returned quantity, but never more than was invoiced beyond what remains received.

    `returned` is the counted quantity that went back with no replacement; `invoiced` is the order line's invoiced
    quantity; `received_after` is what the line still has received once the return is counted. Nothing when the
    supplier has not invoiced past what remains (nothing is at stake), to four places.
    """
    surplus = invoiced - received_after
    if surplus <= 0 or returned <= 0:
        return Decimal("0.0000")
    return min(returned, surplus).quantize(QTY, rounding=ROUND_HALF_UP)
