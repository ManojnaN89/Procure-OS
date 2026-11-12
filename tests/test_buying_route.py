"""The buying route of a request line (D37): contract, single source, emergency, safe reorder, else quotation; the value
counted over thirty days; stricter category rows; a buyer tightening or loosening a route."""

from datetime import datetime, time, timedelta
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session
from test_orders import _logins, shop  # noqa: F401 - fixtures
from test_sourcing import PASSWORD, TODAY, _headers, _price, _source, db  # noqa: F401 - fixtures

from p2p.formulas import buying_route as br
from p2p.formulas.calendar import IST
from p2p.main import app
from p2p.orders import service as orders
from p2p.platform import permissions, proposals, sod, users
from p2p.sourcing import route, service

D = Decimal
client = TestClient(app)
ADMIN = sod.user(1)
RULE = br.RouteRule("buying_route v1", D(25000), 90, D(80), D(25000), D(2), D(25000), D(200000), 30)


# ---- the tests in order, on plain numbers -----------------------------------------------------------


def facts(**kw: object) -> br.Facts:
    """A line that passes every test of a safe reorder unless told otherwise."""
    base: dict[str, object] = {
        "contract": None,
        "contract_note": "No contract price in force for this item",
        "single_source": None,
        "emergency": False,
        "window_value": D(3800),
        "last_supplier": "S2",
        "days_since_last": 17,
        "on_time": D(100),
        "price_change": D(0),
    }
    return br.Facts(**{**base, **kw})  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("value", "quotes"),
    [(D("24999.99"), 1), (D(25000), 2), (D(200000), 2), (D("200000.01"), 3), (None, 2)],
)
def test_the_quotes_a_quotation_needs_follow_the_value_bands(value: Decimal | None, quotes: int) -> None:
    assert br.quotes_needed(value, RULE) == quotes


@pytest.mark.parametrize(("days", "route"), [(89, "reorder"), (90, "reorder"), (91, "rfq")])
def test_a_safe_reorder_needs_a_purchase_within_ninety_days(days: int, route: str) -> None:
    assert br.choose(facts(days_since_last=days), RULE).route == route


@pytest.mark.parametrize(
    ("on_time", "judged", "route"), [(799, 1000, "rfq"), (800, 1000, "reorder"), (4, 5, "reorder")]
)
def test_a_safe_reorder_needs_a_supplier_on_time_at_least_eighty_percent(on_time: int, judged: int, route: str) -> None:
    pct = br.on_time_pct(on_time, judged)

    choice = br.choose(facts(on_time=pct), RULE)

    assert choice.route == route
    if route == "rfq":
        assert "on time 79.9% (at least 80%)" in choice.reason


def test_a_supplier_with_no_delivery_judged_yet_is_not_a_safe_reorder() -> None:
    assert br.on_time_pct(0, 0) is None
    assert br.choose(facts(on_time=None), RULE).route == "rfq"


@pytest.mark.parametrize(("value", "route"), [(D("24999.99"), "reorder"), (D(25000), "rfq"), (None, "rfq")])
def test_a_safe_reorder_is_only_for_a_value_under_twenty_five_thousand(value: Decimal | None, route: str) -> None:
    assert br.choose(facts(window_value=value), RULE).route == route


@pytest.mark.parametrize(("now", "route"), [("102", "reorder"), ("98", "reorder"), ("102.01", "rfq"), ("97.99", "rfq")])
def test_a_safe_reorder_needs_the_price_within_two_percent_of_the_last(now: str, route: str) -> None:
    assert br.choose(facts(price_change=br.change_pct(D(now), D(100))), RULE).route == route


@pytest.mark.parametrize(("value", "agree"), [(D(25000), False), (D("25000.01"), True), (None, True)])
def test_single_source_above_twenty_five_thousand_needs_the_purchase_manager(
    value: Decimal | None, agree: bool
) -> None:
    choice = br.choose(facts(single_source="S3", window_value=value), RULE)

    assert (choice.route, choice.needs_agreement) == ("single_source", agree)


def test_the_first_test_that_passes_decides() -> None:
    everything = facts(contract="Contract K-1", single_source="S3", emergency=True)
    assert br.choose(everything, RULE).route == "contract"
    assert br.choose(facts(single_source="S3", emergency=True), RULE).route == "single_source"
    emergency = br.choose(facts(emergency=True), RULE)
    assert (emergency.route, emergency.needs_agreement) == ("emergency", True)
    assert br.choose(facts(), RULE).route == "reorder"
    # the reason names the test that decided and every one before it that failed
    quoted = br.choose(facts(days_since_last=120, window_value=D(82800)), RULE)
    assert quoted.reason.startswith("quotation: ₹ 82,800.00 in 30 days needs 2 quotes")
    assert "No contract price in force" in quoted.reason and "120 days ago (within 90)" in quoted.reason
    assert [c.test for c in quoted.checks if not c.passed] == [
        "contract",
        "single_source",
        "emergency",
        "reorder.recent",
        "reorder.value",
    ]


def test_a_category_row_can_only_make_the_rule_stricter() -> None:
    rule = br.stricter(
        RULE,
        {"reorder_below": D(10000), "two_quotes_from": D(50000), "reorder_on_time_pct": D(90), "window_days": 20},
        "buying_route v1 + C1 v1",
    )

    assert rule.reorder_below == D(10000) and rule.reorder_on_time_pct == D(90)  # stricter: taken
    assert rule.two_quotes_from == D(25000) and rule.window_days == 30  # looser: ignored
    assert rule.version == "buying_route v1 + C1 v1"


def test_only_a_move_to_quotation_tightens_a_route() -> None:
    assert br.is_tighter("reorder", "rfq") and br.is_tighter("contract", "rfq") and br.is_tighter("manual", "rfq")
    assert not br.is_tighter("rfq", "rfq") and not br.is_tighter("rfq", "direct")


# ---- a company to route in -------------------------------------------------------------------------


def _apex(db: Session) -> None:
    """The Apex example in the sourcing world: a casting on contract, copper wire by the kilo, and bearings last bought
    from Bravo (S2) seventeen days ago, delivered on time every time."""
    db.execute(
        text(
            "insert into item (code, name, category_code, base_uom, purchase_uom, active, stocked, standard_price, "
            "gst_rate_before, gst_rate_after) values "
            "('RM-01', 'Motor body casting 1 HP', 'C1', 'NOS', 'NOS', true, true, 650, 18, 18), "
            "('RM-08', 'Copper winding wire', 'C1', 'KGS', 'KGS', true, true, 920, 18, 18), "
            "('RM-06', 'Ball bearing 6204', 'C1', 'NOS', 'NOS', true, true, 38, 18, 18)"
        )
    )
    db.commit()
    _price(db, "S1", "RM-01", "650")
    _bought(db, "RM-06", "S2", "38", days_ago=17)


def _bought(
    db: Session,
    item: str,
    supplier: str,
    price: str,
    *,
    days_ago: int,
    on_time: int = 5,
    late: int = 0,
    po: str = "PO-OLD-1",
) -> None:
    """An order placed some days ago and its deliveries, on time or three days late."""
    placed = datetime.combine(TODAY - timedelta(days=days_ago), time(10), tzinfo=IST)
    promised = placed.date() + timedelta(days=2)
    db.execute(
        text(
            "insert into po (no, supplier_code, ship_to_location, status, created_at) values (:po, :s, 'WH-BLR', 'received', :at)"
        ),
        {"po": po, "s": supplier, "at": placed},
    )
    db.execute(
        text(
            "insert into po_line (po_no, line_no, request_no, request_line_no, item_code, description, qty, uom, unit_price, "
            "gst_rate, taxable, promised_date, source, status) values (:po, 1, 'OLD', 1, :i, 'old order', 100, 'NOS', :p, "
            "18, 0, :promised, 'award', 'received')"
        ),
        {"po": po, "i": item, "p": price, "promised": promised},
    )
    for n in range(on_time + late):
        arrived = datetime.combine(promised + timedelta(days=0 if n < on_time else 3), time(11), tzinfo=IST)
        db.execute(
            text(
                "insert into goods_receipt (no, po_no, location_code, gate_entry_at, received_by, posted_by) "
                "values (:g, :po, 'WH-BLR', :at, 'U1', 'U1')"
            ),
            {"g": f"{po}-GR{n}", "po": po, "at": arrived},
        )
        db.execute(
            text(
                "insert into gr_line (gr_no, po_no, po_line_no, qty_received, qty_accepted, qty_rejected) values (:g, :po, 1, 10, 10, 0)"
            ),
            {"g": f"{po}-GR{n}", "po": po},
        )
    db.commit()


def _ask(
    db: Session, no: str, lines: list[tuple[str, str, str | None]], *, priority: str = "normal", uom: str = "NOS"
) -> str:
    """An approved request; lines: (item, quantity, estimated price or None for the item's standard price)."""
    db.execute(
        text(
            "insert into request (no, ship_to_location, type, channel, status, total_value, priority) "
            "values (:n, 'WH-BLR', 'goods', 'form', 'approved', 50000, :p)"
        ),
        {"n": no, "p": priority},
    )
    for n, (item, qty, est) in enumerate(lines, start=1):
        db.execute(
            text(
                "insert into request_line (request_no, line_no, item_code, qty, uom, est_price, category_code, need_by, status) "
                "values (:r, :n, :i, :q, (select purchase_uom from item where code = :i), :e, 'C1', '2026-11-20', 'open')"
            ),
            {"r": no, "n": n, "i": item, "q": qty, "e": est},
        )
    db.commit()
    return no


def _decisions(db: Session, no: str) -> dict[int, dict[str, object]]:
    return {
        r["line_no"]: dict(r)
        for r in db.execute(text("select * from sourcing_decision where request_no = :n"), {"n": no}).mappings()
    }


@pytest.fixture
def apex(db: Session) -> Session:
    _apex(db)
    return db


# ---- the Apex example ------------------------------------------------------------------------------


def test_apex_casting_on_contract_copper_wire_to_two_quotes_and_bearings_reordered(apex: Session) -> None:
    no = _ask(apex, "R-1", [("RM-01", "20", None), ("RM-08", "90", None), ("RM-06", "100", None)])

    result = _source(apex, no, dispatch=False)

    by_line = {o.line_no: o for o in result.lines}
    assert [by_line[n].route for n in (1, 2, 3)] == ["contract", "rfq", "reorder"]
    assert by_line[1].method == "contract" and by_line[1].contract_code == "K-ACTIVE"
    copper = by_line[2]
    assert copper.method == "rfq" and copper.line_value == D("82800.00") and copper.min_quotes == 2
    assert copper.reason.startswith("quotation: ₹ 82,800.00 in 30 days needs 2 quotes")
    assert "never bought" in copper.reason
    bearing = by_line[3]
    assert (bearing.method, bearing.supplier_code, bearing.price, bearing.needs_agreement) == (
        "direct",
        "S2",
        D(38),
        False,
    )
    assert "last bought from S2 17 days ago (within 90)" in bearing.reason and "on time 100.0%" in bearing.reason
    saved = _decisions(apex, no)
    assert {saved[n]["route_rule"] for n in (1, 2, 3)} == {"buying_route v1"}
    assert saved[3]["line_value"] == D("3800.00") and saved[3]["decided_on"] == TODAY
    assert [c["test"] for c in saved[3]["checks"]] == ["contract", "single_source", "emergency", "reorder"]  # type: ignore[union-attr]
    assert saved[2]["min_quotes"] == 2
    assert apex.execute(text("select min_quotes from rfq")).scalar_one() == 2
    (decide,) = apex.execute(text("select after from audit_event where action = 'sourcing.decide'")).scalars().all()
    assert decide["direct"] == 1 and decide["routes"]["3"] == {"route": "reorder", "rule": "buying_route v1"}


@pytest.mark.parametrize(("est", "quotes"), [("1000", 2), ("1000.01", 3)])
def test_at_two_lakh_two_quotes_are_wanted_and_above_it_three(apex: Session, est: str, quotes: int) -> None:
    no = _ask(apex, "R-2", [("RM-08", "200", est)])

    (line,) = _source(apex, no, dispatch=False).lines

    assert line.min_quotes == quotes
    assert apex.execute(text("select min_quotes from rfq")).scalar_one() == quotes


def test_the_value_of_an_item_is_counted_over_thirty_days(apex: Session) -> None:
    earlier = _ask(apex, "R-OLD", [("RM-06", "600", None)])  # ₹ 22,800 of bearings, 29 days ago
    service.source_request(apex, apex.info["slug"], earlier, as_of=TODAY - timedelta(days=29), dispatch=False)

    (split,) = _source(apex, _ask(apex, "R-3", [("RM-06", "100", None)]), dispatch=False).lines

    assert split.route == "rfq" and split.window_value == D("26600.00")  # 22,800 + 3,800 is not under 25,000
    assert "₹ 26,600.00 in 30 days (under ₹ 25,000.00)" in split.reason
    # thirty days ago is outside the window, and a cancelled line does not count
    apex.execute(
        text("update sourcing_decision set decided_on = :d where request_no = 'R-OLD'"),
        {"d": TODAY - timedelta(days=30)},
    )
    apex.execute(text("update request_line set status = 'cancelled' where request_no = 'R-3'"))
    apex.commit()
    (alone,) = _source(apex, _ask(apex, "R-4", [("RM-06", "100", None)]), dispatch=False).lines
    assert alone.route == "reorder" and alone.window_value == D("3800.00")
    (next_one,) = _source(apex, _ask(apex, "R-5", [("RM-06", "100", None)]), dispatch=False).lines
    assert next_one.window_value == D("7600.00")  # R-4 today counts with this one


def test_two_lines_of_one_item_in_one_request_are_added_up(apex: Session) -> None:
    no = _ask(apex, "R-6", [("RM-06", "300", None), ("RM-06", "400", None)])  # ₹ 11,400, then ₹ 15,200 more

    first, second = _source(apex, no, dispatch=False).lines

    assert first.route == "reorder" and second.route == "rfq" and second.window_value == D("26600.00")


@pytest.mark.parametrize(("days", "route"), [(89, "reorder"), (91, "rfq")])
def test_the_last_purchase_counts_up_to_ninety_days(db: Session, days: int, route: str) -> None:
    db.execute(
        text(
            "insert into item (code, name, category_code, purchase_uom, active, standard_price) values ('RM-06', 'Bearing', 'C1', 'NOS', true, 38)"
        )
    )
    db.commit()
    _bought(db, "RM-06", "S2", "38", days_ago=days)

    (line,) = _source(db, _ask(db, "R-7", [("RM-06", "100", None)]), dispatch=False).lines

    assert line.route == route


def test_a_supplier_late_too_often_is_not_reordered_from(db: Session) -> None:
    db.execute(
        text(
            "insert into item (code, name, category_code, purchase_uom, active, standard_price) values ('RM-06', 'Bearing', 'C1', 'NOS', true, 38)"
        )
    )
    db.commit()
    _bought(db, "RM-06", "S2", "38", days_ago=17, on_time=3, late=1)  # 75%

    (line,) = _source(db, _ask(db, "R-8", [("RM-06", "100", None)]), dispatch=False).lines

    assert line.route == "rfq" and "on time 75.0% (at least 80%)" in line.reason


def test_a_price_on_file_more_than_two_percent_above_the_last_is_quoted(apex: Session) -> None:
    _price(apex, "S2", "RM-06", "38.77", contract=None)  # 2.03% above the last order's 38

    (line,) = _source(apex, _ask(apex, "R-9", [("RM-06", "100", None)]), dispatch=False).lines

    assert line.route == "rfq" and "price 2.0% from the last (within 2%)" in line.reason  # 2.03 shown to one decimal
    assert not next(c for c in line.checks if c.test == "reorder.price").passed


# ---- the rule as versioned data ----------------------------------------------------------------------


def test_a_category_row_makes_the_route_stricter_and_the_decision_names_both_versions(apex: Session) -> None:
    v = route.save_rule(
        apex, ADMIN, {"reorder_below": D(3000)}, category="C1", effective_from=TODAY, note="tighter for raw"
    )

    (line,) = _source(apex, _ask(apex, "R-10", [("RM-06", "100", None)]), dispatch=False).lines

    assert v == 1 and line.route == "rfq" and line.route_rule == "buying_route v1 + C1 v1"
    assert "₹ 3,800.00 in 30 days (under ₹ 3,000.00)" in line.reason
    (audit_row,) = apex.execute(
        text("select entity_id, rule_version, reason from audit_event where action = 'buying_route.change'")
    ).all()
    assert tuple(audit_row) == ("C1 v1", "buying_route C1 v1", "tighter for raw")


def test_a_looser_category_row_changes_nothing(apex: Session) -> None:
    route.save_rule(apex, ADMIN, {"reorder_below": D(100000)}, category="C1", effective_from=TODAY)
    _price(apex, "S2", "RM-06", "38", contract=None)

    (line,) = _source(apex, _ask(apex, "R-11", [("RM-06", "700", None)]), dispatch=False).lines  # ₹ 26,600

    assert line.route == "rfq" and "(under ₹ 25,000.00)" in line.reason


def test_a_new_version_of_the_company_rule_applies_from_its_date_and_the_old_one_still_answers_for_the_past(
    apex: Session,
) -> None:
    v = route.save_rule(apex, ADMIN, {"reorder_below": D(5000)}, effective_from=TODAY, note="smaller reorders")

    assert v == 2
    assert route.rule_for(apex, "C1", TODAY).reorder_below == D(5000)
    assert route.rule_for(apex, "C1", TODAY).version == "buying_route v2"
    old = route.rule_for(apex, "C1", TODAY - timedelta(days=1))
    assert old.version == "buying_route v1" and old.reorder_below == D(25000)
    with pytest.raises(route.RouteError, match="not a threshold"):
        route.save_rule(apex, ADMIN, {"budget": D(1)}, effective_from=TODAY)


# ---- single source and emergency: direct buys the purchase manager may have to agree to ------------------


def test_a_single_source_item_is_bought_from_its_supplier_and_above_the_threshold_waits_for_the_purchase_manager(
    shop: Session,
) -> None:
    people = _logins(shop)
    route.mark_single_source(shop, ADMIN, "A", "S2", "Only Bravo makes this bearing")
    _price(shop, "S2", "A", "100", contract=None)
    small = _ask(shop, "R-12", [("A", "250", None)])  # ₹ 25,000 exactly: no agreement needed
    big = _ask(shop, "R-13", [("A", "251", None)])

    (s,) = _source(shop, small, dispatch=False).lines
    b_result = _source(shop, big, dispatch=False)
    (b,) = b_result.lines

    assert (s.route, s.method, s.supplier_code, s.price, s.needs_agreement) == (
        "single_source",
        "direct",
        "S2",
        D(100),
        False,
    )
    assert (b.route, b.needs_agreement) == ("single_source", True) and len(b_result.proposals) == 1
    ready, notes = orders.ready_lines(shop, big, TODAY)
    assert ready == [] and "waiting for the purchase manager" in notes[0]
    pid = b_result.proposals[0]
    with pytest.raises(permissions.PermissionDenied):
        proposals.approve(shop, pid, people["buyer"])  # a buyer cannot agree to it
    proposals.approve(shop, pid, people["procurement"])
    (line,), _ = orders.ready_lines(shop, big, TODAY)
    assert (line.source, line.source_ref, line.supplier_code, line.unit_price) == (
        "direct",
        "single_source",
        "S2",
        D(100),
    )
    assert (
        shop.execute(text("select count(*) from audit_event where action = 'sourcing.direct_agreed'")).scalar_one() == 1
    )


def test_an_emergency_is_bought_directly_from_the_last_supplier_once_the_purchase_manager_agrees(apex: Session) -> None:
    no = _ask(apex, "R-14", [("RM-08", "90", None), ("RM-06", "100", None)], priority="emergency")

    result = _source(apex, no, dispatch=False)

    copper, bearing = result.lines
    assert copper.route == "emergency" and copper.method == "manual"  # never bought, no price on file: a buyer acts
    assert "no supplier and price on file" in copper.reason
    assert (bearing.route, bearing.method, bearing.supplier_code, bearing.needs_agreement) == (
        "emergency",
        "direct",
        "S2",
        True,
    )
    (pid,) = result.proposals
    summary = apex.execute(text("select summary, required_permission from proposal where id = :i"), {"i": pid}).one()
    assert "emergency request" in summary[0] and summary[1] == "sourcing.route_approve"


def test_a_direct_buy_becomes_a_purchase_order_line_marked_direct(shop: Session) -> None:
    _logins(shop)
    shop.execute(text("update item set standard_price = 38 where code = 'A'"))
    shop.commit()
    _bought(shop, "A", "S2", "38", days_ago=17)
    no = _ask(shop, "R-15", [("A", "100", None)])
    _source(shop, no, dispatch=False)

    made = orders.order_request(shop, shop.info["slug"], no, as_of=TODAY)

    (po,) = made.pos
    line = shop.execute(
        text("select source, source_ref, unit_price, qty from po_line where po_no = :p"), {"p": po}
    ).one()
    assert tuple(line) == ("direct", "reorder", D("38.0000"), D("100.0000"))


# ---- a buyer tightens or loosens a route ---------------------------------------------------------------


def test_a_buyer_may_tighten_a_route_at_once(apex: Session) -> None:
    no = _ask(apex, "R-16", [("RM-06", "100", None)])
    _source(apex, no, dispatch=False)
    buyer = sod.user(7)

    done = service.change_route(
        apex, apex.info["slug"], no, 1, "rfq", buyer, reason="want fresh prices", as_of=TODAY, dispatch=False
    )

    assert done["status"] == "changed" and done["rfq_no"] == "RFQ-2627-00001"
    d = _decisions(apex, no)[1]
    assert (d["method"], d["route"], d["rfq_no"]) == ("rfq", "rfq", "RFQ-2627-00001")
    assert str(d["reason"]).startswith("Buyer sent the line to quotation instead of reorder: want fresh prices;")
    assert d["line_value"] == D("3800.00")  # still counted once in the window
    (change,) = apex.execute(
        text("select actor_id, before, after, reason from audit_event where action = 'sourcing.route_change'")
    ).all()
    assert change[0] == "7" and change[1]["route"] == "reorder" and change[2]["rfq"] == "RFQ-2627-00001"
    with pytest.raises(service.SourcingError, match="already goes to quotation"):
        service.change_route(apex, apex.info["slug"], no, 1, "rfq", buyer, reason="again", as_of=TODAY)
    with pytest.raises(service.SourcingError, match="reason is needed"):
        service.change_route(apex, apex.info["slug"], no, 1, "rfq", buyer, reason=" ", as_of=TODAY)


def test_loosening_a_route_waits_for_the_purchase_manager(shop: Session) -> None:
    people = _logins(shop)
    no = _ask(shop, "R-17", [("B", "50", "120")])  # never bought: quotation
    _source(shop, no, dispatch=False)
    buyer = sod.user(people["buyer"])
    rfq_no = _decisions(shop, no)[1]["rfq_no"]

    asked = service.change_route(
        shop,
        shop.info["slug"],
        no,
        1,
        "direct",
        buyer,
        reason="the usual seal",
        as_of=TODAY,
        supplier_code="S1",
        price=D(115),
    )

    assert asked["status"] == "proposed"
    assert _decisions(shop, no)[1]["method"] == "rfq"  # nothing changes until the purchase manager agrees
    with pytest.raises(permissions.PermissionDenied):
        proposals.approve(shop, asked["proposal_id"], people["buyer"])
    proposals.approve(shop, asked["proposal_id"], people["procurement"])
    d = _decisions(shop, no)[1]
    assert (d["method"], d["route"], d["supplier_code"], d["price"], d["rfq_no"]) == (
        "direct",
        "direct",
        "S1",
        D(115),
        None,
    )
    assert "the purchase manager agreed" in str(d["reason"])
    assert shop.execute(text("select status from rfq where no = :r"), {"r": rfq_no}).scalar_one() == "cancelled"
    actions = [
        r[0]
        for r in shop.execute(
            text(
                "select action from audit_event where action like 'sourcing.route%' or action = 'rfq.cancel' order by id"
            )
        )
    ]
    assert actions == ["sourcing.route_change_proposed", "rfq.cancel", "sourcing.route_change"]
    (line,), _ = orders.ready_lines(shop, no, TODAY)
    assert (line.supplier_code, line.unit_price, line.source) == ("S1", D(115), "direct")


def test_a_route_cannot_be_loosened_once_the_quotation_request_has_gone_out(db: Session) -> None:
    no = _ask(db, "R-18", [("B", "5", "100")])
    _source(db, no, dispatch=False)
    db.execute(text("update rfq set status = 'sent'"))
    db.commit()

    with pytest.raises(service.SourcingError, match="has gone to the suppliers"):
        service.change_route(
            db, db.info["slug"], no, 1, "direct", sod.user(7), reason="r", as_of=TODAY, supplier_code="S1", price=D(1)
        )
    with pytest.raises(service.SourcingError, match="a supplier and a price"):
        service.change_route(db, db.info["slug"], no, 1, "direct", sod.user(7), reason="r", as_of=TODAY)


# ---- the screen ----------------------------------------------------------------------------------------


def test_the_request_page_shows_each_line_route_its_rule_and_its_tests(apex: Session) -> None:
    slug, sfx = apex.info["slug"], apex.info["sfx"]
    buyer = users.create_user(slug, f"b{sfx}@x.example", PASSWORD, "Buyer", "Buyer")
    asker = users.create_user(slug, f"r{sfx}@x.example", PASSWORD, "Req", "Requester")
    no = _ask(apex, "R-19", [("RM-06", "100", None)])
    _source(apex, no, dispatch=False)

    detail = client.get(f"/api/requisitions/{no}", headers=_headers(slug, buyer, "Buyer")).json()

    source = detail["lines"][0]["source"]
    assert (source["method"], source["route"], source["rule"], source["window_value"]) == (
        "direct",
        "reorder",
        "buying_route v1",
        "3800.00",
    )
    assert [c["passed"] for c in source["checks"]] == [False, False, False, True]
    refused = client.post(
        f"/api/requisitions/{no}/lines/1/route",
        json={"route": "rfq", "reason": "x"},
        headers=_headers(slug, asker, "Requester"),
    )
    assert refused.status_code == 403
    changed = client.post(
        f"/api/requisitions/{no}/lines/1/route",
        json={"route": "rfq", "reason": "fresh prices"},
        headers=_headers(slug, buyer, "Buyer"),
    )
    assert changed.status_code == 200 and changed.json()["status"] == "changed"
    again = client.post(
        f"/api/requisitions/{no}/lines/1/route",
        json={"route": "rfq", "reason": "x"},
        headers=_headers(slug, buyer, "Buyer"),
    )
    assert again.status_code == 409
