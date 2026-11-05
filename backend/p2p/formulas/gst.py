"""FR25: GST split into CGST, SGST and IGST."""

from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum

from p2p.formulas.money import round_money, to_decimal


class Registration(StrEnum):
    REGULAR = "regular"
    COMPOSITION = "composition"
    UNREGISTERED = "unregistered"


@dataclass(frozen=True)
class GstSplit:
    cgst: Decimal
    sgst: Decimal
    igst: Decimal
    charged: Decimal  # tax added to what the supplier invoices us
    reverse_charge: Decimal  # tax computed but paid by us, not added to the supplier's invoice

    @property
    def total_tax(self) -> Decimal:
        return self.cgst + self.sgst + self.igst


def gst_split(
    taxable: Decimal | int | str,
    rate_pct: Decimal | int | str,
    supplier_state: str,
    place_of_supply_state: str,
    registration: Registration = Registration.REGULAR,
    *,
    reverse_charge: bool = False,
) -> GstSplit:
    """Same state: CGST = SGST = rate / 2. Different state: IGST = rate.

    Composition and unregistered suppliers cannot charge GST, so every tax line is zero. With reverse charge the
    tax is computed but payable by us, so nothing is added to the supplier's invoice.
    """
    zero = Decimal("0.00")
    if registration is not Registration.REGULAR:
        return GstSplit(zero, zero, zero, zero, zero)
    base, rate = to_decimal(taxable), to_decimal(rate_pct)
    if supplier_state == place_of_supply_state:
        half = round_money(base * rate / 200)
        cgst, sgst, igst = half, half, zero
    else:
        cgst, sgst, igst = zero, zero, round_money(base * rate / 100)
    total = cgst + sgst + igst
    return GstSplit(cgst, sgst, igst, zero if reverse_charge else total, total if reverse_charge else zero)


def tax_on_invoice_not_allowed(registration: Registration, tax_amount: Decimal | int | str) -> bool:
    """A composition or unregistered supplier showing any GST on an invoice is a flag."""
    return registration is not Registration.REGULAR and to_decimal(tax_amount) != 0
