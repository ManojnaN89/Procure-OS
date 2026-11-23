"""Closing and late bids (task 12.6, block 6): an RFQ closes at the end of its due date; a first quote after that is
refused, a revision from a supplier that quoted on time is a negotiation round; the buyer may extend the date with a
reason, and every invited supplier that has not declined is told in fixed words; the first sending gives suppliers the
full reply time."""

from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session
from test_orders import TODAY, _quote, _sent_rfq, shop  # noqa: F401 - fixtures
from test_sourcing import Outbox, _request, _source, db, outbox  # noqa: F401 - fixtures

from p2p.formulas.calendar import BusinessCalendar, pin_today
from p2p.formulas.sourcing import first_send_due, is_late
from p2p.orders import quotes
from p2p.platform import proposals, users
from p2p.sourcing import closing, service

D = Decimal
DUE = date(2026, 11, 13)  # TODAY (Fri 6 Nov) + 5 working days


def test_a_quote_on_the_closing_date_is_on_time_and_the_day_after_is_late() -> None:
    assert is_late(date(2026, 11, 13), DUE) is False
    assert is_late(date(2026, 11, 12), DUE) is False
    assert is_late(date(2026, 11, 14), DUE) is True


def test_the_first_sending_gives_the_full_reply_time_and_never_shortens_it() -> None:
    cal = BusinessCalendar.of([date(2026, 11, 24)])  # a holiday
    assert first_send_due(date(2026, 11, 27), date(2026, 11, 23), 5, cal) == date(2026, 12, 1)
    assert first_send_due(date(2026, 12, 15), date(2026, 11, 23), 5, cal) == date(2026, 12, 15)


def _due(db: Session, rfq: str) -> date:
    return db.execute(text("select due_date from rfq where no = :n"), {"n": rfq}).scalar_one()  # type: ignore[no-any-return]


def test_a_first_quote_after_closing_is_refused_and_nothing_is_written(shop: Session, outbox: Outbox) -> None:  # noqa: F811
    _, rfq = _sent_rfq(shop, outbox)
    assert _due(shop, rfq) == DUE
    pin_today(DUE)
    on_time = _quote(shop, rfq, "S2", "190")
    pin_today(date(2026, 11, 14))

    with pytest.raises(quotes.QuoteError, match=f"{rfq} closed on 13 Nov 2026: a quote received after the closing date "
                                                "is refused \\(late bid\\)"):  # fmt: skip
        _quote(shop, rfq, "S1", "180")

    shop.rollback()
    assert shop.execute(text("select no from quote where rfq_no = :n"), {"n": rfq}).scalars().all() == [on_time]
    assert shop.execute(text("select received_on, channel from quote where no = :n"), {"n": on_time}).one() == (
        DUE, "entered",
    )  # fmt: skip
    assert shop.execute(text("select count(*) from audit_event where action = 'quote.enter'")).scalar_one() == 1


def test_a_revision_after_closing_from_a_supplier_that_quoted_on_time_is_a_negotiation_round(
    shop: Session,  # noqa: F811
    outbox: Outbox,  # noqa: F811
) -> None:
    _, rfq = _sent_rfq(shop, outbox)
    pin_today(DUE)
    _quote(shop, rfq, "S2", "190")
    pin_today(date(2026, 11, 16))

    second = _quote(shop, rfq, "S2", "185")

    assert second.endswith("-R2")
    assert shop.execute(text("select received_on from quote where no = :n"), {"n": second}).scalar_one() == date(
        2026, 11, 16
    )


def _buyer(db: Session) -> int:
    return users.create_user(db.info["slug"], f"ext{db.info['sfx']}@x.example", "correct horse battery", "Ext Buyer",
                             "Buyer")  # fmt: skip


def test_an_extension_moves_the_date_tells_every_supplier_not_declined_and_lets_a_late_quote_in(
    shop: Session,  # noqa: F811
    outbox: Outbox,  # noqa: F811
) -> None:
    _, rfq = _sent_rfq(shop, outbox)
    shop.execute(
        text("update rfq_supplier set declined = true where rfq_no = :n and supplier_code = 'S3'"),
        {"n": rfq},
    )
    shop.commit()
    pin_today(date(2026, 11, 16))
    before = len(outbox.sent)

    got = closing.extend(shop, rfq, _buyer(shop), date(2026, 11, 20), "two suppliers asked for more time",
                         today=date(2026, 11, 16))  # fmt: skip

    assert got == {"due_date": "2026-11-20", "mailed": 2}
    mails = outbox.sent[before:]
    assert sorted(m["to"] for m in mails) == [
        "s1@supplier.example",
        "s2@supplier.example",
    ]
    assert {m["subject"] for m in mails} == {f"Closing date extended: request for quotation {rfq}"}
    s1 = next(m for m in mails if m["to"] == "s1@supplier.example")
    assert s1["body"] == (
        f"Dear Contact S1,\n\nthe closing date of our request for quotation {rfq} is now 20 Nov 2026. If you have "
        "not quoted yet, please reply by then. A quote you have sent stands unless you revise it.\n\n"
        "Regards,\nProcurement, CBC\n"
    )
    assert _due(shop, rfq) == date(2026, 11, 20)
    row = shop.execute(text("select before, after, reason from audit_event where action = 'rfq.extend'")).one()
    assert row[0]["due_date"] == "2026-11-13" and row[1] == {
        "due_date": "2026-11-20",
        "mailed": 2,
    }
    assert row[2] == "two suppliers asked for more time"
    assert _quote(shop, rfq, "S1", "180")  # no longer late


@pytest.mark.parametrize(
    ("new", "reason", "words"),
    [
        (date(2026, 11, 20), " ", "a reason is needed"),
        (date(2026, 11, 16), "x", "must be after today"),
        (date(2026, 11, 21), "x", "21 Nov 2026 is not a working day"),
        (date(2026, 11, 12), "x", "must be after today"),
    ],
)
def test_an_extension_without_a_reason_to_a_past_day_a_day_off_or_not_later_is_refused(
    shop: Session,  # noqa: F811
    outbox: Outbox,  # noqa: F811
    new: date,
    reason: str,
    words: str,
) -> None:
    _, rfq = _sent_rfq(shop, outbox)
    with pytest.raises(closing.ClosingError, match=words) as err:
        closing.extend(shop, rfq, _buyer(shop), new, reason, today=date(2026, 11, 16))
    assert err.value.status == 422
    assert _due(shop, rfq) == DUE


def test_an_extension_to_a_day_not_after_the_current_closing_is_refused(shop: Session, outbox: Outbox) -> None:  # noqa: F811
    _, rfq = _sent_rfq(shop, outbox)
    with pytest.raises(closing.ClosingError, match="after the current one \\(13 Nov 2026\\)"):
        closing.extend(shop, rfq, _buyer(shop), date(2026, 11, 12), "x", today=date(2026, 11, 9))


def test_the_first_sending_moves_a_closing_date_that_would_be_short(db: Session, outbox: Outbox) -> None:  # noqa: F811
    result = _source(db, _request(db, "R-1", [("B", "5", None, "C1")]))
    (rfq,) = result.rfqs
    assert _due(db, rfq) == DUE
    pin_today(date(2026, 11, 11))  # the buyer confirms five days later
    buyer = users.create_user(
        db.info["slug"],
        f"late{db.info['sfx']}@x.example",
        "correct horse battery",
        "B",
        "Buyer",
    )

    proposals.approve(db, result.proposals[0], buyer)

    assert _due(db, rfq) == date(2026, 11, 18)
    after = db.execute(text("select before, after from audit_event where action = 'rfq.send'")).one()
    assert after[0] == {"due_date": "2026-11-13"} and after[1]["due_date"] == "2026-11-18"
    assert service  # the mails carry the new date
    assert all("Please reply by: 18 Nov 2026" in m["body"] for m in outbox.sent)
