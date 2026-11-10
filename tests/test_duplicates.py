"""The check before ordering (what is already there, what is left to buy) and the repeat-request flag with its override."""

from datetime import date, timedelta
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session
from test_sourcing import PASSWORD, _headers, _request, db  # noqa: F401 - fixture

from p2p.main import app
from p2p.platform import users
from p2p.requests import duplicates

D = Decimal
client = TestClient(app)


def _make(
    db: Session,
    no: str,
    item: str | None = "A",
    qty: str = "10",
    *,
    free: str | None = None,
    need: str = "2026-11-20",
    loc: str = "WH-BLR",
    status: str = "approved",
    days_ago: int = 0,
) -> str:
    """One request with one line, as created `days_ago` days before 6 November 2026."""
    _request(db, no, [(item, qty, free, "C1")], status=status)
    created = date(2026, 11, 6) - timedelta(days=days_ago)
    db.execute(
        text("update request set created_at = :c, need_by = :n, ship_to_location = :l where no = :r"),
        {"c": f"{created} 10:00+05:30", "n": need, "l": loc, "r": no},
    )
    db.execute(
        text("update request_line set need_by = :n, delivery_location = :l where request_no = :r"),
        {"n": need, "l": loc, "r": no},
    )
    db.commit()
    return no


def _po(
    db: Session, no: str, status: str, qty: str, received: str, *, item: str = "A", base: str | None = None
) -> None:
    db.execute(
        text("insert into po (no, supplier_code, ship_to_location, status) values (:n, 'S1', 'WH-BLR', :s)"),
        {"n": no, "s": status},
    )
    db.execute(
        text(
            "insert into po_line (po_no, line_no, request_no, request_line_no, item_code, qty, uom, base_qty, unit_price, gst_rate, taxable, "
            "received_qty, source, status) values (:n, 1, :n, 1, :i, :q, 'NOS', :b, 10, 18, 10, :r, 'award', 'part_received')"
        ),
        {"n": no, "i": item, "q": qty, "b": base or qty, "r": received},
    )
    db.commit()


# ---- the numbers for each line ----------------------------------------------------------------


def test_twenty_ordered_ten_received_twenty_requested_suggests_ten(db: Session) -> None:  # noqa: F811
    _po(db, "PO-1", "sent", "20", "10")  # ten of the twenty are still to come
    no = _make(db, "R-1", qty="20", status="draft")

    (c,) = duplicates.line_checks(db, no)

    assert (c.inbound, c.open_po_balance, c.open_requests) == (D("10"), D("0"), D("0"))
    assert c.suggested_net_qty == D("10")


def test_each_kind_of_supply_is_counted_apart_and_the_suggestion_never_goes_below_zero(db: Session) -> None:  # noqa: F811
    _po(db, "PO-1", "approved", "4", "0")  # ordered, not yet sent
    _po(db, "PO-2", "acknowledged", "3", "1", item="A")  # 2 still to come
    _make(db, "R-OTHER", qty="5", status="approved")  # another request asking for 5 more
    no = _make(db, "R-1", qty="10", status="draft", need="2027-03-01")

    (c,) = duplicates.line_checks(db, no)

    assert (c.open_po_balance, c.inbound, c.open_requests) == (D("4"), D("2"), D("5"))
    assert c.suggested_net_qty == D("0")  # 10 asked for, 11 already coming
    for status in ("received", "cancelled", "closed"):
        db.execute(text("update po set status = :s where no = 'PO-2'"), {"s": status})
    db.commit()
    assert duplicates.line_checks(db, no)[0].inbound == D("0")  # an order that is done is not on the way


def test_stock_on_hand_is_shown_but_does_not_reduce_the_suggestion(db: Session) -> None:  # noqa: F811
    db.execute(
        text(
            "insert into stock_opening (item_code, location_code, qty, unit_cost, as_of) values ('A', 'WH-BLR', 7, 10, '2026-01-01')"
        )
    )
    db.commit()
    no = _make(db, "R-1", qty="10", status="draft")

    (c,) = duplicates.line_checks(db, no)

    assert c.on_hand == D("7") and c.suggested_net_qty == D("10")


def test_quantities_are_worked_out_in_stock_units_and_the_suggestion_comes_back_in_the_line_unit(db: Session) -> None:  # noqa: F811
    _po(db, "PO-1", "sent", "2", "0", base="24")  # two dozen, 24 pieces
    no = _make(db, "R-1", qty="5", status="draft")  # five dozen
    db.execute(text("update request_line set base_qty = 60 where request_no = 'R-1'"))
    db.commit()

    (c,) = duplicates.line_checks(db, no)

    assert (c.base_qty, c.inbound) == (D("60"), D("24"))
    assert c.suggested_net_qty == D("3")  # 36 pieces still to ask for are three dozen


# ---- is it a repeat? --------------------------------------------------------------------------


def test_the_same_item_at_the_same_place_with_close_need_dates_is_flagged_and_a_cancelled_one_is_not(
    db: Session,
) -> None:  # noqa: F811
    _make(db, "R-1", days_ago=2)
    later = _make(db, "R-2", days_ago=0)

    (d,) = duplicates.find_duplicates(db, later)

    assert (d.other_request, d.kind, d.similarity, d.likely) == ("R-1", "same_items", D("1.000"), True)
    assert d.lines[0]["same_qty"] is True
    db.execute(text("update request set status = 'cancelled' where no = 'R-1'"))
    db.commit()
    assert duplicates.find_duplicates(db, later) == []  # a request that was cancelled is nothing to repeat


def test_a_different_quantity_is_a_possible_repeat_not_a_likely_one(db: Session) -> None:  # noqa: F811
    _make(db, "R-1", qty="10", days_ago=1)
    later = _make(db, "R-2", qty="12")

    (d,) = duplicates.find_duplicates(db, later)

    assert d.likely is False and d.similarity == D("1.000")


@pytest.mark.parametrize(("gap_days", "flagged"), [(14, True), (15, False)])
def test_the_window_in_days_decides_both_the_creation_date_and_the_need_date(
    db: Session, gap_days: int, flagged: bool
) -> None:  # noqa: F811
    """The company's window is 14 days: a gap of 14 is inside it, 15 is not."""
    _make(db, "R-1", days_ago=gap_days, need="2026-11-20")
    later = _make(db, "R-2", days_ago=0, need="2026-11-20")
    assert bool(duplicates.find_duplicates(db, later)) is flagged

    other = _make(db, "R-3", days_ago=0, need=str(date(2026, 11, 20) + timedelta(days=gap_days)))
    assert bool([d for d in duplicates.find_duplicates(db, other) if d.other_request == "R-2"]) is flagged


def test_another_place_another_item_or_a_window_set_by_the_company_changes_the_answer(db: Session) -> None:  # noqa: F811
    _make(db, "R-1", item="A", days_ago=3)
    assert duplicates.find_duplicates(db, _make(db, "R-2", item="B")) == []  # another item
    db.execute(text("insert into location (code, name, is_stock_location) values ('WH-PUN', 'Pune store', true)"))
    db.commit()
    assert duplicates.find_duplicates(db, _make(db, "R-3", item="A", loc="WH-PUN")) == []  # another place
    db.execute(text("insert into app_setting (key, value) values ('duplicate_window_days', '2')"))
    db.commit()
    assert (
        duplicates.find_duplicates(db, _make(db, "R-4", item="A")) == []
    )  # the repeat was three days ago, the window is two


def test_free_text_lines_worded_almost_alike_are_flagged_and_unrelated_ones_are_not(db: Session) -> None:  # noqa: F811
    _make(db, "R-1", item=None, free="Special gasket for pump P-12, 150 mm", days_ago=2)
    alike = _make(db, "R-2", item=None, free="special gasket for pump P12 150mm")
    unrelated = _make(db, "R-3", item=None, free="Hi-vis jackets size L")

    (d,) = duplicates.find_duplicates(db, alike)
    assert (d.other_request, d.kind) == ("R-1", "similar_text") and d.lines[0]["wording"] >= 0.82
    assert duplicates.find_duplicates(db, unrelated) == []


def test_part_of_a_request_repeating_gives_a_similarity_below_one(db: Session) -> None:  # noqa: F811
    _request(db, "R-1", [("A", "10", None, "C1"), ("B", "5", None, "C1")])
    _request(db, "R-2", [("A", "10", None, "C1"), ("D", "5", None, "C2")])
    db.commit()

    (d,) = duplicates.find_duplicates(db, "R-2")

    assert d.similarity == D("0.500") and len(d.lines) == 1


# ---- going ahead -------------------------------------------------------------------------------


def test_a_flagged_request_cannot_go_ahead_without_a_reason_and_the_reason_is_kept_with_the_name(db: Session) -> None:  # noqa: F811
    _make(db, "R-1", days_ago=1)
    later = _make(db, "R-2", status="draft")

    with pytest.raises(duplicates.DuplicateError, match="looks like a repeat of R-1"):
        duplicates.gate(db, later, None, 7)
    with pytest.raises(duplicates.DuplicateError, match="reason is needed"):
        duplicates.override(db, later, "  ", 7)
    done = duplicates.gate(db, later, "A second machine broke down", 7)

    assert [d.overridden for d in done] == [True]
    row = db.execute(
        text("select override_reason, overridden_by, similarity from request_check where request_no = 'R-2'")
    ).one()
    assert row == ("A second machine broke down", "7", D("1.000"))
    assert duplicates.gate(db, later, None, 7)  # once given, the reason holds on a later try
    assert (
        db.execute(text("select count(*) from audit_event where action = 'request.duplicate_override'")).scalar_one()
        == 1
    )


def test_a_request_that_repeats_nothing_has_nothing_to_override(db: Session) -> None:  # noqa: F811
    lone = _make(db, "R-1")

    assert duplicates.gate(db, lone, None, 7) == []
    with pytest.raises(duplicates.DuplicateError, match="does not look like a repeat"):
        duplicates.override(db, lone, "why not", 7)
    with pytest.raises(duplicates.DuplicateError, match="does not exist"):
        duplicates.find_duplicates(db, "NOPE")


# ---- the screens' API --------------------------------------------------------------------------


def test_the_requisition_page_shows_the_checks_and_submitting_a_repeat_needs_a_reason(db: Session) -> None:  # noqa: F811
    slug, sfx = db.info["slug"], db.info["sfx"]
    buyer = users.create_user(slug, f"b{sfx}@x.example", PASSWORD, "Buyer", "Buyer")  # may submit requests
    h = _headers(slug, buyer, "Buyer")
    _po(db, "PO-1", "sent", "20", "10")
    _make(db, "R-1", days_ago=1)
    later = _make(db, "R-2", qty="20", status="draft")

    page = client.get(f"/api/requisitions/{later}", headers=h).json()["checks"]
    assert page["lines"][0]["inbound"] == "10" and page["lines"][0]["suggested_net_qty"] in ("0", "10")
    assert page["duplicates"][0]["other_request"] == "R-1" and page["duplicates"][0]["overridden"] is False

    refused = client.post(f"/api/requisitions/{later}/submit", headers=h)
    assert refused.status_code == 409 and "looks like a repeat of R-1" in refused.json()["detail"]
    assert (
        client.post(f"/api/requisitions/{later}/duplicate-override", json={"reason": ""}, headers=h).status_code == 409
    )
    assert client.post("/api/requisitions/NOPE/duplicate-override", json={"reason": "x"}, headers=h).status_code == 404
    assert client.post(
        f"/api/requisitions/{later}/duplicate-override", json={"reason": "Urgent second line"}, headers=h
    ).json() == {"overridden": ["R-1"]}
    shown = client.get(f"/api/requisitions/{later}", headers=h).json()["checks"]["duplicates"][0]
    assert shown["overridden"] is True and shown["override_reason"] == "Urgent second line"
    assert (
        client.post(f"/api/requisitions/{later}/submit", headers=h).status_code != 409
    )  # past the gate (approval rules take over)
    assert client.post(f"/api/requisitions/{later}/submit").status_code == 401
