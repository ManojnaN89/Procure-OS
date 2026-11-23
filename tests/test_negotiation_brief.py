"""The negotiation brief (task 11b.2, block 6): when the lowest counting offer is above the last price paid or the market
beyond the thresholds, the buyer gets a brief with a target worked out by the engine; it never sets a price or mails
anyone, a revision ranks again, and a line within the thresholds gets none."""

from datetime import date, timedelta
from decimal import Decimal
from types import SimpleNamespace

from sqlalchemy import text
from sqlalchemy.orm import Session
from test_orders import TODAY, _quote, _sent_rfq, shop  # noqa: F401 - fixtures
from test_path_case import RULE
from test_sourcing import PASSWORD, Outbox, db, outbox  # noqa: F401 - fixtures

from p2p.formulas import negotiation
from p2p.platform import users
from p2p.sourcing import benchmarks, notes, price_history

D = Decimal
PAID = negotiation.Reference("last_paid", D("905.91"), date(2026, 10, 5), "PO-CKN-2627-00199")
MARKET = negotiation.Reference("market", D("897.00"), date(2026, 10, 31), "Metal index", 7)


def test_n1_the_last_price_paid_triggers_with_the_engines_figures() -> None:
    got = negotiation.brief(D("960.00"), D(500), RULE, last_paid=PAID, markets=[])

    assert got is not None
    (t,) = got.triggers
    assert (t.gap.gap_inr, t.gap.gap_pct, t.gap.saving) == (
        D("54.0900"),
        D("5.6344"),
        D("27045.00"),
    )
    assert (got.target, got.saving_at_target) == (D("905.91"), D("27045.00"))


def test_n2_the_target_is_the_lowest_triggering_reference() -> None:
    got = negotiation.brief(D("960.00"), D(500), RULE, last_paid=PAID, markets=[MARKET])

    assert got is not None and [t.reference.kind for t in got.triggers] == [
        "last_paid",
        "market",
    ]
    m = got.triggers[1].gap
    assert (m.gap_inr, m.gap_pct, m.saving) == (
        D("63.0000"),
        D("6.5625"),
        D("31500.00"),
    )
    assert (got.target, got.saving_at_target) == (D("897.00"), D("31500.00"))


def test_n3_and_n4_within_the_thresholds_or_below_the_saving_floor_there_is_no_brief() -> None:
    assert negotiation.brief(D("900.00"), D(500), RULE, last_paid=PAID, markets=[MARKET]) is None
    assert negotiation.brief(D("960.00"), D(400), RULE, last_paid=PAID, markets=[]) is None  # 21,636.00


def test_the_target_and_saving_are_rounded_half_up_to_paise() -> None:
    odd = negotiation.Reference("market", D("100.005"), None, "x", 1)
    got = negotiation.brief(D("130.00"), D(1001), RULE, last_paid=None, markets=[odd])

    assert got is not None and got.target == D("100.01") and got.saving_at_target == D("30019.99")


# ---- a company ------------------------------------------------------------------------------------------------------


def _history(db: Session, price: str, on: date, po: str = "OLD-1") -> None:
    db.execute(
        text(
            "insert into order_history (po_no, line_no, item_code, location_code, ordered_on, qty, uom, unit_price) "
            "values (:po, 1, 'B', 'WH-BLR', :on, 10, 'NOS', :p)"
        ),
        {"po": po, "on": on, "p": price},
    )
    db.commit()


def _briefs(db: Session) -> list[tuple[str, str]]:
    return [tuple(r) for r in db.execute(text("select status, summary from recommendation where kind = "
                                              "'negotiation_brief' order by id")).all()]  # type: ignore[misc]  # fmt: skip


def _all_quote(db: Session, rfq: str, s1: str = "180") -> None:
    _quote(db, rfq, "S1", s1)
    _quote(db, rfq, "S2", "190")
    _quote(db, rfq, "S3", "195")


def test_the_brief_on_the_last_price_paid_is_written_once_everyone_has_answered(shop: Session, outbox: Outbox) -> None:  # noqa: F811
    _, rfq = _sent_rfq(shop, outbox, lines=[("B", "2000", None, "C1")])
    _history(shop, "150.00", date(2026, 10, 1))
    _quote(shop, rfq, "S1", "180")
    assert _briefs(shop) == []  # two suppliers have not answered and the RFQ is open
    mails = len(outbox.sent)

    _quote(shop, rfq, "S2", "190")
    _quote(shop, rfq, "S3", "195")

    ((status, words),) = _briefs(shop)
    assert status == "proposed"
    assert words == (
        "Alpha Traders is lowest on line 1 (Seal) at ₹ 180.00 per NOS, above the last price paid, ₹ 150.00 on OLD-1 "
        "of 01 Oct 2026 (16.67% gap). Ask for ₹ 150.00 per NOS: ₹ 60,000.00 less on 2000 NOS. The next offer is "
        "₹ 190.00 from Bravo Supplies."
    )
    assert len(outbox.sent) == mails  # a brief never mails anyone
    assert shop.execute(text("select count(*) from recommendation where kind = 'ranking'")).scalar_one() == 1


def test_the_latest_order_at_the_place_wins_and_one_older_than_a_year_does_not_count(
    shop: Session,
) -> None:  # noqa: F811
    _history(shop, "150.00", date(2026, 10, 1))
    _history(shop, "120.00", TODAY - timedelta(days=366), "OLD-0")

    assert price_history.last_paid_at(shop, "B", "WH-BLR", "NOS", TODAY) == price_history.LastPaid(
        D("150.00"), "OLD-1", date(2026, 10, 1)
    )
    _history(shop, "140.00", date(2026, 11, 2), "OLD-2")
    assert price_history.last_paid_at(shop, "B", "WH-BLR", "NOS", TODAY).po_no == "OLD-2"  # type: ignore[union-attr]
    assert price_history.last_paid_at(shop, "B", "ELSEWHERE", "NOS", TODAY) is None


def test_a_verified_benchmark_adds_the_market_and_a_revision_within_the_thresholds_withdraws_the_brief(
    shop: Session,  # noqa: F811
    outbox: Outbox,  # noqa: F811
) -> None:
    _, rfq = _sent_rfq(shop, outbox, lines=[("B", "2000", None, "C1")])
    _history(shop, "150.00", date(2026, 10, 1))
    slug, sfx = shop.info["slug"], shop.info["sfx"]
    enter = users.create_user(slug, f"ne{sfx}@x.example", PASSWORD, "Enterer", "Buyer")
    check = users.create_user(slug, f"nv{sfx}@x.example", PASSWORD, "Checker", "Approver")
    bid = benchmarks.add(shop, {"item_code": "B", "price": D("160"), "uom": "NOS", "effective_date": date(2026, 11, 1),
                                "region_code": None, "source_kind": "price_list", "source_name": "Seal index"},
                         enter, today=TODAY)  # fmt: skip
    benchmarks.verify(shop, bid, check, "checked")
    _all_quote(shop, rfq)

    (status, words) = _briefs(shop)[-1]
    assert "and the market benchmark, ₹ 160.00 (Seal index, 01 Nov 2026; 11.11% gap)" in words
    assert "Ask for ₹ 150.00 per NOS" in words

    _quote(shop, rfq, "S1", "155")  # a revision: 155 vs 150 is a 5.00 gap, not above 10 per unit

    assert [s for s, _ in _briefs(shop)] == ["withdrawn"]
    assert shop.execute(text("select status from recommendation where kind = 'ranking' order by id")).scalars().all() == [
        "superseded", "proposed",
    ]  # fmt: skip


def test_a_split_recommendation_or_an_awarded_line_gets_no_brief(shop: Session) -> None:  # noqa: F811
    split = SimpleNamespace(recommended=None, split_recommended=True, awarded=None, item_code="B")
    awarded = SimpleNamespace(recommended="S1", split_recommended=False, awarded="S1", item_code="B")

    assert notes.brief_figures(shop, "RFQ-X", split, TODAY) is None
    assert notes.brief_figures(shop, "RFQ-X", awarded, TODAY) is None
