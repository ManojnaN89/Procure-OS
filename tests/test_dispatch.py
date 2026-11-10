"""Sending an approved order to the supplier, and the supplier's acknowledgement."""

from datetime import UTC, date, datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session
from test_orders import TODAY, D, _headers, _logins, _price, _request, _source, shop  # noqa: F401 - fixtures
from test_sourcing import Outbox, db, outbox  # noqa: F401 - fixtures

from p2p.agents import autonomy
from p2p.main import app
from p2p.orders import dispatch, service, workflow
from p2p.platform import sod, tenants, worker

client = TestClient(app)


def _approved_order(shop: Session, logins: dict[str, int], qty: str = "10") -> str:
    """An order on the contract price of S1 (Maharashtra, GST 18%) approved by the procurement head."""
    _price(shop, "S1", "A", "100")
    request = _request(shop, f"R-{qty}", [("A", qty, None, "C1")])
    _source(shop, request, dispatch=False)
    made = service.order_request(shop, shop.info["slug"], request, as_of=TODAY)
    (po_no,) = made.waiting or made.pos
    if made.waiting:
        workflow.approve(shop, shop.info["slug"], po_no, logins["procurement"])
    return po_no


def test_an_approved_order_is_emailed_to_the_supplier_with_what_it_says(shop: Session, outbox: Outbox) -> None:  # noqa: F811
    logins = _logins(shop)
    po_no = _approved_order(shop, logins)
    outbox.sent.clear()

    to = dispatch.send_po(shop, po_no, sod.user(logins["buyer"]))

    assert to == "s1@supplier.example"
    (mail,) = outbox.sent
    assert mail["subject"] == f"Purchase order {po_no}" and mail["reply_to"].startswith(f"po+{po_no}@")
    body = mail["body"]
    assert "Dear Contact S1," in body and f"Order: {po_no}" in body and "Deliver to: Bengaluru store" in body
    assert "1. Bearing (A) - 10 NOS" in body and "GST 18%" in body
    assert "Order total:" in body and "1,180.00" in body  # 10 x 100 plus 18%
    status, sent_to, sent_at = shop.execute(text("select status, sent_to, sent_at is not null from po")).one()
    assert (status, sent_to, sent_at) == ("sent", "s1@supplier.example", True)
    assert shop.execute(text("select count(*) from audit_event where action = 'po.send'")).scalar_one() == 1
    assert shop.execute(text("select count(*) from email_message where linked_id = :p"), {"p": po_no}).scalar_one() == 1


@pytest.mark.parametrize("status", ["draft", "submitted", "sent", "cancelled"])
def test_only_an_approved_order_can_be_sent(shop: Session, outbox: Outbox, status: str) -> None:  # noqa: F811
    po_no = _approved_order(shop, _logins(shop))
    shop.execute(text("update po set status = :s"), {"s": status})
    shop.commit()
    outbox.sent.clear()

    with pytest.raises(dispatch.DispatchError, match=f"is {status}"):
        dispatch.send_po(shop, po_no)

    assert outbox.sent == []


def test_a_held_order_a_blocked_supplier_or_missing_address_stops_the_send(shop: Session, outbox: Outbox) -> None:  # noqa: F811
    po_no = _approved_order(shop, _logins(shop))
    outbox.sent.clear()
    shop.execute(text("update po set hold_reason = 'Price above the estimate'"))
    shop.commit()
    with pytest.raises(dispatch.DispatchError, match="on hold"):
        dispatch.send_po(shop, po_no)
    shop.execute(text("update po set hold_reason = null"))
    shop.execute(text("update supplier set status = 'blocked' where code = 'S1'"))
    shop.commit()
    with pytest.raises(dispatch.DispatchError, match="is blocked"):
        dispatch.send_po(shop, po_no)
    shop.execute(text("update supplier set status = 'active' where code = 'S1'"))
    shop.execute(text("update supplier_document set verified = false where supplier_code = 'S1' and doc_type = 'pan'"))
    shop.commit()
    with pytest.raises(dispatch.DispatchError, match="PAN card is not verified"):
        dispatch.send_po(shop, po_no)
    shop.execute(text("update supplier_document set verified = true where supplier_code = 'S1'"))
    shop.execute(text("delete from supplier_contact where supplier_code = 'S1'"))
    shop.commit()
    with pytest.raises(dispatch.DispatchError, match="no email address"):
        dispatch.send_po(shop, po_no)

    assert outbox.sent == [] and shop.execute(text("select status from po")).scalar_one() == "approved"


def test_a_failed_email_leaves_the_order_approved_to_be_sent_again(shop: Session, outbox: Outbox) -> None:  # noqa: F811
    po_no = _approved_order(shop, _logins(shop))
    outbox.refuse = {"s1@supplier.example"}

    with pytest.raises(dispatch.DispatchError, match="failed"):
        dispatch.send_po(shop, po_no)

    assert shop.execute(text("select status, sent_at from po")).one() == ("approved", None)
    outbox.refuse = set()
    assert dispatch.send_po(shop, po_no) == "s1@supplier.example"


def test_the_supplier_acknowledges_with_a_date_and_a_late_promise_is_flagged(shop: Session, outbox: Outbox) -> None:  # noqa: F811
    logins = _logins(shop)
    po_no = _approved_order(shop, logins)
    dispatch.send_po(shop, po_no)
    with pytest.raises(dispatch.DispatchError, match="does not exist"):
        dispatch.acknowledge(shop, "NOPE", sod.user(1), promised=None, note=None)

    # the line is needed by 20 Nov; 23 Nov is within the 3 days the company accepts, 24 Nov is not
    warnings = dispatch.acknowledge(
        shop, po_no, sod.user(logins["buyer"]), promised=date(2026, 11, 24), note="Dispatch Monday"
    )

    assert len(warnings) == 1 and "promised 24 Nov 2026, needed by 20 Nov 2026" in warnings[0]
    assert shop.execute(text("select status, ack_note, acknowledged_at is not null from po")).one() == (
        "acknowledged",
        "Dispatch Monday",
        True,
    )
    assert shop.execute(text("select promised_date from po_line")).scalar_one() == date(2026, 11, 24)
    with pytest.raises(dispatch.DispatchError, match="only a sent order"):
        dispatch.acknowledge(shop, po_no, sod.user(1), promised=None, note=None)


def test_a_promise_within_the_accepted_slip_is_not_flagged(shop: Session, outbox: Outbox) -> None:  # noqa: F811
    po_no = _approved_order(shop, _logins(shop))
    dispatch.send_po(shop, po_no)

    assert dispatch.acknowledge(shop, po_no, sod.user(1), promised=date(2026, 11, 23), note=None) == []


def test_an_order_not_acknowledged_in_two_working_days_is_overdue(shop: Session) -> None:  # noqa: F811
    friday = datetime(2026, 11, 6, 10, 0, tzinfo=UTC)

    assert dispatch.ack_overdue(shop, friday, date(2026, 11, 10)) is False  # Tuesday is the second working day
    assert dispatch.ack_overdue(shop, friday, date(2026, 11, 11)) is True
    assert dispatch.ack_overdue(shop, None, date(2026, 11, 11)) is False


def _queued_sends(slug: str) -> int:
    with tenants.control_engine().connect() as c:
        return int(
            c.execute(
                text("select count(*) from job where tenant_slug = :t and kind = 'send_po' and status = 'queued'"),
                {"t": slug},
            ).scalar_one()
        )


def test_by_default_a_person_sends_and_when_the_company_turns_that_off_the_worker_does(
    shop: Session, outbox: Outbox
) -> None:  # noqa: F811
    logins = _logins(shop)
    slug = shop.info["slug"]
    _approved_order(shop, logins)
    assert _queued_sends(slug) == 0  # nothing is queued: a buyer clicks Send

    shop.execute(text("insert into org_setting (key, value) values ('po_send_requires_confirmation', 'false')"))
    shop.commit()
    _price(shop, "S1", "D", "50")
    request = _request(shop, "R-D", [("D", "4", None, "C2")])
    _source(shop, request, dispatch=False)
    (second,) = service.order_request(shop, slug, request, as_of=TODAY).waiting
    workflow.approve(shop, slug, second, logins["procurement"])
    assert _queued_sends(slug) == 1
    outbox.sent.clear()

    job = worker.JobContext(id=-1, tenant=slug, payload={"po_no": second}, now=datetime.now(UTC))
    autonomy.set_kill_switch(shop, sod.user(1), True, "incident")
    dispatch._auto_send(job)
    assert outbox.sent == []  # the kill switch stops automatic sending
    autonomy.set_kill_switch(shop, sod.user(1), False, "over")
    dispatch._auto_send(job)

    assert (
        len(outbox.sent) == 1
        and shop.execute(text("select status from po where no = :n"), {"n": second}).scalar_one() == "sent"
    )


def test_the_order_page_offers_send_and_acknowledge_to_a_buyer_only_at_the_right_time(
    shop: Session, outbox: Outbox
) -> None:  # noqa: F811
    logins = _logins(shop)
    slug = shop.info["slug"]
    po_no = _approved_order(shop, logins)
    buyer, approver = _headers(slug, logins["buyer"], "Buyer"), _headers(slug, logins["procurement"], "Approver")

    page = client.get(f"/api/purchase-orders/{po_no}", headers=buyer).json()
    assert (page["can_send"], page["can_acknowledge"], page["sent_at"]) == (True, False, None)
    assert client.get(f"/api/purchase-orders/{po_no}", headers=approver).json()["can_send"] is False
    assert client.post(f"/api/purchase-orders/{po_no}/send", headers=approver).status_code == 403
    assert client.post(f"/api/purchase-orders/{po_no}/send").status_code == 401

    sent = client.post(f"/api/purchase-orders/{po_no}/send", headers=buyer)
    assert sent.status_code == 200 and sent.json()["sent_to"] == "s1@supplier.example"
    assert client.post(f"/api/purchase-orders/{po_no}/send", headers=buyer).status_code == 409  # not twice
    page = client.get(f"/api/purchase-orders/{po_no}", headers=buyer).json()
    assert (page["can_send"], page["can_acknowledge"], page["status"]) == (False, True, "sent")

    assert (
        client.post(
            f"/api/purchase-orders/{po_no}/acknowledge", json={"promised_date": "not a date"}, headers=buyer
        ).status_code
        == 422
    )
    acked = client.post(
        f"/api/purchase-orders/{po_no}/acknowledge", json={"promised_date": "2026-11-19"}, headers=buyer
    )
    assert acked.status_code == 200 and acked.json()["warnings"] == []
    page = client.get(f"/api/purchase-orders/{po_no}", headers=buyer).json()
    assert page["status"] == "acknowledged" and page["promised_date"] == "2026-11-19"
    assert page["lines"][0]["promised_date"] == "2026-11-19"
    assert D(page["total"]) == D("1180.00")
