"""Changing a supplier's status is a proposal: asked by one person, approved by another, only with good documents."""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session
from test_sourcing import PASSWORD, _headers, db  # noqa: F401 - fixture

from p2p.main import app
from p2p.platform import permissions, proposals, users

client = TestClient(app)


def _people(db: Session) -> tuple[dict[str, str], dict[str, str], int, int]:
    slug, sfx = db.info["slug"], db.info["sfx"]
    buyer = users.create_user(slug, f"buyer{sfx}@x.example", PASSWORD, "A Buyer", "Buyer")
    boss = users.create_user(slug, f"boss{sfx}@x.example", PASSWORD, "A Boss", "Approver")
    other = users.create_user(slug, f"req{sfx}@x.example", PASSWORD, "A Requester", "Requester")
    return _headers(slug, buyer, "Buyer"), _headers(slug, other, "Requester"), buyer, boss


def _status(db: Session, code: str = "S6") -> str:
    db.expire_all()
    return str(db.execute(text("select status from supplier where code = :c"), {"c": code}).scalar_one())


def test_a_buyer_asks_for_an_activation_and_nothing_changes_until_an_approver_agrees(db: Session) -> None:  # noqa: F811
    buyer, _, _, boss = _people(db)  # S6 is a draft supplier with its PAN and bank proof on file

    r = client.post("/api/vendors/S6/status", json={"to": "active", "reason": "Onboarding complete"}, headers=buyer)

    assert r.status_code == 200
    pid = r.json()["proposal_id"]
    assert _status(db) == "draft"
    summary, perm = db.execute(
        text("select summary, required_permission from proposal where id = :i"), {"i": pid}
    ).one()
    assert "Activate: Foxtrot Draft (S6) is draft" in summary and perm == "supplier.activate"

    proposals.approve(db, pid, boss)

    assert _status(db) == "active"
    row = db.execute(text("select from_status, to_status, reason from supplier_status_history")).one()
    assert row == ("draft", "active", "Onboarding complete")
    assert (
        db.execute(text("select count(*) from audit_event where action = 'supplier.status_change'")).scalar_one() == 1
    )


def test_the_person_who_asked_cannot_approve_their_own_request(db: Session) -> None:  # noqa: F811
    _, _, _, boss = _people(db)
    headers = _headers(db.info["slug"], boss, "Approver")  # an approver may ask too
    pid = client.post("/api/vendors/S6/status", json={"to": "active", "reason": "ok"}, headers=headers).json()[
        "proposal_id"
    ]

    with pytest.raises(permissions.PermissionDenied):
        proposals.approve(db, pid, boss)

    assert _status(db) == "draft"


def test_a_requester_cannot_ask_and_a_buyer_cannot_approve(db: Session) -> None:  # noqa: F811
    buyer, requester, buyer_id, _ = _people(db)

    assert (
        client.post("/api/vendors/S6/status", json={"to": "active", "reason": "x"}, headers=requester).status_code
        == 403
    )
    assert client.post("/api/vendors/S6/status", json={"to": "active", "reason": "x"}).status_code == 401
    pid = client.post("/api/vendors/S6/status", json={"to": "active", "reason": "x"}, headers=buyer).json()[
        "proposal_id"
    ]
    with pytest.raises(permissions.PermissionDenied):
        proposals.approve(db, pid, buyer_id)
    assert _status(db) == "draft"


def test_a_supplier_with_a_missing_document_cannot_even_be_proposed_for_activation(db: Session) -> None:  # noqa: F811
    buyer, _, _, _ = _people(db)
    db.execute(text("delete from supplier_document where supplier_code = 'S6' and doc_type = 'pan'"))
    db.commit()

    r = client.post("/api/vendors/S6/status", json={"to": "active", "reason": "x"}, headers=buyer)

    assert r.status_code == 409 and "PAN card is missing" in r.json()["detail"]
    assert db.execute(text("select count(*) from proposal")).scalar_one() == 0


def test_documents_that_go_bad_before_the_approval_stop_the_activation(db: Session) -> None:  # noqa: F811
    buyer, _, _, boss = _people(db)
    pid = client.post("/api/vendors/S6/status", json={"to": "active", "reason": "x"}, headers=buyer).json()[
        "proposal_id"
    ]
    db.execute(text("update supplier_document set verified = false where supplier_code = 'S6' and doc_type = 'pan'"))
    db.commit()

    proposals.approve(db, pid, boss)

    assert _status(db) == "draft"
    execution = db.execute(text("select result::text from proposal where id = :i"), {"i": pid}).scalar_one()
    assert "PAN card is not verified" in execution


@pytest.mark.parametrize(
    ("code", "to", "reason", "message"),
    [
        ("S6", "blocked", "x", "a supplier that is draft cannot be made blocked"),
        ("S1", "active", "x", "a supplier that is active cannot be made active"),
        ("S1", "blocked", "  ", "a reason is needed"),
        ("S1", "banana", "x", "cannot be made banana"),
        ("NOPE", "active", "x", "does not exist"),
    ],
)
def test_a_change_that_makes_no_sense_is_refused(db: Session, code: str, to: str, reason: str, message: str) -> None:  # noqa: F811
    buyer, _, _, _ = _people(db)

    r = client.post(f"/api/vendors/{code}/status", json={"to": to, "reason": reason}, headers=buyer)

    assert r.status_code in (404, 409) and message in r.json()["detail"]
    assert db.execute(text("select count(*) from proposal")).scalar_one() == 0


def test_blocking_and_reactivating_are_proposals_too_and_one_change_waits_at_a_time(db: Session) -> None:  # noqa: F811
    buyer, _, _, boss = _people(db)
    first = client.post("/api/vendors/S1/status", json={"to": "blocked", "reason": "Quality problems"}, headers=buyer)
    again = client.post("/api/vendors/S1/status", json={"to": "draft", "reason": "Review"}, headers=buyer)

    assert again.status_code == 409 and "already waiting" in again.json()["detail"]
    detail = client.get("/api/vendors/S1", headers=buyer).json()
    assert detail["pending_change"]["id"] == first.json()["proposal_id"] and detail["can_propose"] is True

    proposals.approve(db, first.json()["proposal_id"], boss)
    assert _status(db, "S1") == "blocked"
    # a blocked supplier is not asked to quote
    from test_sourcing import TODAY

    from p2p.sourcing import service

    assert "S1" not in [c.code for c in service.eligible_suppliers(db, "C1", ["A"], TODAY)[0]]

    back = client.post("/api/vendors/S1/status", json={"to": "active", "reason": "Fixed"}, headers=buyer)
    proposals.approve(db, back.json()["proposal_id"], boss)
    assert _status(db, "S1") == "active"
    assert db.execute(text("select count(*) from supplier_status_history where supplier_code = 'S1'")).scalar_one() == 2
