"""Quotes, comparison, award and purchase orders: the money arithmetic, the rules for comparing and awarding, the order
that follows from an award or a contract, and the screens' data."""

from datetime import date
from decimal import Decimal
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session
from test_approvals import _make_logins, _org
from test_sourcing import Outbox, _auto_send, _price, _request, _source, db, outbox  # noqa: F401 - fixtures

from p2p.agents import autonomy
from p2p.formulas.contracts import ContractUse, ceiling_breach
from p2p.formulas.gst import Registration
from p2p.formulas.orders import (
    charge_rate,
    current_gst_rate,
    line_amounts,
    net_unit_price,
    order_totals,
    variance_pct,
)
from p2p.main import app
from p2p.orders import contracts as contract_usage
from p2p.orders import quotes, service, workflow
from p2p.platform import proposals, routing, sod, users
from p2p.platform.tokens import create_access_token
from p2p.receiving.stock import stock_levels

D = Decimal
client = TestClient(app)
TODAY = date(2026, 11, 6)  # a Friday
PASSWORD = "correct horse battery"


# ---- the arithmetic ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("price", "discount", "expected"),
    [
        ("100", "0", "100.0000"),
        ("100", "10", "90.0000"),
        ("99.99", "12.5", "87.4913"),  # 87.49125 rounds half up
        ("0.0001", "50", "0.0001"),  # 0.00005 rounds half up
        ("250", "100", "0.0000"),
    ],
)
def test_the_net_price_is_the_price_less_the_discount_to_four_decimals(
    price: str, discount: str, expected: str
) -> None:
    assert net_unit_price(D(price), D(discount)) == D(expected)


@pytest.mark.parametrize(("price", "discount"), [("-1", "0"), ("10", "-0.01"), ("10", "100.01")])
def test_a_negative_price_or_a_discount_outside_zero_to_hundred_is_refused(price: str, discount: str) -> None:
    with pytest.raises(ValueError):
        net_unit_price(D(price), D(discount))


@pytest.mark.parametrize(
    ("on", "expected"),
    [
        (date(2025, 9, 21), "12"),  # the day before the change
        (date(2025, 9, 22), "18"),  # the day of the change
        (date(2026, 11, 6), "18"),
    ],
)
def test_the_gst_rate_of_an_item_follows_its_change_date(on: date, expected: str) -> None:
    assert current_gst_rate(D(12), D(18), date(2025, 9, 22), on) == D(expected)


def test_an_item_with_no_change_date_keeps_its_first_rate_and_one_with_no_rate_has_none() -> None:
    assert current_gst_rate(D(18), D(18), None, TODAY) == D(18)
    assert current_gst_rate(D(5), None, None, TODAY) == D(5)
    assert current_gst_rate(None, None, None, TODAY) is None


@pytest.mark.parametrize(
    ("qty", "price", "rate", "supplier", "place", "registration", "taxable", "cgst", "sgst", "igst"),
    [
        # same state: half each; different state: all IGST
        ("10", "100", "18", "29", "29", Registration.REGULAR, "1000.00", "90.00", "90.00", "0.00"),
        ("10", "100", "18", "27", "29", Registration.REGULAR, "1000.00", "0.00", "0.00", "180.00"),
        # paise: 3 x 33.33 = 99.99; 12% is 11.9988; each half 5.9994 rounds to 6.00
        ("3", "33.33", "12", "29", "29", Registration.REGULAR, "99.99", "6.00", "6.00", "0.00"),
        ("3", "33.33", "12", "27", "29", Registration.REGULAR, "99.99", "0.00", "0.00", "12.00"),
        # a quantity with decimals and a price with four
        ("2.5", "87.4913", "5", "29", "29", Registration.REGULAR, "218.73", "5.47", "5.47", "0.00"),
        # a composition or unregistered supplier cannot charge GST
        ("10", "100", "18", "29", "29", Registration.COMPOSITION, "1000.00", "0.00", "0.00", "0.00"),
        ("10", "100", "18", "27", "29", Registration.UNREGISTERED, "1000.00", "0.00", "0.00", "0.00"),
        ("10", "100", "0", "27", "29", Registration.REGULAR, "1000.00", "0.00", "0.00", "0.00"),
    ],
)
def test_a_line_is_taxed_by_the_states_and_the_suppliers_registration(
    qty: str,
    price: str,
    rate: str,
    supplier: str,
    place: str,
    registration: Registration,
    taxable: str,
    cgst: str,
    sgst: str,
    igst: str,
) -> None:
    got = line_amounts(D(qty), D(price), D(rate), supplier, place, registration)

    assert (got.taxable, got.cgst, got.sgst, got.igst) == (D(taxable), D(cgst), D(sgst), D(igst))
    assert got.total == got.taxable + got.cgst + got.sgst + got.igst


def test_freight_takes_the_highest_rate_among_the_goods_and_totals_add_up() -> None:
    assert charge_rate([D(5), D(18), D(12)]) == D(18)
    assert charge_rate([]) == D(0)
    parts = [line_amounts(10, 100, 18, "27", "29"), line_amounts(1, 50, 18, "27", "29")]
    totals = order_totals(parts)
    assert (totals.taxable, totals.igst, totals.total) == (D("1050.00"), D("189.00"), D("1239.00"))


@pytest.mark.parametrize(
    ("price", "estimate", "expected"),
    [
        ("115", "100", "15.00"),
        ("115.01", "100", "15.01"),
        ("90", "100", "-10.00"),
        ("100", "100", "0.00"),
        ("5", "0", None),
    ],
)
def test_the_variance_is_how_far_the_price_is_above_the_estimate(
    price: str, estimate: str, expected: str | None
) -> None:
    assert variance_pct(D(price), D(estimate)) == (D(expected) if expected else None)


# ---- a company with suppliers in two states ----------------------------------------------------

PO_RULES = [
    ("PO-01", "po", "0", "500000", "procurement_head", 1),
    ("PO-02", "po", "500000.01", "2500000", "procurement_head>cfo", 1),
    ("PO-03", "po", "2500000.01", "999999999", "procurement_head>cfo>director", 1),
]


def _setup(db: Session) -> None:
    """The sourcing world plus states, GST registrations, item rates, the organisation and the purchase order rules."""
    x = db.execute
    x(text("insert into payment_terms (code, name, days) values ('NET30', 'Net 30', 30), ('NET45', 'Net 45', 45)"))
    x(
        text(
            "insert into number_series (document_type, prefix, scope, format, reset) "
            "values ('po', 'PO', 'location', '{prefix}-{loc}-{fy4}-{seq:05d}', 'fy')"
        )
    )
    x(
        text(
            "update supplier set state_code = '27', gstin = '27AAAAA0000A1Z5', gst_reg_type = 'regular', payment_terms_code = 'NET30' where code = 'S1'"
        )
    )
    x(
        text(
            "update supplier set state_code = '29', gstin = '29CCCCC0000C1Z5', gst_reg_type = 'regular' where code = 'S2'"
        )
    )
    x(
        text(
            "update supplier set state_code = '29', gstin = '29DDDDD0000D1Z5', gst_reg_type = 'composition' where code = 'S3'"
        )
    )
    for code in ("S1", "S2"):  # registered for GST, so the certificate is a required document
        x(
            text(
                "insert into supplier_document (supplier_code, doc_type, number, issued_on, verified) "
                "values (:c, 'gst_cert', 'G', '2020-01-01', true)"
            ),
            {"c": code},
        )
    x(text("update location set gstin = '29BBBBB0000B1Z5', state_code = '29' where code = 'WH-BLR'"))
    x(text("update item set gst_rate_before = 18, gst_rate_after = 18 where code in ('A', 'D', 'R')"))
    x(
        text(
            "update item set gst_rate_before = 12, gst_rate_after = 18, gst_rate_change_date = '2025-09-22' where code = 'B'"
        )
    )
    x(
        text(
            "update contract set payment_terms_code = 'NET45', delivery_terms = 'for_destination' where code = 'K-ACTIVE'"
        )
    )
    _org(db, db.info["sfx"])
    for code, kind, low, high, chain, version in PO_RULES:
        x(
            text(
                "insert into approval_rule (rule_code, document, min_value, max_value, category_code, department_code, "
                "spend_type, approver_chain, version, effective_from) values (:c, :k, :lo, :hi, 'ANY', 'ANY', 'ANY', :ch, :v, '2024-11-01')"
            ),
            {"c": code, "k": kind, "lo": low, "hi": high, "ch": chain, "v": version},
        )
    db.commit()


def _logins(db: Session) -> dict[str, int]:
    slug, sfx = db.info["slug"], db.info["sfx"]
    made = _make_logins(slug, sfx)  # stores head, director, cfo and a storekeeper
    procurement = users.create_user(slug, f"pur{sfx}@x.example", PASSWORD, "Procure Vikram", "Approver")
    buyer = users.create_user(slug, f"buyer{sfx}@x.example", PASSWORD, "A Buyer", "Buyer")
    return {
        "stores": made.stores,
        "director": made.director,
        "cfo": made.cfo,
        "procurement": procurement,
        "buyer": buyer,
        "keeper": made.keeper,
    }


@pytest.fixture
def shop(db: Session) -> Session:
    _setup(db)
    return db


def _quote(
    db: Session,
    rfq: str,
    supplier: str,
    price: str,
    *,
    line: int = 1,
    gst: str = "18",
    discount: str = "0",
    days: int | None = 5,
    valid: str | None = "2026-12-31",
    freight: str = "0",
    packing: str = "0",
    moq: str | None = None,
) -> str:
    return quotes.enter_quote(
        db,
        rfq,
        quotes.QuoteInput(
            supplier_code=supplier,
            valid_until=date.fromisoformat(valid) if valid else None,
            freight=D(freight),
            packing_forwarding=D(packing),
            lines=[
                quotes.QuoteLineInput(
                    rfq_line_no=line,
                    unit_price=D(price),
                    gst_rate=D(gst),
                    discount_pct=D(discount),
                    delivery_days=days,
                    moq=D(moq) if moq else None,
                )
            ],
        ),
        sod.user(1),
    )


def _sent_rfq(db: Session, outbox: Outbox, no: str = "R-1", lines: list | None = None) -> tuple[str, str]:
    """An approved request, sourced: line A on contract, line B out to quotation with three suppliers (auto-sent)."""
    _auto_send(db)
    _price(db, "S1", "A", "100")
    request = _request(db, no, lines or [("A", "10", None, "C1"), ("B", "5", None, "C1")])
    result = _source(db, request)
    return request, result.rfqs[0]


def _comp(db: Session, rfq: str, on: date = TODAY) -> quotes.Comparison:
    return quotes.comparison(db, rfq, on)


# ---- entering quotes --------------------------------------------------------------------------


def test_a_quote_is_stored_as_a_revision_and_marks_the_supplier_as_replied(shop: Session, outbox: Outbox) -> None:
    _, rfq = _sent_rfq(shop, outbox)

    first = _quote(shop, rfq, "S1", "200")
    second = _quote(shop, rfq, "S1", "190")  # a negotiation round

    assert (first, second) == (f"{rfq}-S1-R1", f"{rfq}-S1-R2")
    assert (
        shop.execute(text("select responded_at is not null from rfq_supplier where supplier_code = 'S1'")).scalar_one()
        is True
    )
    assert (
        shop.execute(text("select responded_at is null from rfq_supplier where supplier_code = 'S2'")).scalar_one()
        is True
    )
    assert shop.execute(text("select count(*) from audit_event where action = 'quote.enter'")).scalar_one() == 2
    # only the latest revision is compared
    (line,) = _comp(shop, rfq).lines
    assert [(o.supplier_code, o.revision, o.unit_price) for o in line.offers] == [("S1", 2, D("190"))]


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"price": "0"}, "the price must be above zero"),
        ({"price": "-5"}, "the price must be above zero"),
        ({"discount": "100.5"}, "discount must be between 0 and 100"),
        ({"gst": "101"}, "GST rate must be between 0 and 100"),
        ({"gst": "-1"}, "GST rate must be between 0 and 100"),
        ({"days": -2}, "delivery days must not be negative"),
        ({"freight": "-1"}, "must not be negative"),
        ({"line": 9}, "line 9 is not on"),
    ],
)
def test_a_quote_with_nonsense_numbers_is_refused_and_nothing_is_stored(
    shop: Session, outbox: Outbox, change: dict[str, Any], message: str
) -> None:
    _, rfq = _sent_rfq(shop, outbox)
    args: dict[str, Any] = {"price": "100", **change}
    price = args.pop("price")

    with pytest.raises(quotes.QuoteError, match=message):
        _quote(shop, rfq, "S1", price, **args)

    assert shop.execute(text("select count(*) from quote")).scalar_one() == 0


def test_only_a_supplier_that_was_asked_may_quote_and_only_while_the_rfq_is_out(shop: Session, outbox: Outbox) -> None:
    _, rfq = _sent_rfq(shop, outbox)

    with pytest.raises(quotes.QuoteError, match="was not asked"):
        _quote(shop, rfq, "S4", "100")
    with pytest.raises(quotes.QuoteError, match="does not exist"):
        _quote(shop, "NOPE", "S1", "100")
    shop.execute(text("update rfq set status = 'cancelled'"))
    shop.commit()
    with pytest.raises(quotes.QuoteError, match="cancelled"):
        _quote(shop, rfq, "S1", "100")


def test_a_line_priced_twice_or_a_quote_with_no_lines_is_refused(shop: Session, outbox: Outbox) -> None:
    _, rfq = _sent_rfq(shop, outbox)
    line = quotes.QuoteLineInput(rfq_line_no=1, unit_price=D(10), gst_rate=D(18))

    with pytest.raises(quotes.QuoteError, match="priced twice"):
        quotes.enter_quote(shop, rfq, quotes.QuoteInput(supplier_code="S1", lines=[line, line]), sod.user(1))
    with pytest.raises(quotes.QuoteError, match="at least one"):
        quotes.enter_quote(shop, rfq, quotes.QuoteInput(supplier_code="S1", lines=[]), sod.user(1))


# ---- comparing --------------------------------------------------------------------------------


def test_the_cheapest_compliant_offer_is_recommended_and_a_cheaper_late_one_is_not(
    shop: Session, outbox: Outbox
) -> None:
    _, rfq = _sent_rfq(shop, outbox)
    _quote(shop, rfq, "S1", "200", discount="10", days=5)  # net 180.0000
    _quote(shop, rfq, "S2", "190", days=3)
    _quote(shop, rfq, "S3", "150", days=20)  # the need date is 14 days away: 6 days late

    (line,) = _comp(shop, rfq).lines

    by_supplier = {o.supplier_code: o for o in line.offers}
    assert [o.supplier_code for o in line.offers] == ["S1", "S2", "S3"]  # the qualifying by cost, then the dropped
    assert by_supplier["S1"].net_price == D("180.0000") and by_supplier["S1"].value == D("900.00")
    assert by_supplier["S3"].flags == ["delivers 6 days after it is needed"]
    assert line.recommended == "S1" and line.why.startswith("Lowest total cost among 2 qualifying offers: ₹ 900.00")


def test_equal_prices_are_decided_by_the_faster_delivery_then_the_supplier_code(shop: Session, outbox: Outbox) -> None:
    _, rfq = _sent_rfq(shop, outbox)
    _quote(shop, rfq, "S1", "100", days=9)
    _quote(shop, rfq, "S2", "100", days=4)
    _quote(shop, rfq, "S3", "100", days=4)

    (line,) = _comp(shop, rfq).lines

    assert line.recommended == "S2"  # S2 and S3 deliver in 4 days; the lower code wins


@pytest.mark.parametrize(
    ("kwargs", "flag"),
    [
        ({"valid": "2026-11-05"}, "expired on 05 Nov 2026"),
        ({"moq": "6"}, "below the supplier's minimum order of 6"),
        ({"gst": "12"}, "GST rate 12% differs from the expected 18%"),
        ({"days": 15}, "delivers 1 day after it is needed"),
    ],
)
def test_each_reason_an_offer_does_not_comply_is_named(
    shop: Session, outbox: Outbox, kwargs: dict[str, Any], flag: str
) -> None:
    _, rfq = _sent_rfq(shop, outbox)
    _quote(shop, rfq, "S1", "100", **kwargs)

    (line,) = _comp(shop, rfq).lines

    assert line.offers[0].flags == [flag]
    assert line.recommended is None and line.why == "No offer qualifies; a buyer has to decide"


@pytest.mark.parametrize(
    ("kwargs", "complies"),
    [
        ({"valid": "2026-11-06"}, True),  # valid through the day itself
        ({"moq": "5"}, True),  # the minimum equals the quantity
        ({"days": 14}, True),  # arrives exactly on the need date
    ],
)
def test_the_edges_of_each_rule_still_comply(
    shop: Session, outbox: Outbox, kwargs: dict[str, Any], complies: bool
) -> None:
    _, rfq = _sent_rfq(shop, outbox)
    _quote(shop, rfq, "S1", "100", **kwargs)

    assert _comp(shop, rfq).lines[0].offers[0].compliant is complies


def test_the_comparison_says_who_has_not_answered_and_counts_the_quotes(shop: Session, outbox: Outbox) -> None:
    _, rfq = _sent_rfq(shop, outbox)
    _quote(shop, rfq, "S1", "100")

    comp = _comp(shop, rfq)

    assert (comp.quotes_received, comp.invited, comp.min_quotes) == (1, 3, 2)
    assert comp.waiting_for == ["S2", "S3"]
    assert _comp(shop, rfq).lines[0].why.startswith("Lowest total cost among 1 qualifying offer")
    shop.execute(text("update rfq_supplier set declined = true where supplier_code = 'S2'"))
    shop.commit()
    assert _comp(shop, rfq).waiting_for == ["S3"]  # a supplier that said no is no longer waited for


# ---- awarding ---------------------------------------------------------------------------------


def _three_quotes(db: Session, rfq: str) -> None:
    # S1 is cheaper per unit (180 net) and carries 75.50 of freight and packing; S2 at 200 is still dearer landed
    _quote(db, rfq, "S1", "200", discount="10", freight="50", packing="25.50")
    _quote(db, rfq, "S2", "200")
    _quote(db, rfq, "S3", "150", days=20)


def test_awarding_the_recommendation_needs_no_reason_and_stores_the_net_price(shop: Session, outbox: Outbox) -> None:
    _, rfq = _sent_rfq(shop, outbox)
    _three_quotes(shop, rfq)

    done = quotes.award(shop, rfq, {1: "S1"}, sod.user(7), as_of=TODAY)

    assert (done.awarded, done.overridden, done.complete) == ({1: "S1"}, [], True)
    row = shop.execute(text("select supplier_code, quote_no, qty, price, reason, overridden, by_user from award")).one()
    assert tuple(row[:4]) == ("S1", f"{rfq}-S1-R1", D("5"), D("180")) and row[5:] == (False, "7")
    assert row[4].startswith("Lowest total cost among 2 qualifying offers")  # the comparison's own reason
    assert shop.execute(text("select status from rfq")).scalar_one() == "awarded"
    assert shop.execute(text("select count(*) from audit_event where action = 'rfq.award'")).scalar_one() == 1


def test_choosing_another_supplier_needs_a_reason_and_is_marked_as_an_override(shop: Session, outbox: Outbox) -> None:
    _, rfq = _sent_rfq(shop, outbox)
    _three_quotes(shop, rfq)

    with pytest.raises(quotes.QuoteError, match="a reason is required: a choice is not the recommended"):
        quotes.award(shop, rfq, {1: "S2"}, sod.user(7), as_of=TODAY)
    assert shop.execute(text("select count(*) from award")).scalar_one() == 0

    done = quotes.award(shop, rfq, {1: "S2"}, sod.user(7), reason="Closer to the plant", as_of=TODAY)

    assert done.overridden == [1]
    assert shop.execute(text("select reason, overridden from award")).one() == ("Closer to the plant", True)


def test_a_non_compliant_offer_can_only_be_awarded_with_a_reason(shop: Session, outbox: Outbox) -> None:
    _, rfq = _sent_rfq(shop, outbox)
    _three_quotes(shop, rfq)

    with pytest.raises(quotes.QuoteError, match="reason is required"):
        quotes.award(shop, rfq, {1: "S3"}, sod.user(7), as_of=TODAY)  # S3 is cheapest but late


def test_too_few_quotes_need_a_reason_even_for_the_recommendation(shop: Session, outbox: Outbox) -> None:
    _, rfq = _sent_rfq(shop, outbox)
    _quote(shop, rfq, "S1", "100")  # one quote; the category wants two

    with pytest.raises(quotes.QuoteError, match="only 1 quote came in and 2 are wanted"):
        quotes.award(shop, rfq, {1: "S1"}, sod.user(7), as_of=TODAY)
    quotes.award(shop, rfq, {1: "S1"}, sod.user(7), reason="The only supplier that stocks it", as_of=TODAY)
    assert shop.execute(text("select reason from award")).scalar_one() == "The only supplier that stocks it"


def test_an_award_must_name_a_supplier_that_quoted_the_line_and_cannot_be_repeated(
    shop: Session, outbox: Outbox
) -> None:
    _, rfq = _sent_rfq(shop, outbox)
    _three_quotes(shop, rfq)

    with pytest.raises(quotes.QuoteError, match="S4 has not quoted"):
        quotes.award(shop, rfq, {1: "S4"}, sod.user(7), reason="x", as_of=TODAY)
    with pytest.raises(quotes.QuoteError, match="not on"):
        quotes.award(shop, rfq, {9: "S1"}, sod.user(7), as_of=TODAY)
    with pytest.raises(quotes.QuoteError, match="at least one line"):
        quotes.award(shop, rfq, {}, sod.user(7), as_of=TODAY)
    quotes.award(shop, rfq, {1: "S1"}, sod.user(7), as_of=TODAY)
    with pytest.raises(quotes.QuoteError, match="awarded"):
        quotes.award(shop, rfq, {1: "S1"}, sod.user(7), as_of=TODAY)  # the RFQ is no longer out


def test_awarding_some_lines_leaves_the_rfq_open_for_the_rest(shop: Session, outbox: Outbox) -> None:
    _auto_send(shop)
    request = _request(shop, "R-2", [("B", "5", None, "C1"), (None, "2", "Special gasket", "C1")])
    (rfq,) = _source(shop, request).rfqs
    _quote(shop, rfq, "S1", "100", line=1)
    _quote(shop, rfq, "S2", "110", line=1)

    done = quotes.award(shop, rfq, {1: "S1"}, sod.user(7), as_of=TODAY)

    assert done.complete is False
    assert shop.execute(text("select status from rfq")).scalar_one() == "sent"  # line 2 still waits for quotes


# ---- awarding without a person ----------------------------------------------------------------


def test_the_recommendation_is_awarded_by_itself_only_when_the_gate_allows_and_everyone_has_answered(
    shop: Session, outbox: Outbox
) -> None:
    slug = shop.info["slug"]
    _, rfq = _sent_rfq(shop, outbox)
    _quote(shop, rfq, "S1", "180")
    _quote(shop, rfq, "S2", "190")
    assert quotes.auto_award(shop, slug, rfq, as_of=TODAY) is None  # set to propose by default, and S3 has not answered

    autonomy.configure(shop, sod.user(1), "rfq.award", level="auto")
    assert quotes.auto_award(shop, slug, rfq, as_of=TODAY) is None  # S3 is still silent
    shop.execute(text("update rfq_supplier set declined = true where supplier_code = 'S3'"))
    shop.commit()

    done = quotes.auto_award(shop, slug, rfq, as_of=TODAY)

    assert done is not None and done.awarded == {1: "S1"}
    row = shop.execute(text("select by_user, reason from award")).one()
    assert row[0] == "service:award" and row[1].startswith("Lowest total cost among 2 qualifying offers")
    assert (
        shop.execute(
            text("select count(*) from autonomy_outcome where decision_type = 'rfq.award' and auto")
        ).scalar_one()
        == 1
    )


def test_nothing_is_awarded_by_itself_when_any_line_has_no_compliant_offer_or_the_kill_switch_is_on(
    shop: Session, outbox: Outbox
) -> None:
    slug = shop.info["slug"]
    autonomy.configure(shop, sod.user(1), "rfq.award", level="auto")
    _, rfq = _sent_rfq(shop, outbox)
    _quote(shop, rfq, "S1", "100", days=30)  # too late
    _quote(shop, rfq, "S2", "100", days=30)
    shop.execute(text("update rfq_supplier set declined = true where supplier_code = 'S3'"))
    shop.commit()
    assert quotes.auto_award(shop, slug, rfq, as_of=TODAY) is None  # no compliant offer

    _quote(shop, rfq, "S1", "105", days=3)  # compliant revisions: two quotes qualify, as the route wants
    _quote(shop, rfq, "S2", "100", days=3)
    autonomy.set_kill_switch(shop, sod.user(1), True, "incident")
    assert quotes.auto_award(shop, slug, rfq, as_of=TODAY) is None
    autonomy.set_kill_switch(shop, sod.user(1), False, "over")
    assert quotes.auto_award(shop, slug, rfq, as_of=TODAY) is not None


# ---- from award or contract to a purchase order -----------------------------------------------


def _ordered(shop: Session, outbox: Outbox) -> tuple[str, str, service.OrderResult]:
    """Line A on contract and line B awarded to S1 (Maharashtra) for delivery to Bengaluru (Karnataka)."""
    request, rfq = _sent_rfq(shop, outbox)
    _three_quotes(shop, rfq)
    # S1's quote carries the contract's terms: lines on the same terms share one order (see the test on differing terms)
    shop.execute(
        text(
            "update quote set payment_terms_code = 'NET45', delivery_terms = 'for_destination' where supplier_code = 'S1'"
        )
    )
    shop.commit()
    quotes.award(shop, rfq, {1: "S1"}, sod.user(7), as_of=TODAY)
    return request, rfq, service.order_request(shop, shop.info["slug"], request, as_of=TODAY)


def test_one_order_per_supplier_carries_contract_and_awarded_lines_with_gst_worked_out_by_hand(
    shop: Session, outbox: Outbox
) -> None:
    request, rfq, made = _ordered(shop, outbox)

    assert made.pos == ["PO-BLR-2627-00001"] and made.skipped == []
    po = shop.execute(text("select * from po")).mappings().one()
    # S1 is in state 27 and the goods go to state 29: all IGST.
    # line A: 10 x 100.00 = 1000.00, 18% = 180.00;  line B: 5 x 180.0000 = 900.00, 18% = 162.00
    # freight 50.00 and packing 25.50 at 18%: 9.00 and 4.59
    assert (po["taxable"], po["cgst"], po["sgst"], po["igst"], po["total"]) == (
        D("1975.50"),
        D("0.00"),
        D("0.00"),
        D("355.59"),
        D("2331.09"),
    )
    assert (po["supplier_code"], po["supplier_gstin"], po["buyer_gstin"], po["ship_to_location"]) == (
        "S1",
        "27AAAAA0000A1Z5",
        "29BBBBB0000B1Z5",
        "WH-BLR",
    )
    assert (po["po_type"], po["contract_code"], po["rfq_no"], po["request_no"]) == ("standard", None, rfq, request)
    assert (po["payment_terms_code"], po["delivery_terms"]) == (
        "NET45",
        "for_destination",
    )  # the terms both lines share
    lines = {r["item_code"]: r for r in shop.execute(text("select * from po_line order by line_no")).mappings()}
    a, b = lines["A"], lines["B"]
    assert (a["qty"], a["unit_price"], a["gst_rate"], a["taxable"], a["igst"], a["source"], a["source_ref"]) == (
        D("10"),
        D("100"),
        D("18"),
        D("1000.00"),
        D("180.00"),
        "contract",
        "K-ACTIVE",
    )
    assert (b["qty"], b["unit_price"], b["taxable"], b["igst"], b["source"], b["source_ref"]) == (
        D("5"),
        D("180"),
        D("900.00"),
        D("162.00"),
        "award",
        f"{rfq}-S1-R1",
    )
    charges = [
        (c["type"], c["amount"], c["gst_rate"], c["igst"])
        for c in shop.execute(text("select * from po_charge order by id")).mappings()
    ]
    assert charges == [
        ("freight", D("50.00"), D("18"), D("9.00")),
        ("packing_forwarding", D("25.50"), D("18"), D("4.59")),
    ]


def test_the_total_of_an_order_equals_its_lines_plus_its_charges_plus_tax(shop: Session, outbox: Outbox) -> None:
    _ordered(shop, outbox)

    po = shop.execute(text("select taxable, cgst, sgst, igst, total from po")).one()
    lines = shop.execute(
        text(
            "select coalesce(sum(taxable), 0), coalesce(sum(cgst), 0), coalesce(sum(sgst), 0), coalesce(sum(igst), 0) from po_line"
        )
    ).one()
    charges = shop.execute(
        text(
            "select coalesce(sum(amount), 0), coalesce(sum(cgst), 0), coalesce(sum(sgst), 0), coalesce(sum(igst), 0) from po_charge"
        )
    ).one()
    assert po[0] == lines[0] + charges[0]
    assert (po[1], po[2], po[3]) == (lines[1] + charges[1], lines[2] + charges[2], lines[3] + charges[3])
    assert po[4] == po[0] + po[1] + po[2] + po[3]


def test_a_supplier_in_the_same_state_is_charged_cgst_and_sgst(shop: Session, outbox: Outbox) -> None:
    request, rfq = _sent_rfq(shop, outbox, lines=[("B", "5", None, "C1")])
    _three_quotes(shop, rfq)
    quotes.award(
        shop, rfq, {1: "S2"}, sod.user(7), reason="Local supplier", as_of=TODAY
    )  # S2 is in state 29, like the store

    made = service.order_request(shop, shop.info["slug"], request, as_of=TODAY)

    po = shop.execute(text("select taxable, cgst, sgst, igst, total, supplier_code from po")).one()
    assert made.pos and tuple(po) == (
        D("1000.00"),
        D("90.00"),
        D("90.00"),
        D("0.00"),
        D("1180.00"),
        "S2",
    )  # 5 x 200, 9% + 9%


def test_a_composition_supplier_charges_no_gst_at_all(shop: Session, outbox: Outbox) -> None:
    request, rfq = _sent_rfq(shop, outbox, lines=[("B", "5", None, "C1")])
    _three_quotes(shop, rfq)
    quotes.award(shop, rfq, {1: "S3"}, sod.user(7), reason="Cheapest, delivery agreed by phone", as_of=TODAY)

    service.order_request(shop, shop.info["slug"], request, as_of=TODAY)

    assert shop.execute(text("select taxable, cgst, sgst, igst, total from po")).one() == (
        D("750.00"),
        D("0.00"),
        D("0.00"),
        D("0.00"),
        D("750.00"),
    )


def test_lines_with_an_unknown_state_or_gst_rate_are_not_ordered_and_say_why(shop: Session, outbox: Outbox) -> None:
    request, rfq = _sent_rfq(shop, outbox)
    shop.execute(text("update supplier set state_code = null, gstin = null where code = 'S1'"))  # no GST details
    shop.execute(text("update item set gst_rate_before = null, gst_rate_after = null where code = 'A'"))
    shop.commit()
    _three_quotes(shop, rfq)
    quotes.award(shop, rfq, {1: "S1"}, sod.user(7), reason="GSTIN to follow", as_of=TODAY)  # S1 fails a must-have

    made = service.order_request(shop, shop.info["slug"], request, as_of=TODAY)

    assert made.pos == []
    assert any("the item has no GST rate" in n for n in made.skipped)
    assert any("state of the supplier" in n for n in made.skipped)
    assert shop.execute(text("select count(*) from po")).scalar_one() == 0
    assert shop.execute(text("select status from request_line order by line_no")).scalars().all() == [
        "sourcing",
        "sourcing",
    ]


def test_a_request_bought_entirely_on_one_contract_makes_a_contract_release(shop: Session, outbox: Outbox) -> None:
    _price(shop, "S1", "A", "100")
    _price(shop, "S1", "D", "40")
    request = _request(shop, "R-3", [("A", "10", None, "C1"), ("D", "5", None, "C2")])
    _source(shop, request, dispatch=False)

    made = service.order_request(shop, shop.info["slug"], request, as_of=TODAY)

    po = shop.execute(text("select po_type, contract_code, rfq_no, payment_terms_code, delivery_terms from po")).one()
    assert po == ("contract_release", "K-ACTIVE", None, "NET45", "for_destination")  # the contract's own terms
    assert made.pos == ["PO-BLR-2627-00001"]
    assert (
        shop.execute(text("select status from request where no = 'R-3'")).scalar_one() == "closed"
    )  # every line is ordered


def test_ordering_again_makes_no_second_order_and_a_request_with_open_lines_stays_open(
    shop: Session, outbox: Outbox
) -> None:
    request, _ = _sent_rfq(shop, outbox)  # line B is still out for quotation

    first = service.order_request(shop, shop.info["slug"], request, as_of=TODAY)
    again = service.order_request(shop, shop.info["slug"], request, as_of=TODAY)

    assert first.pos == ["PO-BLR-2627-00001"] and again.pos == []
    assert shop.execute(text("select count(*) from po")).scalar_one() == 1
    assert shop.execute(text("select status from request where no = :n"), {"n": request}).scalar_one() == "approved"
    assert shop.execute(text("select status from request_line order by line_no")).scalars().all() == [
        "ordered",
        "sourcing",
    ]


def test_lines_awarded_to_two_suppliers_make_two_orders(shop: Session, outbox: Outbox) -> None:
    _auto_send(shop)
    request = _request(shop, "R-4", [("B", "5", None, "C1"), (None, "2", "Special gasket", "C1")])
    (rfq,) = _source(shop, request).rfqs
    _quote(shop, rfq, "S1", "100", line=1)
    _quote(shop, rfq, "S2", "110", line=1)
    quotes.enter_quote(
        shop,
        rfq,
        quotes.QuoteInput(
            supplier_code="S2",
            lines=[quotes.QuoteLineInput(rfq_line_no=2, unit_price=D(60), gst_rate=D(18), delivery_days=3)],
        ),
        sod.user(1),
    )
    quotes.award(shop, rfq, {1: "S1"}, sod.user(7), reason="One quote on line 2 only; award line 1 first", as_of=TODAY)
    quotes.award(shop, rfq, {2: "S2"}, sod.user(7), reason="Only quote", as_of=TODAY)

    made = service.order_request(shop, shop.info["slug"], request, as_of=TODAY)

    assert made.pos == ["PO-BLR-2627-00001", "PO-BLR-2627-00002"]
    assert shop.execute(text("select supplier_code from po order by no")).scalars().all() == ["S1", "S2"]


def test_two_orderings_at_the_same_moment_still_make_one_order(shop: Session, outbox: Outbox) -> None:
    from concurrent.futures import ThreadPoolExecutor

    slug = shop.info["slug"]
    request, _ = _sent_rfq(shop, outbox)

    def go() -> int:
        with Session(routing.engine_for(slug)) as mine:
            return len(service.order_request(mine, slug, request, as_of=TODAY).pos)

    with ThreadPoolExecutor(2) as pool:
        made = sorted(f.result() for f in [pool.submit(go), pool.submit(go)])

    assert made == [0, 1]
    assert shop.execute(text("select count(*) from po")).scalar_one() == 1


def test_every_order_is_in_the_audit_log_with_its_amounts(shop: Session, outbox: Outbox) -> None:
    _ordered(shop, outbox)

    (created,) = shop.execute(text("select after from audit_event where action = 'po.create'")).scalars().all()
    assert created["supplier"] == "S1" and created["total"] == "2331.09" and created["place_of_supply"] == "29"
    assert created["supplier_state"] == "27" and created["lines"] == 2 and created["held"] is False


# ---- a price well above the estimate ----------------------------------------------------------


def test_a_price_far_above_the_estimate_holds_the_order_until_a_person_accepts_it(
    shop: Session, outbox: Outbox
) -> None:
    logins = _logins(shop)
    slug = shop.info["slug"]
    _price(shop, "S1", "A", "100")
    request = _request(shop, "R-5", [("A", "10", None, "C1")])
    shop.execute(
        text("update request_line set est_price = 80 where request_no = 'R-5'")
    )  # the contract price is 25% above
    shop.commit()
    _source(shop, request, dispatch=False)

    made = service.order_request(shop, slug, request, as_of=TODAY)

    po = shop.execute(text("select status, hold_reason, hold_proposal_id from po")).one()
    assert made.held == ["PO-BLR-2627-00001"] and po[0] == "draft"
    assert "25.00% above the estimate" in po[1] or "25" in po[1]
    assert shop.execute(text("select count(*) from po_approval_step")).scalar_one() == 0  # not with any approver yet
    with pytest.raises(workflow.ApprovalError, match="on hold"):
        workflow.submit(shop, slug, made.pos[0], sod.user(1), today=TODAY)

    proposals.approve(shop, po[2], logins["procurement"])  # a person accepts the variance

    after = shop.execute(text("select status, hold_reason from po")).one()
    assert after == ("submitted", None)
    assert (
        shop.execute(text("select count(*) from audit_event where action = 'po.variance_accepted'")).scalar_one() == 1
    )


def test_a_price_exactly_at_the_band_is_not_held(shop: Session, outbox: Outbox) -> None:
    _price(shop, "S1", "A", "115")  # 15% above an estimate of 100, and the band is 15
    request = _request(shop, "R-6", [("A", "10", None, "C1")])
    shop.execute(text("update request_line set est_price = 100 where request_no = 'R-6'"))
    shop.commit()
    _source(shop, request, dispatch=False)

    made = service.order_request(shop, shop.info["slug"], request, as_of=TODAY)

    assert made.held == [] and shop.execute(text("select hold_reason from po")).scalar_one() is None


# ---- approving the order ----------------------------------------------------------------------


def test_the_rule_table_decides_who_approves_an_order_by_its_total(shop: Session, outbox: Outbox) -> None:
    logins = _logins(shop)
    slug = shop.info["slug"]
    request, _, made = _ordered(shop, outbox)  # total 2,331.09: the procurement head only

    assert made.waiting == ["PO-BLR-2627-00001"]
    po = shop.execute(text("select status, rule_code, decision_reason from po")).one()
    assert po[:2] == ("submitted", "PO-01") and "Procure Vikram" in po[2]
    assert workflow.can_act(shop, "PO-BLR-2627-00001", logins["procurement"]) is True
    assert workflow.can_act(shop, "PO-BLR-2627-00001", logins["cfo"]) is False

    assert workflow.approve(shop, slug, "PO-BLR-2627-00001", logins["procurement"], "fine") == "approved"

    row = shop.execute(text("select status, approved_at is not null, decision_reason from po")).one()
    assert row == ("approved", True, "Approved by Procure Vikram under rule PO-01 v1")
    assert shop.execute(text("select count(*) from audit_event where action = 'po.approved'")).scalar_one() == 1


def test_a_bigger_order_goes_through_each_person_in_its_chain(shop: Session, outbox: Outbox) -> None:
    logins = _logins(shop)
    slug = shop.info["slug"]
    _price(shop, "S1", "A", "60000")
    request = _request(shop, "R-7", [("A", "20", None, "C1")])  # 20 x 60,000 = 12,00,000 + 18%: over 5 lakh
    _source(shop, request, dispatch=False)
    (po_no,) = service.order_request(shop, slug, request, as_of=TODAY).waiting

    assert shop.execute(text("select rule_code, total from po")).one() == ("PO-02", D("1416000.00"))
    with pytest.raises(workflow.ApprovalError, match="not your turn"):
        workflow.approve(shop, slug, po_no, logins["cfo"])
    assert workflow.approve(shop, slug, po_no, logins["procurement"]) == "submitted"
    assert workflow.approve(shop, slug, po_no, logins["cfo"]) == "approved"


def test_rejecting_an_order_cancels_it_and_makes_its_lines_ready_to_order_again(shop: Session, outbox: Outbox) -> None:
    logins = _logins(shop)
    slug = shop.info["slug"]
    request, _, made = _ordered(shop, outbox)

    with pytest.raises(workflow.ApprovalError, match="reason is required"):
        workflow.reject(shop, made.waiting[0], logins["procurement"], " ")
    assert workflow.reject(shop, made.waiting[0], logins["procurement"], "Wrong supplier") == "cancelled"

    assert shop.execute(text("select status, cancel_reason from po")).one() == (
        "cancelled",
        "Procure Vikram: Wrong supplier",
    )
    assert shop.execute(text("select status from po_line")).scalars().all() == ["cancelled", "cancelled"]
    assert shop.execute(text("select status from request_line order by line_no")).scalars().all() == [
        "sourcing",
        "sourcing",
    ]
    again = service.order_request(shop, slug, request, as_of=TODAY)  # the same lines can be ordered again
    assert again.pos == ["PO-BLR-2627-00002"]


def test_the_buyer_who_ordered_cannot_approve_and_a_stranger_cannot_either(shop: Session, outbox: Outbox) -> None:
    logins = _logins(shop)
    slug = shop.info["slug"]
    _, _, made = _ordered(shop, outbox)
    shop.execute(text("update org_user set code = code where code = 'U5'"))
    shop.execute(
        text("update po set buyer = 'U5'")
    )  # the buyer on the order is the procurement head's own person record
    shop.commit()

    with pytest.raises(workflow.ApprovalError, match="segregation of duties"):
        workflow.approve(shop, slug, made.waiting[0], logins["procurement"])
    with pytest.raises(workflow.ApprovalError, match="not your turn"):
        workflow.approve(shop, slug, made.waiting[0], logins["director"])


# ---- stock on the way -------------------------------------------------------------------------


def test_what_open_orders_still_have_to_deliver_counts_as_stock_on_the_way(shop: Session) -> None:
    shop.execute(
        text("insert into stock_opening (item_code, location_code, qty, as_of) values ('A', 'WH-BLR', 5, '2026-01-01')")
    )
    shop.execute(
        text(
            "insert into item_location (item_code, location_code, reorder_policy, reorder_point, safety_stock, max_qty) "
            "values ('A', 'WH-BLR', 'minmax', 20, 2, 100)"
        )
    )
    request = _request(shop, "R-8", [("A", "10", None, "C1")])  # a request line ordered below
    shop.execute(text("update request set status = 'closed'"))
    shop.execute(text("update request_line set status = 'ordered', base_qty = 10"))

    def add_po(no: str, status: str, line_status: str, qty: str, received: str) -> None:
        shop.execute(
            text(
                "insert into po (no, supplier_code, ship_to_location, request_no, status) values (:n, 'S1', 'WH-BLR', :r, :s)"
            ),
            {"n": no, "r": request, "s": status},
        )
        shop.execute(
            text(
                "insert into po_line (po_no, line_no, request_no, request_line_no, item_code, qty, uom, base_qty, unit_price, "
                "gst_rate, taxable, received_qty, status, source) values (:n, 1, :r, :l, 'A', :q, 'NOS', :q, 10, 18, 10, :rc, :ls, 'contract')"
            ),
            {"n": no, "r": request, "l": 1 if no == "P1" else 99, "q": qty, "rc": received, "ls": line_status},
        )

    add_po("P1", "approved", "part_received", "10", "4")  # 6 still to come
    add_po("P2", "cancelled", "cancelled", "50", "0")  # does not count
    add_po("P3", "received", "received", "30", "30")  # nothing left
    shop.commit()

    (line,) = [x for x in stock_levels(shop) if x.item_code == "A"]

    assert line.on_order == D("6")  # and the 'ordered' request line is not counted a second time
    assert line.status == "reorder"  # 5 + 6 = 11 is still at or below the reorder point 20, so still a reorder
    # but at 11 on hand plus 6 on the way... the position rule only treats it as covered above the reorder point
    shop.execute(text("update po_line set received_qty = 0 where po_no = 'P1'"))
    shop.execute(text("update po_line set qty = 20, base_qty = 20 where po_no = 'P1'"))
    shop.commit()
    (line,) = [x for x in stock_levels(shop) if x.item_code == "A"]
    assert line.on_order == D("20") and line.status == "covered"  # 5 + 20 = 25 is above the reorder point


# ---- the screens' data ------------------------------------------------------------------------


def _headers(slug: str, user_id: int, role: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {create_access_token(user_id, slug, role)}"}


def test_the_rfq_page_data_includes_the_comparison_and_who_may_quote_or_award(shop: Session, outbox: Outbox) -> None:
    logins = _logins(shop)
    slug = shop.info["slug"]
    _, rfq = _sent_rfq(shop, outbox)
    _three_quotes(shop, rfq)
    buyer, requester = (
        _headers(slug, logins["buyer"], "Buyer"),
        _headers(slug, logins["keeper"], "Requester"),
    )

    data = client.get(f"/api/rfqs/{rfq}", headers=buyer).json()["comparison"]

    assert (data["min_quotes"], data["quotes_received"], data["invited"], data["waiting_for"]) == (2, 3, 3, [])
    assert data["can_quote"] is True and data["can_award"] is True
    (line,) = data["lines"]
    assert line["recommended"] == "S1" and line["why"].startswith("Lowest total cost among 2 qualifying offers")
    last = line["offers"][-1]  # the dropped offers come after the ranked ones
    assert (last["supplier_code"], last["net_price"], last["value"], last["compliant"], last["rank"]) == (
        "S3",
        "150.0000",
        "750.00",
        False,
        None,
    )
    assert last["flags"] == ["delivers 6 days after it is needed"]
    s1 = line["offers"][0]
    assert (s1["supplier_code"], s1["rank"], s1["value"], s1["charges"], s1["tax"], s1["landed"], s1["cost"]) == (
        "S1",
        1,
        "900.00",
        "75.50",
        "175.59",
        "1151.09",
        "975.50",
    )
    assert line["split"] is None and line["cheapest"] == "S1" and line["qualifying"] == 2
    assert s1["questions"] == [] and s1["score"] is None and data["lines"][0]["needs_question"] is False
    assert client.get(f"/api/rfqs/{rfq}", headers=requester).json()["comparison"]["can_award"] is False
    assert client.get("/api/rfqs", headers=buyer).json()[0]["quote_count"] == 3


def test_a_buyer_enters_a_quote_and_awards_over_the_api_and_a_requester_cannot(shop: Session, outbox: Outbox) -> None:
    logins = _logins(shop)
    slug = shop.info["slug"]
    _, rfq = _sent_rfq(shop, outbox)
    buyer = _headers(slug, logins["buyer"], "Buyer")
    other = _headers(slug, logins["keeper"], "Requester")
    body = {
        "supplier_code": "S1",
        "valid_until": "2026-12-31",
        "freight": "50",
        "lines": [{"rfq_line_no": 1, "unit_price": "200", "discount_pct": "10", "gst_rate": "18", "delivery_days": 5}],
    }

    assert client.post(f"/api/rfqs/{rfq}/quotes", json=body, headers=other).status_code == 403
    assert client.post(f"/api/rfqs/{rfq}/quotes", json=body).status_code == 401
    ok = client.post(f"/api/rfqs/{rfq}/quotes", json=body, headers=buyer)
    assert ok.json() == {"quote_no": f"{rfq}-S1-R1", "auto_awarded": False}
    bad = client.post(
        f"/api/rfqs/{rfq}/quotes",
        json={**body, "lines": [{"rfq_line_no": 1, "unit_price": "abc", "gst_rate": "18"}]},
        headers=buyer,
    )
    assert bad.status_code == 422 and "not a number" in bad.json()["detail"]
    refused = client.post(f"/api/rfqs/{rfq}/quotes", json={**body, "supplier_code": "S4"}, headers=buyer)
    assert refused.status_code == 409 and "was not asked" in refused.json()["detail"]

    early = client.post(f"/api/rfqs/{rfq}/award", json={"choices": {"1": "S1"}}, headers=buyer)
    assert early.status_code == 409 and "only 1 quote" in early.json()["detail"]  # one quote: a reason is needed
    done = client.post(
        f"/api/rfqs/{rfq}/award", json={"choices": {"1": "S1"}, "reason": "Only quote so far"}, headers=buyer
    )
    assert done.json() == {"awarded": {"1": "S1"}, "overridden": [], "complete": True}


def test_the_purchase_order_pages_show_lines_gst_charges_and_the_approval_trail(shop: Session, outbox: Outbox) -> None:
    logins = _logins(shop)
    slug = shop.info["slug"]
    _, rfq, made = _ordered(shop, outbox)
    po_no = made.pos[0]
    approver, buyer = _headers(slug, logins["procurement"], "Approver"), _headers(slug, logins["buyer"], "Buyer")

    listed = client.get("/api/purchase-orders", headers=buyer).json()
    assert listed[0]["id"] == po_no and listed[0]["amount"] == "2331.09" and listed[0]["status"] == "pending"
    assert listed[0]["waiting_for"] == "Procure Vikram" and listed[0]["supplier"] == "Alpha Traders"

    po = client.get(f"/api/purchase-orders/{po_no}", headers=approver).json()
    assert (po["taxable"], po["igst"], po["total"]) == ("1975.50", "355.59", "2331.09")
    assert po["supplier_gstin"] == "27AAAAA0000A1Z5" and po["buyer_gstin"] == "29BBBBB0000B1Z5"
    assert [(ln["name"], ln["qty"], ln["unit_price"], ln["igst"], ln["source"]) for ln in po["lines"]] == [
        ("Bearing", "10", "100.0000", "180.00", "contract"),
        ("Seal", "5", "180.0000", "162.00", "award"),
    ]
    assert [(c["type"], c["amount"], c["igst"]) for c in po["charges"]] == [
        ("freight", "50.00", "9.00"),
        ("packing_forwarding", "25.50", "4.59"),
    ]
    assert po["approval"]["can_act"] is True and po["approval"]["rule_code"] == "PO-01"
    assert client.get(f"/api/purchase-orders/{po_no}", headers=buyer).json()["approval"]["can_act"] is False
    assert client.get("/api/purchase-orders/NOPE", headers=buyer).status_code == 404

    assert client.post(f"/api/purchase-orders/{po_no}/approve", json={}, headers=buyer).status_code == 403
    early = client.post(f"/api/purchase-orders/{po_no}/reject", json={}, headers=approver)
    assert early.status_code == 409 and "reason" in early.json()["detail"]
    assert client.post(f"/api/purchase-orders/{po_no}/approve", json={"comment": "ok"}, headers=approver).json() == {
        "status": "approved"
    }
    assert client.get(f"/api/requisitions/{_}", headers=buyer).json()["purchase_order"] == {
        "id": po_no,
        "status": "approved",
    }


def test_exactly_the_minimum_number_of_quotes_is_enough_without_a_reason(shop: Session, outbox: Outbox) -> None:
    _, rfq = _sent_rfq(shop, outbox)
    _quote(shop, rfq, "S1", "180")
    _quote(shop, rfq, "S2", "190")  # two quotes, and the category wants two

    done = quotes.award(shop, rfq, {1: "S1"}, sod.user(7), as_of=TODAY)

    assert done.overridden == [] and done.complete is True


# ---- how much of a contract has been used --------------------------------------------------------


def test_reaching_the_ceiling_exactly_fits_and_one_paisa_over_does_not() -> None:
    assert ceiling_breach(D("1000"), D("400"), D("600")) == D("0.00")
    assert ceiling_breach(D("1000"), D("400"), D("600.01")) == D("0.01")
    assert ceiling_breach(None, D("999999"), D("999999")) == D("0.00")  # a contract with no ceiling has no limit
    use = ContractUse(D("1000"), D("250"))
    assert (use.remaining, use.pct_used) == (D("750.00"), D("25.0"))
    assert ContractUse(D("1000"), D("1200")).remaining == D("0.00")  # never negative
    assert ContractUse(None, D("5")).remaining is None and ContractUse(None, D("5")).pct_used is None


def _ceiling(shop: Session, value: str | None) -> None:
    shop.execute(text("update contract set max_value = :v where code = 'K-ACTIVE'"), {"v": value})
    shop.commit()


def test_a_line_that_would_use_more_than_is_left_of_a_contract_goes_to_quotation(shop: Session) -> None:
    _price(shop, "S1", "A", "100")
    _ceiling(shop, "999.99")
    request = _request(shop, "R-20", [("A", "10", None, "C1")])  # 1,000.00 against 999.99

    result = _source(shop, request, dispatch=False)

    (line,) = result.lines
    assert line.method == "rfq" and "left of its ceiling" in line.reason and "999.99" in line.reason

    _ceiling(shop, "1000")  # exactly the line's value: it fits
    again = _request(shop, "R-21", [("A", "10", None, "C1")])
    assert _source(shop, again, dispatch=False).lines[0].method == "contract"


def test_an_order_that_would_go_over_the_ceiling_is_held_until_a_person_accepts_it(
    shop: Session, outbox: Outbox
) -> None:
    logins = _logins(shop)
    slug = shop.info["slug"]
    _price(shop, "S1", "A", "100")
    _ceiling(shop, "1000")
    first = _request(shop, "R-22", [("A", "6", None, "C1")])
    second = _request(shop, "R-23", [("A", "6", None, "C1")])
    _source(shop, first, dispatch=False)
    _source(shop, second, dispatch=False)  # both were fine when they were sourced

    made_first = service.order_request(shop, slug, first, as_of=TODAY)
    made_second = service.order_request(shop, slug, second, as_of=TODAY)

    assert made_first.held == [] and len(made_second.held) == 1
    po_no = made_second.held[0]
    hold, pid = shop.execute(text("select hold_reason, hold_proposal_id from po where no = :n"), {"n": po_no}).one()
    assert "Contract K-ACTIVE would go over its ceiling" in hold and "200.00" in hold
    summary = shop.execute(text("select summary from proposal where id = :p"), {"p": pid}).scalar_one()
    assert summary.startswith(f"Accept {po_no} despite:")
    assert contract_usage.usage(shop, "K-ACTIVE").used == D("1200.00")  # the held order counts: it is a commitment

    proposals.approve(shop, pid, logins["procurement"])

    assert shop.execute(text("select status, hold_reason from po where no = :n"), {"n": po_no}).one() == (
        "submitted",
        None,
    )


def test_a_cancelled_order_gives_its_value_back_to_the_contract(shop: Session, outbox: Outbox) -> None:
    _price(shop, "S1", "A", "100")
    _ceiling(shop, "5000")
    request = _request(shop, "R-24", [("A", "6", None, "C1")])
    _source(shop, request, dispatch=False)
    made = service.order_request(shop, shop.info["slug"], request, as_of=TODAY)
    assert contract_usage.usage(shop, "K-ACTIVE").used == D("600.00")

    shop.execute(text("update po set status = 'cancelled' where no = :n"), {"n": made.pos[0]})
    shop.commit()

    assert contract_usage.usage(shop, "K-ACTIVE").used == D("0")


def test_the_contract_pages_show_the_ceiling_what_is_used_and_the_orders(shop: Session, outbox: Outbox) -> None:
    logins = _logins(shop)
    slug = shop.info["slug"]
    _price(shop, "S1", "A", "100")
    _ceiling(shop, "2400")
    request = _request(shop, "R-25", [("A", "6", None, "C1")])
    _source(shop, request, dispatch=False)
    made = service.order_request(shop, slug, request, as_of=TODAY)
    h = _headers(slug, logins["buyer"], "Buyer")

    listed = {c["id"]: c for c in client.get("/api/contracts", headers=h).json()}
    active = listed["K-ACTIVE"]
    assert (active["value"], active["used"], active["remaining"], active["pct_used"]) == (
        "2400.0000",
        "600.00",
        "1800.00",
        "25.0",
    )
    assert active["vendor"]["name"] == "Alpha Traders" and active["status"] == "active"
    assert listed["K-EXPIRED"]["status"] == "expired" and listed["K-EXPIRED"]["used"] == "0.00"

    detail = client.get("/api/contracts/K-ACTIVE", headers=h).json()
    assert [(o["id"], o["amount"]) for o in detail["orders"]] == [(made.pos[0], "600.00")]
    assert "Payment terms: NET45" in detail["terms"] and "Delivery: for_destination" in detail["terms"]
    assert client.get("/api/contracts/NOPE", headers=h).status_code == 404
    assert client.get("/api/contracts").status_code == 401
