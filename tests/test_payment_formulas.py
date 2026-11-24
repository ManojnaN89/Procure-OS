"""Payment formulas: due dates and the small-supplier rule, the pay-by working day, the tax at source of every section,
bank references and the notes netted in a payment. Given, when, then in each test."""

from datetime import date
from decimal import Decimal

import pytest

from p2p.formulas import payment as rules
from p2p.formulas.calendar import BusinessCalendar
from p2p.invoices import tds as tds_service

D = Decimal
HOLIDAYS = BusinessCalendar.of({date(2026, 12, 25)})


def _due(**kw: object) -> date:
    base = {
        "baseline": "invoice_date", "days": 30, "invoice_date": date(2026, 11, 6), "accepted_on": date(2026, 11, 10),
        "msme_category": None, "msme_agreement": False, "agreed_days": None,
    }  # fmt: skip
    return rules.due_date(**{**base, **kw})  # type: ignore[arg-type]


def test_a_due_date_counts_the_terms_from_the_invoice_date_or_from_acceptance() -> None:
    # given an invoice dated 6 November on Net 30, when it is booked, then it is due 30 calendar days on from its date
    assert _due() == date(2026, 12, 6)
    # and when the terms count from acceptance on 10 November, then the due date follows that baseline
    assert _due(baseline="acceptance_date") == date(2026, 12, 10)
    assert _due(baseline="receipt_date", days=0) == date(2026, 11, 10)


def test_a_small_supplier_without_a_written_agreement_takes_the_earlier_of_the_terms_and_fifteen_days() -> None:
    # given a micro supplier with no written agreement, accepted on 10 November,
    # when the terms say Net 30 (6 December), then the 15-day deadline (25 November) stands (DF-30)
    assert _due(msme_category="micro", msme_agreement=False) == date(2026, 11, 25)
    # and when the terms are immediate on a 3 November invoice accepted 2 November, then the terms date (3 November)
    # is earlier than the deadline and stands (Y2b)
    assert _due(
        baseline="invoice_date", days=0, invoice_date=date(2026, 11, 3), accepted_on=date(2026, 11, 2),
        msme_category="micro", msme_agreement=False,
    ) == date(2026, 11, 3)  # fmt: skip


@pytest.mark.parametrize(
    ("agreed", "expected"),
    [(30, date(2026, 12, 6)), (20, date(2026, 11, 30)), (45, date(2026, 12, 6)), (90, date(2026, 12, 6))],
)
def test_a_small_supplier_with_a_written_agreement_is_paid_by_the_earlier_of_the_terms_and_the_agreed_days(
    agreed: int, expected: date
) -> None:
    # given an agreement of this many days from acceptance on 10 November, when the terms say 6 December,
    # then the earlier of the two stands, never later than 45 days
    assert _due(msme_category="small", msme_agreement=True, agreed_days=agreed) == expected


def test_a_due_date_on_a_holiday_or_a_sunday_is_paid_by_the_working_day_before() -> None:
    # given Christmas Day 2026 (a Friday, a central holiday), when the pay-by day is worked out,
    # then it is Thursday 24 December; Saturday 26 December also pays by Thursday 24 December (Y1, Y1b)
    assert rules.pay_by(date(2026, 12, 25), HOLIDAYS) == date(2026, 12, 24)
    assert rules.pay_by(date(2026, 12, 26), HOLIDAYS) == date(2026, 12, 24)
    assert rules.pay_by(date(2026, 12, 23), HOLIDAYS) == date(2026, 12, 23)


def test_the_pay_by_day_across_the_christmas_holiday_never_goes_forward() -> None:
    # given Monday 28 December, when asked for the working day on or before it, then it is itself;
    # and from Sunday 27 December, then Friday 25 December is a holiday, so Thursday 24 December (OP-4)
    assert HOLIDAYS.on_or_before(date(2026, 12, 28)) == date(2026, 12, 28)
    assert HOLIDAYS.on_or_before(date(2026, 12, 27)) == date(2026, 12, 24)


def _rule(code: str, rate: str, *, annual: str | None, single: str | None, base_rule: str) -> rules.TdsSection:
    return rules.TdsSection(1, code, code, D(rate), D("20"), None if single is None else D(single),
                            None if annual is None else D(annual), base_rule)  # fmt: skip


def test_194q_taxes_only_the_part_of_the_year_above_fifty_lakh() -> None:
    # given a purchase of 0.01 that lands exactly on 50,00,000 for the year, when the tax is worked out,
    # then there is no base and nothing to hold; one paisa more gives a base of 0.01 and 0.00 after rounding
    rule = _rule("194Q", "0.1", annual="5000000", single=None, base_rule="excess over threshold")
    assert rules.tds_for(rule, D("0.01"), D("4999999.99"), D(0)).base == D("0.00")
    got = rules.tds_for(rule, D("0.02"), D("4999999.99"), D(0))
    assert (got.base, got.amount) == (D("0.01"), D("0.00"))
    # and a purchase wholly above the threshold is taxed in full on its base
    assert rules.tds_for(rule, D("100000"), D("5000000"), D(0)).amount == D("100.00")


def test_194c_charges_the_whole_sum_only_above_the_single_or_annual_threshold() -> None:
    # given a work contract of exactly 30,000 and a year below 1,00,000, when judged, then nothing is held;
    # one paisa above the single sum charges the whole base (OP-9, no catch-up)
    rule = _rule("194C", "2", annual="100000", single="30000", base_rule="entire amount")
    assert rules.tds_for(rule, D("30000"), D(0), D("30000")).amount == D("0")
    assert rules.tds_for(rule, D("30000.01"), D(0), D("30000.01")).amount == D("600.00")
    # and the rate follows the PAN's fourth letter: P or H is an individual or HUF at 1%, anyone else 2%
    assert rules.payee_type("ABCPD0000P") == "individual_huf"
    assert rules.payee_type("ABCHD0000H") == "individual_huf"
    assert rules.payee_type("ABCCC0000C") == "others"


def test_194j_at_exactly_fifty_thousand_holds_nothing_and_one_paisa_above_charges_the_whole_base() -> None:
    # given a year at exactly 50,000, when a 12,000 service invoice arrives, then 62,000 is above the threshold and
    # 2% of 12,000 is 240.00: the earlier sums are not made up (Y4)
    rule = _rule("194J(a)", "2", annual="50000", single=None, base_rule="entire amount")
    assert rules.tds_for(rule, D("12000"), D("50000"), D(0)).amount == D("240.00")
    assert rules.tds_for(rule, D("10000"), D("40000"), D(0)).amount == D("0")
    assert rules.tds_for(rule, D("0.01"), D("49999.99"), D(0)).amount == D("0")


def test_a_lower_rate_certificate_holds_on_its_last_day_and_only_inside_its_limit() -> None:
    # given a lower rate of 0.5% valid to 30 November and a limit of 1,00,000, when the year stays within the limit,
    # then the lower rate applies; the day after it lapses, the section's own rate applies
    rule = _rule("194J(a)", "2", annual="50000", single=None, base_rule="entire amount")
    assert rules.tds_for(rule, D("10000"), D("60000"), D(0), lower_rate=D("0.5"), lower_limit=D("100000")).rate == D(
        "0.5"
    )
    assert rules.tds_for(rule, D("10000"), D("60000"), D(0), lower_rate=D("0.5"), lower_limit=D("69999.99")).rate == D(
        "2"
    )


def test_a_pan_the_department_calls_inoperative_takes_the_no_pan_rate() -> None:
    rule = rules.TdsSection(1, "194Q", "194Q", D("0.1"), D("5"), None, D("5000000"), "excess over threshold")
    # given 1,00,000 above the threshold and an inoperative PAN, when the tax is worked out, then 5% is held
    assert rules.tds_for(rule, D("100000"), D("5000000"), D(0), pan_inoperative=True).amount == D("5000.00")


@pytest.mark.parametrize(
    ("value", "ok"),
    [
        ("HDFC26122400000101", True),
        ("ABC123", True),
        ("ABC12", False),
        ("AC-1234", False),
        ("A" * 31, False),
        ("", False),
    ],
)
def test_a_bank_reference_is_letters_and_digits_six_to_thirty_long(value: str, ok: bool) -> None:
    # given a reference a person or a bank line gave, when checked, then only letters and digits of 6 to 30 pass
    assert rules.utr_ok(value) is ok


def test_notes_are_used_in_order_and_never_make_a_payment_negative() -> None:
    # given 5,310.00 to pay and two notes (1,180.00 then 6,000.00), when they are netted,
    # then the first is used whole, the second takes only the 4,130.00 left, and nothing is due
    used, left = rules.offset(D("5310.00"), [("CN-1", D("1180.00")), ("CN-2", D("6000.00"))])
    assert used == [("CN-1", D("1180.00")), ("CN-2", D("4130.00"))]
    assert left == D("0.00")
    # and a note larger than the payment leaves the rest for the next run; a zero payment uses nothing
    used, left = rules.offset(D("0"), [("CN-2", D("1870.00"))])
    assert used == [] and left == D("0")


def test_an_advance_taken_off_lines_pro_rata_leaves_the_remainder_paisa_on_the_last_line() -> None:
    # given three lines of 100.00, 100.00 and 100.01 and an advance taxable of 100.00, when it is spread,
    # then each share is rounded half up and the last line takes what rounding left (33.33 + 33.33 + 33.34)
    lines = [
        tds_service.LineSection(1, D("100.00"), "194Q", True),
        tds_service.LineSection(2, D("100.00"), "194Q", True),
        tds_service.LineSection(3, D("100.01"), "194Q", True),
    ]
    shares = tds_service.advance_taxable_split(lines, D("100.00"))
    assert shares == [D("33.33"), D("33.33"), D("33.34")]
