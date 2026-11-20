"""Vendor scores from deliveries, receipts and prices, and the 'send to the best vendor' mode of sourcing."""

from datetime import date, datetime, timedelta
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session
from test_orders import _headers, _logins, _price, _request, _source, shop  # noqa: F401 - fixtures
from test_sourcing import Outbox, db, outbox  # noqa: F401 - fixtures

from p2p.formulas import scoring as rules
from p2p.main import app
from p2p.orders import service as orders
from p2p.suppliers import scoring

D = Decimal
client = TestClient(app)
TODAY = date(2026, 11, 6)


# ---- the rules ---------------------------------------------------------------------------------


def test_few_observations_are_pulled_towards_the_neutral_value() -> None:
    assert rules.smoothed(D(0), D(0)) == D("0.7")  # no history: neutral
    assert rules.smoothed(D(4), D(4)).quantize(D("0.000001")) == D("0.871429")  # four good ones are not perfect
    assert rules.smoothed(D(400), D(400)).quantize(D("0.01")) == D("1.00")  # a long record is believed


def test_the_lowest_price_scores_one_and_a_dearer_one_the_best_price_over_its_own() -> None:
    assert rules.price_score(D("100"), D("100")) == D(1)
    assert rules.price_score(D("100"), D("125")) == D("0.8")
    assert rules.price_score(D("100"), D("80")) == D(1)  # never more than one
    assert rules.price_score(D("100"), D(0)) == D(0)


def test_delivery_is_eighty_percent_on_time_receipts_and_twenty_percent_quick_acknowledgements() -> None:
    assert rules.delivery_score(0, 0, 0, 0) == D("0.7")
    assert rules.delivery_score(6, 6, 0, 0) == D("0.8") * D("0.9") + D("0.2") * D("0.7")


def test_the_total_is_the_weighted_average_in_hundreds_and_zero_weights_give_zero() -> None:
    assert rules.combine(rules.Weights(), D("0.8"), D("0.86"), D("0.9")) == D("84.4")
    assert rules.combine(rules.Weights(D(1), D(0), D(0)), D("0.5"), D(1), D(1)) == D("50.0")
    assert rules.combine(rules.Weights(D(0), D(0), D(0)), D(1), D(1), D(1)) == D(0)


@pytest.mark.parametrize(
    ("score", "level"), [("76", "low"), ("75", "medium"), ("46", "medium"), ("45", "high"), ("0", "high")]
)
def test_risk_is_the_other_side_of_the_score_with_the_company_cut_offs(score: str, level: str) -> None:
    assert rules.risk_level(D(score), D(25), D(55)) == level  # risk 24 low, 25 medium, 55 high


# ---- scores from the company's own history -----------------------------------------------------


def _history(
    db: Session, supplier: str, n: int, *, late: int = 0, rejected: str = "0", acked_days: int | None = None
) -> None:
    """n deliveries of 10 pieces, each `late` days after the promised date, `rejected` of the 10 pieces rejected."""
    for k in range(n):
        po = f"H-{supplier}-{k}"
        promised = date(2026, 8, 1) + timedelta(days=7 * k)
        sent = datetime(2026, 7, 20, 10, 0).astimezone() + timedelta(days=7 * k)
        db.execute(
            text(
                "insert into po (no, supplier_code, ship_to_location, status, sent_at, acknowledged_at) "
                "values (:n, :s, 'WH-BLR', 'received', :sent, :ack)"
            ),
            {
                "n": po,
                "s": supplier,
                "sent": sent,
                "ack": None if acked_days is None else sent + timedelta(days=acked_days),
            },
        )
        db.execute(
            text(
                "insert into po_line (po_no, line_no, request_no, request_line_no, item_code, qty, uom, unit_price, gst_rate, taxable, "
                "received_qty, promised_date, source, status) values (:n, 1, :n, 1, 'A', 10, 'NOS', 10, 18, 100, 10, :p, 'award', 'received')"
            ),
            {"n": po, "p": promised},
        )
        db.execute(
            text(
                "insert into goods_receipt (no, po_no, location_code, gate_entry_at, received_by, posted_by) "
                "values (:g, :n, 'WH-BLR', :at, 'U1', 'x')"
            ),
            {
                "g": f"G-{supplier}-{k}",
                "n": po,
                "at": datetime.combine(promised + timedelta(days=late), datetime.min.time()).astimezone()
                + timedelta(hours=12),
            },
        )
        db.execute(
            text(
                "insert into gr_line (gr_no, po_no, po_line_no, qty_received, qty_accepted, qty_rejected) values (:g, :n, 1, 10, :a, :r)"
            ),
            {"g": f"G-{supplier}-{k}", "n": po, "a": D(10) - D(rejected), "r": rejected},
        )
    db.commit()


def test_a_reliable_supplier_beats_a_late_one_and_one_that_rejects_a_third_of_what_it_ships(db: Session) -> None:  # noqa: F811
    _history(db, "S1", 6)  # on time, nothing rejected
    _history(db, "S2", 6, late=5)  # five days late every time
    _history(db, "S3", 6, rejected="3")  # on time, 3 of 10 rejected

    scores = scoring.score_all(db, TODAY)

    assert (scores["S1"].total, scores["S3"].total, scores["S2"].total) == (D("84.4"), D("80.4"), D("63.1"))
    assert scores["S4"].total == D("74.0")  # no history at all: neutral, in between
    one = scores["S2"].numbers()
    assert (one["receipts_judged"], one["on_time"], one["on_time_pct"], one["late_days_average"]) == (
        6,
        0,
        "0.0",
        "5.0",
    )
    assert scores["S3"].numbers()["accepted_pct"] == "70.0" and scores["S3"].numbers()["rejected_qty"] == "18"


def test_only_what_had_happened_by_the_date_counts(db: Session) -> None:  # noqa: F811
    _history(db, "S1", 6)  # promised 1 Aug, 8 Aug ... 5 Sep

    assert scoring.histories(db, date(2026, 8, 20))["S1"].receipts == 3
    assert scoring.histories(db, TODAY)["S1"].receipts == 6


def test_quick_acknowledgement_counts_and_a_slow_one_does_not(db: Session) -> None:  # noqa: F811
    _history(db, "S1", 4, acked_days=1)
    _history(db, "S2", 4, acked_days=9)

    h = scoring.histories(db, TODAY)

    assert (h["S1"].acks, h["S1"].quick_acks) == (4, 4) and (h["S2"].acks, h["S2"].quick_acks) == (4, 0)
    assert rules.delivery_score(4, 4, 4, 4) > rules.delivery_score(4, 4, 0, 4)


def test_the_price_is_judged_against_the_others_on_offer_for_the_same_item(db: Session) -> None:  # noqa: F811
    _price(db, "S1", "A", "100", contract=None)
    _price(db, "S2", "A", "125", contract=None)
    _price(db, "S1", "B", "50", contract=None)  # nobody else prices B: not compared

    scores = scoring.score_all(db, TODAY)

    assert scores["S1"].price == D(1) and scores["S2"].price == D("0.8")
    assert (scores["S1"].price_items, scores["S2"].price_items) == (1, 1) and scores["S2"].price_gap_pct == D("20")
    assert scores["S3"].price == rules.NEUTRAL_PRICE  # no price to compare


def test_the_company_sets_the_weights(db: Session) -> None:  # noqa: F811
    _history(db, "S1", 6, late=5)
    _history(db, "S2", 6)
    _price(db, "S1", "A", "100", contract=None)
    _price(db, "S2", "A", "200", contract=None)
    default = scoring.score_all(db, TODAY)
    db.execute(
        text(
            "insert into app_setting (key, value) values ('score_weight_price', '100'), ('score_weight_delivery', '0'), ('score_weight_quality', '0')"
        )
    )
    db.commit()

    by_price = scoring.score_all(db, TODAY)

    assert default["S2"].total > default["S1"].total  # the record outweighs a price twice as high
    assert (by_price["S1"].total, by_price["S2"].total) == (D("100.0"), D("50.0"))


def test_a_supplier_that_is_no_longer_active_has_a_score_but_is_not_ranked(db: Session) -> None:  # noqa: F811
    _history(db, "S1", 3)
    db.execute(text("update supplier set status = 'inactive' where code = 'S1'"))
    db.commit()

    assert "S1" not in scoring.score_all(db, TODAY)
    assert scoring.score_supplier(db, "S1", TODAY).history.receipts == 3


def test_ranking_orders_by_score_then_price_then_code(db: Session) -> None:  # noqa: F811
    _history(db, "S1", 6)
    _history(db, "S2", 6, late=5)

    ranked = scoring.rank(db, [("S2", D("90")), ("S1", D("100")), ("S4", D("100")), ("S3", D("100"))], TODAY)

    assert [r.supplier_code for r in ranked] == [
        "S1",
        "S3",
        "S4",
        "S2",
    ]  # 88.4, then S3 and S4 tied at 78.0 (the code decides), then 71.1
    assert ranked == sorted(ranked, key=lambda r: (-r.total, r.price, r.supplier_code))
    assert scoring.rank(db, [], TODAY) == []
    tie = scoring.rank(db, [("S4", D("100")), ("S3", D("100"))], TODAY)
    assert [r.supplier_code for r in tie] == ["S3", "S4"]  # the same score and price: the code decides


# ---- sourcing in best-vendor mode --------------------------------------------------------------


def _best_vendor_world(shop: Session) -> None:
    """S1 is cheaper but late; S2 costs a little more and always delivers on time."""
    _history(shop, "S1", 6, late=5)
    _history(shop, "S2", 6)
    _price(shop, "S1", "B", "100", contract=None)
    _price(shop, "S2", "B", "105", contract=None)
    shop.commit()


def _set(db: Session, mode: str) -> None:
    db.execute(
        text(
            "insert into app_setting (key, value) values ('sourcing_mode', :m) on conflict (key) do update set value = :m"
        ),
        {"m": mode},
    )
    db.commit()


def test_by_default_a_line_with_prices_on_file_still_goes_to_quotation(shop: Session, outbox: Outbox) -> None:  # noqa: F811
    _best_vendor_world(shop)
    no = _request(shop, "R-1", [("B", "10", None, "C1")])

    (line,) = _source(shop, no, dispatch=False).lines

    assert line.method == "rfq"  # a price list is not a contract: today's behaviour is unchanged


def test_in_best_vendor_mode_the_line_goes_to_the_best_scored_supplier_without_a_quotation_request(
    shop: Session, outbox: Outbox
) -> None:  # noqa: F811
    _best_vendor_world(shop)
    _set(shop, "best_vendor")
    no = _request(shop, "R-1", [("B", "10", None, "C1")])

    result = _source(shop, no, dispatch=False)

    (line,) = result.lines
    assert (line.method, line.supplier_code, line.contract_code, line.price) == ("contract", "S2", None, D("105"))
    assert "Best vendor" in line.reason and "of 2 priced" in line.reason and "score" in line.reason
    assert result.rfqs == [] and shop.execute(text("select count(*) from rfq")).scalar_one() == 0


def test_the_best_vendor_gets_an_ordinary_order_that_goes_through_the_usual_approval(
    shop: Session, outbox: Outbox
) -> None:  # noqa: F811
    _best_vendor_world(shop)
    _set(shop, "best_vendor")
    no = _request(shop, "R-1", [("B", "10", None, "C1")])
    _source(shop, no, dispatch=False)

    made = orders.order_request(shop, shop.info["slug"], no, as_of=TODAY)

    assert len(made.pos) == 1
    po = shop.execute(
        text("select supplier_code, po_type, contract_code, taxable, status from po where no not like 'H-%'")
    ).one()
    assert po[:3] == ("S2", "standard", None) and po[3] == D("1050.00") and po[4] in ("submitted", "approved")
    line = shop.execute(text("select source, source_ref, unit_price from po_line where po_no like 'PO-%'")).one()
    assert line == ("contract", "price_list", D("105"))  # a listed price, not a contract (DF-09)


@pytest.mark.parametrize(
    ("spoil", "expected"),
    [
        (
            "update supplier_document set verified = false where supplier_code = 'S2' and doc_type = 'pan'",
            "S1",
        ),  # bad documents
        ("delete from supplier_contact where supplier_code = 'S2'", "S1"),  # nobody to send the order to
        ("update supplier set status = 'blocked' where code = 'S2'", "S1"),  # not active
    ],
)
def test_a_supplier_that_could_not_be_asked_is_not_chosen_however_well_it_scores(
    shop: Session, outbox: Outbox, spoil: str, expected: str
) -> None:  # noqa: F811
    _best_vendor_world(shop)
    _set(shop, "best_vendor")
    shop.execute(text(spoil))
    shop.commit()
    no = _request(shop, "R-1", [("B", "10", None, "C1")])

    (line,) = _source(shop, no, dispatch=False).lines

    assert line.supplier_code == expected


def test_a_sanctioned_supplier_is_never_chosen_even_when_it_is_cheapest(shop: Session, outbox: Outbox) -> None:  # noqa: F811
    _best_vendor_world(shop)
    _price(shop, "S7", "B", "10", contract=None)  # S7 is on the sanctions list
    shop.execute(
        text(
            "insert into supplier_contact (supplier_code, name, email, is_primary) values ('S7', 'C', 's7@x.example', true)"
        )
    )
    _set(shop, "best_vendor")
    no = _request(shop, "R-1", [("B", "10", None, "C1")])

    (line,) = _source(shop, no, dispatch=False).lines

    assert line.supplier_code == "S2"


def test_a_contract_with_no_room_left_is_passed_over_for_the_next_supplier(shop: Session, outbox: Outbox) -> None:  # noqa: F811
    _history(shop, "S1", 6)  # S1 is the better supplier this time
    _history(shop, "S2", 6, late=5)
    _price(shop, "S1", "B", "100", contract="K-ACTIVE")  # on a contract that cannot take 1,000 more
    _price(shop, "S2", "B", "105", contract=None)
    shop.execute(text("update contract set max_value = 900 where code = 'K-ACTIVE'"))
    _set(shop, "best_vendor")
    no = _request(shop, "R-1", [("B", "10", None, "C1")])

    (line,) = _source(shop, no, dispatch=False).lines
    assert line.supplier_code == "S2"

    shop.execute(text("update contract set max_value = 1000 where code = 'K-ACTIVE'"))  # exactly enough
    shop.commit()
    again = _request(shop, "R-2", [("B", "10", None, "C1")])
    (fits,) = _source(shop, again, dispatch=False).lines
    assert (fits.supplier_code, fits.contract_code) == ("S1", "K-ACTIVE")


def test_the_quantity_break_a_price_needs_must_be_reached(shop: Session, outbox: Outbox) -> None:  # noqa: F811
    _price(shop, "S2", "B", "80", contract=None, min_qty="100")  # only from 100 pieces
    _price(shop, "S1", "B", "100", contract=None)
    _set(shop, "best_vendor")
    few = _request(shop, "R-1", [("B", "10", None, "C1")])
    many = _request(shop, "R-2", [("B", "120", None, "C1")])

    assert _source(shop, few, dispatch=False).lines[0].supplier_code == "S1"
    assert _source(shop, many, dispatch=False).lines[0].supplier_code == "S2"


def test_when_nobody_qualifies_or_there_is_no_price_history_the_usual_sourcing_applies(
    shop: Session, outbox: Outbox
) -> None:  # noqa: F811
    _price(shop, "S7", "B", "10", contract=None)  # the only price is a sanctioned supplier's
    shop.execute(
        text(
            "insert into supplier_contact (supplier_code, name, email, is_primary) values ('S7', 'C', 's7@x.example', true)"
        )
    )
    _set(shop, "best_vendor")
    nobody = _request(shop, "R-1", [("B", "10", None, "C1")])
    no_prices = _request(shop, "R-2", [("A", "10", None, "C1"), (None, "2", "Special gasket", "C1")])

    assert [o.method for o in _source(shop, nobody, dispatch=False).lines] == ["rfq"]
    assert [o.method for o in _source(shop, no_prices, dispatch=False).lines] == ["rfq", "rfq"]


# ---- the screens' API --------------------------------------------------------------------------


def test_the_vendor_page_shows_the_numbers_behind_the_score(shop: Session, outbox: Outbox) -> None:  # noqa: F811
    logins = _logins(shop)
    slug = shop.info["slug"]
    _history(shop, "S2", 6, late=5)
    h = _headers(slug, logins["buyer"], "Buyer")

    s = client.get("/api/vendors/S2", headers=h).json()

    assert s["score"]["score"] == "63.1" and s["score"]["on_time"] == 0 and s["score"]["receipts_judged"] == 6
    assert s["score"]["weights"] == {"price": "40", "delivery": "40", "quality": "20"}
    assert s["risk"]["risk_level"] == "medium" and s["risk"]["risk_score"] == "36.9"
    assert s["risk"]["late_delivery_rate_pct"] == "100.0" and "6 delivery lines" in s["risk"]["basis"]
    new = client.get("/api/vendors/S4", headers=h).json()
    assert new["score"]["score"] == "74.0" and new["score"]["on_time_pct"] is None  # no history: neutral and says so


def test_the_mode_and_weights_are_settings_with_checks(shop: Session) -> None:
    logins = _logins(shop)
    admin = shop.info["slug"]
    from p2p.platform import users  # noqa: PLC0415

    boss = users.create_user(admin, f"adm{shop.info['sfx']}@x.example", "correct horse battery", "Admin", "Admin")
    h = _headers(admin, boss, "Admin")

    assert (
        client.post("/api/settings", json={"sourcing_mode": "cheapest"}, headers=h)
        .json()["error"]
        .startswith("sourcing_mode: choose Compare quotations")
    )
    assert client.post(
        "/api/settings", json={"sourcing_mode": "best_vendor", "score_weight_price": "50"}, headers=h
    ).json() == {"updated": ["sourcing_mode", "score_weight_price"]}
    assert (
        client.get("/api/settings", headers=_headers(admin, logins["buyer"], "Buyer")).json()["sourcing_mode"]
        == "best_vendor"
    )
    assert (
        client.post("/api/settings", json={"score_weight_price": "lots"}, headers=h)
        .json()["error"]
        .endswith("must be a number")
    )


def test_a_best_vendor_line_bought_on_a_price_list_is_labelled_so_is_never_touchless_and_is_never_ordered_twice(
    shop: Session,
    outbox: Outbox,  # noqa: F811
) -> None:
    _best_vendor_world(shop)
    _set(shop, "best_vendor")
    no = _request(shop, "R-1", [("B", "10", None, "C1")])
    _source(shop, no, dispatch=False)

    made = orders.order_request(shop, shop.info["slug"], no, as_of=TODAY)

    (po_no,) = made.pos
    line = shop.execute(text("select source, source_ref from po_line where po_no = :n"), {"n": po_no}).one()
    assert tuple(line) == ("contract", "price_list")  # bought like a contract line, but there is no contract
    po = shop.execute(text("select touchless, touchless_note from po where no = :n"), {"n": po_no}).one()
    assert po[0] is False and "price list: never touchless" in po[1]
    again = orders.order_request(shop, shop.info["slug"], no, as_of=TODAY)
    assert again.pos == []
    assert shop.execute(text("select count(*) from po_line where po_no like 'PO-%'")).scalar_one() == 1
