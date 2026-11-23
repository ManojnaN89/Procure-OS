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


# ---- the differences between two versions (block 7, R9) ------------------------------------------------------------


def _full(
    *,
    qty: str = "200",
    price: str = "650.00",
    need_by: date = APPROVED,
    taxable: str = "130000.00",
    cgst: str = "11700.00",
    sgst: str = "11700.00",
    igst: str = "0.00",
    total: str = "153400.00",
    record: int = 11,
    cv: int = 1,
    place: str = "PLT-CKN",
    gstin: str = "27APXCA0000P1Z5",
    terms: str = "NET30",
    delivery: str = "for_destination",
    lines: tuple[pv.LineState, ...] | None = None,
    charges: tuple[pv.Charge, ...] = (),
    version: int = 1,
) -> pv.Snapshot:
    """A version as block 7 keeps it: header, tax, a line with its contract price version, and any charges."""
    line = pv.LineState(
        1, "RM-01", D(qty), D(price), need_by, "Casting", "NOS", D("18"), D(taxable), D(cgst), D(sgst), D(igst),
        record, cv, "open",
    )  # fmt: skip
    return pv.Snapshot(
        version, "SUP-001", place, terms, D(total), None, lines or (line,), None, gstin, delivery, "CNT-0001",
        D(taxable), D(cgst), D(sgst), D(igst), charges,
    )  # fmt: skip


def test_two_equal_versions_have_no_difference_and_650_equals_650_00() -> None:
    a = _full(price="650")
    b = _full(price="650.00", version=2)
    assert pv.diff(a, b) == []


def test_a_header_change_lists_only_the_changed_fields_in_the_fixed_order() -> None:
    # the place moves to another state: our GSTIN and the tax split change, the total does not
    a = _full()
    b = _full(place="PLT-SND", gstin="24APXCA0000P1Z1", cgst="0.00", sgst="0.00", igst="23400.00", version=2)
    found = pv.diff(a, b)
    header = [(d.field, d.before, d.after) for d in found if d.scope == "header"]
    assert header == [
        ("ship_to_location", "PLT-CKN", "PLT-SND"),
        ("buyer_gstin", "27APXCA0000P1Z5", "24APXCA0000P1Z1"),
        ("cgst", D("11700.00"), D("0.00")),
        ("sgst", D("11700.00"), D("0.00")),
        ("igst", D("0.00"), D("23400.00")),
    ]
    assert [(d.line_no, d.field) for d in found if d.scope == "line"] == [(1, "cgst"), (1, "sgst"), (1, "igst")]


def test_a_re_price_shows_the_price_the_contract_version_and_the_amounts() -> None:
    a = _full()
    b = _full(price="672.00", taxable="134400.00", cgst="12096.00", sgst="12096.00", total="158592.00", record=12, cv=2)
    fields = {(d.scope, d.field): (d.before, d.after) for d in pv.diff(a, b)}
    assert fields[("line", "unit_price")] == (D("650.00"), D("672.00"))
    assert fields[("line", "contract_version")] == (1, 2) and fields[("line", "price_record_id")] == (11, 12)
    assert fields[("header", "total")] == (D("153400.00"), D("158592.00"))
    assert ("header", "delivery_terms") not in fields and ("line", "need_by") not in fields


def test_a_date_is_compared_as_a_date_and_a_line_only_in_one_version_is_added_or_dropped() -> None:
    a = _full()
    moved = _full(need_by=date(2026, 12, 19), version=2)
    assert [(d.field, d.before, d.after) for d in pv.diff(a, moved)] == [("need_by", APPROVED, date(2026, 12, 19))]
    extra = pv.LineState(2, "RM-02", D("5"), D("480.00"), None, "Impeller casting")
    two = _full(lines=(a.lines[0], extra), version=2)
    assert [(d.scope, d.line_no, d.field, d.before, d.after) for d in pv.diff(a, two)] == [
        ("line", 2, "added", None, "Impeller casting")
    ]
    assert [(d.line_no, d.field) for d in pv.diff(two, a)] == [(2, "dropped")]


def test_a_charge_added_changed_or_dropped_is_a_difference_of_its_own() -> None:
    freight = pv.Charge("freight", D("500.00"), D("18"), D("45.00"), D("45.00"), D("0.00"))
    a, b = _full(), _full(charges=(freight,), version=2)
    assert [(d.scope, d.charge, d.field, d.after) for d in pv.diff(a, b)] == [
        ("charge", "freight", "added", D("500.00"))
    ]
    dearer = pv.Charge("freight", D("600.00"), D("18"), D("54.00"), D("54.00"), D("0.00"))
    changed = [(d.charge, d.field) for d in pv.diff(b, _full(charges=(dearer,), version=3))]
    assert changed == [("freight", "amount"), ("freight", "cgst"), ("freight", "sgst")]


def test_a_block_4_snapshot_still_loads_and_its_material_reasons_are_unchanged() -> None:
    # given a snapshot stored by block 4 (five line fields, three header fields, no tax or charges)
    old = {
        "supplier_code": "SUP-005", "ship_to_location": "PLT-CKN", "payment_terms_code": "NET45",
        "lines": [{"line_no": 1, "item_code": "RM-08", "qty": "120", "unit_price": "905.00", "need_by": "2026-12-14"}],
    }  # fmt: skip
    base = pv.Snapshot.from_json(old, version=1, total=D("128148.00"), band_max=D("500000.00"), rule_code="PO-02")
    assert base == _snap()
    assert base.taxable is None and base.charges == () and base.lines[0].description is None
    assert pv.material_reasons(base, _snap(qty="121", total="129215.90"), 3) == [
        "line 1: quantity up",
        "the total went up",
    ]


def test_a_full_snapshot_survives_storing_and_reading_with_its_own_total() -> None:
    freight = pv.Charge("freight", D("500.00"), D("18"), D("45.00"), D("45.00"), D("0.00"))
    snap = _full(charges=(freight,))
    back = pv.Snapshot.from_json(snap.as_json(), version=1)
    assert back == snap and back.total == D("153400.00")


def test_delivery_terms_alone_are_not_material_q19() -> None:
    # OP-2 waits for the owner: until then, Q19 as written
    assert pv.material_reasons(_full(), _full(delivery="ex_works", version=2), 3) == []
