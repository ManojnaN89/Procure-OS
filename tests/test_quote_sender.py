"""Quotes only from registered contacts, with the sender authentication kept (D53, task 1.3, block 6). An unknown or
failed sender is a case for the buyer and never reaches a model; an unauthenticated sender is read but always confirmed
by a person; a typed quote records the buyer's login."""

import uuid
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session
from test_orders import TODAY, _quote, _sent_rfq, shop  # noqa: F401 - fixtures
from test_quote_reading import SEAL_EMAIL, _as_json, _gateway, _seal
from test_sourcing import Outbox, db, outbox  # noqa: F401 - fixtures

from p2p.agents import autonomy
from p2p.config import get_settings
from p2p.formulas.calendar import pin_today
from p2p.platform import emails, sod
from p2p.platform.mailbox import IncomingMail
from p2p.sourcing import replies

D = Decimal
OURS = "mx.cbc.example"
PASS = f"{OURS}; spf=pass smtp.mailfrom=supplier.example; dkim=pass header.d=supplier.example; dmarc=pass"


def _trust(db: Session, server: str = OURS) -> None:
    db.execute(
        text("update org_setting set value = :v where key = 'mail_authserv_id'"),
        {"v": server},
    )
    db.commit()


def _mail(db: Session, rfq: str, sender: str, *headers: str, body: str = SEAL_EMAIL) -> int:
    to = f"rfq+{rfq}@{get_settings().mail_domain}"
    raw = "".join(f"Authentication-Results: {h}\r\n" for h in headers)
    raw += f"From: {sender}\r\nTo: {to}\r\nSubject: Re: {rfq}\r\nMessage-ID: <{uuid.uuid4().hex}@x>\r\n\r\n{body}\r\n"
    got = emails.ingest(
        db, db.info["slug"],
        IncomingMail(f"<{uuid.uuid4().hex}@x>", sender, [to], f"Re: {rfq}", body, datetime.now(UTC), raw=raw.encode()),
    )  # fmt: skip
    assert got is not None
    return got


def _draft(db: Session, draft_id: int | None) -> dict[str, Any]:
    return dict(db.execute(text("select * from quote_draft where id = :i"), {"i": draft_id}).mappings().one())


def _tasks(db: Session) -> list[tuple[str, str]]:
    return [tuple(r) for r in db.execute(text("select kind, payload->>'rfq_no' from task where kind = "
                                              "'quote_sender_check'")).all()]  # type: ignore[misc]  # fmt: skip


def test_an_authenticated_registered_contact_is_read_and_its_quote_keeps_how_it_came(
    shop: Session, outbox: Outbox
) -> None:  # noqa: F811
    _, rfq = _sent_rfq(shop, outbox)
    _trust(shop)
    gw, provider = _gateway(_as_json(_seal()))
    email_id = _mail(shop, rfq, "Alpha <s1@supplier.example>", PASS)
    assert shop.execute(text("select auth->>'verdict', received_on from email_message where id = :i"),
                        {"i": email_id}).one() == ("pass", TODAY)  # fmt: skip
    assert (
        shop.execute(text("select after->>'auth_verdict' from audit_event where action = 'email.receive'")).scalar_one()
        == "pass"
    )

    draft_id = replies.read_reply(shop, shop.info["slug"], email_id, gw, as_of=TODAY)
    assert _draft(shop, draft_id)["status"] == "pending" and _draft(shop, draft_id)["assumptions"] == []
    replies.confirm_draft(shop, shop.info["slug"], draft_id, sod.user(7), as_of=TODAY)  # type: ignore[arg-type]

    q = shop.execute(
        text("select channel, email_id, sender_address, contact_id, sender_auth, received_on from quote")
    ).one()
    contact = shop.execute(text("select id from supplier_contact where email = 's1@supplier.example'")).scalar_one()
    assert (q[0], q[1], q[2], q[3], q[4]["verdict"], q[5]) == ("email", email_id, "s1@supplier.example", contact,
                                                               "pass", TODAY)  # fmt: skip
    assert provider.calls == ["cheap"]
    after = shop.execute(text("select after from audit_event where action = 'quote.enter'")).scalar_one()
    assert (after["channel"], after["verdict"]) == ("email", "pass")


def test_a_failed_sender_is_not_read_and_the_buyer_gets_a_case(shop: Session, outbox: Outbox) -> None:  # noqa: F811
    _, rfq = _sent_rfq(shop, outbox)
    _trust(shop)
    gw, provider = _gateway()

    draft_id = replies.read_reply(
        shop,
        shop.info["slug"],
        _mail(shop, rfq, "s1@supplier.example", f"{OURS}; dmarc=fail"),
        gw,
        as_of=TODAY,
    )

    d = _draft(shop, draft_id)
    assert d["status"] == "needs_person"
    assert d["problems"] == [
        "sender authentication failed (dmarc=fail) for s1@supplier.example: the email was not read"
    ]
    assert provider.calls == [] and _tasks(shop) == [("quote_sender_check", rfq)]


def test_a_header_written_by_another_server_is_ignored_and_the_reading_always_waits_for_a_person(
    shop: Session,  # noqa: F811
    outbox: Outbox,  # noqa: F811
) -> None:
    _, rfq = _sent_rfq(shop, outbox)
    _trust(shop)
    autonomy.configure(shop, sod.user(1), "quote.read", level="auto", min_confidence=D("0.5"))
    gw, _ = _gateway(_as_json(_seal()))

    email_id = _mail(shop, rfq, "s1@supplier.example", "relay.other.example; dmarc=pass")
    draft_id = replies.read_reply(shop, shop.info["slug"], email_id, gw, as_of=TODAY)

    d = _draft(shop, draft_id)
    assert d["status"] == "pending" and "the sender could not be authenticated" in d["assumptions"]
    assert shop.execute(text("select count(*) from quote")).scalar_one() == 0  # not confirmed by itself


def test_an_unknown_address_is_not_read_even_at_a_suppliers_domain(shop: Session, outbox: Outbox) -> None:  # noqa: F811
    _, rfq = _sent_rfq(shop, outbox)
    gw, provider = _gateway()

    draft_id = replies.read_reply(
        shop,
        shop.info["slug"],
        _mail(shop, rfq, "quotes@supplier.example"),
        gw,
        as_of=TODAY,
    )

    assert _draft(shop, draft_id)["problems"] == [
        f"the sender quotes@supplier.example is not a registered contact of a supplier invited to {rfq}: "
        "the email was not read"
    ]
    assert provider.calls == [] and _tasks(shop) == [("quote_sender_check", rfq)]


def test_the_address_the_rfq_was_mailed_to_alone_identifies_nobody(shop: Session, outbox: Outbox) -> None:  # noqa: F811
    _, rfq = _sent_rfq(shop, outbox)
    shop.execute(text("update rfq_supplier set email = 'orders@s1.example' where supplier_code = 'S1'"))
    shop.commit()

    assert replies.sender_of(shop, rfq, "orders@s1.example").supplier is None
    assert replies.sender_of(shop, rfq, "S1@Supplier.Example").supplier == "S1"


def test_a_contact_of_a_blocked_supplier_a_deleted_contact_and_an_address_of_two_suppliers_match_nobody(
    shop: Session,  # noqa: F811
    outbox: Outbox,  # noqa: F811
) -> None:
    _, rfq = _sent_rfq(shop, outbox)
    shop.execute(text("update supplier set status = 'blocked' where code = 'S2'"))
    shop.execute(text("insert into supplier_contact (supplier_code, name, email) values ('S3', 'Shared', "
                      "'s1@supplier.example')"))  # fmt: skip
    shop.execute(text("delete from supplier_contact where supplier_code = 'S3' and email = 's3@supplier.example'"))
    shop.commit()

    assert replies.sender_of(shop, rfq, "s2@supplier.example").supplier is None
    assert replies.sender_of(shop, rfq, "s3@supplier.example").supplier is None
    both = replies.sender_of(shop, rfq, "s1@supplier.example")
    assert (
        both.supplier is None
        and both.problem == "s1@supplier.example is registered for S1 and S3: the email was not read"
    )


def test_a_late_mail_is_not_read_unless_the_sender_quoted_on_time(shop: Session, outbox: Outbox) -> None:  # noqa: F811
    _, rfq = _sent_rfq(shop, outbox)
    _quote(shop, rfq, "S2", "190")  # on time
    pin_today(date(2026, 11, 16))
    gw, provider = _gateway(_as_json(_seal()))

    late = replies.read_reply(
        shop,
        shop.info["slug"],
        _mail(shop, rfq, "s1@supplier.example"),
        gw,
        as_of=TODAY,
    )

    assert _draft(shop, late)["problems"] == [
        f"arrived on 16 Nov 2026, after {rfq} closed on 13 Nov 2026: a late quote is not read"
    ]
    assert provider.calls == []
    revision = replies.read_reply(
        shop,
        shop.info["slug"],
        _mail(shop, rfq, "s2@supplier.example"),
        gw,
        as_of=TODAY,
    )
    assert _draft(shop, revision)["status"] == "pending" and provider.calls == ["cheap"]


def test_an_emailed_quote_is_received_the_day_the_mail_was_stored_not_the_day_it_is_confirmed(
    shop: Session,  # noqa: F811
    outbox: Outbox,  # noqa: F811
) -> None:
    _, rfq = _sent_rfq(shop, outbox)
    pin_today(date(2026, 11, 13))
    gw, _ = _gateway(_as_json(_seal()))
    draft_id = replies.read_reply(
        shop,
        shop.info["slug"],
        _mail(shop, rfq, "s1@supplier.example"),
        gw,
        as_of=TODAY,
    )
    pin_today(date(2026, 11, 16))  # the buyer confirms after closing

    replies.confirm_draft(shop, shop.info["slug"], draft_id, sod.user(7), as_of=TODAY)  # type: ignore[arg-type]

    assert shop.execute(text("select received_on, channel from quote")).one() == (
        date(2026, 11, 13),
        "email",
    )


def test_a_typed_quote_records_the_buyers_login(shop: Session, outbox: Outbox) -> None:  # noqa: F811
    _, rfq = _sent_rfq(shop, outbox)

    _quote(shop, rfq, "S3", "170")

    assert shop.execute(text("select channel, sender_auth, received_on from quote")).one() == (
        "entered", {"method": "login", "user": 1}, TODAY,
    )  # fmt: skip
