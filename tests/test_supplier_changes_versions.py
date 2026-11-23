"""A supplier's change is the buyer's decision, on the version it was asked on (block 8 K6, K6b, 8.4): changes from the
link need the code and record their version; the changes of one answer make versions without going stale; a change asked
on terms that have since changed cannot be decided; the answer is the supplier's acknowledgement only when all of it is
accepted and its last version carried the approval; a rejection gives a fresh link to the version as it stands."""

from datetime import date
from decimal import Decimal

from confirmation_support import audits, link_of, send_p, token_in
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session
from supplier_answer_support import answer
from test_orders import _headers, shop  # noqa: F401 - fixtures
from test_sourcing import Outbox, db, outbox  # noqa: F401 - fixtures

from p2p.main import app
from p2p.orders import changes, dispatch, workflow
from p2p.platform import sod

D = Decimal
client = TestClient(app)


def _ask(shop: Session, outbox: Outbox, token: str, data: dict[str, str]) -> list[int]:
    got = answer(client, outbox.sent, token, data)
    assert got.status_code == 200, got.text
    shop.rollback()
    return [int(r[0]) for r in shop.execute(text("select id from po_change order by po_line_no"))]


def _decide(
    shop: Session, user: int, change: int, accept: bool, note: str | None = None
) -> tuple[int, dict[str, object]]:
    got = client.post(
        f"/api/supplier-changes/{change}/decide",
        json={"accept": accept, **({"note": note} if note else {})},
        headers=_headers(shop.info["slug"], user, "Buyer"),
    )
    shop.rollback()
    return got.status_code, got.json()


def _po(shop: Session) -> tuple[int, str, int | None]:
    shop.rollback()
    row = shop.execute(text("select version, status, acknowledged_version from po")).one()
    return int(row[0]), str(row[1]), row[2]


def test_an_answer_of_two_dates_makes_versions_and_is_acknowledged_only_once_approved_and_accepted(
    shop: Session,
    outbox: Outbox,  # noqa: F811
) -> None:
    # given P, and the supplier asks with its code: line 1 from 7 to 8 Dec, line 3 from 14 to 21 Dec ("die repair")
    po_no, logins, token = send_p(shop, outbox)
    first, third = _ask(
        shop,
        outbox,
        token,
        {"action": "change", "date_1": "2026-12-08", "date_3": "2026-12-21", "reason": "die repair"},
    )
    rows = shop.execute(text("select po_version, task_id from po_change order by id")).all()
    assert {r[0] for r in rows} == {1} and len({r[1] for r in rows}) == 1  # both on version 1, one task
    assert audits(shop, "po.change_proposed")[0]["po_version"] == 1

    # when the buyer accepts line 1: version 2 (one day, the approval carries), and no acknowledgement yet
    assert _decide(shop, logins["buyer"], first, True)[0] == 200
    assert _po(shop) == (2, "sent", None)

    # then line 3 is not stale (its sibling made version 2) and makes version 3: seven days is material
    status, made = _decide(shop, logins["buyer"], third, True)
    assert status == 200 and made["version"] == 3
    assert _po(shop) == (3, "submitted", None)
    material = shop.execute(text("select material from po_revision where version = 3")).scalar_one()
    assert any("line 3: date moved 7 days" in m for m in material)

    # the purchase manager approves version 3, the buyer sends it, the supplier accepts it with link and code
    workflow.approve(shop, shop.info["slug"], po_no, logins["procurement"])
    dispatch.send_po(shop, po_no, sod.user(logins["buyer"]), tenant=shop.info["slug"])
    l3 = token_in(outbox.sent[-1]["body"])
    assert link_of(l3).id and answer(client, outbox.sent, l3, {"action": "accept"}).status_code == 200
    assert _po(shop) == (3, "acknowledged", 3)
    assert shop.execute(text("select ack_via from po_version where version = 3")).scalar_one() == "link"


def test_an_answer_all_accepted_within_the_approval_is_the_acknowledgement_of_its_last_version(
    shop: Session,
    outbox: Outbox,  # noqa: F811
) -> None:
    po_no, logins, token = send_p(shop, outbox)
    first, third = _ask(shop, outbox, token, {"action": "change", "date_1": "2026-12-08", "date_3": "2026-12-15"})

    _decide(shop, logins["buyer"], first, True)
    assert _po(shop) == (2, "sent", None)  # the first accepted change of two no longer acknowledges
    _decide(shop, logins["buyer"], third, True)

    assert _po(shop) == (3, "acknowledged", 3)
    via, by, ids = shop.execute(
        text("select ack_via, acknowledged_by, ack_change_ids from po_version where version = 3")
    ).one()
    assert (via, by, sorted(ids)) == ("app", f"user:{logins['buyer']}", sorted([first, third]))
    assert [r[0] for r in shop.execute(text("select promised_date from po_line order by line_no"))] == [
        date(2026, 12, 8), date(2026, 12, 7), date(2026, 12, 15),
    ]  # fmt: skip


def test_a_rejected_change_of_the_answer_means_no_acknowledgement_and_a_link_to_the_version_as_it_stands(
    shop: Session,
    outbox: Outbox,  # noqa: F811
) -> None:
    po_no, logins, token = send_p(shop, outbox)
    first, third = _ask(shop, outbox, token, {"action": "change", "date_1": "2026-12-08", "date_3": "2026-12-21"})
    _decide(shop, logins["buyer"], first, True)

    status, _ = _decide(shop, logins["buyer"], third, False, "the line is needed then")

    assert status == 200 and _po(shop) == (2, "sent", None)
    told = outbox.sent[-1]
    assert "change not accepted" in told["subject"] and "the line is needed then" in told["body"]
    fresh = token_in(told["body"])
    version, sent_to = shop.execute(
        text("select po_version, sent_to from supplier_link where id = :i"), {"i": link_of(fresh).id}
    ).one()
    assert (version, sent_to) == (2, "s1@supplier.example")
    assert (
        shop.execute(text("select po_version from email_message where subject = :s"), {"s": told["subject"]}).scalar()
        == 2
    )


def test_a_change_asked_before_the_order_changed_otherwise_is_stale(shop: Session, outbox: Outbox) -> None:  # noqa: F811
    # given the supplier asks line 3 from 150 to 160 on version 1, then the buyer moves line 1's need-by (version 2)
    po_no, logins, token = send_p(shop, outbox)
    (change,) = _ask(shop, outbox, token, {"action": "change", "qty_3": "160"})
    changes.revise(
        shop, shop.info["slug"], po_no, logins["buyer"], "earlier", [changes.LineChange(1, need_by=date(2026, 12, 8))]
    )
    task = shop.execute(text("select task_id from po_change")).scalar_one()

    # when the buyer accepts the quantity
    status, said = _decide(shop, logins["buyer"], change, True)

    # then it is refused: stale, its task done, the order as the buyer left it
    assert status == 409
    assert said["detail"] == (
        "the order changed since the supplier asked (version 1, now version 2): ask the supplier again"
    )
    assert shop.execute(text("select status from po_change")).scalar_one() == "stale"
    assert shop.execute(text("select status from task where id = :t"), {"t": task}).scalar_one() == "done"
    assert shop.execute(text("select qty from po_line where line_no = 3")).scalar_one() == D(150)
    (stale,) = audits(shop, "po.change_stale")
    assert stale == {"change": change, "po_version": 1, "now": 2}
    assert _decide(shop, logins["buyer"], change, True)[0] == 409  # already stale
