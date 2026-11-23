"""Quote validity at selection and at the order (DF-33, task 12.6, block 6): an expired quote is never selected, even
with a reason; an award whose quote expires before its order is made lapses, the RFQ is out again and its buyer is told;
nothing is awarded to anyone else by itself; a quote with no validity date stays valid."""

from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session
from test_orders import _quote, _sent_rfq, shop  # noqa: F401 - fixtures
from test_sourcing import Outbox, db, outbox  # noqa: F401 - fixtures

from p2p.formulas.calendar import pin_today
from p2p.orders import quotes, service
from p2p.platform import sod

D = Decimal
SELECTED = date(2026, 11, 9)
LATER = date(2026, 11, 12)


def _three(db: Session, rfq: str, valid: str = "2026-11-10") -> None:
    _quote(db, rfq, "S1", "180", valid=valid)  # the cheapest, valid to 10 Nov
    _quote(db, rfq, "S2", "190")
    _quote(db, rfq, "S3", "195", days=5)


def test_an_expired_quote_is_refused_even_with_a_reason(shop: Session, outbox: Outbox) -> None:  # noqa: F811
    _, rfq = _sent_rfq(shop, outbox)
    _three(shop, rfq, valid="2026-11-05")

    with pytest.raises(quotes.QuoteError, match="S1's quote expired on 05 Nov 2026: ask for a renewed price before "
                                                "selecting it"):  # fmt: skip
        quotes.award(
            shop,
            rfq,
            {1: "S1"},
            sod.user(7),
            reason="best price",
            as_of=date(2026, 11, 6),
        )


def test_an_award_whose_quote_expired_before_its_order_lapses_and_the_buyer_decides_again(
    shop: Session,  # noqa: F811
    outbox: Outbox,  # noqa: F811
) -> None:
    request, rfq = _sent_rfq(shop, outbox, lines=[("B", "5", None, "C1")])
    pin_today(SELECTED)
    _three(shop, rfq)
    quotes.award(shop, rfq, {1: "S1"}, sod.user(7), as_of=SELECTED)
    pin_today(LATER)

    made = service.order_request(shop, shop.info["slug"], request, as_of=LATER)

    assert made.pos == []
    assert any("S1's quote" in s and "expired on 10 Nov 2026" in s for s in made.skipped)
    lapsed, why = shop.execute(text("select lapsed_at is not null, lapse_reason from award")).one()
    assert lapsed and why == f"Alpha Traders's quote {rfq}-S1-R1 expired on 10 Nov 2026 before the order was made"
    assert shop.execute(text("select status from rfq where no = :n"), {"n": rfq}).scalar_one() == "sent"
    task = shop.execute(text("select title, payload from task where kind = 'quote_expired'")).one()
    assert task[0] == f"{rfq} line 1: Alpha Traders's quote expired before the order was made"
    assert shop.execute(text("select count(*) from audit_event where action = 'award.lapsed'")).scalar_one() == 1
    assert shop.execute(text("select count(*) from award where lapsed_at is null")).scalar_one() == 0  # nobody else

    with pytest.raises(quotes.QuoteError, match="expired on 10 Nov 2026"):
        quotes.award(shop, rfq, {1: "S1"}, sod.user(7), reason="again", as_of=LATER)
    quotes.award(shop, rfq, {1: "S2"}, sod.user(7), reason="S1's price lapsed", as_of=LATER)
    again = service.order_request(shop, shop.info["slug"], request, as_of=LATER)
    assert len(again.pos) == 1
    assert shop.execute(text("select unit_price from po_line")).scalar_one() == D("190")


def test_a_quote_with_no_validity_date_is_ordered(shop: Session, outbox: Outbox) -> None:  # noqa: F811
    request, rfq = _sent_rfq(shop, outbox, lines=[("B", "5", None, "C1")])
    _three(shop, rfq, valid="")
    shop.execute(text("update quote set valid_until = null"))
    shop.commit()
    quotes.award(shop, rfq, {1: "S1"}, sod.user(7), as_of=SELECTED)

    made = service.order_request(shop, shop.info["slug"], request, as_of=date(2027, 6, 1))

    assert len(made.pos) == 1


def test_in_a_split_only_the_expired_part_lapses_and_the_other_is_ordered(shop: Session, outbox: Outbox) -> None:  # noqa: F811
    request, rfq = _sent_rfq(shop, outbox, lines=[("B", "10", None, "C1")])
    pin_today(SELECTED)
    _quote(shop, rfq, "S1", "180", valid="2026-11-10")
    _quote(shop, rfq, "S2", "190")
    quotes.award(shop, rfq, {}, sod.user(7), reason="share it", as_of=SELECTED,
                 splits={1: [("S1", D(6)), ("S2", D(4))]})  # fmt: skip
    pin_today(LATER)

    made = service.order_request(shop, shop.info["slug"], request, as_of=LATER)

    assert len(made.pos) == 1
    assert shop.execute(text("select qty from po_line")).scalar_one() == D(4)
    assert shop.execute(text("select supplier_code from award where lapsed_at is not null")).scalar_one() == "S1"
