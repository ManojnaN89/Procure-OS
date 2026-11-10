"""Comparing quotes on what they cost to our door, asking about a price that looks too good, and the weighted comparison."""

from datetime import date
from decimal import Decimal

from sqlalchemy import text
from sqlalchemy.orm import Session
from test_orders import TODAY, _comp, _quote, _sent_rfq, shop  # noqa: F401 - fixtures
from test_sourcing import Outbox, _auto_send, _request, _source, db, outbox  # noqa: F401 - fixtures

from p2p.agents import autonomy
from p2p.orders import quotes, service
from p2p.platform import sod

D = Decimal


def _set(db: Session, key: str, value: str) -> None:
    db.execute(
        text("insert into org_setting (key, value) values (:k, :v) on conflict (key) do update set value = :v"),
        {"k": key, "v": value},
    )
    db.commit()


def test_a_dearer_unit_price_can_still_be_the_cheapest_once_freight_and_tax_are_counted(
    shop: Session,
    outbox: Outbox,  # noqa: F811
) -> None:
    _, rfq = _sent_rfq(shop, outbox)
    _quote(shop, rfq, "S1", "180", freight="100")  # 900 for 5, plus 100 to deliver
    _quote(shop, rfq, "S2", "190")  # 950 for 5, delivered

    line = _comp(shop, rfq).lines[0]

    s2, s1 = line.offers
    assert [o.supplier_code for o in line.offers] == ["S2", "S1"]  # sorted by landed cost
    assert (s2.value, s2.charges, s2.tax, s2.landed) == (D("950.00"), D("0.00"), D("171.00"), D("1121.00"))
    assert (s1.value, s1.charges, s1.tax, s1.landed) == (D("900.00"), D("100.00"), D("180.00"), D("1180.00"))
    assert line.recommended == "S2"
    assert line.why == "Lowest landed cost (price, freight and tax) among 2 compliant offer(s)"


def test_when_the_cheapest_unit_price_is_also_cheapest_landed_the_reason_says_price(
    shop: Session,
    outbox: Outbox,  # noqa: F811
) -> None:
    _, rfq = _sent_rfq(shop, outbox)
    _quote(shop, rfq, "S1", "180", freight="10")
    _quote(shop, rfq, "S2", "190")

    line = _comp(shop, rfq).lines[0]

    assert (line.recommended, line.why) == ("S1", "Lowest price among 2 compliant offer(s)")


def test_a_quotes_freight_is_shared_over_the_lines_it_prices_by_value_and_adds_up_exactly(
    shop: Session,
    outbox: Outbox,  # noqa: F811
) -> None:
    _auto_send(shop)
    request = _request(shop, "R-2", [("B", "5", None, "C1"), (None, "2", "Special gasket", "C1")])
    (rfq,) = _source(shop, request).rfqs
    quotes.enter_quote(
        shop,
        rfq,
        quotes.QuoteInput(
            supplier_code="S1",
            valid_until=date(2026, 12, 31),
            freight=D("100.01"),
            packing_forwarding=D("0"),
            lines=[
                quotes.QuoteLineInput(1, D("100"), D("18"), delivery_days=5),  # 500
                quotes.QuoteLineInput(2, D("250"), D("18"), delivery_days=5),  # 500
            ],
        ),
        sod.user(1),
    )

    lines = _comp(shop, rfq).lines

    shares = [o.charges for line in lines for o in line.offers]
    assert shares == [D("50.01"), D("50.00")]  # the odd paisa goes to the first line
    assert sum(shares) == D("100.01")


def test_a_price_far_below_the_other_quotes_is_a_question_and_the_reason_says_so(
    shop: Session,
    outbox: Outbox,  # noqa: F811
) -> None:
    _, rfq = _sent_rfq(shop, outbox)
    _quote(shop, rfq, "S1", "180")
    _quote(shop, rfq, "S2", "190")
    _quote(shop, rfq, "S3", "100", days=3)  # 44% below the median of the others

    line = _comp(shop, rfq).lines[0]

    cheapest = next(o for o in line.offers if o.supplier_code == "S3")
    assert line.recommended == "S3" and cheapest.compliant
    assert cheapest.questions and "below the other quotes" in cheapest.questions[0]
    assert "ask the supplier first" in line.why
    assert all(not o.questions for o in line.offers if o.supplier_code != "S3")


def test_a_price_just_inside_the_threshold_is_not_a_question(shop: Session, outbox: Outbox) -> None:  # noqa: F811
    _, rfq = _sent_rfq(shop, outbox)
    _quote(shop, rfq, "S1", "180")
    _quote(shop, rfq, "S2", "190")
    _quote(shop, rfq, "S3", "112", days=3)  # 40% below the median of 185 is 111; 112 is 39.5% below

    line = _comp(shop, rfq).lines[0]

    assert line.recommended == "S3" and not line.needs_question


def test_the_question_can_be_switched_off_by_a_threshold_of_zero(shop: Session, outbox: Outbox) -> None:  # noqa: F811
    _set(shop, "low_price_flag_pct", "0")
    _, rfq = _sent_rfq(shop, outbox)
    _quote(shop, rfq, "S1", "180")
    _quote(shop, rfq, "S2", "190")
    _quote(shop, rfq, "S3", "10", days=3)

    assert not _comp(shop, rfq).lines[0].needs_question


def test_a_recommendation_with_a_question_is_never_awarded_by_itself(shop: Session, outbox: Outbox) -> None:  # noqa: F811
    slug = shop.info["slug"]
    _, rfq = _sent_rfq(shop, outbox)
    _quote(shop, rfq, "S1", "180")
    _quote(shop, rfq, "S2", "190")
    _quote(shop, rfq, "S3", "100", days=3)
    autonomy.configure(shop, sod.user(1), "rfq.award", level="auto")

    assert quotes.auto_award(shop, slug, rfq, as_of=TODAY) is None  # everyone has answered, the gate is open, still no

    done = quotes.award(shop, rfq, {1: "S3"}, sod.user(7), as_of=TODAY)  # a person may award it
    assert done.awarded == {1: "S3"}


def test_a_price_far_below_the_last_price_paid_is_a_question_even_with_one_quote(
    shop: Session,
    outbox: Outbox,  # noqa: F811
) -> None:
    request, first = _sent_rfq(shop, outbox)
    _quote(shop, first, "S1", "200")
    quotes.award(shop, first, {1: "S1"}, sod.user(7), reason="Only quote", as_of=TODAY)
    service.order_request(shop, shop.info["slug"], request, as_of=TODAY)  # the last price paid for B is 200
    second_request = _request(shop, "R-3", [("B", "5", None, "C1")])
    (second,) = _source(shop, second_request).rfqs
    _quote(shop, second, "S2", "100")  # half of what we paid

    offer = _comp(shop, second).lines[0].offers[0]

    assert offer.last_price == D("200.0000")
    assert offer.questions == ["50.00% below the last price paid (200.00)"]


def test_the_weighted_comparison_recommends_the_best_score_and_says_how_it_was_scored(
    shop: Session,
    outbox: Outbox,  # noqa: F811
) -> None:
    _set(shop, "comparison_mode", "weighted")
    _set(shop, "score_weight_price", "100")
    _set(shop, "score_weight_delivery", "0")
    _set(shop, "score_weight_quality", "0")
    _, rfq = _sent_rfq(shop, outbox)
    _quote(shop, rfq, "S1", "180")
    _quote(shop, rfq, "S2", "200")

    line = _comp(shop, rfq).lines[0]

    s1, s2 = line.offers
    assert (s1.supplier_code, s1.score) == ("S1", D("100.0"))
    assert (s2.supplier_code, s2.score) == ("S2", D("90.0"))  # 900 over 1000
    assert line.recommended == "S1"
    assert (
        line.why
        == "Best score (100.0 of 100) among 2 compliant offer(s): price 100, delivery 70, quality 70; weights 100/0/0"
    )


def test_in_the_weighted_comparison_a_knocked_out_offer_is_never_recommended_however_well_it_scores(
    shop: Session,
    outbox: Outbox,  # noqa: F811
) -> None:
    _set(shop, "comparison_mode", "weighted")
    _, rfq = _sent_rfq(shop, outbox)
    _quote(shop, rfq, "S1", "180")
    _quote(shop, rfq, "S3", "50", days=20)  # far too late: knocked out

    line = _comp(shop, rfq).lines[0]

    assert line.recommended == "S1"
    knocked = next(o for o in line.offers if o.supplier_code == "S3")
    assert not knocked.compliant and knocked.score is None
    assert line.offers[-1].supplier_code == "S3"  # compliant offers first


def test_the_default_comparison_is_by_price_alone_so_no_scores_are_given(shop: Session, outbox: Outbox) -> None:  # noqa: F811
    _, rfq = _sent_rfq(shop, outbox)
    _quote(shop, rfq, "S1", "180")
    _quote(shop, rfq, "S2", "190")

    assert all(o.score is None for o in _comp(shop, rfq).lines[0].offers)
