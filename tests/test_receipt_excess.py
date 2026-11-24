"""Goods above the order are held, then accepted by a revision or returned (C3, task 4.2, 9.2-9.3)."""

from decimal import Decimal

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session
from test_orders import shop  # noqa: F401 - fixtures
from test_receiving import _on_hand, _receive, _sent, store  # noqa: F401 - fixtures
from test_sourcing import PASSWORD, Outbox, db, outbox  # noqa: F401 - fixtures

from p2p.platform import permissions, proposals, users
from p2p.receiving import excess, goods

D = Decimal
TODAY = __import__("test_receiving").TODAY


def _login(shop: Session, code: str) -> int:
    email = shop.execute(text("select email from org_user where code = :c"), {"c": code}).scalar_one()
    found = users.user_id_by_email(shop.info["slug"], str(email))
    assert found is not None
    return found


def _row(shop: Session, po_no: str) -> dict:
    shop.expire_all()
    return dict(
        shop.execute(text("select * from receipt_excess where po_no = :p order by id desc limit 1"), {"p": po_no})
        .mappings()
        .one()
    )


def _quarantine(shop: Session, item: str = "A") -> Decimal:
    shop.expire_all()
    value = shop.execute(
        text("select coalesce(sum(qty), 0) from stock_quality_level where item_code = :i and quality = 'quarantine'"),
        {"i": item},
    ).scalar_one()
    return D(value)


def _over(shop: Session) -> tuple[str, dict[str, int]]:
    po_no, logins = _sent(shop)  # order A: 10 at 100, tolerance 5% (room 10.5)
    done = _receive(shop, po_no, "12")
    assert done.accepted == {1: D("10.5")} and done.excess == {1: D("1.5")}
    return po_no, logins


def test_given_goods_above_the_order_when_the_store_receives_them_then_they_wait_in_quarantine(
    store: Session, outbox: Outbox
) -> None:  # noqa: F811
    po_no, _ = _over(store)

    row = _row(store, po_no)
    assert (row["status"], D(row["qty"]), row["uom"]) == ("waiting", D("1.5"), "NOS")
    hold = store.execute(text("select quality, left_qty, source from stock_hold where id = :i"), {"i": row["hold_id"]}).one()
    assert (hold[0], D(hold[1]), hold[2]) == ("quarantine", D("1.5"), "excess")
    assert _on_hand(store) == D("10.5") and _quarantine(store) == D("1.5")
    # held excess is never set aside, issued or invoiceable: it is not usable stock
    assert _on_hand(store) == D("10.5")


def test_given_an_order_whose_room_is_exactly_met_when_one_more_unit_arrives_then_only_that_unit_is_above_it(
    store: Session, outbox: Outbox
) -> None:  # noqa: F811
    po_no, logins = _sent(store, qty="20")
    at_room = _receive(store, po_no, "21")  # 20 + 5% exactly
    assert at_room.excess == {} and at_room.accepted == {1: D("21")}

    over, _ = _sent(store, qty="10", logins=logins)
    one_more = _receive(store, over, "10.5001")
    assert one_more.accepted == {1: D("10.5")} and one_more.excess == {1: D("0.0001")}


def test_given_a_serialised_item_when_it_arrives_above_the_room_then_it_is_refused(
    store: Session, outbox: Outbox
) -> None:  # noqa: F811
    store.execute(text("update item set serialised = true where code = 'A'"))
    store.commit()
    po_no, _ = _sent(store)

    with pytest.raises(goods.ReceiptError, match="11 is more than the 10.5000 that may still be received"):
        goods.post_receipt(
            store,
            po_no,
            [goods.LineIn(1, D("11"), serials=[f"S{i}" for i in range(11)])],
            goods.Header(),
            1,
            as_of=TODAY,
        )


def test_given_a_waiting_excess_when_the_bands_person_approves_the_revision_then_the_order_and_stock_follow(
    store: Session, outbox: Outbox
) -> None:  # noqa: F811
    po_no, _ = _over(store)
    row = _row(store, po_no)

    asked = excess.propose_accept(store, store.info["slug"], row["id"], 1, 1, "We can use the two extra", as_of=TODAY)
    approver = _login(store, asked["approver"])
    assert D(asked["new_qty"]) == D("12")
    proposals.approve(store, asked["proposal_id"], approver)

    store.expire_all()
    po = store.execute(text("select version, total, acknowledged_version from po where no = :p"), {"p": po_no}).one()
    line = store.execute(text("select qty, received_qty, status from po_line where po_no = :p"), {"p": po_no}).one()
    assert po[0] == 2 and D(po[1]) == D("1416.00")  # 12 x 100 plus 18% GST
    assert (D(line[0]), D(line[1]), line[2]) == (D("12"), D("12"), "received")
    assert _on_hand(store) == D("12") and _quarantine(store) == D("0")
    assert _row(store, po_no)["status"] == "accepted" and _row(store, po_no)["version_made"] == 2
    assert store.execute(text("select count(*) from budget_actual where po_no = :p"), {"p": po_no}).scalar_one() == 2


def test_a_maker_of_the_order_never_approves_the_acceptance(store: Session, outbox: Outbox) -> None:  # noqa: F811
    po_no, _ = _over(store)
    store.execute(text("update po set buyer = 'U5' where no = :p"), {"p": po_no})  # the procurement head made it
    store.commit()
    row = _row(store, po_no)
    asked = excess.propose_accept(store, store.info["slug"], row["id"], 1, 1, "The line needs them", as_of=TODAY)
    maker = _login(store, "U5")  # the procurement head made the order
    assert str(asked["approver"]) != "U5"
    with pytest.raises(permissions.PermissionDenied):
        proposals.approve(store, asked["proposal_id"], maker)


def test_given_a_changed_order_when_the_excess_is_offered_then_the_proposal_is_refused(
    store: Session, outbox: Outbox
) -> None:  # noqa: F811
    po_no, _ = _over(store)
    row = _row(store, po_no)

    with pytest.raises(excess.ExcessError, match="the order changed: return the goods or propose again"):
        excess.propose_accept(store, store.info["slug"], row["id"], 1, 9, "Too late", as_of=TODAY)

    assert _row(store, po_no)["status"] == "waiting"


def test_given_a_rejected_acceptance_when_it_ends_then_the_excess_waits_again_with_a_task(
    store: Session, outbox: Outbox
) -> None:  # noqa: F811
    po_no, _ = _over(store)
    store.execute(text("update po set buyer = 'U3' where no = :p"), {"p": po_no})
    store.commit()
    row = _row(store, po_no)
    asked = excess.propose_accept(store, store.info["slug"], row["id"], 1, 1, "We can use them", as_of=TODAY)
    approver = _login(store, asked["approver"])

    proposals.reject(store, asked["proposal_id"], approver, "Return them to the supplier")

    store.expire_all()
    now = _row(store, po_no)
    assert now["status"] == "waiting" and now["proposal_id"] is None
    assert now["task_id"] is not None


def test_given_an_excess_when_the_buyer_returns_it_then_a_draft_return_names_it(
    store: Session, outbox: Outbox
) -> None:  # noqa: F811
    po_no, _ = _over(store)
    row = _row(store, po_no)

    made = excess.send_back(store, store.info["slug"], row["id"], 1, "Not needed", as_of=TODAY)

    now = _row(store, po_no)
    assert now["status"] == "returning" and now["return_no"] == made["return_no"]
    line = store.execute(
        text("select quality, qty, counted from supplier_return_line where return_no = :r"), {"r": made["return_no"]}
    ).one()
    assert (line[0], D(line[1]), line[2]) == ("quarantine", D("1.5"), False)
    assert _on_hand(store) == D("10.5")  # still in quarantine until the return is dispatched
