"""Helpers shared by the block 7 tests (purchase order versions): reading what was kept, a second delivery place, the
revise call through the API, and an order sent with its link."""

import hashlib
from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session
from test_dispatch import _approved_order
from test_orders import _headers, _logins
from test_sourcing import Outbox
from test_supplier_confirmation import _token

from p2p.main import app
from p2p.orders import dispatch
from p2p.platform import sod

client = TestClient(app)


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def rows(db: Session, sql: str, **params: Any) -> list[dict[str, Any]]:
    db.rollback()  # read what the service committed, not this session's old snapshot
    return [dict(r) for r in db.execute(text(sql), params).mappings()]


def order(db: Session, po_no: str) -> dict[str, Any]:
    (row,) = rows(db, "select * from po where no = :n", n=po_no)
    return row


def versions(db: Session, po_no: str) -> list[dict[str, Any]]:
    return rows(db, "select * from po_version where po_no = :n order by version", n=po_no)


def audits(db: Session, action: str, po_no: str) -> list[dict[str, Any]]:
    return [
        r["after"]
        for r in rows(
            db, "select after from audit_event where action = :a and entity_id = :n order by id", a=action, n=po_no
        )
    ]


def second_place(db: Session) -> None:
    """A Pune store in Maharashtra, the state of the test supplier S1, with its own GSTIN."""
    db.execute(
        text(
            "insert into location (code, name, gstin, state_code, is_stock_location) "
            "values ('WH-PUN', 'Pune store', '27BBBBB0000B2Z5', '27', true)"
        )
    )
    db.commit()


def sent_order(db: Session, outbox: Outbox, qty: str = "10") -> tuple[str, dict[str, int], str]:
    """An order of item A from S1 (Maharashtra) to the Bengaluru store, approved and sent with its link: the order, the
    logins and the token in its mail."""
    logins = _logins(db)
    po_no = _approved_order(db, logins, qty)
    outbox.sent.clear()
    dispatch.send_po(db, po_no, sod.user(logins["buyer"]), tenant=db.info["slug"])
    return po_no, logins, _token(outbox.sent[-1]["body"])


def buyer(db: Session, logins: dict[str, int]) -> dict[str, str]:
    return _headers(db.info["slug"], logins["buyer"], "Buyer")


def revise(db: Session, logins: dict[str, int], po_no: str, body: dict[str, Any]) -> Any:
    return client.post(
        f"/api/purchase-orders/{po_no}/revise", json={"reason": "Plan changed", **body}, headers=buyer(db, logins)
    )


def attachment(outbox: Outbox) -> bytes:
    """The one file attached to the last mail."""
    ((_, _, data),) = outbox.sent[-1]["attachments"]
    return bytes(data)
