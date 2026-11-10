"""Comparing quotes: landed cost, the split of freight over lines, and the 'too good to be true' price question."""

from decimal import Decimal

import pytest

from p2p.formulas import comparison as c

D = Decimal


@pytest.mark.parametrize(
    ("total", "shares", "expected"),
    [
        ("100", ["1", "1", "1"], ["33.34", "33.33", "33.33"]),  # the odd paisa goes to the first of equal remainders
        ("100", ["500", "300", "200"], ["50.00", "30.00", "20.00"]),
        ("10.01", ["1", "2"], ["3.34", "6.67"]),
        ("0", ["5", "5"], ["0.00", "0.00"]),
        ("9", ["0", "0", "0"], ["3.00", "3.00", "3.00"]),  # nothing to go by: split evenly
        ("50", ["4000"], ["50.00"]),
    ],
)
def test_freight_is_split_over_lines_by_value_and_always_adds_up_exactly(
    total: str, shares: list[str], expected: list[str]
) -> None:
    parts = c.allocate(D(total), [D(s) for s in shares])

    assert [str(p) for p in parts] == expected
    assert sum(parts) == D(total)


def test_nothing_to_split_over_is_an_empty_split() -> None:
    assert c.allocate(D("10"), []) == []


def test_landed_cost_is_price_plus_its_share_of_charges_plus_gst_on_both() -> None:
    landed = c.landed_cost(D("1000.00"), D("50.00"), D("18"))

    assert (landed.value, landed.charges, landed.tax, landed.total) == (
        D("1000.00"),
        D("50.00"),
        D("189.00"),
        D("1239.00"),
    )


def test_gst_on_a_landed_cost_is_rounded_to_the_paisa() -> None:
    assert c.landed_cost(D("33.33"), D("0.00"), D("5")).tax == D("1.67")  # 1.6665 rounds up


def test_a_zero_rate_adds_no_tax() -> None:
    assert c.landed_cost(D("100"), D("10"), D("0")).total == D("110")


@pytest.mark.parametrize(
    ("price", "reference", "expected"),
    [("90", "100", "-10.00"), ("110", "100", "10.00"), ("100", "100", "0.00"), ("1", "3", "-66.67")],
)
def test_the_gap_to_a_reference_price_is_in_percent_to_two_places(price: str, reference: str, expected: str) -> None:
    assert c.gap_pct(D(price), D(reference)) == D(expected)


def test_a_gap_to_no_reference_price_is_unknown() -> None:
    assert c.gap_pct(D("5"), D("0")) is None


@pytest.mark.parametrize(
    ("price", "flagged"),
    [("59", True), ("60", True), ("60.01", False), ("100", False), ("150", False)],
)
def test_a_price_forty_percent_or_more_under_the_others_is_a_question(price: str, flagged: bool) -> None:
    questions = c.price_questions(D(price), None, [D("100"), D("100")], D("40"))

    assert bool(questions) is flagged


def test_the_others_are_judged_by_their_median_so_one_odd_quote_does_not_hide_another() -> None:
    questions = c.price_questions(D("50"), None, [D("100"), D("110"), D("5")], D("40"))  # median 100

    assert questions and "50.00% below the other quotes" in questions[0]


def test_a_price_far_below_the_last_price_paid_is_a_question_too() -> None:
    questions = c.price_questions(D("55"), D("100"), [], D("40"))

    assert len(questions) == 1 and "below the last price paid" in questions[0]


def test_both_reasons_are_given_when_both_apply() -> None:
    assert len(c.price_questions(D("40"), D("100"), [D("100")], D("40"))) == 2


def test_a_threshold_of_zero_switches_the_question_off() -> None:
    assert c.price_questions(D("1"), D("100"), [D("100")], D("0")) == []


def test_with_nothing_to_compare_with_there_is_no_question() -> None:
    assert c.price_questions(D("1"), None, [], D("40")) == []
