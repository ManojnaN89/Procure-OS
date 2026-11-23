"""A line's due date and the date a supplier may give for a line when accepting an order (block 8, 8.5): pure rules,
on the calendar of the place the goods go to (Chakan works Monday to Saturday and is closed on 24 Nov 2026; Bengaluru
works Monday to Friday)."""

from datetime import date

import pytest

from p2p.formulas.calendar import BusinessCalendar
from p2p.formulas.deliveries import check_promised, line_due

CHAKAN = BusinessCalendar.of([date(2026, 11, 24), date(2026, 12, 25)], saturday_working=True)
BENGALURU = BusinessCalendar.of([date(2026, 12, 25)])
TODAY = date(2026, 11, 23)
ORDERED = date(2026, 12, 7)  # a Monday


def test_a_lines_due_date_is_its_promised_date_else_its_need_by_date() -> None:
    assert line_due(date(2026, 12, 9), ORDERED) == (date(2026, 12, 9), "promised")
    assert line_due(None, ORDERED) == (ORDERED, "need_by")
    assert line_due(None, None) == (None, None)


@pytest.mark.parametrize(
    ("given", "refusal"),
    [
        (date(2026, 12, 10), None),  # exactly supplier_date_days (3) later: accepted
        (
            date(2026, 12, 11),
            "line 2: 11 Dec 2026 is 4 days after the date ordered (07 Dec 2026), more than the 3 "
            "accepted: ask for a change instead",
        ),  # one day more: refused
        (date(2026, 12, 2), None),  # earlier than ordered: accepted
        (TODAY, None),  # today: accepted
        (date(2026, 11, 21), "line 2: 21 Nov 2026 is in the past"),  # yesterday's working day: refused
        (date(2026, 12, 13), "line 2: 13 Dec 2026 is not a working day at Plant, Chakan (Pune)"),  # a Sunday
        (date(2026, 11, 24), "line 2: 24 Nov 2026 is not a working day at Plant, Chakan (Pune)"),  # a holiday
        (date(2026, 12, 5), None),  # a Saturday: Chakan works
    ],
)
def test_a_date_given_when_accepting_is_checked_at_the_boundaries(given: date, refusal: str | None) -> None:
    assert check_promised(2, ORDERED, given, 3, TODAY, CHAKAN, "Plant, Chakan (Pune)") == refusal


def test_a_saturday_is_refused_where_the_week_ends_on_friday() -> None:
    assert check_promised(1, ORDERED, date(2026, 12, 5), 3, TODAY, BENGALURU, "Office, Bengaluru") == (
        "line 1: 05 Dec 2026 is not a working day at Office, Bengaluru"
    )


def test_a_line_with_no_date_ordered_takes_any_working_day_ahead() -> None:
    assert check_promised(1, None, date(2027, 3, 1), 3, TODAY, CHAKAN, "Chakan") is None


def test_yesterday_is_in_the_past() -> None:
    assert check_promised(1, ORDERED, date(2026, 11, 22), 3, TODAY, CHAKAN, "Chakan") == (
        "line 1: 22 Nov 2026 is in the past"
    )


def test_the_buyer_may_record_a_closed_day_but_never_a_past_or_too_late_one() -> None:
    assert check_promised(3, ORDERED, date(2026, 12, 6), 3, TODAY, CHAKAN, "Chakan", closed_ok=True) is None  # Sunday
    assert check_promised(3, ORDERED, date(2026, 11, 22), 3, TODAY, CHAKAN, "Chakan", closed_ok=True) == (
        "line 3: 22 Nov 2026 is in the past"
    )
    assert "4 days after" in str(check_promised(3, ORDERED, date(2026, 12, 11), 3, TODAY, CHAKAN, "C", closed_ok=True))
