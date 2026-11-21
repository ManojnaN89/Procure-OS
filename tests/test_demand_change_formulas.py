"""The arithmetic of a change in the ERP's demand (rule Q21) and of a net MRP line, with its boundary cases."""

from datetime import date
from decimal import Decimal

import pytest

from p2p.formulas.demand_change import date_change, excess, late_for, qty_change
from p2p.formulas.material import net_want
from p2p.requests.demand import change_line

D = Decimal


@pytest.mark.parametrize(
    ("need", "issued", "reserved", "open_cover", "buyer", "want"),
    [
        # A1: MO-2's seals 100 -> 60, 10 set aside, 90 on a request not yet with the buyer: the request takes the cut
        ("60", "0", "10", "90", "0", ("40", "0", "0", "0")),
        # a cut exactly equal to the open cover: the request goes to 0, the reservation is untouched
        ("50", "0", "50", "50", "0", ("50", "0", "0", "0")),
        # A1: the water seals all set aside, 100 -> 60: 40 released
        ("60", "0", "100", "0", "0", ("0", "40", "0", "0")),
        # more over than open cover and reservation: the rest is the buyer's, in Decimal kilograms
        ("3.5", "0", "10", "3.5", "40", ("3.5", "10", "36.5", "0")),
        # A5: impellers issued 30, set aside 20, cut to 20: 20 released, 10 issued too many
        ("20", "30", "20", "0", "0", ("0", "20", "0", "10")),
        # a cancel after part issue: everything goes, the issued part is the store's
        ("0", "30", "20", "0", "0", ("0", "20", "0", "30")),
        # nothing over: nothing moves
        ("60", "0", "10", "50", "0", ("0", "0", "0", "0")),
        # an increase is never an excess
        ("120", "0", "50", "50", "0", ("0", "0", "0", "0")),
    ],
)
def test_the_excess_is_taken_open_cover_first_then_stock_then_the_buyer_then_the_store(
    need: str,
    issued: str,
    reserved: str,
    open_cover: str,
    buyer: str,
    want: tuple[str, ...],
) -> None:
    got = excess(D(need), D(issued), D(reserved), D(open_cover), D(buyer))
    assert (got.cut, got.release, got.buyer, got.store) == tuple(D(w) for w in want)
    held = D(issued) + D(reserved) + D(open_cover) + D(buyer)
    assert got.total == max(held - D(need), D(0))


def test_dates_and_quantities_say_which_way_they_moved() -> None:
    assert date_change(date(2026, 12, 15), date(2026, 12, 3)) == "earlier"
    assert date_change(date(2026, 12, 15), date(2026, 12, 18)) == "later"
    assert date_change(date(2026, 12, 15), date(2026, 12, 15)) is None
    assert qty_change(D(100), D(60)) == "qty_down"
    assert qty_change(D(100), D("100.0001")) == "qty_up"
    assert qty_change(D(100), D("100.0000")) is None


def test_an_order_promised_after_the_new_date_is_late() -> None:
    assert late_for(date(2026, 12, 10), date(2026, 12, 1), date(2026, 12, 3))  # the promise counts first
    assert not late_for(date(2026, 12, 3), date(2026, 12, 20), date(2026, 12, 3))  # on the day is in time
    assert late_for(None, date(2026, 12, 15), date(2026, 12, 3))
    assert not late_for(None, None, date(2026, 12, 3))


@pytest.mark.parametrize(
    ("need", "issued", "reserved", "requested", "bought", "want"),
    [
        (
            "20",
            "0",
            "0",
            "0",
            "0",
            "0",
        ),  # nothing bought yet: no free stock in place of buying
        ("20", "0", "0", "20", "20", "0"),  # bought, not arrived
        ("20", "0", "0", "8", "20", "12"),  # 12 arrived: those are set aside
        ("20", "0", "12", "0", "20", "8"),  # the rest arrived
        ("20", "5", "15", "0", "20", "0"),  # all arrived, set aside or issued
    ],
)
def test_a_net_line_takes_only_what_arrived_for_it(
    need: str, issued: str, reserved: str, requested: str, bought: str, want: str
) -> None:
    assert net_want(D(need), D(issued), D(reserved), D(requested), D(bought)) == D(want)


def test_a_change_in_one_line_for_a_person() -> None:
    change = {
        "doc_no": "MO-2", "item_code": "RM-10", "what": ["qty_down"], "before": {"qty": "100"}, "after": {"qty": "60"},
        "released": {"RM-10": "40"}, "requests_cut": [{"request_no": "REQ-1", "line_no": 8, "qty_before": "90", "qty_after": "50"}],
        "requests_cancelled": [], "need_by_moved": [],
        "cases": [{"task_id": 14, "assignee": "Neha Kulkarni", "due_at": "2026-11-25T10:00:00+05:30"}],
    }  # fmt: skip
    assert (
        change_line(change)
        == "MO-2 RM-10: 100 -> 60; released 40; REQ-1 line 8 90 -> 50; case 14 for Neha Kulkarni due 25 Nov"
    )
    assert (
        change_line(
            {
                **change,
                "what": ["cancelled"],
                "released": {},
                "requests_cut": [],
                "cases": [],
            }
        )
        == "MO-2 RM-10: cancelled"
    )
