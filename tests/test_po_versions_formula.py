"""What a change of an approved order is measured from, and when it is material (task 0.2, Q19, DF-07, DF-08): every
reason of formulas/po_versions.material_reasons and its boundaries, and the cumulative price rule's base."""

from datetime import date
from decimal import Decimal

import pytest

from p2p.formulas import po_versions as pv
from p2p.formulas.money import format_inr
from p2p.formulas.supplier_changes import judge_price

D = Decimal
APPROVED = date(2026, 12, 14)


def _snap(
    *,
    qty: str = "120",
    price: str = "905.00",
    need_by: date = APPROVED,
    total: str = "128148.00",
    band_max: str | None = "500000.00",
    item: str = "RM-08",
    supplier: str = "SUP-005",
    place: str = "PLT-CKN",
    terms: str = "NET45",
    lines: tuple[pv.LineState, ...] | None = None,
    version: int = 1,
) -> pv.Snapshot:
    line = pv.LineState(1, item, D(qty), D(price), need_by)
    return pv.Snapshot(
        version, supplier, place, terms, D(total), None if band_max is None else D(band_max), lines or (line,), "PO-02"
    )


def test_nothing_changed_is_not_material() -> None:
    assert pv.material_reasons(_snap(), _snap(version=2), 3) == []


@pytest.mark.parametrize(
    ("days", "material"),
    [(3, False), (4, True), (-3, False), (-4, True)],
)  # the tolerance is 3 days either way, the tolerance itself allowed
def test_a_date_beyond_the_tolerance_from_the_approved_date_is_material(days: int, material: bool) -> None:
    moved = date.fromordinal(APPROVED.toordinal() + days)
    reasons = pv.material_reasons(_snap(), _snap(need_by=moved), 3)
    assert bool(reasons) is material
    if material:
        assert reasons == [
            f"line 1: date moved {abs(days)} days from the approved 14 Dec 2026, more than the 3 accepted"
        ]


def test_two_small_slips_add_up_because_both_are_measured_from_the_approved_date() -> None:
    base = _snap()
    first = _snap(need_by=date(2026, 12, 16), version=2)  # +2: within
    second = _snap(need_by=date(2026, 12, 18), version=3)  # +2 again: 4 from the approved date
    back = _snap(need_by=date(2026, 12, 14), version=3)  # +2 then -2: back where it was approved
    assert pv.material_reasons(base, first, 3) == []
    assert pv.material_reasons(base, second, 3) == [
        "line 1: date moved 4 days from the approved 14 Dec 2026, more than the 3 accepted"
    ]
    assert pv.material_reasons(base, back, 3) == []


@pytest.mark.parametrize(
    ("qty", "total", "material"),
    [("100", "106790.00", False), ("110", "117469.00", False), ("120", "128148.00", False), ("121", "129215.90", True)],
)  # approved 120: anything up to it again needs no approval; above it does
def test_a_quantity_is_material_only_above_the_approved_quantity(qty: str, total: str, material: bool) -> None:
    reasons = pv.material_reasons(_snap(), _snap(qty=qty, total=total), 3)
    assert ("line 1: quantity up" in reasons) is material
    assert ("the total went up" in reasons) is material


def test_a_price_up_and_the_total_up_give_their_reasons_in_order() -> None:
    reasons = pv.material_reasons(_snap(), _snap(price="920.00", total="130272.00"), 3)
    assert reasons == ["line 1: price up", "the total went up"]
    assert pv.material_reasons(_snap(), _snap(price="900.00", total="127440.00"), 3) == []  # a lower price is not


def test_a_header_change_is_material() -> None:
    new = _snap(supplier="SUP-003", place="WH-PUN", terms="ADV30")
    assert pv.material_reasons(_snap(), new, 3) == [
        "supplier changed",
        "delivery location changed",
        "payment terms changed",
    ]


def test_another_item_on_a_line_or_a_new_line_is_material() -> None:
    other = _snap(item="RM-09")
    assert pv.material_reasons(_snap(), other, 3) == ["line 1: item changed"]
    two = (pv.LineState(1, "RM-08", D("120"), D("905.00"), APPROVED), pv.LineState(2, "RM-09", D("1"), D("85"), None))
    assert pv.material_reasons(_snap(), _snap(lines=two), 3) == ["line 2: new line"]


def test_a_total_above_the_approved_band_is_material_even_with_no_other_reason() -> None:
    base = _snap(total="499000.00")
    at_top = pv.material_reasons(base, _snap(total="500000.00"), 3)
    above = pv.material_reasons(base, _snap(total="500000.01"), 3)
    assert at_top == ["the total went up"]
    assert above == [f"the total is above the PO-02 band (up to {format_inr(D('500000.00'))})", "the total went up"]
    # the top band has no top
    assert pv.material_reasons(_snap(band_max=None, total="600000.00"), _snap(total="9000000.00"), 3) == [
        "the total went up"
    ]


@pytest.mark.parametrize(
    ("base", "new", "within"),
    [("905.00", "923.10", True), ("905.00", "923.11", False), ("905.00", "925.00", False), ("920.00", "925.00", True)],
)  # 2% of the first approved price 905.00 is 18.10 exactly
def test_the_cumulative_price_rule_judges_from_the_first_approved_price(base: str, new: str, within: bool) -> None:
    assert judge_price(D(base), D(new), D("2.00")).ok is within


def test_the_rise_in_percent_is_for_display_with_two_decimals() -> None:
    assert pv.rise_pct(D("905"), D("925")) == D("2.21")
    assert pv.rise_pct(D("905"), D("920")) == D("1.66")
    assert pv.rise_pct(D("905"), D("923.10")) == D("2.00")
    assert pv.rise_pct(None, D("925")) is None
    assert pv.rise_pct(D("0"), D("925")) is None


def test_a_snapshot_survives_storing_and_reading() -> None:
    snap = _snap()
    back = pv.Snapshot.from_json(snap.as_json(), version=1, total=snap.total, band_max=snap.band_max, rule_code="PO-02")
    assert back == snap
