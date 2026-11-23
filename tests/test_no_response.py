"""The no-response case at closing (task 12.6, AT-10, block 6): an RFQ that closed with fewer counting quotes than it
wants opens one case for its buyer per closing; nothing else happens by itself; the case is done when the RFQ is
extended or fully awarded."""

from datetime import date

from sqlalchemy import text
from sqlalchemy.orm import Session
from test_orders import _comp, _quote, _sent_rfq, shop  # noqa: F401 - fixtures
from test_sourcing import Outbox, db, outbox  # noqa: F401 - fixtures

from p2p.formulas.calendar import pin_today
from p2p.orders import quotes
from p2p.platform import sod, users
from p2p.sourcing import closing

AFTER = date(2026, 11, 16)  # the first working day after the closing date (Fri 13 Nov)


def _cases(db: Session) -> list[dict[str, object]]:
    return [
        dict(r) for r in db.execute(text("select * from task where kind = 'rfq_no_response' order by id")).mappings()
    ]


def test_an_rfq_closed_short_opens_one_case_for_its_buyer_and_nothing_else(shop: Session, outbox: Outbox) -> None:  # noqa: F811
    _, rfq = _sent_rfq(shop, outbox)
    _quote(shop, rfq, "S2", "190", days=1)  # still on time for the need-by date after closing
    shop.execute(
        text("update rfq_supplier set declined = true where rfq_no = :n and supplier_code = 'S3'"),
        {"n": rfq},
    )
    shop.commit()
    wanted = _comp(shop, rfq).min_quotes
    pin_today(AFTER)
    mails = len(outbox.sent)

    assert closing.run(shop, shop.info["slug"], AFTER) == [rfq]
    assert closing.run(shop, shop.info["slug"], AFTER) == []  # the same closing never opens a second case

    (case,) = _cases(shop)
    assert case["title"] == f"{rfq} closed on 13 Nov 2026 with 1 of {wanted} quotes wanted"
    assert case["assignee"] == closing.buyer_of(shop, shop.info["slug"], rfq)
    p = case["payload"]
    assert (p["answered"], p["silent"], p["declined"], p["counting"], p["min_quotes"]) == (  # type: ignore[index]
        ["S2"], ["S1"], ["S3"], 1, wanted,
    )  # fmt: skip
    assert p["next"] == closing.NEXT  # type: ignore[index]
    assert len(outbox.sent) == mails  # no mail to any supplier
    assert shop.execute(text("select status, due_date from rfq where no = :n"), {"n": rfq}).one() == (
        "sent", date(2026, 11, 13),
    )  # fmt: skip
    assert shop.execute(text("select count(*) from audit_event where action = 'rfq.no_response'")).scalar_one() == 1


def test_no_quotes_at_all_says_so(shop: Session, outbox: Outbox) -> None:  # noqa: F811
    _, rfq = _sent_rfq(shop, outbox)
    pin_today(AFTER)

    closing.run(shop, shop.info["slug"], AFTER)

    assert _cases(shop)[0]["title"] == f"{rfq} closed on 13 Nov 2026 with no quotes"


def test_enough_quotes_or_an_rfq_still_open_opens_no_case(shop: Session, outbox: Outbox) -> None:  # noqa: F811
    _, rfq = _sent_rfq(shop, outbox)
    assert closing.run(shop, shop.info["slug"], date(2026, 11, 13)) == []  # closes at the end of the day
    for supplier, price in (("S1", "180"), ("S2", "190"), ("S3", "185")):
        _quote(shop, rfq, supplier, price, days=1)
    pin_today(AFTER)

    assert closing.run(shop, shop.info["slug"], AFTER) == []
    assert _cases(shop) == []


def test_an_extension_completes_the_case_and_the_next_closing_may_open_a_new_one(shop: Session, outbox: Outbox) -> None:  # noqa: F811
    _, rfq = _sent_rfq(shop, outbox)
    pin_today(AFTER)
    closing.run(shop, shop.info["slug"], AFTER)
    buyer = users.create_user(
        shop.info["slug"],
        f"x{shop.info['sfx']}@x.example",
        "correct horse battery",
        "B",
        "Buyer",
    )

    closing.extend(shop, rfq, buyer, date(2026, 11, 18), "more time", today=AFTER)

    assert [c["status"] for c in _cases(shop)] == ["done"]
    pin_today(date(2026, 11, 19))
    assert closing.run(shop, shop.info["slug"], date(2026, 11, 19)) == [rfq]
    assert [c["status"] for c in _cases(shop)] == ["done", "open"]
    assert _cases(shop)[1]["payload"]["due_date"] == "2026-11-18"  # type: ignore[index]


def test_selecting_a_supplier_for_every_line_completes_the_case(shop: Session, outbox: Outbox) -> None:  # noqa: F811
    _, rfq = _sent_rfq(shop, outbox)
    _quote(shop, rfq, "S2", "190", days=1)
    pin_today(AFTER)
    closing.run(shop, shop.info["slug"], AFTER)

    quotes.award(shop, rfq, {1: "S2"}, sod.user(7), reason="only one quote came in", as_of=AFTER)

    assert [c["status"] for c in _cases(shop)] == ["done"]
