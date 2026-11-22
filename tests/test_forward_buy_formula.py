"""The forward-buy engine's arithmetic (task 13.5) on the Apex copper wire figures (spec 3.4), and its boundaries: the
dip and the saving exactly at their minimums, a month that nets nothing, cover beyond all need, storage and budget below
one unit, shelf life, a falling trend, too few trend points, ties, rounding to whole purchase units."""

from dataclasses import replace
from decimal import Decimal

from p2p.formulas import forward_buy as fb

D = Decimal
# the monthly averages of the verified benchmarks, November 2025 to September 2026 (October is not verified)
POINTS = [(x, D(p)) for x, p in zip(range(-12, -1), ["870", "879", "875", "890", "888", "883", "892", "891", "889", "897", "899"], strict=True)]  # fmt: skip
RULE = fb.Rule("forward_buy v1", True, D("5.0000"), 6, 3, D("1.5000"), D("10000.00"))
COPPER = fb.Inputs(
    price_now=D("828.0000"),
    points=POINTS,
    avg_use=fb.average_use(D("2984.5")),
    needs={1: D("200"), 2: D("216")},
    usable=D("50"),
    supply=D("366"),
    anchor_offset=2,  # the MRP line needed in January, evaluated in November
    est_price=D("920.00"),
)


def test_copper_at_828_recommends_746_kg_for_february_to_april() -> None:
    got = fb.evaluate(RULE, COPPER)
    assert got.recommended and got.reason == ""
    assert (got.trend_now, got.slope, got.dip_pct, got.avg_use, got.cover) == (
        D("903.5000"), D("2.4091"), D("8.3564"), D("248.7083"), D(0),
    )  # fmt: skip
    assert [(c.expected, fb.q4(c.net_per_unit), c.qty) for c in got.months] == [
        (D("910.7273"), D("70.3073"), D("248.7083")),
        (D("913.1364"), D("60.2964"), D("248.7083")),
        (D("915.5455"), D("50.2855"), D("248.5834")),
    ]
    assert (got.units, got.value, got.gross_saving, got.holding_cost, got.net_saving, got.limit) == (
        D(746), D("686320.00"), D("63511.45"), D("18529.09"), D("44982.36"), "max_cover_months",
    )  # fmt: skip


def test_holding_cost_ends_the_months_before_a_longer_cover_does() -> None:
    got = fb.evaluate(replace(RULE, max_cover_months=12, storage_max_qty=D("2500")), COPPER)
    assert (got.units, got.limit, len(got.months)) == (D(1989), "holding", 8)
    assert got.months[-1].qty == D("248.0419") and fb.q4(got.months[-1].net_per_unit) == D("0.2309")
    assert (got.gross_saving, got.holding_cost, got.net_saving) == (D("181309.81"), D("111136.24"), D("70173.56"))


def test_storage_and_budget_cut_from_the_latest_month() -> None:
    got = fb.evaluate(replace(RULE, storage_max_qty=D("600")), COPPER)
    assert (got.units, got.limit, got.months[-1].qty, got.net_saving) == (
        D(600),
        "storage",
        D("102.5834"),
        D("37640.68"),
    )
    # storage below the cover leaves no room at all
    none = fb.evaluate(replace(RULE, storage_max_qty=D("0")), COPPER)
    assert (none.units, none.reason, none.recommended) == (D(0), "no_need", False)
    # budget left for less than one unit buys nothing; for 100 kg buys 100
    assert fb.evaluate(RULE, replace(COPPER, budget_left=D("919.99"))).reason == "no_need"
    hundred = fb.evaluate(RULE, replace(COPPER, budget_left=D("92000.00")))
    assert (hundred.units, hundred.limit) == (D(100), "budget")


def test_the_dip_and_the_saving_must_be_above_their_minimums() -> None:
    assert fb.evaluate(RULE, replace(COPPER, price_now=D("858.3250"))).reason == "not_below_trend"  # exactly 5%
    assert fb.evaluate(RULE, replace(COPPER, price_now=D("858.3200"))).recommended  # 5.0006%
    at = fb.evaluate(replace(RULE, min_net_saving_inr=D("44982.36")), COPPER)
    assert (at.recommended, at.reason) == (False, "saving_too_small")
    assert fb.evaluate(replace(RULE, min_net_saving_inr=D("44982.35")), COPPER).recommended


def test_a_month_that_nets_exactly_nothing_is_left_out() -> None:
    # a flat trend at 900 and a price of 891: holding 13.365 a month makes month 1 net -4.365: nothing at all
    flat = [(x, D("900")) for x in range(-12, 0)]
    got = fb.evaluate(
        replace(RULE, min_gap_pct=D("0")),
        replace(COPPER, points=flat, price_now=D("891"), anchor_offset=0, supply=D(0)),
    )
    assert got.reason == "no_need" and got.limit == "holding"
    # net exactly 0: at 1% a month on 891.0891, h = 8.9109 and 900 - 891.0891 - 8.9109 = 0
    exact = fb.evaluate(
        replace(RULE, holding_cost_pct_month=D("1"), min_gap_pct=D("0")),
        replace(COPPER, points=flat, price_now=D("891.0891"), anchor_offset=0, supply=D(0)),
    )
    assert exact.months == [] and exact.limit == "holding"


def test_cover_larger_than_all_need_buys_nothing() -> None:
    got = fb.evaluate(RULE, replace(COPPER, usable=D("5000")))
    assert (got.units, got.reason) == (D(0), "no_need")


def test_shelf_life_keeps_the_months_below_it() -> None:
    one = fb.evaluate(replace(RULE, shelf_life_months=1), COPPER)
    assert (one.units, one.limit, one.reason) == (D(0), "shelf_life", "no_need")
    three = fb.evaluate(replace(RULE, shelf_life_months=3), COPPER)
    assert (len(three.months), three.units, three.limit) == (2, D(497), "shelf_life")


def test_a_falling_trend_and_too_few_points_never_recommend() -> None:
    falling = [(x, D("900") - D(5) * (x + 12)) for x in range(-12, 0)]  # 900 down to 845
    got = fb.evaluate(RULE, replace(COPPER, points=falling, price_now=D("828")))
    assert got.slope == D("-5.0000") and got.reason == "not_below_trend"  # trend now 840: 1.43% below
    assert fb.evaluate(RULE, replace(COPPER, points=POINTS[:5])).reason == "no_trend"
    assert fb.evaluate(RULE, replace(COPPER, points=POINTS[:6])).reason != "no_trend"


def test_a_price_not_below_the_last_one_paid_is_not_a_dip() -> None:
    assert fb.evaluate(RULE, replace(COPPER, last_paid=D("828"))).reason == "not_below_last_paid"
    assert fb.evaluate(RULE, replace(COPPER, last_paid=D("828.0001"))).recommended


def test_whole_purchase_units_of_a_pack() -> None:
    # bought in reels of 25 kg at Rs 23,000 a reel: 746.12 kg becomes 29 reels, 725 kg
    got = fb.evaluate(RULE, replace(COPPER, unit_factor=D(25), est_price=D("23000.00")))
    assert (got.units, got.qty_base, got.value) == (D(29), D(725), D("667000.00"))
    assert sum((c.qty for c in got.months), D(0)) == D(725)


def test_months_of_no_issue_lower_the_average_use() -> None:
    assert fb.average_use(D("0")) == D("0.0000")
    assert fb.average_use(D("133")) == D("11.0833")


def test_accepting_less_cuts_the_latest_months_and_reworks_the_money() -> None:
    got = fb.evaluate(RULE, COPPER)
    less = fb.rework(got, D(500), D(1), D("920.00"))
    assert [c.qty for c in less.months] == [D("248.7083"), D("248.7083"), D("2.5834")]
    assert sum((c.qty for c in less.months), D(0)) == D(500) and less.value == D("460000.00")
    assert less.net_saving < got.net_saving
