"""Block 2 arithmetic, no database: time-phased cover (rule Q17), safety stock (C4) and the causes of a shortage."""

from datetime import date
from decimal import Decimal

from p2p.formulas import material as rules
from p2p.formulas.calendar import BusinessCalendar

D = Decimal
MON_SAT = BusinessCalendar.of([date(2026, 11, 24)], saturday_working=True)  # Guru Nanak's birthday closes the plant
MON_FRI = BusinessCalendar.of([date(2026, 11, 27)])  # Kanakadasa Jayanthi in Karnataka


def part(qty: str, day: int, month: int = 12) -> rules.Part:
    return rules.Part(D(qty), date(2026, month, day), day)


def test_latest_arrival_moves_back_working_days_on_the_place_calendar() -> None:
    assert rules.latest_arrival(date(2026, 12, 5), 0, MON_SAT) == date(2026, 12, 5)  # buffer 0 is the need-by itself
    assert rules.latest_arrival(date(2026, 12, 5), 1, MON_SAT) == date(2026, 12, 4)  # Saturday back to Friday
    assert rules.latest_arrival(date(2026, 12, 1), 2, MON_FRI) == date(2026, 11, 26)  # skips the weekend and a holiday
    assert rules.latest_arrival(date(2026, 11, 25), 1, MON_SAT) == date(2026, 11, 23)  # a holiday inside the buffer
    assert rules.latest_arrival(date(2026, 12, 6), 0, MON_SAT) == date(2026, 12, 6)  # a Sunday need-by, buffer 0
    assert rules.latest_arrival(date(2026, 12, 6), 1, MON_SAT) == date(2026, 12, 5)  # Sunday back to Saturday
    assert rules.latest_arrival(date(2026, 12, 7), 2, MON_FRI) == date(2026, 12, 3)


def test_a_passed_date_with_nothing_in_counts_as_today() -> None:
    today = date(2026, 12, 7)
    assert rules.supply_date(date(2026, 12, 3), date(2026, 12, 1), today) == today
    assert rules.supply_date(None, date(2026, 12, 10), today) == date(2026, 12, 10)
    assert rules.supply_date(date(2026, 12, 8), date(2026, 12, 1), date(2026, 11, 23)) == date(2026, 12, 8)


def test_parts_go_earliest_first_to_the_lines_in_need_by_order() -> None:
    got = rules.time_phase([part("30", 10), part("50", 3)], [D(40), D(40)])
    assert [[(p.qty, p.date.day) for p in ln] for ln in got] == [[(D(40), 3)], [(D(10), 3), (D(30), 10)]]


def test_a_line_wanting_more_than_the_parts_gets_what_there_is() -> None:
    assert [[p.qty for p in ln] for ln in rules.time_phase([part("10", 3)], [D(36.5), D(5)])] == [[D(10)], []]


def test_decimal_quantities_split_exactly() -> None:
    got = rules.time_phase([part("20.25", 4), part("16.25", 9)], [D("36.5")])
    assert sum(p.qty for p in got[0]) == D("36.5")


def test_supply_on_the_latest_arrival_is_timely_and_a_day_later_is_late() -> None:
    latest = date(2026, 12, 5)
    parts = [part("20", 5), part("30", 6)]
    timely = sum(p.qty for p in parts if p.date <= latest)
    late = sum(p.qty for p in parts if p.date > latest)
    assert (timely, late) == (D(20), D(30))


def test_late_supply_does_not_cover_the_need_but_is_not_short() -> None:
    c = rules.cover(D(50), D(0), D(0), D(0), D(50))
    assert (c.lacking, c.at_risk, c.short) == (D(50), D(50), D(0))
    c = rules.cover(D(50), D(0), D(20), D(0), D(50))  # 20 found in stock: 30 still at risk
    assert (c.at_risk, c.short) == (D(30), D(0))
    c = rules.cover(D(50), D(0), D(0), D(10), D(20))  # timely first, then late, the rest short
    assert (c.at_risk, c.short) == (D(20), D(20))
    c = rules.cover(D(50), D(10), D(45), D(0), D(5))  # issued and set aside beyond the need: nothing lacking
    assert (c.lacking, c.at_risk, c.short) == (D(0), D(0), D(0))


def test_stock_taking_over_open_cover_never_exceeds_what_is_lacking() -> None:
    c = rules.cover(D(10), D(0), D(0), D(0), D(10))  # all at risk, on a request not yet with the buyer
    assert rules.releasable_timed(c, D(10)) == D(0)  # pass 1 already gives the at-risk part
    c = rules.cover(D(50), D(0), D(40), D(10), D(0))
    assert rules.releasable_timed(c, D(10)) == D(10)
    assert rules.releasable_timed(c, D(4)) == D(4)


def test_free_stock_less_safety_negative_zero_and_exact() -> None:
    assert rules.free_stock(D(60), D(50), D(20)) == D(-10)
    assert rules.free_stock(D(60), D(40), D(20)) == D(0)
    assert rules.free_stock(D(4), D(0), D(2)) == D(2)
    assert rules.free_stock(D(4), D(0), None) == D(4)
    assert rules.to_reserve(D(5), rules.free_stock(D(60), D(50), D(20))) == D(0)


def _causes(c: rules.Cover, **kw: str) -> list[str]:
    args = {"need": "50", "issued": "0", "reserved": "0", "usable": "0", "set_aside_elsewhere": "0", "safety": "0",
            "held": "0", **kw}  # fmt: skip
    return rules.causes(c, **{k: D(v) for k, v in args.items()})


def test_each_cause_from_the_engine() -> None:
    assert _causes(rules.cover(D(50), D(0), D(50), D(0), D(0)), reserved="50", usable="60") == ["covered"]
    assert _causes(rules.cover(D(50), D(0), D(0), D(50), D(0)), usable="0") == ["no_stock", "on_order"]
    assert _causes(rules.cover(D(50), D(0), D(0), D(0), D(50))) == ["late_supply", "no_stock"]
    assert _causes(rules.cover(D(50), D(0), D(0), D(0), D(0)), usable="10", set_aside_elsewhere="10") == [
        "held_for_earlier", "short",
    ]  # fmt: skip
    assert _causes(rules.cover(D(50), D(0), D(40), D(0), D(0)), reserved="40", usable="60", safety="20") == [
        "safety_stock", "short",
    ]  # fmt: skip
    assert _causes(rules.cover(D(100), D(0), D(10), D(0), D(0)), reserved="10", usable="10", held="20") == [
        "held_quality", "short",
    ]  # fmt: skip
