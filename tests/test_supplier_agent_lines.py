"""The supplier agent (AG02) on versions and lines (block 8 K5, K5b, K5c, K8, 8.7): a date moves only the line it names
(DF-27), never when no line is named on an order of several, and never when it would come after a material need; an
emailed acknowledgement answers the version of the mail it replies to, is recorded only from a registered contact
whose sender was authenticated (OP-4 interim), and a failed sender is not read at all."""

from datetime import date
from typing import Any

import pytest
from confirmation_support import audits, count, send_p
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session
from supplier_answer_support import answer
from test_orders import shop  # noqa: F401 - fixtures
from test_sourcing import Outbox, db, outbox  # noqa: F401 - fixtures
from test_supplier_agent import AUTHSERV, _authenticated, _gateway, _mail, _reading

from p2p.agents import autonomy, supplier_comms
from p2p.formulas.calendar import pin_today
from p2p.main import app
from p2p.orders import changes
from p2p.platform import sod

client = TestClient(app)
SALES = "Contact S1 <s1@supplier.example>"


def _acknowledged_k4(shop: Session, outbox: Outbox) -> tuple[str, dict[str, int]]:
    """P acknowledged with a date per line: 7, 9 and 14 Dec (K4)."""
    po_no, logins, token = send_p(shop, outbox)
    assert answer(client, outbox.sent, token, {"action": "accept", "date_2": "2026-12-09"}).status_code == 200
    return po_no, logins


def _order_mail(shop: Session, po_no: str, version: int) -> str:
    """The Message-ID of our order mail that carried the version."""
    return str(
        shop.execute(
            text(
                "select message_id from email_message where direction = 'out' and linked_id = :p and po_version = :v "
                "order by id limit 1"
            ),
            {"p": po_no, "v": version},
        ).scalar_one()
    )


def _read(
    shop: Session, po_no: str, body: str, reading: str, *, sender: str = SALES, raw: bytes | None = None,
    in_reply_to: str | None = None, auto: bool = True,
) -> tuple[supplier_comms.Handled | None, Any]:  # fmt: skip
    autonomy.configure(
        shop,
        sod.user(1),
        supplier_comms.DECISION_CHANGE,
        level="auto" if auto else "propose",
    )
    gw, provider = _gateway(reading)
    email_id = _mail(
        shop,
        po_no,
        body,
        sender=sender,
        raw=raw if raw is not None else _authenticated(shop),
        in_reply_to=in_reply_to,
    )
    return supplier_comms.handle_email(shop, shop.info["slug"], email_id, gw, as_of=date(2026, 11, 30)), provider


def _dates(shop: Session) -> list[date]:
    shop.rollback()
    return [r[0] for r in shop.execute(text("select promised_date from po_line order by line_no"))]


def test_a_date_for_one_named_line_moves_only_that_line(shop: Session, outbox: Outbox) -> None:  # noqa: F811
    po_no, _ = _acknowledged_k4(shop, outbox)
    pin_today(date(2026, 11, 30))

    done, _ = _read(
        shop, po_no, "The Seal (B) will come on 10 Dec 2026; the rest as ordered.",
        _reading("date_change", item="Seal", new_date=date(2026, 12, 10)),
    )  # fmt: skip

    assert done is not None and done.status == "applied" and "line 2" in done.action
    assert _dates(shop) == [date(2026, 12, 7), date(2026, 12, 10), date(2026, 12, 14)]
    row = shop.execute(
        text("select po_line_no, promised_date, po_version from po_commitment where source = 'agent_date'")
    ).one()
    assert tuple(row) == (2, date(2026, 12, 10), 1)
    (moved,) = audits(shop, "po.promised_date_change")
    assert moved["line"] == 2 and moved["version"] == 1


def test_a_date_for_no_line_on_an_order_of_several_moves_nothing_and_proposes_each_line(
    shop: Session,
    outbox: Outbox,  # noqa: F811
) -> None:
    po_no, _ = _acknowledged_k4(shop, outbox)
    pin_today(date(2026, 11, 30))

    done, _ = _read(
        shop, po_no, f"All goods against {po_no} will be delivered on 12 Dec 2026.",
        _reading("date_change", new_date=date(2026, 12, 12)),
    )  # fmt: skip

    assert done is not None and done.status == "task"
    assert _dates(shop) == [date(2026, 12, 7), date(2026, 12, 9), date(2026, 12, 14)]
    proposed = shop.execute(text("select po_line_no, old_value, new_value, task_id from po_change order by po_line_no"))
    rows = [tuple(r) for r in proposed]
    assert [r[:3] for r in rows] == [
        (1, "2026-12-07", "2026-12-12"), (2, "2026-12-09", "2026-12-12"), (3, "2026-12-14", "2026-12-12"),
    ]  # fmt: skip
    assert (
        len({r[3] for r in rows}) == 1 and count(shop, "select count(*) from task where kind = 'supplier_change'") == 1
    )


def test_a_date_that_would_come_after_a_material_need_is_proposed_not_applied(shop: Session, outbox: Outbox) -> None:  # noqa: F811
    # given line 2 buys for a material request of MO-1 needed on 9 Dec at the same place
    po_no, _ = _acknowledged_k4(shop, outbox)
    request_no, request_line = shop.execute(
        text("select request_no, request_line_no from po_line where line_no = 2")
    ).one()
    imp = shop.execute(
        text(
            "insert into demand_import (source, files, loaded, already_loaded, rejected) values ('t', '{}', 1, 0, 0) returning id"
        )
    ).scalar_one()
    order = shop.execute(
        text(
            "insert into production_order (order_no, product_code, qty, need_by, location_code, import_id) "
            "values ('MO-1', 'PUMP', 5, '2026-12-09', 'WH-BLR', :i) returning id"
        ),
        {"i": imp},
    ).scalar_one()
    need = shop.execute(
        text(
            "insert into material_requirement (source_kind, source_doc_no, line_key, production_order_id, item_code, qty, "
            "unit, base_qty, need_by, location_code, import_id) values ('production_order', 'MO-1', 'B', :p, 'B', 200, "
            "'NOS', 200, '2026-12-09', 'WH-BLR', :i) returning id"
        ),
        {"p": order, "i": imp},
    ).scalar_one()
    shop.execute(
        text(
            "insert into material_request (no, production_order_id, order_no, product_code, location_code, need_by) "
            "values ('MR-1', :p, 'MO-1', 'PUMP', 'WH-BLR', '2026-12-09')"
        ),
        {"p": order},
    )
    line_id = shop.execute(
        text(
            "insert into material_request_line (mr_no, line_no, requirement_id, item_code, location_code, uom, need_qty, "
            "need_by) values ('MR-1', 1, :r, 'B', 'WH-BLR', 'NOS', 200, '2026-12-09') returning id"
        ),
        {"r": need},
    ).scalar_one()
    shop.execute(
        text(
            "insert into material_request_buy (mr_line_id, request_no, request_line_no, qty) values (:l, :r, :n, 200)"
        ),
        {"l": line_id, "r": request_no, "n": request_line},
    )
    shop.commit()
    pin_today(date(2026, 11, 30))

    done, _ = _read(
        shop, po_no, "The Seal (B) will come on 10 Dec 2026; the rest as ordered.",
        _reading("date_change", item="Seal", new_date=date(2026, 12, 10)),
    )  # fmt: skip

    assert done is not None and done.status == "task" and "MR-1" in done.action
    assert _dates(shop)[1] == date(2026, 12, 9)
    assert [tuple(r) for r in shop.execute(text("select po_line_no, new_value from po_change"))] == [(2, "2026-12-10")]


def _unacknowledged_v2(shop: Session, outbox: Outbox) -> tuple[str, str, str]:
    """P sent (mail M1) and revised on 25 Nov (mail M2), not acknowledged."""
    po_no, logins, _ = send_p(shop, outbox)
    pin_today(date(2026, 11, 25))
    changes.revise(
        shop,
        shop.info["slug"],
        po_no,
        logins["buyer"],
        "plan cut",
        [changes.LineChange(3, qty=140)],
    )
    return po_no, _order_mail(shop, po_no, 1), _order_mail(shop, po_no, 2)


def test_an_emailed_acknowledgement_answers_the_version_of_the_mail_it_replies_to(
    shop: Session, outbox: Outbox
) -> None:  # noqa: F811
    po_no, m1, m2 = _unacknowledged_v2(shop, outbox)
    outbox.sent.clear()

    to_v1, _ = _read(shop, po_no, "We confirm the order.", _reading("ack"), in_reply_to=m1)

    assert (
        to_v1 is not None
        and to_v1.status == "noted"
        and "answers version 1; the order is now version 2" in to_v1.action
    )
    shop.rollback()
    assert shop.execute(text("select acknowledged_version from po")).scalar() is None
    (reply,) = outbox.sent
    assert "Order " + po_no + " has been revised (version 2, sent on 25 Nov 2026)" in reply["body"]
    assert reply.get("document_ids") or reply.get("attachments")  # version 2's stored document

    to_v2, _ = _read(shop, po_no, "We confirm the order.", _reading("ack"), in_reply_to=m2)

    assert to_v2 is not None and to_v2.status == "applied"
    assert shop.execute(text("select acknowledged_version, status from po")).one() == (
        2,
        "acknowledged",
    )
    via, auth, email_id = shop.execute(
        text("select ack_via, ack_sender_auth, ack_email_id from po_version where version = 2")
    ).one()
    assert via == "email" and auth["verdict"] == "pass" and email_id


def test_an_unauthenticated_acknowledgement_waits_for_the_buyers_click(shop: Session, outbox: Outbox) -> None:  # noqa: F811
    po_no, _, m2 = _unacknowledged_v2(shop, outbox)

    done, _ = _read(
        shop,
        po_no,
        "We confirm the order.",
        _reading("ack"),
        raw=b"raw",
        in_reply_to=m2,
    )

    assert done is not None and done.status == "task"
    shop.rollback()
    assert shop.execute(text("select status, acknowledged_version from po")).one() == (
        "sent",
        None,
    )
    title, payload = shop.execute(text("select title, payload from task where kind = 'supplier_ack_confirm'")).one()
    assert title == f"Record Alpha Traders's acknowledgement of {po_no} version 2 (email from s1@supplier.example)"
    assert payload["version"] == 2 and payload["po_no"] == po_no
    # the buyer records it: the task is done by the system
    from p2p.orders import dispatch  # noqa: PLC0415

    buyer = int(shop.execute(text("select user_id from user_profile where role = 'Buyer' order by user_id")).scalar())
    dispatch.acknowledge(shop, po_no, sod.user(buyer), version=2, via="app", note="from the email")
    assert shop.execute(text("select status from task where kind = 'supplier_ack_confirm'")).scalar_one() == "done"


def test_another_registered_contact_is_recognised_and_a_failed_sender_is_not_read(
    shop: Session, outbox: Outbox
) -> None:  # noqa: F811
    po_no, _, m2 = _unacknowledged_v2(shop, outbox)
    shop.execute(
        text("insert into supplier_contact (supplier_code, name, email, is_primary) values ('S1', 'Desk', :e, false)"),
        {"e": "orders@supplier.example"},
    )
    shop.commit()
    _authenticated(shop)
    failed = f"Authentication-Results: {AUTHSERV}; dmarc=fail header.from=supplier.example\r\n\r\nx".encode()

    bad, provider = _read(shop, po_no, "We confirm.", _reading("ack"), raw=failed, in_reply_to=m2)

    assert bad is not None and bad.status == "ignored" and provider.calls == []  # no model call
    assert count(shop, "select count(*) from model_call") == 0
    assert count(shop, "select count(*) from task where kind = 'supplier_sender_check'") == 1
    assert shop.execute(text("select acknowledged_version from po")).scalar() is None

    desk, _ = _read(
        shop,
        po_no,
        "We confirm.",
        _reading("ack"),
        sender="Desk <orders@supplier.example>",
        in_reply_to=m2,
    )

    assert desk is not None and desk.status == "applied"
    assert shop.execute(text("select ack_address from po_version where version = 2")).scalar_one() == (
        "orders@supplier.example"
    )


def test_a_reply_with_no_thread_answers_version_1_only_while_there_is_no_other(shop: Session, outbox: Outbox) -> None:  # noqa: F811
    po_no, _, _ = _unacknowledged_v2(shop, outbox)

    done, _ = _read(shop, po_no, "We confirm the order.", _reading("ack"))

    assert done is not None and done.status == "task" and "which version" in done.action
    order = {"no": po_no, "version": 1}
    mail = {"in_reply_to": None}
    assert supplier_comms.reply_version(shop, mail, order) == 1
    assert supplier_comms.reply_version(shop, mail, {"no": po_no, "version": 2}) is None


@pytest.mark.parametrize("acknowledged", [True, False])
def test_a_reply_labelled_an_acknowledgement_that_gives_a_new_date_for_no_line_proposes_each_line(
    shop: Session,
    outbox: Outbox,  # noqa: F811
    acknowledged: bool,
) -> None:
    # given P acknowledged with 7, 9 and 14 Dec, or only sent (lines due 7, 7 and 14 Dec)
    if acknowledged:
        po_no, _ = _acknowledged_k4(shop, outbox)
    else:
        po_no, _, _ = send_p(shop, outbox)
    pin_today(date(2026, 11, 30))
    before = _dates(shop)

    # when the model labels the reply an acknowledgement, with the date it gives (code decides, rule 2)
    done, _ = _read(
        shop, po_no, f"All goods against {po_no} will be delivered on 12 Dec 2026.",
        _reading("ack", new_date=date(2026, 12, 12)),
    )  # fmt: skip

    # then nothing moves and nothing is acknowledged by it; each line is proposed to the buyer on one task
    assert done is not None and done.status == "task" and done.kind == "ack"
    assert _dates(shop) == before
    rows = [
        tuple(r) for r in shop.execute(text("select po_line_no, new_value, task_id from po_change order by po_line_no"))
    ]
    assert [r[:2] for r in rows] == [
        (1, "2026-12-12"),
        (2, "2026-12-12"),
        (3, "2026-12-12"),
    ]
    assert len({r[2] for r in rows}) == 1
    status = "acknowledged" if acknowledged else "sent"
    assert shop.execute(text("select status from po")).scalar_one() == status


def test_a_reply_labelled_an_acknowledgement_with_a_date_on_or_before_every_line_is_only_noted(
    shop: Session,
    outbox: Outbox,  # noqa: F811
) -> None:
    po_no, _ = _acknowledged_k4(shop, outbox)
    pin_today(date(2026, 11, 30))

    done, _ = _read(
        shop,
        po_no,
        "We confirm delivery by 7 Dec 2026.",
        _reading("ack", new_date=date(2026, 12, 7)),
    )

    assert done is not None and done.status == "noted"
    assert count(shop, "select count(*) from po_change") == 0
