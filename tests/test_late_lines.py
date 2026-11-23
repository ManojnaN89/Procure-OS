"""Line dates downstream (block 8 K9, 8.6): a line is due on its promised date, else its need-by date; the late-delivery
alert, the exceptions screen and the store's list go line by line, a line with only a need-by date counts, and the
delivery reminders count the working days of the place the order delivers to."""

from datetime import date
from decimal import Decimal

from confirmation_support import send_p
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session
from supplier_answer_support import answer
from test_orders import _headers, shop  # noqa: F401 - fixtures
from test_sourcing import Outbox, db, outbox  # noqa: F401 - fixtures

from p2p.formulas import receiving as receiving_rules
from p2p.formulas.calendar import pin_today
from p2p.main import app
from p2p.orders import delivery_reminders
from p2p.platform import alerts, exceptions, reference

client = TestClient(app)


def _k4(shop: Session, outbox: Outbox) -> tuple[str, dict[str, int]]:
    po_no, logins, token = send_p(shop, outbox)
    assert answer(client, outbox.sent, token, {"action": "accept", "date_2": "2026-12-09"}).status_code == 200
    return po_no, logins


def _receive_line_1(shop: Session) -> None:
    qty = Decimal(shop.execute(text("select qty from po_line where line_no = 1")).scalar_one())
    shop.execute(
        text("update po_line set received_qty = qty, status = :s where line_no = 1"),
        {"s": receiving_rules.line_status(qty, qty)},
    )
    shop.execute(text("update po set status = 'part_received'"))
    shop.commit()


def test_late_lines_are_listed_line_by_line_and_a_line_received_is_not(shop: Session, outbox: Outbox) -> None:  # noqa: F811
    po_no, logins = _k4(shop, outbox)
    _receive_line_1(shop)
    today = date(2026, 12, 10)
    pin_today(today)

    (found,) = alerts.late_deliveries(alerts.Context(shop, today, reference.calendar_for(shop)))

    assert found.title == f"Goods on {po_no} are 1 day late"
    assert found.detail == "1 line late: line 2 (Seal) due 09 Dec 2026 (promised)" and found.since == date(2026, 12, 9)
    rows = [s for s in exceptions.collect(shop, today, reference.calendar_for(shop)) if s.kind == "supplier"]
    assert any("line 2 (Seal) due 09 Dec 2026 (promised)" in s.reason for s in rows)
    (waiting,) = client.get(
        "/api/receiving/waiting", headers=_headers(shop.info["slug"], logins["keeper"], "Store")
    ).json()
    assert [(ln["line_no"], ln["due"], ln["due_source"], ln["late"]) for ln in waiting["lines"]] == [
        (2, "2026-12-09", "promised", True), (3, "2026-12-14", "promised", False),
    ]  # fmt: skip
    assert waiting["promised_date"] == "2026-12-14"  # the order's latest line date stays


def test_a_line_with_only_its_need_by_date_is_late_after_it(shop: Session, outbox: Outbox) -> None:  # noqa: F811
    po_no, logins, _ = send_p(shop, outbox)  # sent, never acknowledged: no promised dates
    today = date(2026, 12, 8)

    (found,) = alerts.late_deliveries(alerts.Context(shop, today, reference.calendar_for(shop)))

    assert found.title == f"Goods on {po_no} are 1 day late"
    assert (
        found.detail
        == "2 lines late: line 1 (Bearing) due 07 Dec 2026 (needed), line 2 (Seal) due 07 Dec 2026 (needed)"
    )
    pin_today(today)
    (waiting,) = client.get(
        "/api/receiving/waiting", headers=_headers(shop.info["slug"], logins["keeper"], "Store")
    ).json()
    assert [(ln["due_source"], ln["late"]) for ln in waiting["lines"]] == [
        ("need_by", True), ("need_by", True), ("need_by", False),
    ]  # fmt: skip


def test_reminders_go_per_line_on_the_working_days_of_the_delivery_place(shop: Session, outbox: Outbox) -> None:  # noqa: F811
    po_no, _ = _k4(shop, outbox)
    slug = shop.info["slug"]

    rows = "select kind, promised_date from delivery_reminder order by id"
    made: dict[date, list[tuple[str, date]]] = {}
    for n in range(3, 19):  # the daily job, every day from 3 to 18 Dec; line 1 arrives in full on Mon 7 Dec
        day = date(2026, 12, n)
        if day == date(2026, 12, 7):
            _receive_line_1(shop)
        before = len(shop.execute(text(rows)).all())
        delivery_reminders.run(shop, slug, as_of=day)
        made[day] = [tuple(r) for r in shop.execute(text(rows)).all()[before:]]  # type: ignore[misc]

    def days(kind: str, due: date) -> list[date]:
        return [d for d, new in made.items() if (kind, due) in new]

    # before: line 1 (7 Dec) on Fri 4 Dec, line 2 (9 Dec) on Mon 7 Dec, line 3 (14 Dec) on Fri 11 Dec (Saturdays work)
    assert days("before", date(2026, 12, 7)) == [date(2026, 12, 4)]
    assert days("before", date(2026, 12, 9)) == [date(2026, 12, 7)]
    assert days("before", date(2026, 12, 14)) == [date(2026, 12, 11)]
    # nothing arrives on line 2: overdue on Thu 10, Mon 14 and Thu 17 Dec; line 1 arrived, so never overdue
    assert days("overdue", date(2026, 12, 9)) == [date(2026, 12, 10), date(2026, 12, 14), date(2026, 12, 17)]
    assert days("overdue", date(2026, 12, 7)) == [] and po_no
