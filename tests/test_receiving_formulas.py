"""The receipt formulas of block 9: excess above the order, inspection flags, rejected serials and recovery claims."""

from decimal import Decimal

import pytest

from p2p.formulas import budget as budget_rules
from p2p.formulas import receiving as rules
from p2p.formulas import returns as return_rules

D = Decimal


def test_an_item_or_its_category_asking_for_inspection_is_enough() -> None:
    assert rules.needs_inspection(True, False) is True
    assert rules.needs_inspection(False, True) is True
    assert rules.needs_inspection(True, True) is True
    assert rules.needs_inspection(False, False) is False


@pytest.mark.parametrize(
    ("good", "room", "counted", "excess"),
    [
        ("10", "10", "10", "0"),  # exactly the room: nothing above
        ("10", "9.5", "9.5", "0.5"),
        ("10.0001", "10", "10", "0.0001"),  # one unit more
        ("0", "5", "0", "0"),
        ("7.5", "0", "0", "7.5"),  # a category with no tolerance (Brightwave)
    ],
)
def test_good_units_split_into_what_counts_and_what_waits(good: str, room: str, counted: str, excess: str) -> None:
    split = rules.split_excess(D(good), D(room))
    assert (split.counted, split.excess) == (D(counted), D(excess))


def test_a_negative_quantity_or_room_is_refused() -> None:
    with pytest.raises(ValueError, match="must not be negative"):
        rules.split_excess(D("-1"), D("2"))
    with pytest.raises(ValueError, match="must not be negative"):
        rules.split_excess(D("1"), D("-2"))


def test_the_rejected_serials_are_named_one_each_and_must_be_received() -> None:
    received = ["BWL-0001", "BWL-0002"]
    assert rules.check_rejected_serials(D("1"), ["bwl-0002"], received, 1) == ["BWL-0002"]
    with pytest.raises(ValueError, match="name the 1 serial number rejected, from those received"):
        rules.check_rejected_serials(D("1"), [], received, 1)
    with pytest.raises(ValueError, match="not received on this line: BWL-0009"):
        rules.check_rejected_serials(D("1"), ["BWL-0009"], received, 1)
    with pytest.raises(ValueError, match="given twice"):
        rules.check_rejected_serials(D("2"), ["BWL-0001", "BWL-0001"], received, 1)
    with pytest.raises(ValueError, match="whole units"):
        rules.check_rejected_serials(D("0.5"), ["BWL-0001"], received, 1)


@pytest.mark.parametrize(
    ("returned", "invoiced", "received_after", "expected"),
    [
        ("20", "500", "480", "20"),  # the whole return was invoiced
        ("20", "490", "480", "10"),  # only ten were invoiced beyond the goods kept
        ("20", "480", "480", "0"),  # nothing at stake: no claim
        ("20", "470", "480", "0"),
        ("5", "500", "490", "5"),  # the returned quantity is the most
        ("0", "500", "400", "0"),
    ],
)
def test_a_claim_is_the_returned_quantity_that_was_invoiced_beyond_what_remains(
    returned: str, invoiced: str, received_after: str, expected: str
) -> None:
    assert return_rules.claim_qty(D(returned), D(invoiced), D(received_after)) == D(expected)


def test_a_receipt_reversal_takes_back_the_actual_and_keeps_the_paise() -> None:
    assert budget_rules.receipt_reversal_actual(D("10"), D("220")) == D("-2200.00")
    assert budget_rules.receipt_reversal_actual(D("0.0033"), D("100")) == D("-0.33")
    assert budget_rules.receipt_reversal_actual(D("0"), D("100")) is None
