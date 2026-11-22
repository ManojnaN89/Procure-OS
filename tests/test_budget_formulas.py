"""The budget engine's arithmetic (task 3.7): what an open order line still holds, what a receipt spends, which month an
actual is booked in when its own month is locked, which month may be locked, and the months of a financial year."""

from datetime import date
from decimal import Decimal

import pytest

from p2p.formulas.budget import fy_of, fy_periods, lockable, open_order_value, post_period, receipt_actual, valid_fy

D = Decimal


def test_an_open_order_line_holds_its_value_less_what_is_already_spent_on_it() -> None:
    # given 36.5 kg at Rs 920.1234 ordered, nothing short closed and nothing spent: paise half up
    assert open_order_value(D("36.5"), D(0), D("920.1234"), D(0)) == D("33584.50")
    # when all of it is spent it holds nothing, and spending more never makes it negative
    assert open_order_value(D("40"), D(0), D("880"), D("35200.00")) == D("0.00")
    assert open_order_value(D("40"), D(0), D("880"), D("35700.00")) == D("0.00")
    # a balance closed short is released
    assert open_order_value(D("60"), D("60"), D("38"), D(0)) == D("0.00")
    assert open_order_value(D("60"), D("2"), D("38"), D("2204.00")) == D("0.00")
    assert open_order_value(D("60"), D(0), D("38"), D("2204.00")) == D("76.00")


def test_a_receipt_spends_the_accepted_quantity_at_the_order_price() -> None:
    assert receipt_actual(D("40"), D("880")) == D("35200.00")
    assert receipt_actual(D(0), D("880")) is None  # nothing accepted, nothing spent
    assert receipt_actual(D("-1"), D("880")) is None
    # a tiny quantity still makes a row, even when it rounds to nothing
    assert receipt_actual(D("0.001"), D("0.0049")) == D("0.00")
    assert receipt_actual(D("3"), D("0.005")) == D("0.02")  # 0.015 rounds half up


def test_an_actual_whose_month_is_locked_goes_to_the_first_open_month_after_it() -> None:
    assert post_period("2026-11", set(), "2026-12") == "2026-11"  # open: stays
    assert post_period("2026-11", {"2026-11"}, "2026-12") == "2026-12"
    assert post_period("2026-10", {"2026-10", "2026-11"}, "2026-12") == "2026-12"  # the next one is locked too
    assert post_period("2026-12", {"2026-11"}, "2026-12") == "2026-12"  # the current month itself
    assert post_period("2026-11", {"2026-11"}, "2026-11") == "2026-11"  # never later than the current month


@pytest.mark.parametrize(
    ("period", "today", "locked", "why"),
    [
        ("2026-11", date(2026, 12, 7), {f"2026-{m:02d}" for m in range(4, 11)}, None),
        ("2026-12", date(2026, 12, 7), {f"2026-{m:02d}" for m in range(4, 12)}, "not ended"),  # today's month
        ("2026-11", date(2026, 12, 7), {f"2026-{m:02d}" for m in range(4, 12)}, "already locked"),
        ("2026-05", date(2026, 12, 7), set(), "lock 2026-04 first"),
        ("2026-04", date(2026, 12, 7), set(), None),  # the first month of the year needs nothing before it
        ("2027-03", date(2027, 4, 1), {f"2026-{m:02d}" for m in range(4, 13)} | {"2027-01", "2027-02"}, None),
        ("2027-03", date(2027, 4, 1), {f"2026-{m:02d}" for m in range(4, 13)} | {"2027-01"}, "lock 2027-02 first"),
    ],
)
def test_a_month_is_locked_only_once_it_has_ended_and_in_order(
    period: str, today: date, locked: set[str], why: str | None
) -> None:
    assert lockable(period, today, locked) == why


def test_the_financial_year_runs_april_to_march() -> None:
    assert fy_periods("2026-27") == [f"2026-{m:02d}" for m in range(4, 13)] + ["2027-01", "2027-02", "2027-03"]
    assert fy_of("2027-03") == "2026-27" and fy_of("2027-04") == "2027-28" and fy_of("2026-04") == "2026-27"
    assert valid_fy("2026-27") and not valid_fy("2026-28") and not valid_fy("26-27")
