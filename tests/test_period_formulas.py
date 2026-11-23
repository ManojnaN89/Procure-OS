"""The arithmetic of period RFQs and their awards (formulas/period.py): months, the period's limits, the suggested
quantity and the award's value with GST. Pure: no database."""

from datetime import date
from decimal import Decimal

import pytest

from p2p.formulas import period

D = Decimal


@pytest.mark.parametrize(
    ("start", "end", "months"),
    [
        (date(2027, 1, 1), date(2027, 3, 31), 3),
        (date(2027, 1, 15), date(2027, 2, 14), 2),  # a part month counts as one
        (date(2027, 1, 1), date(2027, 1, 1), 1),  # one day
        (date(2027, 4, 1), date(2028, 3, 31), 12),  # across the financial year's end
        (date(2026, 12, 31), date(2027, 1, 1), 2),
    ],
)
def test_a_period_counts_every_calendar_month_it_touches(start: date, end: date, months: int) -> None:
    assert period.months_in(start, end) == months


TODAY = date(2026, 11, 23)


@pytest.mark.parametrize(
    ("start", "end", "problems"),
    [
        (date(2026, 11, 23), date(2027, 1, 31), ["the period must start after today"]),  # starts today
        (date(2026, 11, 22), date(2027, 1, 31), ["the period must start after today"]),
        (date(2026, 11, 24), date(2027, 1, 31), []),  # tomorrow is fine
        (date(2027, 4, 1), date(2028, 3, 31), []),  # exactly 12 months
        (date(2027, 4, 1), date(2028, 4, 1), ["a period is at most 12 months"]),  # 12 months and a day
        (date(2027, 1, 31), date(2028, 1, 30), []),  # 12 months from the 31st ends on the 30th
        (date(2027, 1, 31), date(2028, 1, 31), ["a period is at most 12 months"]),
        (date(2027, 3, 1), date(2027, 2, 28), ["the period ends before it starts"]),
        (date(2027, 1, 1), date(2027, 1, 1), []),  # one day
    ],
)
def test_a_period_starts_after_today_and_lasts_at_most_12_months(start: date, end: date, problems: list[str]) -> None:
    assert period.check_period(start, end, TODAY) == problems


def test_the_last_day_of_a_period_starting_on_the_29th_of_february_is_the_28th_a_year_on() -> None:
    assert period.last_day(date(2028, 2, 29)) == date(2029, 2, 27)
    assert period.add_months(date(2027, 1, 31), 1) == date(2027, 2, 28)


@pytest.mark.parametrize(
    ("open_need", "ordered", "months", "qty", "part"),
    [
        ("216", "3009.5", 3, "753", "752.375"),  # Apex RM-08: the history pro rata is larger, rounded up
        ("216", "0", 3, "216", "0"),  # nothing bought: the open need
        ("0", "735", 12, "735", "735"),  # a whole year: what was bought
        ("100.2", "0", 3, "101", "0"),  # a part unit is rounded up
        ("753", "3012", 3, "753", "753"),  # equal figures
        ("0", "0", 3, "0", "0"),
        ("0", "1", 1, "1", "0.08333333333333333333333333333"),  # 1/12 of a unit still asks one
    ],
)
def test_the_suggested_quantity_is_the_larger_figure_rounded_up_to_a_whole_unit(
    open_need: str, ordered: str, months: int, qty: str, part: str
) -> None:
    got = period.suggested(D(open_need), D(ordered), months)

    assert got.qty == D(qty)
    assert got.history_part == D(part)


def test_p1_the_award_inside_maharashtra_is_split_into_cgst_and_sgst() -> None:
    got = period.award_value([period.AwardPart(D("900"), D("880.00"), D("18"))], "27", "27")

    assert (got.taxable, got.cgst, got.sgst, got.igst, got.total) == (
        D("792000.00"), D("71280.00"), D("71280.00"), D("0.00"), D("934560.00"),
    )  # fmt: skip


def test_p2_round_two_at_sup_003s_price() -> None:
    got = period.award_value([period.AwardPart(D("900"), D("895.00"), D("18"))], "27", "27")

    assert (got.taxable, got.cgst, got.total) == (D("805500.00"), D("72495.00"), D("950490.00"))


def test_p4_gujarat_to_maharashtra_is_igst() -> None:
    got = period.award_value([period.AwardPart(D("1000"), D("84.00"), D("18"))], "24", "27")

    assert (got.taxable, got.cgst, got.sgst, got.igst, got.total) == (
        D("84000.00"), D("0.00"), D("0.00"), D("15120.00"), D("99120.00"),
    )  # fmt: skip


def test_b1_two_lines_from_tamil_nadu_to_karnataka() -> None:
    parts = [period.AwardPart(D("60"), D("2350.00"), D("18")), period.AwardPart(D("60"), D("1750.00"), D("18"))]

    got = period.award_value(parts, "33", "29")

    assert (got.taxable, got.igst, got.total) == (D("246000.00"), D("44280.00"), D("290280.00"))


def test_each_half_of_the_gst_is_rounded_half_up_to_paise_per_part() -> None:
    # 1 x 0.05 at 18%: taxable 0.05, each half 0.0045 -> 0.00; 1 x 0.25: half 0.0225 -> 0.02
    assert period.award_value([period.AwardPart(D("1"), D("0.25"), D("18"))], "27", "27").cgst == D("0.02")
    assert period.award_value([period.AwardPart(D("1"), D("0.75"), D("18"))], "27", "27").cgst == D("0.07")  # 0.0675
    # quantity x price is rounded half up to paise first: 3 x 0.335 = 1.005 -> 1.01
    assert period.award_value([period.AwardPart(D("3"), D("0.335"), D("0"))], "27", "27").taxable == D("1.01")


def test_two_suppliers_of_one_award_add_up() -> None:
    a = period.award_value([period.AwardPart(D("10"), D("100"), D("18"))], "27", "27")
    b = period.award_value([period.AwardPart(D("10"), D("100"), D("18"))], "24", "27")

    both = a.plus(b)

    assert (both.taxable, both.cgst, both.sgst, both.igst, both.total) == (
        D("2000.00"), D("90.00"), D("90.00"), D("180.00"), D("2360.00"),
    )  # fmt: skip
    assert period.NOTHING.plus(a) == a
