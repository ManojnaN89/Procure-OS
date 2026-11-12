"""Supplier selection (plan step 6, P1-34): the must-haves that drop a quote, the total cost compared (GST shown but not
counted when the input credit is claimed), the weighted score and the tie-break as versioned data, splitting a line only
when the saving beats the extra freight, the written reasons, the orders per supplier, the printable RFQ and the Apex
copper wire example."""

from datetime import date, timedelta
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session
from test_buying_route import _apex, _ask, _bought
from test_order_changes import _pdf_text
from test_orders import TODAY, _comp, _headers, _logins, _quote, _sent_rfq, shop  # noqa: F401 - fixtures
from test_sourcing import Outbox, _auto_send, _source, db, outbox  # noqa: F401 - fixtures

from p2p.formulas import selection as sel
from p2p.main import app
from p2p.orders import quotes, selection_rule, service
from p2p.platform import sod

D = Decimal
client = TestClient(app)
BUYER = sod.user(7)
NEED_BY = date(2026, 11, 20)  # every test request line is needed fourteen days after TODAY
RULE = sel.SelectionRule(1, "total_cost", sel.Weights(D(40), D(30), D(20), D(10)), ("delivery", "vendor_score"), True)


def facts(**kw: object) -> sel.OfferFacts:
    """A quote that passes every must-have unless told otherwise."""
    base: dict[str, object] = {
        "as_of": TODAY,
        "qty": D(5),
        "need_by": NEED_BY,
        "valid_until": date(2026, 12, 31),
        "delivery_days": 5,
        "moq": None,
        "gst_rate": D(18),
        "expected_gst_rate": D(18),
        "supplier_status": "active",
        "approved": True,
        "gst_reg_type": "regular",
        "gstin": "27AAAAA0000A1Z5",
    }
    return sel.OfferFacts(**{**base, **kw})  # type: ignore[arg-type]


# ---- the must-haves ------------------------------------------------------------------------------


@pytest.mark.parametrize(("days", "dropped"), [(13, []), (14, []), (15, ["delivers 1 day(s) after it is needed"])])
def test_delivery_on_the_need_by_date_qualifies_and_one_day_late_is_dropped(days: int, dropped: list[str]) -> None:
    # given a line needed in fourteen days, when a quote delivers in 13, 14 or 15 days, then only 15 is dropped
    assert sel.must_haves(facts(delivery_days=days)) == dropped


@pytest.mark.parametrize(
    ("valid_until", "dropped"),
    [(TODAY + timedelta(days=1), []), (TODAY, []), (TODAY - timedelta(days=1), ["expired on 05 Nov 2026"])],
)
def test_a_quote_expiring_today_still_qualifies_and_one_that_expired_yesterday_does_not(
    valid_until: date, dropped: list[str]
) -> None:
    assert sel.must_haves(facts(valid_until=valid_until)) == dropped


@pytest.mark.parametrize(
    ("change", "reason"),
    [
        ({"supplier_status": "blocked"}, "supplier is blocked, not active"),
        ({"supplier_status": None}, "supplier is not set up, not active"),
        ({"approved": False}, "supplier is not approved for this category"),
        ({"gstin": None}, "GST details missing: no GSTIN on file for a registered supplier"),
        ({"gst_reg_type": "composition", "gstin": ""}, "GST details missing"),
        ({"gst_rate": D(12)}, "GST rate 12% differs from the expected 18%"),
    ],
)
def test_each_failed_must_have_drops_the_quote_with_its_reason(change: dict[str, object], reason: str) -> None:
    (got,) = sel.must_haves(facts(**change))
    assert got.startswith(reason)


def test_an_unregistered_supplier_needs_no_gstin_and_charges_no_gst() -> None:
    assert sel.must_haves(facts(gst_reg_type="unregistered", gstin=None, gst_rate=D(18))) == []
    assert sel.charged_rate(D(18), "unregistered") == 0 and sel.charged_rate(D(18), "composition") == 0
    assert sel.charged_rate(D(18), "regular") == D(18) and sel.charged_rate(D(18), None) == D(18)


# ---- what is compared ----------------------------------------------------------------------------


def test_gst_is_shown_but_not_counted_when_the_input_credit_is_claimed() -> None:
    # given 900 of goods, 100 of freight and 180 of GST
    assert sel.total_cost(D(900), D(100), D(180), input_credit=True) == D(1000)
    assert sel.total_cost(D(900), D(100), D(180), input_credit=False) == D(1180)


def c(code: str, cost: str, days: int | None = 5, vendor: str = "70", score: str | None = None) -> sel.Candidate:
    return sel.Candidate(code, D(cost), days, D(vendor), D(score) if score else None)


def test_equal_totals_go_to_the_earlier_delivery_then_the_higher_vendor_score_then_the_code() -> None:
    by_delivery = sel.rank([c("S1", "1000", 9), c("S2", "1000", 4)], RULE)
    assert [x.supplier_code for x in by_delivery] == ["S2", "S1"]
    assert sel.tie_reason(by_delivery, RULE) == "tied with S1, decided by earlier delivery"

    by_vendor = sel.rank([c("S1", "1000", 5, "60"), c("S2", "1000", 5, "80")], RULE)
    assert [x.supplier_code for x in by_vendor] == ["S2", "S1"]
    assert sel.tie_reason(by_vendor, RULE) == "tied with S1, decided by higher vendor score"

    same = sel.rank([c("S2", "1000"), c("S1", "1000")], RULE)
    assert [x.supplier_code for x in same] == ["S1", "S2"]
    assert sel.tie_reason(same, RULE) == "tied with S2, decided by supplier code"
    assert sel.tie_reason(sel.rank([c("S1", "999.99"), c("S2", "1000")], RULE), RULE) is None  # no tie at all


def test_the_tie_break_order_is_data() -> None:
    vendor_first = sel.SelectionRule(2, "total_cost", RULE.weights, ("vendor_score", "delivery"), True)
    offers = [c("S1", "1000", 4, "60"), c("S2", "1000", 9, "80")]

    assert sel.rank(offers, RULE)[0].supplier_code == "S1"  # earlier delivery first
    assert sel.rank(offers, vendor_first)[0].supplier_code == "S2"  # higher vendor score first


def test_the_weighted_score_counts_price_delivery_quality_and_payment_terms_by_their_weights() -> None:
    w = sel.Weights(D(40), D(30), D(20), D(10))
    assert sel.weighted_score(w, D(1), D(1), D(1), D(1)) == D("100.0")
    # 40 x 0.9 + 30 x 0.8 + 20 x 0.7 + 10 x 0.5 = 79 of 100
    assert sel.weighted_score(w, D("0.9"), D("0.8"), D("0.7"), D("0.5")) == D("79.0")
    assert sel.price_score(D(900), D(1000)) == D("0.9") and sel.price_score(D(900), D(900)) == 1
    assert sel.terms_score(30, 45) == D(30) / D(45) and sel.terms_score(45, 45) == 1
    assert sel.terms_score(0, 0) == 1  # nobody offers credit: no one is better on terms


def test_a_rule_that_makes_no_sense_is_refused() -> None:
    zero = sel.Weights(D(0), D(0), D(0), D(0))
    assert sel.check_rule("weighted", zero, ()) == ["a weighted comparison needs at least one weight above zero"]
    assert sel.check_rule("cheapest", RULE.weights, ()) == ["the method must be one of total_cost, weighted"]
    assert sel.check_rule("total_cost", RULE.weights, ("delivery", "delivery"))
    assert sel.check_rule("total_cost", sel.Weights(D(-1), D(0), D(0), D(0)), ()) == ["a weight must not be negative"]
    assert sel.check_rule("total_cost", zero, ("vendor_score",)) == []


# ---- splitting a line ----------------------------------------------------------------------------


def so(code: str, price: str, available: str | None = None, fixed: str = "0", moq: str | None = None) -> sel.SplitOffer:
    return sel.SplitOffer(code, D(price), D(0), D(available) if available else None, D(moq) if moq else None, D(fixed))


@pytest.mark.parametrize(
    ("freight", "splits"),
    [("59.99", True), ("60", False), ("60.01", False)],
)
def test_a_split_happens_only_when_the_saving_beats_the_extra_freight(freight: str, splits: bool) -> None:
    # given 5 needed: S1 at 100 can supply 3 (with its own freight), S2 at 120 supplies all 5 with free delivery
    # single S2: 600; split: 3 x 100 + 2 x 120 = 540, so the split saves 60 on the goods
    plan = sel.plan_split(D(5), [so("S1", "100", "3", freight), so("S2", "120")])

    assert plan is not None
    assert [(p.supplier_code, p.qty) for p in plan.parts] == [("S1", D(3)), ("S2", D(2))]
    assert (plan.saving, plan.extra_freight) == (D("60.00"), D(freight))
    assert plan.worthwhile is splits  # a saving exactly equal to the extra freight does not split
    assert ("beats" if splits else "does not beat") in plan.reason()


def test_no_split_when_the_cheapest_supplies_everything_and_a_forced_split_when_no_one_does() -> None:
    assert sel.plan_split(D(5), [so("S1", "100"), so("S2", "90", "3")]) is not None  # S2 is cheaper for part
    assert sel.plan_split(D(5), [so("S1", "90"), so("S2", "100")]) is None  # the cheapest covers it all
    forced = sel.plan_split(D(5), [so("S1", "100", "3"), so("S2", "120", "2", fixed="500")])
    assert forced is not None and forced.single is None and forced.worthwhile
    assert sel.plan_split(D(5), [so("S1", "100", "3"), so("S2", "120", "1")]) is None  # 4 of 5: cannot be covered
    # a part below a supplier's minimum order is not offered to it
    assert sel.plan_split(D(5), [so("S1", "100", "4"), so("S2", "120", moq="2")]) is None


# ---- the comparison on stored quotes -------------------------------------------------------------


def _quote_part(db: Session, rfq: str, supplier: str, price: str, offered: str, *, freight: str = "0") -> str:
    return quotes.enter_quote(
        db,
        rfq,
        quotes.QuoteInput(
            supplier_code=supplier,
            valid_until=date(2026, 12, 31),
            freight=D(freight),
            lines=[quotes.QuoteLineInput(1, D(price), D(18), delivery_days=5, qty=D(offered))],
        ),
        sod.user(1),
    )


def _one_line(shop: Session, outbox: Outbox) -> tuple[str, str]:
    """Seal (B) x 5, needed in fourteen days, out to S1, S2 and S3; the store is registered, so GST is claimed back."""
    return _sent_rfq(shop, outbox, lines=[("B", "5", None, "C1")])


def test_failing_quotes_are_dropped_with_reasons_and_the_rest_ranked_on_total_cost(
    shop: Session, outbox: Outbox
) -> None:
    _, rfq = _one_line(shop, outbox)
    _quote(shop, rfq, "S1", "100", freight="100")  # 500 + 100 = 600 (GST 108 shown, claimed back)
    _quote(shop, rfq, "S2", "110")  # 550
    _quote(shop, rfq, "S3", "90", days=15)  # cheapest, but a day late
    shop.execute(text("update supplier set status = 'blocked' where code = 'S1'"))
    shop.commit()

    (line,) = _comp(shop, rfq).lines

    s2, s3, s1 = line.offers  # the qualifying first, then the dropped by cost
    assert (s2.supplier_code, s2.rank, s2.cost, s2.landed) == ("S2", 1, D("550.00"), D("649.00"))
    assert s1.flags == ["supplier is blocked, not active"] and s1.rank is None and s1.cost == D("600.00")
    assert s3.flags == ["delivers 1 day(s) after it is needed"]
    assert (line.recommended, line.cheapest, line.qualifying) == ("S2", "S2", 1)
    assert line.why.startswith("Lowest total cost among 1 qualifying offer(s): ₹ 550.00 (price and freight; GST is")


def test_without_a_gst_registration_the_gst_is_part_of_the_cost(shop: Session, outbox: Outbox) -> None:
    shop.execute(text("update location set gstin = null"))
    shop.commit()
    _, rfq = _one_line(shop, outbox)
    _quote(shop, rfq, "S2", "100")
    _quote(shop, rfq, "S3", "110")  # a composition supplier charges no GST: 550 against 590

    comp = _comp(shop, rfq)

    s3, s2 = comp.lines[0].offers
    assert (s3.supplier_code, s3.tax, s3.cost) == ("S3", D("0.00"), D("550.00"))
    assert (s2.supplier_code, s2.tax, s2.cost) == ("S2", D("90.00"), D("590.00"))
    assert comp.basis == "price, freight and GST" and comp.rule == "supplier_selection v1"


def test_equal_totals_in_the_store_go_to_the_better_vendor_record(shop: Session, outbox: Outbox) -> None:
    _bought(shop, "A", "S1", "10", days_ago=40, on_time=0, late=5)  # S1 delivered late five times; S2 has no record
    _, rfq = _one_line(shop, outbox)
    _quote(shop, rfq, "S1", "100")
    _quote(shop, rfq, "S2", "100")

    (line,) = _comp(shop, rfq).lines

    assert line.recommended == "S2" and line.why.endswith("tied with S1, decided by higher vendor score")
    by = {o.supplier_code: o for o in line.offers}
    assert by["S2"].vendor_score is not None and by["S1"].vendor_score is not None
    assert by["S2"].vendor_score > by["S1"].vendor_score
    # the tie-break's winner counts as the cheapest qualifying offer: selecting it needs no reason
    assert line.cheapest == "S2" and quotes.award(shop, rfq, {1: "S2"}, BUYER, as_of=TODAY).overridden == []


def test_a_quote_expiring_today_is_selected_without_a_reason(shop: Session, outbox: Outbox) -> None:
    _, rfq = _one_line(shop, outbox)
    _quote(shop, rfq, "S1", "100", valid=TODAY.isoformat())
    _quote(shop, rfq, "S2", "110")

    done = quotes.award(shop, rfq, {1: "S1"}, BUYER, as_of=TODAY)

    assert done.overridden == [] and done.awarded == {1: "S1"}
    with pytest.raises(quotes.QuoteError, match="supplier can only be selected while the RFQ is out"):
        quotes.award(shop, rfq, {1: "S1"}, BUYER, as_of=TODAY)


# ---- the written reasons -------------------------------------------------------------------------


def test_selecting_other_than_the_cheapest_qualifying_quote_needs_a_written_reason(
    shop: Session, outbox: Outbox
) -> None:
    # given the company compares on a weighted score with payment terms only: S1 gives 30 days, S2 none
    selection_rule.save_rule(
        shop,
        sod.user(1),
        effective_from=TODAY,
        method="weighted",
        weights=sel.Weights(D(0), D(0), D(0), D(100)),
        note="credit matters most",
    )
    _, rfq = _one_line(shop, outbox)
    _quote(shop, rfq, "S1", "110")
    _quote(shop, rfq, "S2", "100")

    (line,) = _comp(shop, rfq).lines

    # then S1 is recommended on its score, though S2 is cheaper
    assert (line.recommended, line.cheapest) == ("S1", "S2")
    assert line.why.startswith("Best score (100.0 of 100) among 2 qualifying offer(s)")
    assert "payment terms 100; weights 0/0/0/100" in line.why
    for pick in ("S1", "S2"):  # neither is both the recommendation and the cheapest
        with pytest.raises(quotes.QuoteError, match="a reason is required: a choice is not the recommended"):
            quotes.award(shop, rfq, {1: pick}, BUYER, as_of=TODAY)
    quotes.award(shop, rfq, {1: "S1"}, BUYER, reason="Thirty days of credit is worth Rs 50", as_of=TODAY)
    assert shop.execute(text("select reason, overridden, selection_rule from award")).one() == (
        "Thirty days of credit is worth Rs 50",
        True,
        "supplier_selection v2",
    )


def test_fewer_qualifying_quotes_than_the_route_asked_for_need_a_reason(shop: Session, outbox: Outbox) -> None:
    # given two quotes came in, as the route wants, but one is late: only one qualifies
    _, rfq = _one_line(shop, outbox)
    _quote(shop, rfq, "S1", "100")
    _quote(shop, rfq, "S2", "90", days=20)

    with pytest.raises(
        quotes.QuoteError, match=r"line 1: only 1 quote\(s\) came in and 2 are wanted \(1 more did not qualify\)"
    ):
        quotes.award(shop, rfq, {1: "S1"}, BUYER, as_of=TODAY)

    done = quotes.award(shop, rfq, {1: "S1"}, BUYER, reason="S2 cannot deliver in time", as_of=TODAY)
    assert done.overridden == []  # S1 is the recommendation; the reason is for the missing quote
    assert shop.execute(text("select reason from award")).scalar_one() == "S2 cannot deliver in time"


def test_a_supplier_that_offers_only_part_cannot_be_selected_for_the_whole_line(shop: Session, outbox: Outbox) -> None:
    _, rfq = _one_line(shop, outbox)
    _quote_part(shop, rfq, "S1", "100", "3")
    _quote(shop, rfq, "S2", "120")

    with pytest.raises(quotes.QuoteError, match="S1 offers only 3 of 5 on line 1; split the line"):
        quotes.award(shop, rfq, {1: "S1"}, BUYER, reason="x", as_of=TODAY)
    for parts, message in (
        ([("S1", D(3)), ("S2", D(1))], "the split must add up to 5"),
        ([("S1", D(4)), ("S2", D(1))], "S1 cannot take 4"),
        ([("S1", D(5))], "two or more different suppliers"),
        ([("S1", D(3)), ("S4", D(2))], "S4 has not quoted line 1"),
    ):
        with pytest.raises(quotes.QuoteError, match=message):
            quotes.award(shop, rfq, {}, BUYER, reason="x", as_of=TODAY, splits={1: parts})


# ---- a split line and the orders that follow -----------------------------------------------------


def test_a_line_is_split_when_it_saves_and_one_order_goes_to_each_supplier(shop: Session, outbox: Outbox) -> None:
    # given S1 at 100 offers 3 of the 5 seals and S2 at 120 offers all 5, both delivered free
    request, rfq = _one_line(shop, outbox)
    _quote_part(shop, rfq, "S1", "100", "3")
    _quote(shop, rfq, "S2", "120")

    (line,) = _comp(shop, rfq).lines
    assert line.split_recommended and line.recommended is None
    assert line.why == (
        "Split S1 3 and S2 2: saves ₹ 60.00 on the goods, which beats the extra freight of ₹ 0.00 "
        "(single supplier S2 at ₹ 600.00)"
    )

    # when the buyer selects the split as shown, no reason is needed
    done = quotes.award(shop, rfq, {}, BUYER, as_of=TODAY, splits={1: [("S1", D(3)), ("S2", D(2))]})
    made = service.order_request(shop, shop.info["slug"], request, as_of=TODAY)

    # then each supplier gets an order for its part, at its own price
    assert done.awarded == {1: "S1, S2"} and done.overridden == [] and done.complete
    assert shop.execute(text("select supplier_code, qty, split from award order by supplier_code")).all() == [
        ("S1", D(3), True),
        ("S2", D(2), True),
    ]
    assert len(made.pos) == 2
    rows = shop.execute(
        text(
            "select p.supplier_code, l.qty, l.unit_price, l.taxable from po p join po_line l on l.po_no = p.no order by 1"
        )
    ).all()
    assert rows == [("S1", D(3), D(100), D("300.00")), ("S2", D(2), D(120), D("240.00"))]
    assert shop.execute(text("select status from request_line")).scalar_one() == "ordered"
    # a second ordering run makes nothing more
    assert service.order_request(shop, shop.info["slug"], request, as_of=TODAY).pos == []


def test_a_split_saving_exactly_the_extra_freight_is_not_recommended_and_needs_a_reason(
    shop: Session, outbox: Outbox
) -> None:
    # given S1's part saves 60 on the goods but brings 60 of freight
    _, rfq = _one_line(shop, outbox)
    _quote_part(shop, rfq, "S1", "100", "3", freight="60")
    _quote(shop, rfq, "S2", "120")

    (line,) = _comp(shop, rfq).lines

    assert line.split is not None and not line.split.worthwhile
    assert line.recommended == "S2" and "does not beat the extra freight of ₹ 60.00" in line.split.reason()
    parts = [("S1", D(3)), ("S2", D(2))]
    with pytest.raises(quotes.QuoteError, match="a reason is required"):
        quotes.award(shop, rfq, {}, BUYER, as_of=TODAY, splits={1: parts})
    done = quotes.award(shop, rfq, {}, BUYER, reason="Two sources for supply security", as_of=TODAY, splits={1: parts})
    assert done.overridden == [1]


def test_every_selection_and_rule_change_is_in_the_audit_log_with_the_ranking(shop: Session, outbox: Outbox) -> None:
    _, rfq = _one_line(shop, outbox)
    _quote(shop, rfq, "S1", "100")
    _quote(shop, rfq, "S2", "110")
    _quote(shop, rfq, "S3", "90", days=20)
    quotes.award(shop, rfq, {1: "S1"}, BUYER, as_of=TODAY)
    selection_rule.save_rule(shop, sod.user(1), effective_from=TODAY + timedelta(days=1), input_credit=False)

    after, rule = shop.execute(text("select after, rule_version from audit_event where action = 'rfq.award'")).one()
    change = shop.execute(
        text("select before, after from audit_event where action = 'supplier_selection.change'")
    ).one()

    assert rule == "supplier_selection v1"
    assert after["awards"]["1"] == [{"supplier": "S1", "qty": "5", "price": "100.0000", "quote": f"{rfq}-S1-R1"}]
    assert [(r["supplier"], r["rank"]) for r in after["ranking"]["1"]] == [("S1", 1), ("S2", 2), ("S3", None)]
    assert after["ranking"]["1"][2]["dropped"] == ["delivers 6 day(s) after it is needed"]
    assert after["why"]["1"].startswith("Lowest total cost among 2 qualifying offer(s)")
    assert change[0]["input_credit"] is True and change[1]["input_credit"] is False and change[1]["version"] == 2


def test_a_rule_change_applies_from_its_date_and_the_old_version_before_it(shop: Session) -> None:
    v = selection_rule.save_rule(shop, sod.user(1), effective_from=TODAY + timedelta(days=1), method="weighted")

    assert v == 2
    assert selection_rule.rule_for(shop, TODAY).method == "total_cost"
    assert selection_rule.rule_for(shop, TODAY + timedelta(days=1)).label == "supplier_selection v2"
    with pytest.raises(selection_rule.SelectionRuleError, match="tie-break"):
        selection_rule.save_rule(shop, sod.user(1), effective_from=TODAY, tie_break=("price",))


# ---- the printable RFQ ---------------------------------------------------------------------------


def test_the_printable_rfq_names_the_rfq_lines_need_by_date_delivery_place_terms_and_deadline(
    shop: Session, outbox: Outbox
) -> None:
    logins = _logins(shop)
    _, rfq = _one_line(shop, outbox)
    h = _headers(shop.info["slug"], logins["buyer"], "Buyer")

    got = client.get(f"/api/rfqs/{rfq}/suppliers/S2/pdf", headers=h)

    assert got.status_code == 200 and got.headers["content-type"] == "application/pdf"
    page = _pdf_text(got.content)
    due = shop.execute(text("select due_date from rfq")).scalar_one()
    for expected in (
        f"REQUEST FOR QUOTATION {rfq}",
        "Bravo Supplies",
        "Seal (B)",
        "20 Nov 2026",  # the need-by date
        "Bengaluru store",
        f"Quote by: {due:%d %b %Y}",
        "Delivery terms:",
        "Payment terms:",
        f"Quote the reference {rfq}",
    ):
        assert expected in page, expected
    assert client.get(f"/api/rfqs/{rfq}/suppliers/S4/pdf", headers=h).status_code == 404  # S4 was not asked
    assert client.get(f"/api/rfqs/{rfq}/suppliers/S2/pdf").status_code == 401


# ---- the worked example: Apex copper wire --------------------------------------------------------


def test_apex_copper_wire_goes_to_pune_copper_at_rs_36000_for_the_40_kg_shortfall(
    shop: Session, outbox: Outbox
) -> None:
    # given MO-1 needs 90 kg of copper wire and 50 kg are in stock: the request is for the 40 kg short (the route values
    # the 90 kg need at Rs 82,800; the 40 kg line at the standard Rs 920/kg is Rs 36,800, which still needs two quotes)
    _apex(shop)
    shop.execute(text("update supplier set trade_name = 'Pune Copper Co.' where code = 'S2'"))
    shop.execute(text("update supplier set trade_name = 'Shree Wires' where code = 'S3'"))
    shop.commit()
    _auto_send(shop)
    no = _ask(shop, "R-MO1", [("RM-08", "40", None)])
    (decision,) = _source(shop, no).lines
    assert (decision.route, decision.line_value, decision.min_quotes) == ("rfq", D("36800.00"), 2)
    rfq = str(decision.rfq_no)

    # when Pune Copper quotes Rs 900/kg in 8 days and Shree Wires Rs 915/kg in 5, and the third supplier declines
    _quote(shop, rfq, "S2", "900", days=8)
    _quote(shop, rfq, "S3", "915", days=5)
    shop.execute(text("update rfq_supplier set declined = true where supplier_code = 'S1'"))
    shop.commit()
    (line,) = _comp(shop, rfq).lines

    # then Pune Copper is recommended and selected at Rs 36,000, and its order follows with GST
    assert (line.recommended, line.offers[0].supplier_name, line.offers[0].cost) == ("S2", "Pune Copper Co.", D(36000))
    assert line.why.startswith("Lowest total cost among 2 qualifying offer(s): ₹ 36,000.00")
    quotes.award(shop, rfq, {1: "S2"}, BUYER, as_of=TODAY)
    made = service.order_request(shop, shop.info["slug"], no, as_of=TODAY)
    po = shop.execute(
        text("select supplier_code, taxable, cgst, sgst, total from po where request_no = :n"), {"n": no}
    ).one()
    assert made.pos and tuple(po) == ("S2", D("36000.00"), D("3240.00"), D("3240.00"), D("42480.00"))
