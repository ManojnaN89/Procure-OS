"""The block 0b formulas, without a database: regional scope (D44), when a benchmark counts (D52), the price-gap test
and the expiry trigger (D40, D41)."""

from datetime import date
from decimal import Decimal as D

import pytest

from p2p.formulas import benchmarks as bench
from p2p.formulas import price_gap as gap
from p2p.formulas.scope import EVERYWHERE, Scope, contains, intersects, label

REGION_OF = {"PLT-CKN": "MH", "PLT-SND": "GJ", "PLT-NEW": None}
RULE = gap.ThresholdRule("price_threshold v1", D("5.0000"), D("10.00"), D("25000.00"), 45, 1, 60)


# ---- scope -----------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("scope", "location", "inside"),
    [
        (EVERYWHERE, "PLT-SND", True),
        (EVERYWHERE, None, True),  # no location: only what applies everywhere
        (Scope.of(regions=["MH"]), "PLT-CKN", True),
        (Scope.of(regions=["MH"]), "PLT-SND", False),
        (Scope.of(regions=["MH"]), None, False),
        (Scope.of(locations=["PLT-SND"]), "PLT-SND", True),
        (Scope.of(locations=["PLT-SND"]), "PLT-CKN", False),
        (Scope.of(regions=["MH"]), "PLT-NEW", False),  # a location in no region is only inside everywhere
    ],
)
def test_a_location_is_inside_a_scope_when_listed_or_its_region_is(
    scope: Scope, location: str | None, inside: bool
) -> None:
    assert contains(scope, location, REGION_OF) is inside


@pytest.mark.parametrize(
    ("a", "b", "meet"),
    [
        (EVERYWHERE, Scope.of(regions=["MH"]), True),  # everywhere and MH cannot coexist for one price
        (Scope.of(regions=["MH"]), Scope.of(regions=["GJ"]), False),  # MH 650 and GJ 685 can
        (Scope.of(regions=["MH"]), Scope.of(locations=["PLT-CKN"]), True),  # a region meets its own locations
        (Scope.of(locations=["PLT-SND"]), Scope.of(regions=["MH"]), False),
        (Scope.of(locations=["PLT-SND"]), Scope.of(locations=["PLT-SND"]), True),
    ],
)
def test_two_scopes_meet_when_one_place_could_be_in_both(a: Scope, b: Scope, meet: bool) -> None:
    assert intersects(a, b, REGION_OF) is meet
    assert intersects(b, a, REGION_OF) is meet


def test_a_scope_is_said_in_words() -> None:
    assert label(EVERYWHERE) == "everywhere"
    assert label(Scope.of(regions=["MH"]), {"MH": "Maharashtra plants"}) == "Maharashtra plants"


# ---- when a benchmark counts -------------------------------------------------------------------------

ON = date(2026, 11, 20)


def _b(**kw: object) -> bench.Benchmark:
    base: dict[str, object] = {
        "id": 1, "price": D("628.0000"), "uom": "NOS", "effective_date": date(2026, 10, 31), "region": "MH",
        "status": "verified",
    }  # fmt: skip
    base.update(kw)
    return bench.Benchmark(**base)  # type: ignore[arg-type]


MH = Scope.of(regions=["MH"])


def _standing(b: bench.Benchmark, scope: Scope = MH, uom: str = "NOS", conv=None):  # type: ignore[no-untyped-def]
    return bench.standing(b, on=ON, max_age_days=45, scope=scope, region_of=REGION_OF, uom=uom, conversions=conv or {})


@pytest.mark.parametrize(
    ("days_old", "counts", "reason"),
    [(0, True, "fresh"), (45, True, "fresh"), (46, False, "stale: 46 days old"), (51, False, "stale: 51 days old")],
)
def test_a_benchmark_is_fresh_up_to_the_limit_and_stale_the_day_after(days_old: int, counts: bool, reason: str) -> None:
    s = _standing(_b(effective_date=date.fromordinal(ON.toordinal() - days_old)))
    assert (s.counts, s.reason, s.age_days) == (counts, reason, days_old)


def test_only_a_verified_benchmark_for_the_region_in_a_convertible_unit_counts() -> None:
    assert _standing(_b(status="unverified")).reason == "unverified"
    assert _standing(_b(status="rejected")).reason == "rejected"
    assert _standing(_b(region="GJ")).reason == "other region"  # a Gujarat rate never counts for a Maharashtra price
    assert _standing(_b(region=None)).counts  # a national benchmark counts everywhere
    assert _standing(_b(region="MH"), scope=EVERYWHERE).reason == "other region"  # a region does not cover everywhere
    assert _standing(_b(region="MH"), scope=Scope.of(locations=["PLT-CKN"])).counts
    assert _standing(_b(uom="KGS")).reason == "unit not convertible"
    assert _standing(_b(effective_date=date(2026, 11, 21))).reason == "not in force yet"


def test_a_benchmark_in_another_unit_is_converted_by_the_items_conversion_either_way() -> None:
    per_kg = _b(price=D("40.0000"), uom="KGS", region=None)
    assert bench.price_in(per_kg, "NOS", {("NOS", "KGS"): D("0.5")}) == D("20.0000")  # one carton is half a kilo
    assert bench.price_in(per_kg, "NOS", {("KGS", "NOS"): D("2")}) == D("20.0000")
    assert bench.price_in(per_kg, "NOS", {}) is None
    assert _standing(per_kg, conv={("KGS", "NOS"): D("2")}).counts


# ---- the price-gap test --------------------------------------------------------------------------------


def test_the_gap_is_a_share_of_the_contract_price_as_in_the_sugar_example() -> None:
    assert gap.gap_pct(D("42"), D("36")) == D("14.2857")  # Rs 6 on Rs 42


def test_exactly_five_percent_does_not_go_ahead_and_just_above_does() -> None:
    at = gap.test(D("62000.00"), [D("58900.0000")], D(40), RULE)
    above = gap.test(D("62000.00"), [D("58899.0000")], D(40), RULE)

    assert (at.gap_pct, at.goes_ahead, at.tests["gap_pct"]) == (D("5.0000"), False, False)
    assert (above.gap_pct, above.goes_ahead) == (D("5.0016"), True)


def test_every_test_must_pass_and_each_is_strict() -> None:
    cheap = gap.test(D("38.00"), [D("36.00")], D(100000), RULE)  # 5.26% but only Rs 2 a unit
    no_qty = gap.test(D("672.00"), [D("628.00")], D(0), RULE)  # 6.5% and Rs 44, but no quantity: no saving
    rupee_edge = gap.test(D("210.00"), [D("200.00")], D(10000), RULE)  # exactly Rs 10 a unit is not above
    saving_edge = gap.test(D("672.00"), [D("628.00")], D("568.18"), RULE)  # 44 x 568.18 = 24,999.92

    assert not cheap.goes_ahead and cheap.tests == {
        "gap_pct": True, "gap_inr_per_unit": False, "min_saving_inr": True, "min_sources": True,
    }  # fmt: skip
    assert not no_qty.goes_ahead and no_qty.saving == D("0.00")
    assert not rupee_edge.tests["gap_inr_per_unit"]
    assert saving_edge.saving == D("24999.92") and not saving_edge.goes_ahead


def test_the_lowest_counting_benchmark_is_the_market_and_without_one_there_is_no_claim() -> None:
    two = gap.test(D("650.00"), [D("640.00"), D("628.00")], D(1000), RULE)
    none = gap.test(D("650.00"), [], D(1000), RULE)

    assert two.market == D("628.00") and two.gap_inr == D("22.0000") and two.gap_pct == D("3.3846")
    assert not two.goes_ahead  # 3.3846% is below 5%
    assert none.market is None and not none.goes_ahead and "no market claim" in none.why


def test_too_few_sources_stops_the_review() -> None:
    rule = gap.ThresholdRule("v", D(5), D(10), D(0), 45, 2, 60)
    assert not gap.test(D("672.00"), [D("600.00")], D(1000), rule).goes_ahead
    assert gap.test(D("672.00"), [D("600.00"), D("610.00")], D(1000), rule).goes_ahead


def test_a_category_row_only_makes_the_rule_stricter() -> None:
    tighter = gap.stricter(RULE, {"gap_pct": D("3"), "min_saving_inr": D("50000"), "evidence_max_age_days": 30}, "x")
    assert (tighter.gap_pct, tighter.min_saving_inr, tighter.evidence_max_age_days) == (D("3"), D("25000.00"), 30)


@pytest.mark.parametrize(
    ("on", "due"),
    [(date(2027, 1, 30), True), (date(2027, 1, 29), False), (date(2027, 3, 31), True), (date(2027, 4, 1), False)],
)
def test_a_contract_opens_an_expiry_review_sixty_days_before_its_end_not_sixty_one(on: date, due: bool) -> None:
    assert gap.expiry_due(date(2027, 3, 31), on, 60) is due


def test_an_urgent_need_stays_on_its_contract() -> None:
    assert gap.urgent("emergency", None, ON, 10, 5)
    assert gap.urgent("normal", date(2026, 11, 30), ON, 10, 5)  # needed in 10 days, lead 10 + RFQ 5
    assert not gap.urgent("normal", date(2026, 12, 20), ON, 10, 5)
