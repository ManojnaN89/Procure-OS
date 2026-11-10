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
# The tax rules are data: the rate, the threshold and the act come from the reference table `ref_tds_section` (see
# invoices/payment.py). These are the values in force when this was written and are used only when the table has no row
# for the date, so a company with its reference data not loaded still gets the statutory figure.
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


@dataclass(frozen=True)
class TdsRule:
    """One row of the tax table as it applies on a date: what the decision was worked out against."""

    section: str  # as it is quoted for the date (the 1961 form, or the 2025 Act's form from April 2026)
    rate: Decimal
    rate_no_pan: Decimal
    threshold: Decimal


def default_tds_rule(on: date) -> TdsRule:
    section = SECTION_LABEL["194Q"] if on >= NEW_ACT_FROM else "194Q"
    return TdsRule(section, TDS_194Q_RATE, TDS_NO_PAN_RATE, TDS_194Q_THRESHOLD)


def tds_on_goods(
    cumulative_before: Decimal, taxable: Decimal, on: date, *, pan_inoperative: bool, rule: TdsRule | None = None
) -> Tds:
    """Tax at source on a purchase of goods (section 194Q): 0.1% (5% without a valid PAN) on what a supplier is paid in
    a financial year above 50 lakh. `cumulative_before` is what was bought from the supplier earlier in the year.
    The rate, threshold and section come from `rule` (the tax table's row for the date)."""
    rule = rule or default_tds_rule(on)
    after = cumulative_before + taxable
    over = max(Decimal(0), after - max(rule.threshold, cumulative_before))
    rate = rule.rate_no_pan if pan_inoperative else rule.rate
    return Tds(rule.section, over, rate, q2(over * rate / 100))


def quarter_label(on: date) -> str:
    """'2026-27 Q2': the financial year and its quarter (April to June is the first)."""
    return f"{financial_year(on)} Q{(on.month - 4) % 12 // 3 + 1}"


# ---- advances -----------------------------------------------------------------------------------


def advance_amounts(order_total: Decimal, order_taxable: Decimal, advance_pct: Decimal) -> tuple[Decimal, Decimal]:
    """What is paid in advance on an order: the percentage of its total, and of its taxable value (the tax at source
    on the advance is worked out on the taxable part)."""
    return q2(order_total * advance_pct / 100), q2(order_taxable * advance_pct / 100)


def advance_use(
    left_gross: Decimal, left_taxable: Decimal, invoice_total: Decimal, invoice_taxable: Decimal
) -> tuple[Decimal, Decimal]:
    """How much of what is left of an advance an invoice takes: as much as it can, in total and in taxable value."""
    return min(left_gross, invoice_total), min(left_taxable, invoice_taxable)


# ---- early-payment discount ---------------------------------------------------------------------


def discount_window_end(baseline_date: date, discount_days: int) -> date:
    """The last day on which paying earns the discount (the day itself still does)."""
    return baseline_date + timedelta(days=discount_days)


def early_discount(
    payable_taxable: Decimal, discount_pct: Decimal, discount_days: int, baseline_date: date, paid_on: date
) -> Decimal:
    """The cash discount for paying within the window, on the taxable value still to be paid.

    The invoice's tax stays as invoiced and so does the tax at source: the discount only reduces the cash paid.
    """
    if discount_pct <= 0 or discount_days <= 0 or paid_on > discount_window_end(baseline_date, discount_days):
        return Decimal(0)
    return q2(payable_taxable * discount_pct / 100)
