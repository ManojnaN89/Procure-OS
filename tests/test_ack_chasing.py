"""Chasing an order the supplier has not acknowledged (block 8 K7, K7b, 8.8): on the working days of the place it delivers
to, the first chaser once the current version is overdue, then every 'supplier_chase_days' working days, at most
'supplier_chase_max' per version, each with a fresh link and the version's document; then one task for the buyer that
escalates to the purchase manager; a new version starts again; the kill switch stops the mail."""

from datetime import UTC, date, datetime
from decimal import Decimal

from confirmation_support import audits, count, send_p, token_in
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session
from supplier_answer_support import answer
from test_orders import _headers, shop  # noqa: F401 - fixtures
from test_sourcing import Outbox, db, outbox  # noqa: F401 - fixtures

from p2p.agents import autonomy, supplier_comms
from p2p.formulas.calendar import pin_today
from p2p.main import app
from p2p.orders import changes, dispatch
from p2p.platform import alerts, exceptions, reference, sod

client = TestClient(app)
D = Decimal
SENT = date(2026, 11, 26)  # a Thursday


def _k7(shop: Session, outbox: Outbox) -> tuple[str, dict[str, int]]:
    po_no, logins, _ = send_p(shop, outbox, on=SENT)
    shop.execute(text("update org_user set designation = 'Purchase Manager' where code = 'U5'"))
    shop.commit()
    autonomy.configure(shop, sod.user(1), supplier_comms.DECISION_REMIND, level="auto")
    return po_no, logins


def _run(shop: Session, day: date) -> supplier_comms.ChaseResult:
    pin_today(day)
    return supplier_comms.chase_run(
        shop, shop.info["slug"], as_of=day, now=datetime(day.year, day.month, day.day, 4, 15, tzinfo=UTC)
    )


def test_an_order_never_acknowledged_is_chased_on_working_days_then_handed_to_the_buyer(
    shop: Session,
    outbox: Outbox,  # noqa: F811
) -> None:
    po_no, logins = _k7(shop, outbox)
    po = dict(shop.execute(text("select * from po")).mappings().one())
    assert dispatch.ack_due_on(shop, po) == date(2026, 11, 28)  # Fri 27, Sat 28: the place works Saturdays

    chased: dict[date, list[tuple[str, str]]] = {}
    handed: dict[date, list[str]] = {}
    for n in range(27, 31):
        day = date(2026, 11, n)
        got = _run(shop, day)
        chased[day], handed[day] = got.emailed, got.overdue
    for n in range(1, 13):
        day = date(2026, 12, n)
        got = _run(shop, day)
        chased[day], handed[day] = got.emailed, got.overdue

    days = [d for d, e in chased.items() if e]
    assert days == [date(2026, 11, 30), date(2026, 12, 3), date(2026, 12, 7)]  # none on Sun 29 Nov
    assert [d for d, h in handed.items() if h] == [date(2026, 12, 10)]
    shop.rollback()
    title, assignee, chain, payload = shop.execute(
        text("select title, assignee, escalation_chain, payload from task where kind = 'po_ack_overdue'")
    ).one()
    assert title == f"Alpha Traders has not acknowledged {po_no} version 1 after 3 reminders"
    assert assignee == int(
        shop.execute(text("select user_id from user_profile where role = 'Buyer' order by user_id")).scalar()
    )
    assert chain == [logins["procurement"]]
    assert payload["version"] == 1 and payload["chasers"] == 3 and payload["sent_on"] == "2026-11-26"
    assert (
        payload["next"]
        == "Call the supplier, record its acknowledgement with a note, send the order again, or cancel it"
    )
    assert len(audits(shop, "po.ack_overdue")) == 1
    numbers = [a["number"] for a in audits(shop, "supplier.chase")]
    assert numbers == [1, 2, 3] and {a["version"] for a in audits(shop, "supplier.chase")} == {1}

    # each chaser: the fixed words, a fresh link for version 1 that asks for a code, the version's document
    chasers = [m for m in outbox.sent if m["subject"].startswith("Reminder: please accept purchase order")]
    assert len(chasers) == 3 and len({token_in(m["body"]) for m in chasers}) == 3
    assert (
        f"We sent you purchase order {po_no} (version 1) on 26 Nov 2026 and have not received your acceptance. "
        "Please accept it, or tell us what you can do, here (the link works once and asks for a code we email to you): "
        in chasers[0]["body"]
    )
    assert all(m.get("document_ids") or m.get("attachments") for m in chasers)
    assert count(shop, "select count(*) from email_message where subject like 'Reminder:%' and po_version = 1") == 3

    # a second run on the day adds nothing; the alert and the exceptions screen show it from the day after it fell due
    assert (
        _run(shop, date(2026, 12, 10)).emailed == []
        and count(shop, "select count(*) from task where kind = 'po_ack_overdue'") == 1
    )
    ctx = alerts.Context(shop, date(2026, 12, 10), reference.calendar_for(shop))
    (finding,) = alerts.orders_not_acknowledged(ctx)
    assert finding.title == f"{po_no} version 1 has not been acknowledged by Alpha Traders"
    assert finding.detail == "Sent on 26 Nov 2026; 3 reminders." and finding.since == date(2026, 11, 29)
    kinds = {s.kind for s in exceptions.collect(shop, date(2026, 12, 10), reference.calendar_for(shop))}
    assert {"supplier", "po_ack_overdue"} <= kinds

    # once acknowledged, the task is done by the system
    token = token_in(chasers[-1]["body"])
    assert answer(client, outbox.sent, token, {"action": "accept"}).status_code == 200
    assert shop.execute(text("select status from task where kind = 'po_ack_overdue'")).scalar_one() == "done"


def test_a_new_version_starts_the_clock_and_the_count_again(shop: Session, outbox: Outbox) -> None:  # noqa: F811
    po_no, logins = _k7(shop, outbox)
    for day in (date(2026, 11, 30), date(2026, 12, 3), date(2026, 12, 7)):
        assert _run(shop, day).emailed == [(po_no, "ack")]
    pin_today(date(2026, 12, 8))
    changes.revise(shop, shop.info["slug"], po_no, logins["buyer"], "plan cut", [changes.LineChange(3, qty=D(140))])
    po = dict(shop.execute(text("select * from po")).mappings().one())

    assert dispatch.ack_due_on(shop, po) == date(2026, 12, 10)
    assert _run(shop, date(2026, 12, 10)).overdue == [] and _run(shop, date(2026, 12, 10)).emailed == []
    assert _run(shop, date(2026, 12, 11)).emailed == [(po_no, "ack")]
    (last,) = [a for a in audits(shop, "supplier.chase") if a["version"] == 2]
    assert last["number"] == 1
    assert count(shop, "select count(*) from task where kind = 'po_ack_overdue'") == 0


def test_a_revision_completes_the_overdue_task(shop: Session, outbox: Outbox) -> None:  # noqa: F811
    po_no, logins = _k7(shop, outbox)
    for day in (date(2026, 11, 30), date(2026, 12, 3), date(2026, 12, 7), date(2026, 12, 10)):
        _run(shop, day)
    assert count(shop, "select count(*) from task where kind = 'po_ack_overdue' and status = 'open'") == 1

    changes.revise(shop, shop.info["slug"], po_no, logins["buyer"], "plan cut", [changes.LineChange(3, qty=D(140))])

    assert count(shop, "select count(*) from task where kind = 'po_ack_overdue' and status = 'done'") == 1


def test_the_kill_switch_stops_the_chasers_mail(shop: Session, outbox: Outbox) -> None:  # noqa: F811
    po_no, _ = _k7(shop, outbox)
    autonomy.set_kill_switch(shop, sod.user(1), True, "incident")
    outbox.sent.clear()

    got = _run(shop, date(2026, 11, 30))

    assert got.emailed == [] and got.tasks == [(po_no, "ack")] and outbox.sent == []


def test_an_order_page_shows_the_due_day_and_the_chasers_of_its_version(shop: Session, outbox: Outbox) -> None:  # noqa: F811
    po_no, logins = _k7(shop, outbox)
    _run(shop, date(2026, 11, 30))

    page = client.get(f"/api/purchase-orders/{po_no}", headers=_headers(shop.info["slug"], logins["buyer"], "Buyer"))

    body = page.json()
    assert body["ack_due_on"] == "2026-11-28" and body["ack_overdue"] is True
    assert [(c["version"], c["number"]) for c in body["chasers"]] == [(1, 1)]
    assert body["can_acknowledge"] is True and body["can_resend_link"] is True
