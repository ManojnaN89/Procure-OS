"""When an invoice is due, what tax is held back, and what late payment to a small supplier costs. Plain rules on dates
and Decimal money."""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal

from p2p.formulas.fy import financial_year

TWO = Decimal("0.01")
MSME_CATEGORIES = ("micro", "small")  # medium enterprises are outside the 45-day rule
STATUTORY_DAYS = 45  # the most a buyer may take, agreed in writing, from acceptance
NO_AGREEMENT_DAYS = 15  # without a written agreement
TDS_194Q_THRESHOLD = Decimal("5000000")
TDS_194Q_RATE = Decimal("0.1")
TDS_NO_PAN_RATE = Decimal("5")
NEW_ACT_FROM = date(2026, 4, 1)  # from this date the sections are quoted in the Income-tax Act 2025 form
SECTION_LABEL = {"194Q": "393(1) Sl.8(ii) [194Q]"}


def q2(x: Decimal) -> Decimal:
    return x.quantize(TWO, rounding=ROUND_HALF_UP)


def due_date(
    *,
    baseline: str,
    days: int,
    invoice_date: date,
    accepted_on: date,
    msme_category: str | None,
    msme_agreement: bool,
    agreed_days: int | None,
) -> date:
    """The payment terms' day count from their baseline (the invoice date, or the day the goods were accepted).

    A micro or small supplier must be paid within what was agreed in writing, never more than 45 days from acceptance,
    and within 15 days if nothing was agreed in writing.
    """
    base = invoice_date if baseline == "invoice_date" else accepted_on
    due = base + timedelta(days=days)
    if msme_category in MSME_CATEGORIES:
        if msme_agreement:
            return min(due, accepted_on + timedelta(days=min(STATUTORY_DAYS, int(agreed_days or STATUTORY_DAYS))))
        return accepted_on + timedelta(days=NO_AGREEMENT_DAYS)
    return due


def bank_rate(on: date, rates: Sequence[tuple[date, Decimal]]) -> Decimal:
    """The RBI bank rate in force on a date (the first one on file if the date is earlier than all of them)."""
    ordered = sorted(rates)
    rate = ordered[0][1]
    for effective, value in ordered:
        if effective <= on:
            rate = value
    return rate


def msme_interest(total: Decimal, due: date, paid: date, rates: Sequence[tuple[date, Decimal]]) -> Decimal:
    """Interest on a payment to a small supplier after its due date: three times the bank rate, compounded monthly."""
    if paid <= due:
        return Decimal(0)
    days = (paid - due).days
    r = bank_rate(due, rates) * 3 / 100
    return q2(total * ((1 + r / 12) ** (Decimal(days) / Decimal("30.4")) - 1))


@dataclass(frozen=True)
class Tds:
    section: str
    base: Decimal  # what the tax is worked out on
    rate: Decimal
    amount: Decimal


def tds_on_goods(cumulative_before: Decimal, taxable: Decimal, on: date, *, pan_inoperative: bool) -> Tds:
    """Tax at source on a purchase of goods (section 194Q): 0.1% (5% without a valid PAN) on what a supplier is paid in
    a financial year above 50 lakh. `cumulative_before` is what was bought from the supplier earlier in the year."""
    after = cumulative_before + taxable
    over = max(Decimal(0), after - max(TDS_194Q_THRESHOLD, cumulative_before))
    rate = TDS_NO_PAN_RATE if pan_inoperative else TDS_194Q_RATE
    section = SECTION_LABEL["194Q"] if on >= NEW_ACT_FROM else "194Q"
    return Tds(section, over, rate, q2(over * rate / 100))


def quarter_label(on: date) -> str:
    """'2026-27 Q2': the financial year and its quarter (April to June is the first)."""
    return f"{financial_year(on)} Q{(on.month - 4) % 12 // 3 + 1}"
