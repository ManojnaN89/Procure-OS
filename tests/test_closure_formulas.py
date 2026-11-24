"""The arithmetic and rules of closing an order (block 13 R1, R2): short-close values and bands, flags, blockers and
supply done. Pure functions only."""

from datetime import date
from decimal import Decimal

import pytest

from p2p.formulas import closure as rules
from p2p.formulas.gst import Registration
from p2p.formulas.receiving import order_status

D = Decimal
DAY = date(2026, 12, 1)


def line(balance: str = "0", *, received: str = "0", closed: str = "0", price: str = "220", rate: str = "5",
         supplier: str = "27", place: str = "27", line_no: int = 1) -> rules.ShortLine:  # fmt: skip
    qty = D(received) + D(closed) + D(balance)
    return rules.ShortLine(
        line_no,
        "RM-02",
        "NOS",
        qty,
        D(received),
        D(closed),
        D(price),
        D(rate),
        supplier,
        place,
        Registration.REGULAR,
    )


def test_given_a_balance_of_80_at_220_with_5_percent_when_valued_then_gst_is_added_to_the_paisa() -> None:
    # SC1: 80 x 220 = 17,600.00 plus CGST 440.00 and SGST 440.00
    facts = rules.short_close_facts([line("80")])

    assert facts.value == D("18480.00")
    assert facts.lines[0]["balance"] == D("80.0000")


def test_given_the_value_exactly_25000_when_valued_then_the_automatic_band_holds() -> None:
    # 100 x 220 + 5% = 23,100; use a price that lands exactly on the limit: 25,000 / 1.05 = 23809.5238...
    ln = rules.ShortLine(1, "X", "NOS", D("1"), D("0"), D("0"), D("23809.5238"), D("5"), "27", "27")
    facts = rules.short_close_facts([ln])

    assert facts.value == D("25000.00")
    assert rules.risk("auto", []) == "low"
    assert rules.risk("auto", [rules.Flag("unmet_need", "need")]) == "high"


def test_given_a_value_a_paisa_above_the_limit_when_banded_then_it_is_the_next_band() -> None:
    # the band lookup is data; the value boundary itself is exact (25,000.01 is above the automatic band)
    ln = rules.ShortLine(1, "X", "NOS", D("1"), D("0"), D("0"), D("23809.5334"), D("5"), "27", "27")
    facts = rules.short_close_facts([ln])

    assert facts.value == D("25000.01")
    assert rules.risk("person", []) == "high"


def test_given_a_line_already_short_closed_when_valued_then_the_closed_part_is_not_valued_again() -> None:
    ln = line("0", received="20", closed="80")

    facts = rules.short_close_facts([ln])

    assert facts.lines == ()
    assert facts.value == D("0.00")


def test_given_a_cancelled_line_when_supply_is_checked_then_it_is_ignored_and_an_all_cancelled_order_is_not_done() -> (
    None
):
    assert rules.supply_done([rules.LineState(1, D(1), D(1), D(1), "received"),
                              rules.LineState(2, D(1), D(0), D(0), "cancelled")]) is True  # fmt: skip
    assert rules.supply_done([rules.LineState(2, D(1), D(0), D(0), "cancelled")]) is False


def test_given_a_short_closed_line_when_supply_is_checked_then_it_counts_as_received() -> None:
    assert rules.supply_done([rules.LineState(1, D(100), D(40), D(40), "short_closed")]) is True
    assert order_status(["received", "short_closed"]) == "received"
    assert order_status(["short_closed", "part_received"]) == "part_received"


def test_given_invoiced_equal_to_accepted_when_the_money_is_checked_then_nothing_blocks_it() -> None:
    got = rules.blockers([rules.LineState(1, D(10), D(10), D(10), "closed")])

    assert got == ()


def test_given_one_unit_invoiced_beyond_accepted_or_received_beyond_invoiced_then_each_side_blocks() -> None:
    short = rules.blockers([rules.LineState(1, D(10), D(10), D(9), "received")])
    over = rules.blockers([rules.LineState(1, D(10), D(9), D(10), "received")])

    assert [b.code for b in short] == ["not_invoiced"]
    assert short[0].words == "line 1: 1 received, not invoiced"
    assert [b.code for b in over] == ["invoiced_not_received"]
    assert over[0].words == "line 1: 1 invoiced, not received"


def test_given_a_claim_for_the_invoiced_beyond_accepted_when_checked_then_it_is_not_a_blocker() -> None:
    got = rules.blockers([rules.LineState(1, D(10), D(9), D(10), "received", D(1))])

    assert got == ()


def test_given_a_released_price_difference_when_checked_then_it_never_blocks() -> None:
    # there is no value input to the blockers at all: only quantities
    got = rules.blockers([rules.LineState(1, D(10), D(10), D(10), "closed")])

    assert got == ()


def test_every_other_open_document_is_named_in_its_own_words() -> None:
    got = rules.blockers(
        [rules.LineState(1, D(1), D(1), D(1), "received")],
        invoices=[rules.OpenInvoice("PI-1", "approved")],
        failed_payments=[rules.FailedPayment("PAY-1", DAY)],
        advances=[rules.OpenAdvance("PAY-2", D("1000.00"))],
        notes=[rules.OpenNote("credit_note", "CN-1")],
        returns=[rules.OpenReturn("RTN-1")],
        excesses=[rules.OpenExcess(D(2), "GR-1")],
        claims=[rules.OpenClaim("RCL-1")],
        short_close_approver="Sanjay Rao",
    )

    assert [b.code for b in got] == [
        "invoice_open", "payment_failed", "advance_open", "credit_open", "return_open", "excess_open",
        "claim_open", "short_close_waiting",
    ]  # fmt: skip
    assert got[0].words == "invoice PI-1 is booked and not paid"
    assert got[1].words == "payment PAY-1 failed on 01 Dec 2026"
    assert got[-1].words == "a short-close waits for Sanjay Rao"


@pytest.mark.parametrize(
    ("code", "flag"),
    [
        ("unmet_need", rules.Flag("unmet_need", "production still needs 20 NOS of X by 05 Dec 2026 (MO-1)")),
        ("advance_open", rules.Flag("advance_open", "advance PAY-1: ₹ 1,000.00 not yet used")),
        ("in_transit", rules.Flag("in_transit", "5 dispatched on 01 Dec 2026")),
    ],
)
def test_a_flag_at_any_value_makes_a_close_high_risk(code: str, flag: rules.Flag) -> None:
    assert flag.code == code
    assert rules.risk("auto", [flag]) == "high"
