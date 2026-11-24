"""Inspection by another person (task 3.8, DF-35): the receiver, the buyer, an approver and the requester never inspect."""

from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session
from test_orders import shop  # noqa: F401 - the store fixture builds on it
from test_receiving import _receive, _sent, store  # noqa: F401 - fixtures
from test_sourcing import PASSWORD, Outbox, db, outbox  # noqa: F401 - fixtures

from p2p.main import app
from p2p.platform import readiness, users
from p2p.receiving import goods

D = Decimal
TODAY = __import__("test_receiving").TODAY
client = TestClient(app)


def _waiting(shop: Session, *, receiver: int | None = None) -> tuple[str, dict[str, int]]:
    shop.execute(text("update item set inspection_required = true where code = 'A'"))
    shop.commit()
    po_no, logins = _sent(shop)
    done = _receive(shop, po_no, "10", user=receiver if receiver is not None else 1)
    return done.no, logins


def test_given_a_receipt_waiting_for_inspection_when_the_receiver_inspects_it_then_it_is_refused(
    store: Session, outbox: Outbox
) -> None:  # noqa: F811
    store.execute(text("update item set inspection_required = true where code = 'A'"))
    store.commit()
    po_no, logins = _sent(store)
    gr = _receive(store, po_no, "10", user=logins["stores"]).no

    with pytest.raises(goods.ReceiptError, match="the person who received the goods cannot also inspect them"):
        goods.inspect(store, gr, [goods.Decision(1, D("10"), D("0"))], logins["stores"], as_of=TODAY)

    assert store.execute(text("select inspection_status from goods_receipt")).scalar_one() == "pending"


def test_given_the_same_order_when_its_buyer_or_requester_inspects_then_the_duties_are_refused(
    store: Session, outbox: Outbox
) -> None:  # noqa: F811
    gr, logins = _waiting(store)
    store.execute(text("update po set buyer = 'U3'"))
    store.execute(text("update request set requester = 'U4'"))
    store.commit()

    with pytest.raises(goods.ReceiptError, match="the buyer who ordered cannot also inspect the goods"):
        goods.inspect(store, gr, [goods.Decision(1, D("10"), D("0"))], logins["stores"], as_of=TODAY)
    with pytest.raises(goods.ReceiptError, match="the person who asked for the goods cannot also inspect them"):
        goods.inspect(store, gr, [goods.Decision(1, D("10"), D("0"))], logins["keeper"], as_of=TODAY)
    with pytest.raises(goods.ReceiptError, match="an approver of the order cannot also inspect the goods"):
        goods.inspect(store, gr, [goods.Decision(1, D("10"), D("0"))], logins["procurement"], as_of=TODAY)


def test_given_a_receipt_when_another_store_person_inspects_it_then_it_is_accepted(
    store: Session, outbox: Outbox
) -> None:  # noqa: F811
    gr, _ = _waiting(store)
    slug, sfx = store.info["slug"], store.info["sfx"]
    other = users.create_user(slug, f"store2{sfx}@x.example", PASSWORD, "A Second Storekeeper", "Store")

    done = goods.inspect(store, gr, [goods.Decision(1, D("10"), D("0"))], other, as_of=TODAY)

    assert done.accepted == {1: D("10")}
    assert (
        store.execute(text("select inspected_by is not null from goods_receipt where no = :n"), {"n": gr}).scalar_one()
        is True
    )


def test_the_screen_offers_the_inspection_to_quality_and_not_to_the_receiver(store: Session, outbox: Outbox) -> None:  # noqa: F811
    store.execute(text("update item set inspection_required = true where code = 'A'"))
    store.commit()
    po_no, _ = _sent(store)
    slug, sfx = store.info["slug"], store.info["sfx"]
    keeper = users.create_user(slug, f"store{sfx}@x.example", PASSWORD, "A Store", "Store")
    quality = users.create_user(slug, f"quality{sfx}@x.example", PASSWORD, "A Quality", "Quality")
    gr = _receive(store, po_no, "10", user=keeper).no
    h_store = {
        "Authorization": "Bearer "
        + client.post("/api/auth/login", json={"email": f"store{sfx}@x.example", "password": PASSWORD}).json()[
            "access_token"
        ]
    }
    h_quality = {
        "Authorization": "Bearer "
        + client.post("/api/auth/login", json={"email": f"quality{sfx}@x.example", "password": PASSWORD}).json()[
            "access_token"
        ]
    }
    assert quality

    assert client.get(f"/api/receipts/{gr}", headers=h_store).json()["can_inspect"] is False
    assert client.get(f"/api/receipts/{gr}", headers=h_quality).json()["can_inspect"] is True
    blocked = client.post(
        f"/api/receipts/{gr}/inspect", json={"lines": [{"line_no": 1, "qty_accepted": "10"}]}, headers=h_store
    )
    assert blocked.status_code in (403, 409)  # the receiver is never offered it


def test_readiness_has_the_inspection_separation_rule() -> None:
    sep = next(s for s in readiness.SEPARATION if s.code == "SEP-16")
    assert (sep.what, sep.makers, sep.checkers) == ("an inspection decided", "grn.create", "grn.inspect")
    assert "receiving/goods.py" in sep.enforced_in
