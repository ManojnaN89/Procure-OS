"""Block 8's scenario P on the test company (spec section 9), shaped like Apex's: one order of three lines to S1,
delivered to a place that works Monday to Saturday and is closed on 24 Nov 2026, sent on Monday 23 Nov 2026.

Line 1: A 100 x 480.00 need-by 7 Dec; line 2: B 200 x 220.00 need-by 7 Dec; line 3: D 150 x 180.00 need-by 14 Dec.
"""

from datetime import date
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session
from test_orders import _logins
from test_sourcing import _price, _request, _source

from p2p.formulas.calendar import pin_today
from p2p.orders import dispatch, service, supplier_links, workflow
from p2p.platform import sod

SENT_ON = date(2026, 11, 23)  # a Monday
NEED_BY = {1: date(2026, 12, 7), 2: date(2026, 12, 7), 3: date(2026, 12, 14)}
LINES = (("A", "100", "480"), ("B", "200", "220"), ("D", "150", "180"))
PLACE = "Bengaluru store"


def plant_calendar(shop: Session) -> None:
    """The delivery place works Monday to Saturday and is closed on 24 Nov and 25 Dec 2026."""
    shop.execute(text("update location set working_days = 'Mon-Sat' where code = 'WH-BLR'"))
    shop.execute(
        text(
            "insert into ref_holiday (holiday_date, name, kind, scope) values ('2026-11-24', 'Guru Nanak Jayanti', "
            "'closed', 'central'), ('2026-12-25', 'Christmas', 'closed', 'central')"
        )
    )
    shop.commit()


def order_p(shop: Session, logins: dict[str, int] | None = None, *, no: str = "R-P") -> tuple[str, dict[str, int]]:
    """Scenario P approved (not yet sent)."""
    logins = logins or _logins(shop)
    plant_calendar(shop)
    for item, _, price in LINES:
        _price(shop, "S1", item, price)
    request = _request(shop, no, [(item, qty, None, "C1") for item, qty, _ in LINES])
    for line, need in NEED_BY.items():
        shop.execute(
            text("update request_line set need_by = :d where request_no = :r and line_no = :l"),
            {"d": need, "r": request, "l": line},
        )
    shop.commit()
    _source(shop, request, dispatch=False)
    made = service.order_request(shop, shop.info["slug"], request, as_of=date(2026, 11, 6))
    (po_no,) = made.waiting or made.pos
    if made.waiting:
        workflow.approve(shop, shop.info["slug"], po_no, logins["procurement"])
    return po_no, logins


def send_p(shop: Session, outbox: Any, *, on: date = SENT_ON) -> tuple[str, dict[str, int], str]:
    """Scenario P sent on the day: returns the order, the logins and the respond link's token from the mail."""
    pin_today(on)
    po_no, logins = order_p(shop)
    outbox.sent.clear()
    dispatch.send_po(shop, po_no, sod.user(logins["buyer"]), tenant=shop.info["slug"])
    shop.execute(text("update po_version set sent_at = :d where po_no = :p"), {"d": f"{on} 10:00+05:30", "p": po_no})
    shop.commit()
    return po_no, logins, token_in(outbox.sent[-1]["body"])


def token_in(body: str) -> str:
    return body.split("/supplier/")[1].split()[0]


def link_of(token: str) -> supplier_links.Link:
    return supplier_links.read(token)


def count(shop: Session, sql: str, **params: Any) -> int:
    shop.rollback()
    return int(shop.execute(text(sql), params).scalar_one())


def audits(shop: Session, action: str) -> list[dict[str, Any]]:
    shop.rollback()
    return [
        dict(r[0] or {})
        for r in shop.execute(text("select after from audit_event where action = :a order by id"), {"a": action})
    ]
