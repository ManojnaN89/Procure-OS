"""Company A's receipts, goods above an order, returns and claims are not company B's (block 9, 9.10.11)."""

import uuid

from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session
from test_orders import shop  # noqa: F401 - fixtures
from test_receiving import _receive, _sent, store  # noqa: F401 - fixtures
from test_sourcing import PASSWORD, Outbox, _headers, db, outbox  # noqa: F401 - fixtures

from p2p.main import app
from p2p.platform import tenants, users
from p2p.receiving import quality, returns

D = __import__("decimal").Decimal
TODAY = __import__("test_receiving").TODAY
client = TestClient(app)


def test_a_company_sees_only_its_own_receipts_excess_returns_and_claims(store: Session, outbox: Outbox) -> None:  # noqa: F811
    po_no, _ = _sent(store)
    gr = _receive(store, po_no, "10").no
    held = quality.hold(store, store.info["slug"], 1, "A", "WH-BLR", D("2"), "blocked", "Dented")
    made = returns.create(
        store, store.info["slug"], 1, "Goes back",
        [returns.ReturnLineIn(held.hold_id, D("2"), gr_no=gr, po_line_no=1)], as_of=TODAY,
    )  # fmt: skip
    excess_id = store.execute(
        text(
            "insert into receipt_excess (gr_no, po_no, po_line_no, qty, uom) values (:g, :p, 1, 1, 'NOS') returning id"
        ),
        {"g": gr, "p": po_no},
    ).scalar_one()
    store.commit()

    other = "q" + uuid.uuid4().hex[:8]
    assert tenants.create_tenant(other, load_reference=False).ok
    stranger = users.create_user(other, f"store{store.info['sfx']}@y.example", PASSWORD, "Elsewhere", "Store")
    stranger_buyer = users.create_user(
        other, f"buyer{store.info['sfx']}@y.example", PASSWORD, "Elsewhere Buyer", "Buyer"
    )
    h = _headers(other, stranger, "Store")
    h_buyer = _headers(other, stranger_buyer, "Buyer")

    assert client.get(f"/api/receipts/{gr}", headers=h).status_code == 404
    assert client.get(f"/api/returns/{made.no}", headers=h).status_code == 404
    assert client.get("/api/returns", headers=h).json() == []
    assert client.get("/api/receipt-excess?open=true", headers=h).json() == []
    assert client.get("/api/recovery-claims", headers=h).status_code in (403, 200)
    if client.get("/api/recovery-claims", headers=h).status_code == 200:
        assert client.get("/api/recovery-claims", headers=h).json() == []
    assert (
        client.post(f"/api/receipt-excess/{excess_id}/return", json={"reason": "not mine"}, headers=h_buyer).status_code
        == 404
    )
    assert client.get("/api/recovery-claims", headers=h_buyer).json() == []
    assert (
        client.post(
            "/api/returns", json={"reason": "not mine", "lines": [{"hold_id": held.hold_id, "qty": "1"}]}, headers=h
        ).status_code
        == 404
    )
