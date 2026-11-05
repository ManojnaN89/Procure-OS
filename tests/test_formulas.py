from datetime import date, datetime
from decimal import Decimal

import pytest

from p2p.formulas.calendar import IST, BusinessCalendar, sla_due
from p2p.formulas.fy import financial_year, fy_bounds, quarter
from p2p.formulas.gst import Registration, gst_split, tax_on_invoice_not_allowed
from p2p.formulas.money import ceil_to_multiple, format_inr, round_money, round_rupee, to_decimal
from p2p.formulas.moq import moq_round
from p2p.formulas.receipt import ReceiptStatus, max_receivable, receipt_status

D = Decimal


# ---- money -----------------------------------------------------------------------------------


def test_floats_are_refused_everywhere_money_is_handled() -> None:
    for call in (
        lambda: to_decimal(1.5),
        lambda: round_money(0.1),
        lambda: moq_round(2.5),
        lambda: gst_split(1.0, 18, "27", "27"),
    ):
        with pytest.raises(TypeError):
            call()


def test_rounding_goes_half_up_not_to_even() -> None:
    assert round_money("2.675") == D("2.68")
    assert round_money("2.665") == D("2.67")
    assert round_rupee("100.50") == D("101")
    assert round_rupee("101.50") == D("102")  # half-even would give 102 here and 100 above


def test_indian_digit_grouping() -> None:
    assert format_inr("12345678.9") == "₹ 1,23,45,678.90"
    assert format_inr("999") == "₹ 999.00"
    assert format_inr("1000") == "₹ 1,000.00"
    assert format_inr("100000") == "₹ 1,00,000.00"
    assert format_inr("-1234567.5", symbol=False) == "-12,34,567.50"


def test_ceil_to_multiple() -> None:
    assert ceil_to_multiple(73, 12) == 84
    assert ceil_to_multiple(72, 12) == 72
    with pytest.raises(ValueError, match="positive"):
        ceil_to_multiple(1, 0)


# ---- financial year --------------------------------------------------------------------------


def test_financial_year_runs_april_to_march() -> None:
    assert financial_year(date(2025, 3, 31)) == "2024-25"
    assert financial_year(date(2025, 4, 1)) == "2025-26"
    assert financial_year(date(2026, 1, 15)) == "2025-26"
    assert financial_year(date(2099, 12, 31)) == "2099-00"
    assert fy_bounds(date(2026, 2, 1)) == (date(2025, 4, 1), date(2026, 3, 31))
    assert [quarter(date(2025, m, 1)) for m in (4, 7, 10, 12)] == [1, 2, 3, 3]
    assert quarter(date(2026, 1, 1)) == 4


# ---- FR06 MOQ and pack rounding --------------------------------------------------------------


def test_fr06_moq_and_multiple_follow_the_plan_examples() -> None:
    assert moq_round(37, moq=50) == 50
    assert moq_round(73, multiple=12) == 84
    assert moq_round(73, moq=50, multiple=12) == 84
    assert moq_round(0) == 0
    assert moq_round("2.5", multiple="0.5") == D("2.5")


# ---- FR20 goods receipt tolerance ------------------------------------------------------------


def test_fr20_over_by_one_unit_beyond_tolerance_is_blocked() -> None:
    assert max_receivable(100, 5) == D("105")
    assert receipt_status(100, 105, over_pct=5) is ReceiptStatus.COMPLETE  # exactly at the limit is fine
    assert receipt_status(100, 106, over_pct=5) is ReceiptStatus.OVER_BLOCKED


def test_fr20_under_delivery_keeps_the_line_open_unless_closed_short() -> None:
    assert receipt_status(100, 90, under_pct=10) is ReceiptStatus.COMPLETE  # exactly at the limit
    assert receipt_status(100, 89, under_pct=10) is ReceiptStatus.OPEN
    assert receipt_status(100, 89, under_pct=10, closed_short=True) is ReceiptStatus.COMPLETE
    assert receipt_status(100, 0) is ReceiptStatus.OPEN
    assert receipt_status(100, 100) is ReceiptStatus.COMPLETE


# ---- FR25 GST split --------------------------------------------------------------------------


def test_fr25_same_state_splits_into_cgst_and_sgst() -> None:
    g = gst_split("1000.00", 18, "27", "27")
    assert (g.cgst, g.sgst, g.igst) == (D("90.00"), D("90.00"), D("0.00"))
    assert g.charged == D("180.00") and g.reverse_charge == D("0.00")


def test_fr25_different_state_charges_igst() -> None:
    g = gst_split("1000.00", 18, "24", "27")
    assert (g.cgst, g.sgst, g.igst) == (D("0.00"), D("0.00"), D("180.00"))


def test_fr25_odd_amounts_round_half_up_per_component() -> None:
    g = gst_split("333.33", 5, "27", "27")  # 333.33 x 2.5% = 8.33325 -> 8.33 each
    assert (g.cgst, g.sgst) == (D("8.33"), D("8.33"))
    assert gst_split("1.00", D("2.5"), "27", "24").igst == D("0.03")  # 0.025 rounds up


@pytest.mark.parametrize("reg", [Registration.COMPOSITION, Registration.UNREGISTERED])
def test_fr25_composition_and_unregistered_suppliers_charge_no_tax(reg: Registration) -> None:
    g = gst_split("1000.00", 18, "27", "27", reg)
    assert g.total_tax == 0 and g.charged == 0
    assert tax_on_invoice_not_allowed(reg, "180.00")
    assert not tax_on_invoice_not_allowed(reg, "0")
    assert not tax_on_invoice_not_allowed(Registration.REGULAR, "180.00")


def test_fr25_reverse_charge_computes_tax_but_adds_nothing_to_the_invoice() -> None:
    g = gst_split("10000.00", 5, "24", "27", reverse_charge=True)
    assert g.igst == D("500.00") and g.charged == 0 and g.reverse_charge == D("500.00")


# ---- business calendar and FR41 SLA ----------------------------------------------------------


def _ist(y: int, m: int, d: int, h: int, minute: int = 0) -> datetime:
    return datetime(y, m, d, h, minute, tzinfo=IST)


def test_fr41_friday_5pm_plus_8_hours_lands_on_monday() -> None:
    cal = BusinessCalendar.of()  # working 09:00 to 18:00, Saturday and Sunday off
    due = sla_due(_ist(2026, 11, 6, 17), 8, cal)  # Friday 6 Nov 2026
    assert due == _ist(2026, 11, 9, 16)  # 1 hour on Friday, 7 hours on Monday


def test_sla_skips_holidays_and_starts_at_opening_time() -> None:
    cal = BusinessCalendar.of([date(2026, 11, 9)])  # Monday is a holiday
    assert sla_due(_ist(2026, 11, 6, 17), 8, cal) == _ist(2026, 11, 10, 16)
    assert sla_due(_ist(2026, 11, 7, 12), 1, cal) == _ist(
        2026, 11, 10, 10
    )  # Saturday noon: counting starts Tuesday 09:00
    assert sla_due(_ist(2026, 11, 5, 7), 1, cal) == _ist(2026, 11, 5, 10)  # before opening: counting starts at 09:00


def test_sla_within_one_day_and_across_several_days() -> None:
    cal = BusinessCalendar.of()
    assert sla_due(_ist(2026, 11, 4, 10), 2, cal) == _ist(2026, 11, 4, 12)
    assert sla_due(_ist(2026, 11, 4, 10), 20, cal) == _ist(2026, 11, 6, 12)  # 8h Wednesday, 9h Thursday, 3h Friday
    assert sla_due(_ist(2026, 11, 4, 10), "0.5", cal) == _ist(2026, 11, 4, 10, 30)
    assert sla_due(_ist(2026, 11, 4, 18), 0, cal) == _ist(2026, 11, 5, 9)


def test_a_six_day_week_works_on_saturday() -> None:
    cal = BusinessCalendar.of(saturday_working=True)
    assert sla_due(_ist(2026, 11, 7, 17), 2, cal) == _ist(2026, 11, 9, 10)  # Saturday 1h, then Monday 1h
    assert cal.add_working_days(date(2026, 11, 6), 1) == date(2026, 11, 7)


def test_naive_times_and_negative_hours_are_refused() -> None:
    cal = BusinessCalendar.of()
    with pytest.raises(ValueError, match="time zone"):
        sla_due(datetime(2026, 11, 4, 10), 1, cal)  # noqa: DTZ001
    with pytest.raises(ValueError, match="negative"):
        sla_due(_ist(2026, 11, 4, 10), -1, cal)
