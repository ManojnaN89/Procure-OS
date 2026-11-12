"""The supplier's answer to an order (plan step 8): the signed link in the order email (accept, decline, ask for a
change), the buyer's decision on each change (a new version of the order, the requester told, a price above the
tolerance for the purchase manager), dispatch notes for the store, and the reminders about the delivery date."""

import uuid
from datetime import date, timedelta
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session
from test_dispatch import _approved_order
from test_orders import PASSWORD, _headers, _logins, shop  # noqa: F401 - fixtures
from test_sourcing import Outbox, db, outbox  # noqa: F401 - fixtures
from test_supplier_agent import _handle, _placed, _reading

from p2p.formulas import supplier_changes as judge
from p2p.formulas.calendar import BusinessCalendar
from p2p.formulas.deliveries import before_reminder_due, overdue_reminder_due, overdue_reminder_on, working_days_before
from p2p.main import app
from p2p.orders import confirmation, delivery_reminders, dispatch, service, supplier_links
from p2p.platform import routing, sod, tenants, users

D = Decimal
client = TestClient(app)
HOLIDAY_MONDAY = date(2026, 11, 9)


# ---- the rules on dates and prices ----------------------------------------------------------------


@pytest.mark.parametrize(
    ("promised", "holidays", "remind_on"),
    [
        (date(2026, 11, 10), (), date(2026, 11, 6)),  # Tuesday: Monday is one, the Friday before is two
        (date(2026, 11, 10), (HOLIDAY_MONDAY,), date(2026, 11, 5)),  # the Monday a holiday: back to Thursday
        (date(2026, 11, 16), (), date(2026, 11, 12)),  # Monday: the weekend does not count
        (date(2026, 11, 15), (), date(2026, 11, 12)),  # a Sunday date counts from the Friday before it
    ],
)
def test_a_reminder_goes_exactly_two_working_days_before_over_a_weekend_or_holiday(
    promised: date, holidays: tuple[date, ...], remind_on: date
) -> None:
    calendar = BusinessCalendar.of(holidays)

    assert working_days_before(promised, 2, calendar) == remind_on
    assert not before_reminder_due(promised, 2, remind_on - timedelta(days=1), calendar)  # a day early: not yet
    assert before_reminder_due(promised, 2, remind_on, calendar)
    assert before_reminder_due(promised, 2, promised, calendar)  # a missed run still sends it up to the date
    assert not before_reminder_due(promised, 2, promised + timedelta(days=1), calendar)


@pytest.mark.parametrize(
    ("holidays", "first", "second"),
    [((), date(2026, 11, 9), date(2026, 11, 12)), ((HOLIDAY_MONDAY,), date(2026, 11, 10), date(2026, 11, 13))],
)
def test_an_overdue_reminder_goes_one_working_day_after_then_every_three_up_to_the_most(
    holidays: tuple[date, ...], first: date, second: date
) -> None:
    calendar, promised = BusinessCalendar.of(holidays), date(2026, 11, 6)  # due on a Friday

    assert overdue_reminder_on(promised, 1, 1, 3, calendar) == first
    assert not overdue_reminder_due(promised, 0, 1, 3, 3, first - timedelta(days=1), calendar)
    assert overdue_reminder_due(promised, 0, 1, 3, 3, first, calendar)
    assert overdue_reminder_on(promised, 2, 1, 3, calendar) == second
    assert not overdue_reminder_due(promised, 1, 1, 3, 3, second - timedelta(days=1), calendar)
    assert not overdue_reminder_due(promised, 3, 1, 3, 3, date(2027, 1, 1), calendar)  # the most were sent


@pytest.mark.parametrize(
    ("new", "ok"), [("102.00", True), ("102.01", False), ("90", True), ("100.01", True), ("150", False)]
)
def test_a_price_rise_is_the_buyers_up_to_the_tolerance_and_the_purchase_managers_above_it(new: str, ok: bool) -> None:
    assert judge.judge_price(D("100"), D(new), D("2")).ok is ok


# ---- a sent order, its link and the page -----------------------------------------------------------


def _sent(shop: Session, outbox: Outbox, *, requester: str | None = "U4") -> tuple[str, dict[str, int], str]:
    """An order sent with the link: returns the order, the logins and the token in its email."""
    logins = _logins(shop)
    po_no = _approved_order(shop, logins)
    if requester:
        shop.execute(text("update request set requester = :r"), {"r": requester})
        shop.commit()
    outbox.sent.clear()
    dispatch.send_po(shop, po_no, sod.user(logins["buyer"]), tenant=shop.info["slug"])
    return po_no, logins, _token(outbox.sent[-1]["body"])


def _token(body: str) -> str:
    return body.split("/supplier/")[1].split()[0]


def _audit(shop: Session, action: str) -> int:
    shop.rollback()
    return int(shop.execute(text("select count(*) from audit_event where action = :a"), {"a": action}).scalar_one())


def test_the_supplier_accepts_from_the_link_once_and_gets_a_link_for_the_dispatch(
    shop: Session, outbox: Outbox
) -> None:  # noqa: F811
    # given an order sent with its link
    po_no, _, token = _sent(shop, outbox)
    shown = client.get(f"/supplier/{token}")
    assert shown.status_code == 200 and po_no in shown.text and "Bearing" in shown.text
    assert _audit(shop, "po.acknowledge") == 0  # opening the page changes nothing

    # when the supplier accepts with a date
    done = client.post(f"/supplier/{token}", data={"action": "accept", "promised": "2026-11-19"})

    # then the order is acknowledged with the promised date, the link is used up, and a dispatch link is emailed
    assert done.status_code == 200 and "accepted" in done.text
    shop.rollback()
    assert shop.execute(text("select status from po")).scalar_one() == "acknowledged"
    assert shop.execute(text("select promised_date from po_line")).scalar_one() == date(2026, 11, 19)
    assert _audit(shop, "po.acknowledge") == 1
    again = client.post(f"/supplier/{token}", data={"action": "accept"})
    assert again.status_code == 409 and "works once" in again.text
    assert "record what you sent" in outbox.sent[-1]["body"] and "/supplier/" in outbox.sent[-1]["body"]


def test_a_link_altered_expired_or_for_another_suppliers_order_is_refused(shop: Session, outbox: Outbox) -> None:  # noqa: F811
    po_no, _, token = _sent(shop, outbox)
    slug = shop.info["slug"]
    link = supplier_links.read(token)

    altered = client.get(f"/supplier/{token[:-3]}abc")
    expired = supplier_links.SIGNER.sign([slug, link.id, po_no, "S1", "respond", 1_000])
    shop.execute(
        text(
            "insert into supplier_link (po_no, supplier_code, purpose, po_version, expires_at) "
            "values (:p, 'S2', 'respond', 1, now() + interval '1 day')"
        ),
        {"p": po_no},
    )
    other_id = shop.execute(text("select max(id) from supplier_link")).scalar_one()
    shop.commit()
    far = 4_102_444_800  # 2100
    others = supplier_links.SIGNER.sign([slug, other_id, po_no, "S2", "respond", far])
    renamed = supplier_links.SIGNER.sign([slug, link.id, po_no, "S2", "respond", far])
    approval = __import__("p2p.approvals.email_links", fromlist=["SIGNER"]).SIGNER.sign(
        [slug, link.id, po_no, "S1", "respond", far]
    )

    assert altered.status_code == 400 and "not valid" in altered.text
    assert client.get(f"/supplier/{expired}").status_code == 400
    assert "expired" in client.get(f"/supplier/{expired}").text
    for_other = client.post(f"/supplier/{others}", data={"action": "accept"})
    assert for_other.status_code == 409 and "not placed with you" in for_other.text
    assert client.get(f"/supplier/{renamed}").status_code == 409  # the token and its row disagree
    assert client.get(f"/supplier/{approval}").status_code == 400  # a key for another purpose is not ours
    shop.rollback()
    assert shop.execute(text("select status from po")).scalar_one() == "sent"
    assert shop.execute(text("select count(*) from supplier_link where used_at is not null")).scalar_one() == 0


def test_a_link_for_an_order_that_changed_since_is_refused(shop: Session, outbox: Outbox) -> None:  # noqa: F811
    po_no, logins, token = _sent(shop, outbox)
    from p2p.orders import changes

    changes.revise(shop, shop.info["slug"], po_no, logins["buyer"], "less", [changes.LineChange(1, qty=D(8))])

    got = client.get(f"/supplier/{token}")

    assert got.status_code == 409 and "has changed since" in got.text


# ---- a date change: version 2, the requester told (the plan's "done when") ------------------------------


def test_a_date_change_from_the_link_makes_version_2_and_tells_the_requester(shop: Session, outbox: Outbox) -> None:  # noqa: F811
    # given the bearing order, needed by 20 Nov, and the supplier asks to move it to 6 Dec
    po_no, logins, token = _sent(shop, outbox)
    asked = client.post(
        f"/supplier/{token}", data={"action": "change", "date_1": "2026-12-06", "reason": "casting delay"}
    )
    assert asked.status_code == 200
    shop.rollback()
    assert shop.execute(text("select version, status from po")).one() == (1, "sent")  # nothing moved yet
    buyer = _headers(shop.info["slug"], logins["buyer"], "Buyer")
    (change,) = client.get(f"/api/purchase-orders/{po_no}/supplier-answer", headers=buyer).json()["changes"]
    assert (change["kind"], change["old_value"], change["new_value"], change["status"]) == (
        "date", "2026-11-20", "2026-12-06", "proposed",
    )  # fmt: skip
    assert change["can_decide"] is True

    # when the buyer accepts it
    done = client.post(f"/api/supplier-changes/{change['id']}/decide", json={"accept": True}, headers=buyer)

    # then the order is version 2 with the new date, the supplier has the revised order, the requester is told
    assert done.status_code == 200 and done.json()["version"] == 2
    shop.rollback()
    assert shop.execute(text("select version, status from po")).one() == (2, "acknowledged")
    assert shop.execute(text("select need_by, promised_date from po_line")).one() == (date(2026, 12, 6),) * 2
    assert shop.execute(text("select version, reapproval from po_revision")).one() == (2, False)
    keeper = shop.execute(
        text("select title from notification where user_id = :u and title like '%version 2%'"), {"u": logins["keeper"]}
    ).scalar()
    assert keeper == f"Order {po_no} changed (version 2)"
    assert any(f"{po_no} (version 2)" in m["subject"] for m in outbox.sent)
    assert [_audit(shop, a) for a in ("po.change_proposed", "po.change_accepted", "po.revise")] == [1, 1, 1]
    again = client.post(f"/api/supplier-changes/{change['id']}/decide", json={"accept": True}, headers=buyer)
    assert again.status_code == 409


def test_a_rejected_change_leaves_the_order_and_gives_the_supplier_a_new_link(shop: Session, outbox: Outbox) -> None:  # noqa: F811
    po_no, logins, token = _sent(shop, outbox)
    client.post(f"/supplier/{token}", data={"action": "change", "qty_1": "8"})
    shop.rollback()
    change_id = shop.execute(text("select id from po_change")).scalar_one()
    buyer = _headers(shop.info["slug"], logins["buyer"], "Buyer")

    no_reason = client.post(f"/api/supplier-changes/{change_id}/decide", json={"accept": False}, headers=buyer)
    done = client.post(
        f"/api/supplier-changes/{change_id}/decide", json={"accept": False, "note": "we need all ten"}, headers=buyer
    )

    assert no_reason.status_code == 409 and done.status_code == 200
    shop.rollback()
    assert shop.execute(text("select version, status from po")).one() == (1, "sent")
    assert shop.execute(text("select qty from po_line")).scalar_one() == D(10)
    told = outbox.sent[-1]
    assert "change not accepted" in told["subject"] and "we need all ten" in told["body"]
    assert client.get(f"/supplier/{_token(told['body'])}").status_code == 200  # a fresh link to answer
    assert _audit(shop, "po.change_rejected") == 1


def test_a_requester_cannot_decide_a_suppliers_change(shop: Session, outbox: Outbox) -> None:  # noqa: F811
    po_no, logins, token = _sent(shop, outbox)
    client.post(f"/supplier/{token}", data={"action": "change", "date_1": "2026-12-06"})
    shop.rollback()
    change_id = shop.execute(text("select id from po_change")).scalar_one()

    got = client.post(
        f"/api/supplier-changes/{change_id}/decide",
        json={"accept": True},
        headers=_headers(shop.info["slug"], logins["keeper"], "Requester"),
    )

    assert got.status_code == 403
    shop.rollback()
    assert shop.execute(text("select version from po")).scalar_one() == 1


# ---- a price change: within the tolerance the buyer, above it the purchase manager --------------------


def _price_rule(shop: Session) -> None:
    shop.execute(
        text(
            "insert into approval_rule (rule_code, document, min_value, max_value, category_code, department_code, "
            "spend_type, approver_chain, version, effective_from) values ('VAR-01', 'price_variance', 0, 999999999, "
            "'ANY', 'ANY', 'ANY', 'procurement_head', 1, '2024-11-01')"
        )
    )
    shop.commit()


def test_a_price_rise_within_the_tolerance_is_accepted_by_the_buyer(shop: Session, outbox: Outbox) -> None:  # noqa: F811
    po_no, logins, token = _sent(shop, outbox)
    client.post(f"/supplier/{token}", data={"action": "change", "price_1": "102.00"})  # 2% on 100: the edge, inside
    shop.rollback()
    change_id, within = shop.execute(text("select id, within_tolerance from po_change")).one()
    buyer = _headers(shop.info["slug"], logins["buyer"], "Buyer")

    done = client.post(f"/api/supplier-changes/{change_id}/decide", json={"accept": True}, headers=buyer)

    assert within is True and done.status_code == 200 and done.json()["version"] == 2
    shop.rollback()
    line = shop.execute(text("select unit_price, taxable, igst from po_line")).one()
    assert line == (D("102.0000"), D("1020.00"), D("183.60"))
    assert shop.execute(text("select total, status from po")).one() == (D("1203.60"), "acknowledged")
    assert shop.execute(text("select count(*) from proposal where action = 'po.supplier_price'")).scalar_one() == 0


def test_a_price_rise_just_above_the_tolerance_needs_the_purchase_manager(shop: Session, outbox: Outbox) -> None:  # noqa: F811
    _price_rule(shop)
    po_no, logins, token = _sent(shop, outbox)
    client.post(f"/supplier/{token}", data={"action": "change", "price_1": "102.01", "reason": "steel price"})
    shop.rollback()
    change_id, within = shop.execute(text("select id, within_tolerance from po_change")).one()
    slug = shop.info["slug"]
    buyer, director = _headers(slug, logins["buyer"], "Buyer"), _headers(slug, logins["director"], "Approver")
    manager = _headers(slug, logins["procurement"], "Approver")

    # the buyer's acceptance only refers it: a proposal for the person the price variance rule names
    referred = client.post(f"/api/supplier-changes/{change_id}/decide", json={"accept": True}, headers=buyer)
    assert within is False and referred.status_code == 200
    assert referred.json()["status"] == "waiting_approval" and referred.json()["approver"] == "Procure Vikram"
    shop.rollback()
    assert shop.execute(text("select unit_price, version from po_line join po on po.no = po_line.po_no")).one() == (
        D("100.0000"), 1,
    )  # fmt: skip
    task = shop.execute(text("select assignee from task where kind = 'supplier_price_change'")).scalar_one()
    assert task == logins["procurement"]
    # the buyer and another approver cannot accept it; the purchase manager can
    assert (
        client.post(f"/api/supplier-changes/{change_id}/decide", json={"accept": True}, headers=buyer).status_code
        == 403
    )
    assert (
        client.post(f"/api/supplier-changes/{change_id}/decide", json={"accept": True}, headers=director).status_code
        == 403
    )
    done = client.post(f"/api/supplier-changes/{change_id}/decide", json={"accept": True}, headers=manager)

    assert done.status_code == 200
    shop.rollback()
    assert shop.execute(text("select unit_price from po_line")).scalar_one() == D("102.0100")
    assert shop.execute(text("select version from po")).scalar_one() == 2
    assert shop.execute(text("select status, decided_by from proposal")).one() == ("approved", logins["procurement"])
    assert _audit(shop, "po.change_referred") == 1 and _audit(shop, "proposal.approved") == 1


def test_the_purchase_manager_may_reject_a_price_rise(shop: Session, outbox: Outbox) -> None:  # noqa: F811
    _price_rule(shop)
    po_no, logins, token = _sent(shop, outbox)
    client.post(f"/supplier/{token}", data={"action": "change", "price_1": "130"})
    shop.rollback()
    change_id = shop.execute(text("select id from po_change")).scalar_one()
    slug = shop.info["slug"]
    client.post(
        f"/api/supplier-changes/{change_id}/decide",
        json={"accept": True},
        headers=_headers(slug, logins["buyer"], "Buyer"),
    )

    got = client.post(
        f"/api/supplier-changes/{change_id}/decide",
        json={"accept": False, "note": "too high"},
        headers=_headers(slug, logins["procurement"], "Approver"),
    )

    assert got.status_code == 200
    shop.rollback()
    assert shop.execute(text("select status from po_change")).scalar_one() == "rejected"
    assert shop.execute(text("select status from proposal")).scalar_one() == "rejected"
    assert shop.execute(text("select unit_price from po_line")).scalar_one() == D("100.0000")


# ---- a decline -----------------------------------------------------------------------------------------


def test_a_decline_accepted_cancels_the_order_and_returns_the_line_to_the_buyer(shop: Session, outbox: Outbox) -> None:  # noqa: F811
    po_no, logins, token = _sent(shop, outbox)
    no_reason = client.post(f"/supplier/{token}", data={"action": "decline", "reason": " "})
    assert no_reason.status_code == 409  # a reason is needed, and the link still works
    declined = client.post(f"/supplier/{token}", data={"action": "decline", "reason": "plant shut for a month"})
    assert declined.status_code == 200
    shop.rollback()
    assert shop.execute(text("select status from po")).scalar_one() == "sent"
    change_id = shop.execute(text("select id from po_change where kind = 'decline'")).scalar_one()

    done = client.post(
        f"/api/supplier-changes/{change_id}/decide",
        json={"accept": True},
        headers=_headers(shop.info["slug"], logins["buyer"], "Buyer"),
    )

    assert done.status_code == 200
    shop.rollback()
    assert shop.execute(text("select status from po")).scalar_one() == "cancelled"
    assert shop.execute(text("select status from request_line")).scalar_one() == "open"
    method, reason = shop.execute(text("select method, reason from sourcing_decision")).one()
    assert method == "manual" and "plant shut for a month" in reason
    assert shop.execute(text("select count(*) from task where kind = 'source_again'")).scalar_one() == 1
    # nothing orders it from the same supplier by itself
    assert service.sweep(shop, shop.info["slug"], as_of=date(2026, 11, 6)) == []
    assert shop.execute(text("select count(*) from po")).scalar_one() == 1


# ---- dispatch notes ---------------------------------------------------------------------------------


def test_a_dispatch_note_for_part_of_the_quantity_leaves_the_rest_to_ship(shop: Session, outbox: Outbox) -> None:  # noqa: F811
    po_no, logins, token = _sent(shop, outbox)
    client.post(f"/supplier/{token}", data={"action": "accept"})
    dispatch_token = _token(outbox.sent[-1]["body"])

    too_many = client.post(f"/supplier/{dispatch_token}", data={"action": "dispatch", "ship_1": "11"})
    future = client.post(
        f"/supplier/{dispatch_token}", data={"action": "dispatch", "ship_1": "4", "shipped_on": "2026-11-09"}
    )
    part = client.post(
        f"/supplier/{dispatch_token}",
        data={"action": "dispatch", "ship_1": "4", "shipped_on": "2026-11-06", "expected_on": "2026-11-10",
              "vehicle": "MH12AB0000", "lr_no": "LR-881", "invoice_no": "INV-77"},
    )  # fmt: skip

    assert too_many.status_code == 409 and "more than the 10 still to ship" in too_many.text
    assert future.status_code == 409 and "future" in future.text
    assert part.status_code == 200 and "still to ship" in part.text
    shop.rollback()
    assert shop.execute(text("select count(*) from dispatch_note")).scalar_one() == 1
    store = _headers(shop.info["slug"], logins["keeper"], "Store")
    (waiting,) = client.get("/api/receiving/waiting", headers=store).json()
    (note,) = waiting["dispatches"]
    assert (note["vehicle"], note["lr_no"], note["invoice_no"], note["expected_on"]) == (
        "MH12AB0000", "LR-881", "INV-77", "2026-11-10",
    )  # fmt: skip
    assert note["lines"] == [{"line_no": 1, "name": "Bearing", "qty": "4", "uom": "NOS"}]
    assert shop.execute(text("select received_qty from po_line")).scalar_one() == 0  # a note receives nothing
    # the used link is spent; the rest goes on the new link emailed for it
    assert client.post(f"/supplier/{dispatch_token}", data={"action": "dispatch", "ship_1": "6"}).status_code == 409
    rest = _token(outbox.sent[-1]["body"])
    assert client.post(f"/supplier/{rest}", data={"action": "dispatch", "ship_1": "7"}).status_code == 409
    assert client.post(f"/supplier/{rest}", data={"action": "dispatch", "ship_1": "6"}).status_code == 200
    assert _audit(shop, "po.dispatch_note") == 2


# ---- by email: the agent proposes, the buyer decides -------------------------------------------------


def test_a_late_date_by_email_becomes_a_proposed_change_for_the_buyer(shop: Session, outbox: Outbox) -> None:  # noqa: F811
    po_no = _placed(shop)  # promised 20 Nov; the company accepts 3 days by itself

    handled, _ = _handle(
        shop, po_no, "We can deliver only on 6 Dec 2026.", _reading("date_change", new_date=date(2026, 12, 6))
    )

    assert handled is not None and handled.status == "task"
    shop.rollback()
    change = shop.execute(text("select kind, old_value, new_value, source, task_id from po_change")).one()
    task = shop.execute(text("select id from task where kind = 'supplier_change'")).scalar_one()
    assert change == ("date", "2026-11-20", "2026-12-06", "email", task)
    assert shop.execute(text("select version from po")).scalar_one() == 1


def test_a_dispatch_by_email_is_stored_for_the_store(shop: Session, outbox: Outbox) -> None:  # noqa: F811
    po_no = _placed(shop)

    handled, _ = _handle(
        shop,
        po_no,
        "Dispatched 4 nos Bearing today by vehicle MH12AB0000, LR LR-12.",
        _reading("dispatch", qty=D(4), item="Bearing", vehicle="MH12AB0000", lr_no="LR-12"),
    )

    assert handled is not None and handled.status == "applied" and "(part)" in handled.action
    shop.rollback()
    assert shop.execute(text("select vehicle_no, lr_no, source from dispatch_note")).one() == (
        "MH12AB0000",
        "LR-12",
        "email",
    )
    assert shop.execute(text("select qty from dispatch_note_line")).scalar_one() == D(4)


# ---- delivery reminders ------------------------------------------------------------------------------


def test_delivery_reminders_go_once_before_and_after_the_date_while_nothing_has_arrived(
    shop: Session,
    outbox: Outbox,  # noqa: F811
) -> None:
    po_no = _placed(shop, promised=date(2026, 11, 10))  # a Tuesday
    slug = shop.info["slug"]

    def run(day: date) -> list[tuple[str, str, int]]:
        outbox.sent.clear()
        return delivery_reminders.run(shop, slug, as_of=day).sent

    assert run(date(2026, 11, 5)) == []
    assert run(date(2026, 11, 6)) == [(po_no, "before", 1)]  # two working days before
    assert "due on 10 Nov 2026" in outbox.sent[0]["body"] and "/supplier/" in outbox.sent[0]["body"]
    assert run(date(2026, 11, 6)) == [] and outbox.sent == []  # once
    assert run(date(2026, 11, 10)) == []
    assert run(date(2026, 11, 11)) == [(po_no, "overdue", 1)]  # one working day after
    assert run(date(2026, 11, 13)) == []
    assert run(date(2026, 11, 16)) == [(po_no, "overdue", 2)]  # three working days on
    shop.execute(text("update po_line set received_qty = 1"))
    shop.commit()
    assert run(date(2026, 11, 30)) == []  # something arrived: the supplier agent chases the balance instead
    assert _audit(shop, "po.delivery_reminder") == 3


def test_a_failed_reminder_email_is_not_recorded_and_goes_next_time(shop: Session, outbox: Outbox) -> None:  # noqa: F811
    po_no = _placed(shop, promised=date(2026, 11, 10))
    outbox.refuse = {"s1@supplier.example"}

    first = delivery_reminders.run(shop, shop.info["slug"], as_of=date(2026, 11, 6))
    outbox.refuse = set()
    second = delivery_reminders.run(shop, shop.info["slug"], as_of=date(2026, 11, 6))

    assert first.failed == [(po_no, "before")] and second.sent == [(po_no, "before", 1)]
    assert shop.execute(text("select count(*) from delivery_reminder")).scalar_one() == 1


def test_the_kill_switch_stops_delivery_reminders(shop: Session, outbox: Outbox) -> None:  # noqa: F811
    _placed(shop, promised=date(2026, 11, 10))
    shop.execute(text("update autonomy_global set kill_switch = true"))
    shop.commit()

    got = delivery_reminders.run(shop, shop.info["slug"], as_of=date(2026, 11, 6))

    assert got.stopped and got.sent == []


# ---- one company never sees another's ------------------------------------------------------------------


def test_another_company_cannot_see_or_decide_a_suppliers_change(shop: Session, outbox: Outbox) -> None:  # noqa: F811
    po_no, _, token = _sent(shop, outbox)
    client.post(f"/supplier/{token}", data={"action": "change", "date_1": "2026-12-06"})
    shop.rollback()
    change_id = shop.execute(text("select id from po_change")).scalar_one()
    link_id = supplier_links.read(token).id
    other = "b" + uuid.uuid4().hex[:8]
    assert tenants.create_tenant(other, load_reference=False).ok
    try:
        theirs = users.create_user(other, f"buyer-{other}@x.example", PASSWORD, "Their Buyer", "Buyer")
        h = _headers(other, theirs, "Buyer")

        seen = client.get(f"/api/purchase-orders/{po_no}/supplier-answer", headers=h)
        decided = client.post(f"/api/supplier-changes/{change_id}/decide", json={"accept": True}, headers=h)
        crossed = client.get(
            f"/supplier/{supplier_links.SIGNER.sign([other, link_id, po_no, 'S1', 'respond', 4_102_444_800])}"
        )

        assert seen.status_code == 404 and decided.status_code == 404
        assert crossed.status_code == 409  # their database has no such link
    finally:
        routing.clear_engine_cache()
        tenants.drop_tenant(other)
    shop.rollback()
    assert shop.execute(text("select status from po_change")).scalar_one() == "proposed"


def test_the_confirmation_service_refuses_an_order_no_longer_with_the_supplier(shop: Session, outbox: Outbox) -> None:  # noqa: F811
    po_no, _, _ = _sent(shop, outbox)
    shop.execute(text("update po set status = 'received'"))
    shop.commit()

    with pytest.raises(confirmation.ConfirmationError, match="with the supplier"):
        confirmation.propose(
            shop, shop.info["slug"], po_no, [confirmation.Proposed("decline")], source="link", reason="x",
            actor=confirmation.supplier_actor("S1"),
        )  # fmt: skip
