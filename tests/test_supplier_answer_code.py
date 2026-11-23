"""A forwarded link decides nothing (D53, block 8 K1, K2, K10): the supplier's answer from a respond link needs a one-time
code, emailed only to the address the link went to, while it is a registered contact of an active supplier; wrong,
locked and expired codes write nothing; a code works only on the link it was asked from; and one company's link or code
means nothing to another."""

import base64
import json
import uuid
from datetime import UTC, datetime, timedelta

from confirmation_support import audits, count, link_of, send_p, token_in
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session
from supplier_answer_support import ask_code, code_from, code_mail
from test_orders import PASSWORD, _headers, shop  # noqa: F401 - fixtures
from test_sourcing import Outbox, db, outbox  # noqa: F401 - fixtures

from p2p.main import app
from p2p.orders import confirmation, dispatch, supplier_links
from p2p.platform import routing, tenants, users

client = TestClient(app)
ADDRESS = "s1@supplier.example"


def _nothing_written(shop: Session, po_no: str) -> None:
    shop.rollback()
    assert shop.execute(text("select status, acknowledged_version from po")).one() == ("sent", None)
    assert count(shop, "select count(*) from supplier_link where purpose = 'respond' and used_at is not null") == 0
    assert count(shop, "select count(*) from po_change") == 0
    assert count(shop, "select count(*) from po_commitment") == 0
    assert audits(shop, "po.acknowledge") == []


def _code_row(shop: Session) -> tuple[str, int]:
    shop.rollback()
    row = shop.execute(text("select status, attempts from one_time_code order by id desc limit 1")).one()
    return str(row[0]), int(row[1])


def test_a_forwarded_link_decides_nothing_until_the_contacts_own_code(shop: Session, outbox: Outbox) -> None:  # noqa: F811
    # given order P sent to the primary contact; a second registered contact exists (the order desk)
    po_no, _, token = send_p(shop, outbox)
    shop.execute(
        text("insert into supplier_contact (supplier_code, name, email, is_primary) values ('S1', 'Desk', :e, false)"),
        {"e": "orders@supplier.example"},
    )
    shop.commit()
    page = client.get(f"/supplier/{token}")
    assert page.status_code == 200 and "s****@supplier.example" in page.text and "Email me a code" in page.text

    # when someone holding the link accepts without a code, then with a code before any was sent
    without = client.post(f"/supplier/{token}", data={"action": "accept"})
    guessed = client.post(f"/supplier/{token}", data={"action": "accept", "code": "123456"})
    assert without.status_code == 409 and "this needs a one-time code" in without.text
    assert guessed.status_code == 409 and "this needs a one-time code" in guessed.text
    _nothing_written(shop, po_no)

    # and asks for a code: exactly one code mail, to the address the order went to
    outbox.sent.clear()
    asked = ask_code(client, token)
    assert asked.status_code == 200 and "A code was sent to s****@supplier.example" in asked.text
    assert [m["to"] for m in outbox.sent] == [ADDRESS]
    code = code_from(outbox.sent, ADDRESS)
    wrong = "000000" if code != "000000" else "111111"
    bad = client.post(f"/supplier/{token}", data={"action": "accept", "code": wrong})
    assert bad.status_code == 409 and "wrong code; 4 tries left" in bad.text
    _nothing_written(shop, po_no)

    # then with the real code it is version 1's acknowledgement by link, with the address and the code
    done = client.post(f"/supplier/{token}", data={"action": "accept", "code": code})
    assert done.status_code == 200
    shop.rollback()
    assert shop.execute(text("select status, acknowledged_version from po")).one() == ("acknowledged", 1)
    via, address, with_code, link = shop.execute(
        text("select ack_via, ack_address, ack_code_id is not null, ack_link_id from po_version")
    ).one()
    assert (via, address, with_code, link) == ("link", ADDRESS, True, link_of(token).id)
    (ack,) = audits(shop, "po.acknowledge")
    assert ack["version"] == 1 and ack["via"] == "link" and ack["by"] == ADDRESS and ack["code"]
    again = client.post(f"/supplier/{token}", data={"action": "accept", "code": code})
    assert again.status_code == 409 and "already been used" in again.text
    assert _code_row(shop)[0] == "used"


def test_five_wrong_codes_lock_it_and_an_expired_code_is_refused(shop: Session, outbox: Outbox) -> None:  # noqa: F811
    po_no, _, token = send_p(shop, outbox)
    ask_code(client, token)
    code = code_from(outbox.sent)
    wrong = "000000" if code != "000000" else "111111"

    said = [
        client.post(f"/supplier/{token}", data={"action": "decline", "reason": "no", "code": wrong}) for _ in range(5)
    ]

    assert "4 tries left" in said[0].text and "1 tries left" in said[3].text
    assert "wrong code 5 times" in said[4].text and _code_row(shop) == ("locked", 5)
    right = client.post(f"/supplier/{token}", data={"action": "decline", "reason": "no", "code": code})
    assert right.status_code == 409 and "this needs a one-time code" in right.text  # the locked code is gone
    _nothing_written(shop, po_no)
    wrongs = audits(shop, "otp.wrong")
    assert len(wrongs) == 5 and wrongs[-1] == {"attempts": 5, "locked": True}
    actor = shop.execute(text("select actor_id from audit_event where action = 'otp.wrong' limit 1")).scalar_one()
    assert actor == f"contact:{ADDRESS}"

    # a new code, asked a minute later, then left for eleven minutes
    shop.execute(text("update one_time_code set created_at = created_at - interval '1 minute'"))
    shop.commit()
    assert ask_code(client, token).status_code == 200
    fresh = code_from(outbox.sent)
    shop.execute(text("update one_time_code set expires_at = now() - interval '1 minute' where status = 'open'"))
    shop.commit()
    expired = client.post(f"/supplier/{token}", data={"action": "accept", "code": fresh})
    assert expired.status_code == 409 and "the code has expired" in expired.text
    assert len(audits(shop, "otp.expired")) == 1
    _nothing_written(shop, po_no)


def test_a_second_code_within_thirty_seconds_is_refused(shop: Session, outbox: Outbox) -> None:  # noqa: F811
    _, _, token = send_p(shop, outbox)
    assert ask_code(client, token).status_code == 200

    again = ask_code(client, token)

    assert again.status_code == 409 and "a moment ago" in again.text
    assert len([m for m in outbox.sent if "one-time code" in m["body"]]) == 1


def test_a_code_works_only_on_the_link_it_was_asked_from(shop: Session, outbox: Outbox) -> None:  # noqa: F811
    # given two links of the same version: the order mail's and a re-sent one
    po_no, logins, first = send_p(shop, outbox)
    supplier_links.mail_current(shop, shop.info["slug"], po_no, why="test")
    second = token_in(outbox.sent[-1]["body"])
    assert link_of(second).id != link_of(first).id
    ask_code(client, first)
    code = code_from(outbox.sent)

    got = client.post(f"/supplier/{second}", data={"action": "accept", "code": code})

    assert got.status_code == 409 and "this needs a one-time code" in got.text
    _nothing_written(shop, po_no)
    assert client.post(f"/supplier/{first}", data={"action": "accept", "code": code}).status_code == 200


def test_no_code_goes_to_a_contact_removed_or_for_a_supplier_not_active(shop: Session, outbox: Outbox) -> None:  # noqa: F811
    po_no, _, token = send_p(shop, outbox)
    shop.execute(text("update supplier_contact set email = 'new@supplier.example' where supplier_code = 'S1'"))
    shop.commit()
    outbox.sent.clear()

    removed = ask_code(client, token)

    assert removed.status_code == 409 and supplier_links.NO_CODE in removed.text and outbox.sent == []
    # the buyer sends the order again: a new link to the current contact, whose code then works
    buyer = _headers(shop.info["slug"], _buyer(shop), "Buyer")
    again = client.post(f"/api/purchase-orders/{po_no}/resend-link", headers=buyer)
    assert again.status_code == 200 and again.json() == {"sent_to": "new@supplier.example", "version": 1}
    assert len(audits(shop, "po.link_resend")) == 1
    fresh = token_in(outbox.sent[-1]["body"])
    shop.execute(text("update supplier set status = 'blocked' where code = 'S1'"))
    shop.commit()
    blocked = ask_code(client, fresh)
    assert blocked.status_code == 409 and supplier_links.NO_CODE in blocked.text
    assert not [m for m in outbox.sent if "one-time code" in m["body"]]


def _buyer(shop: Session) -> int:
    return int(shop.execute(text("select user_id from user_profile where role = 'Buyer' order by user_id")).scalar())


def test_a_refused_date_after_a_right_code_writes_nothing_and_leaves_the_code_open(
    shop: Session,
    outbox: Outbox,  # noqa: F811
) -> None:
    # the code and the answer commit together: a failure after the code leaves the code open
    po_no, _, token = send_p(shop, outbox)
    ask_code(client, token)
    code = code_from(outbox.sent)

    late = client.post(f"/supplier/{token}", data={"action": "accept", "code": code, "date_2": "2026-12-11"})

    assert late.status_code == 409 and "line 2: 11 Dec 2026 is 4 days after the date ordered" in late.text
    assert _code_row(shop) == ("open", 0)
    _nothing_written(shop, po_no)
    assert client.post(f"/supplier/{token}", data={"action": "accept", "code": code}).status_code == 200


def test_the_dispatch_link_needs_no_code(shop: Session, outbox: Outbox) -> None:  # noqa: F811
    po_no, _, token = send_p(shop, outbox)
    ask_code(client, token)
    client.post(f"/supplier/{token}", data={"action": "accept", "code": code_from(outbox.sent)})
    dispatch_token = token_in(outbox.sent[-1]["body"])

    page = client.get(f"/supplier/{dispatch_token}")
    no_code = client.post(f"/supplier/{dispatch_token}/code")

    assert page.status_code == 200 and "Email me a code" not in page.text
    assert no_code.status_code == 409 and "needs no code" in no_code.text
    done = client.post(f"/supplier/{dispatch_token}", data={"action": "dispatch", "ship_1": "10"})
    assert done.status_code == 200


def test_the_answer_service_itself_refuses_without_the_code(shop: Session, outbox: Outbox) -> None:  # noqa: F811
    po_no, _, token = send_p(shop, outbox)
    link = link_of(token)

    for call in (
        lambda: confirmation.accept_by_link(shop, shop.info["slug"], link, None, None, None),
        lambda: confirmation.answer_by_link(
            shop, shop.info["slug"], link, [confirmation.Proposed("qty", 1, "90")], None, "999999"
        ),
    ):
        try:
            call()
        except supplier_links.LinkError as exc:
            assert "one-time code" in str(exc)
        else:
            raise AssertionError("answered without a code")
    _nothing_written(shop, po_no)


def test_another_companys_link_or_code_means_nothing_here(shop: Session, outbox: Outbox) -> None:  # noqa: F811
    # given P sent in this company, and another company with an order link of its own
    po_no, _, token = send_p(shop, outbox)
    link = link_of(token)
    ask_code(client, token)
    code = code_from(outbox.sent)
    other = "b" + uuid.uuid4().hex[:8]
    assert tenants.create_tenant(other, load_reference=False).ok
    try:
        theirs = users.create_user(other, f"buyer-{other}@x.example", PASSWORD, "Their Buyer", "Buyer")
        far = int((datetime.now(UTC) + timedelta(days=5)).timestamp())
        # the token with the company changed fails its signature
        body, signature = token.split(".")
        values = json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))
        values[0] = other
        moved = base64.urlsafe_b64encode(json.dumps(values, separators=(",", ":")).encode()).decode().rstrip("=")
        moved = f"{moved}.{signature}"
        crossed = supplier_links.SIGNER.sign([other, link.id, po_no, "S1", "respond", far])

        assert client.get(f"/supplier/{moved}").status_code == 400
        assert client.post(f"/supplier/{crossed}", data={"action": "accept", "code": code}).status_code == 409
        h = _headers(other, theirs, "Buyer")
        assert client.get(f"/api/purchase-orders/{po_no}", headers=h).status_code == 404
        assert client.post(f"/api/purchase-orders/{po_no}/resend-link", headers=h).status_code == 404
        with Session(routing.engine_for(other)) as them:
            assert them.execute(text("select count(*) from one_time_code")).scalar_one() == 0
            assert them.execute(text("select count(*) from po_version where ack_via is not null")).scalar_one() == 0
    finally:
        routing.clear_engine_cache()
        tenants.drop_tenant(other)
    _nothing_written(shop, po_no)
    assert dispatch.waiting_ack(dict(shop.execute(text("select * from po")).mappings().one()))
    assert code_mail(outbox.sent)["to"] == ADDRESS  # the code went only to our contact
