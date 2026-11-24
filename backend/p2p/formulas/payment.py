"""When an invoice is due, what tax is held back, and what late payment to a small supplier costs. Plain rules on dates
and Decimal money."""

import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal

from p2p.formulas.calendar import BusinessCalendar
from p2p.formulas.fy import financial_year

TWO = Decimal("0.01")
MSME_CATEGORIES = ("micro", "small")  # medium enterprises are outside the 45-day rule
# The day limits of the MSMED Act, section 15, are data: the settings `msme_max_days` and `msme_no_agreement_days`
# (invoices/payment.py msme_limits). These are the statute's figures, used when a company has not set them.
STATUTORY_DAYS = 45  # the most a buyer may take, agreed in writing, from acceptance
NO_AGREEMENT_DAYS = 15  # without a written agreement


@dataclass(frozen=True)
class MsmeLimits:
    max_days: int = STATUTORY_DAYS
    no_agreement_days: int = NO_AGREEMENT_DAYS


def msme_deadline(accepted_on: date, agreement: bool, agreed_days: int | None, limits: MsmeLimits) -> date:
    """The last day a micro or small supplier may be paid: the days agreed in writing (never more than the maximum), or
    the no-agreement days, counted in calendar days from the day the goods or service were accepted."""
    if agreement:
        return accepted_on + timedelta(days=min(limits.max_days, int(agreed_days or limits.max_days)))
    return accepted_on + timedelta(days=limits.no_agreement_days)


def days_late(deadline: date, on: date) -> int:
    """Days past the deadline on a date: 0 on the deadline itself, negative while there is still time."""
    return (on - deadline).days


# The tax rules are data: the rate, the threshold and the act come from the reference table `ref_tds_section` (see
# invoices/payment.py). These are the values in force when this was written and are used only when the table has no row
# for the date, so a company with its reference data not loaded still gets the statutory figure.
TDS_194Q_THRESHOLD = Decimal("5000000")
TDS_194Q_RATE = Decimal("0.1")
TDS_NO_PAN_RATE = Decimal("5")
NEW_ACT_FROM = date(2026, 4, 1)  # from this date the sections are quoted in the Income-tax Act 2025 form
SECTION_LABEL = {
    "194Q": "393(1) Sl.8(ii) [194Q]",
    "194C": "393(1) Sl.6(i) [194C]",
    "194J(a)": "393(1) Sl.6(iii) [194J]",
    "194J(b)": "393(1) Sl.6(iii) [194J]",
}
# a section quoted in the 2025 Act form from 1 April 2026, the 1961 form before it
SECTION_1961 = {"194Q": "194Q", "194C": "194C", "194J(a)": "194J(a)", "194J(b)": "194J(b)"}


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
    limits: MsmeLimits | None = None,
) -> date:
    """The payment terms' day count from their baseline (the invoice date, or the day the goods were accepted).

    A micro or small supplier must be paid within what was agreed in writing, never more than 45 days from acceptance,
    and within 15 days if nothing was agreed in writing (the limits as the company has them). Whatever the terms say,
    the earlier of the two dates stands (DF-30).
    """
    base = invoice_date if baseline == "invoice_date" else accepted_on
    due = base + timedelta(days=days)
    if msme_category in MSME_CATEGORIES:
        deadline = msme_deadline(accepted_on, msme_agreement, agreed_days, limits or MsmeLimits())
        return min(due, deadline)
    return due


def pay_by(due: date, calendar: BusinessCalendar) -> date:
    """The last day a run may pay an invoice and still be on time: its due date, or the working day before it (OP-3)."""
    return calendar.on_or_before(due)


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


# ---- tax at source on every section ------------------------------------------------------------


@dataclass(frozen=True)
class TdsSection:
    """One row of the tax table as it applies on the booking date: the figures the decision used."""

    id: int | None
    code: str  # 194Q, 194C, 194J(a), 194J(b)
    label: str  # as the books quote it on that date
    rate_pct: Decimal
    rate_no_pan_pct: Decimal
    threshold_single: Decimal | None
    threshold_annual: Decimal | None
    base_rule: str  # 'entire amount' or 'excess over threshold'
    payee_type: str = "ANY"


def section_label(code: str, on: date) -> str:
    """The section as the books quote it: the Income-tax Act 2025 form from 1 April 2026, else the 1961 form."""
    if on >= NEW_ACT_FROM and code in SECTION_LABEL:
        return SECTION_LABEL[code]
    return SECTION_1961.get(code, code)


def default_section(code: str, on: date) -> TdsSection:
    """The statutory figures for a section, used only when the tax table has no row for the date."""
    label = section_label(code, on)
    if code == "194Q":
        return TdsSection(None, code, label, TDS_194Q_RATE, TDS_NO_PAN_RATE, None, TDS_194Q_THRESHOLD,
                          "excess over threshold")  # fmt: skip
    if code == "194C":
        return TdsSection(None, code, label, Decimal("2"), Decimal("20"), Decimal("30000"), Decimal("100000"),
                          "entire amount", "Others")  # fmt: skip
    if code == "194J(b)":
        return TdsSection(None, code, label, Decimal("10"), Decimal("20"), None, Decimal("50000"), "entire amount")
    return TdsSection(None, code, label, Decimal("2"), Decimal("20"), None, Decimal("50000"), "entire amount")


def payee_type(pan: str | None) -> str:
    """194C's two rates: an individual or Hindu undivided family (the PAN's fourth letter P or H), or anyone else."""
    return "individual_huf" if pan and len(pan) >= 4 and pan[3].upper() in ("P", "H") else "others"


def tds_for(
    rule: TdsSection,
    base: Decimal,
    cumulative_before: Decimal,
    single: Decimal,
    *,
    pan_inoperative: bool = False,
    lower_rate: Decimal | None = None,
    lower_limit: Decimal | None = None,
) -> Tds:
    """The tax to hold back under one section (2.5, OP-9).

    `base` is this invoice's taxable value under the section; `cumulative_before` is what the supplier was booked for
    under it this year; `single` is the order's value (the single-sum threshold). 194Q charges the part of the year
    above the annual threshold. Every other section charges the whole base once the year is above its annual
    threshold or the order is above its single-sum threshold, and nothing below both: exactly at a threshold is not
    above it, and earlier sums are never made up (OP-9). A lower-rate certificate applies while it is valid and the
    year stays inside its limit; a PAN the department says is inoperative takes the no-PAN rate.
    """
    after = cumulative_before + base
    if rule.base_rule == "entire amount":
        over_annual = rule.threshold_annual is None or after > rule.threshold_annual
        over_single = rule.threshold_single is not None and single > rule.threshold_single
        charged = base if (over_annual or over_single) else Decimal(0)
    else:  # 194Q: only the part of the year's purchases above the threshold
        threshold = rule.threshold_annual or Decimal(0)
        charged = max(Decimal(0), after - max(threshold, cumulative_before))
    rate = rule.rate_no_pan_pct if pan_inoperative else rule.rate_pct
    if not pan_inoperative and lower_rate is not None and lower_limit is not None and after <= lower_limit:
        rate = lower_rate
    return Tds(rule.label, charged, rate, q2(charged * rate / 100))


# ---- a bank reference --------------------------------------------------------------------------


_UTR = re.compile(r"[A-Za-z0-9]{6,30}")


def utr_ok(value: str | None) -> bool:
    """A bank reference is letters and digits, 6 to 30 of them (a person or a bank line gives it, never the system)."""
    return bool(value) and bool(_UTR.fullmatch(str(value).strip()))


# ---- notes netted in a payment -----------------------------------------------------------------


def offset(payable: Decimal, notes: Sequence[tuple[str, Decimal]]) -> tuple[list[tuple[str, Decimal]], Decimal]:
    """How much of each note is used against what is still to pay, in the order given: never more than is due, never
    a negative payment. Returns what each note used and what is left to pay (the rest of a note waits for a later run).
    """
    left = max(payable, Decimal(0))
    used: list[tuple[str, Decimal]] = []
    for ref, amount in notes:
        take = min(max(amount, Decimal(0)), left)
        if take <= 0:
            continue
        used.append((ref, take))
        left -= take
    return used, left


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
