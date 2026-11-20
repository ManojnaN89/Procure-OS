"""Supplier documents (added, then verified by someone else) and contracts (stored as a draft, priced, activated by an approver)."""

from datetime import date

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session
from test_sourcing import PASSWORD, TODAY, _headers, db  # noqa: F401 - fixture

from p2p.main import app
from p2p.masters import documents_and_contracts as masters
from p2p.platform import users
from p2p.sourcing import documents

client = TestClient(app)


def _people(db_: Session) -> tuple[int, int, int]:
    slug, sfx = db_.info["slug"], db_.info["sfx"]
    buyer = users.create_user(slug, f"b{sfx}@x.example", PASSWORD, "A Buyer", "Buyer")
    boss = users.create_user(slug, f"o{sfx}@x.example", PASSWORD, "An Owner", "Approver")
    other = users.create_user(slug, f"r{sfx}@x.example", PASSWORD, "A Requester", "Requester")
    return buyer, boss, other


def test_a_document_counts_only_after_someone_else_has_verified_it(db: Session) -> None:  # noqa: F811
    buyer, boss, _ = _people(db)
    db.execute(text("delete from supplier_document where supplier_code = 'S1' and doc_type = 'pan'"))
    db.commit()
    assert "PAN card is missing" in documents.check_supplier(db, "S1", TODAY).blocking

    doc = masters.add_document(db, "S1", "pan", "ABCDE0000F", date(2020, 1, 1), None, buyer)

    assert "PAN card is not verified" in documents.check_supplier(db, "S1", TODAY).blocking
    with pytest.raises(masters.MasterError, match="cannot verify it"):
        masters.set_verified(db, "S1", doc, True, buyer)  # the person who added it may not verify it
    masters.set_verified(db, "S1", doc, True, boss)
    assert documents.check_supplier(db, "S1", TODAY).ok
    assert db.execute(
        text("select verified_by, verified_at is not null from supplier_document where id = :i"), {"i": doc}
    ).one() == (str(boss), True)
    masters.set_verified(db, "S1", doc, False, boss)  # taking the mark away is always allowed
    assert not documents.check_supplier(db, "S1", TODAY).ok
    assert (
        db.execute(text("select count(*) from audit_event where action like 'supplier.document_%'")).scalar_one() == 3
    )


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"doc_type": "banana"}, "must be one of"),
        ({"supplier": "NOPE"}, "does not exist"),
        ({"issued_on": date(2026, 1, 2), "expires_on": date(2026, 1, 1)}, "before it was issued"),
    ],
)
def test_a_document_that_makes_no_sense_is_refused(db: Session, kwargs: dict, message: str) -> None:  # noqa: F811
    buyer, _, _ = _people(db)
    args = {"supplier": "S1", "doc_type": "pan", "issued_on": None, "expires_on": None, **kwargs}

    with pytest.raises(masters.MasterError, match=message):
        masters.add_document(db, args["supplier"], args["doc_type"], "X", args["issued_on"], args["expires_on"], buyer)


def _contract(db_: Session, user_id: int, **kw: object) -> str:
    data = {
        "supplier_code": "S1",
        "type": "rate_contract",
        "valid_from": date(2026, 11, 1),
        "valid_to": date(2027, 10, 31),
        "max_value": 100000,
    }
    return masters.create_contract(db_, {**data, **kw}, user_id)


def test_a_contract_is_stored_as_a_draft_priced_and_activated_by_an_approver(db: Session) -> None:  # noqa: F811
    buyer, boss, _ = _people(db)

    code = _contract(db, buyer)

    assert code == "CNT-0001" and _contract(db, buyer) == "CNT-0002"
    assert db.execute(text("select status from contract where code = :c"), {"c": code}).scalar_one() == "draft"
    with pytest.raises(masters.MasterError, match="no prices"):
        masters.activate(db, code, boss, today=TODAY)
    masters.add_price(db, code, {"item_code": "A", "price": "90", "uom": "NOS", "min_qty": 10}, buyer)
    row = db.execute(
        text(
            "select supplier_code, contract_code, price, min_qty, valid_to from price_record where contract_code = :c"
        ),
        {"c": code},
    ).one()
    assert (
        row[:2] == ("S1", code) and str(row[2]) == "90.0000" and row[4] == date(2027, 10, 31)
    )  # the contract's own end date

    masters.activate(db, code, boss, today=TODAY)

    assert db.execute(text("select status from contract where code = :c"), {"c": code}).scalar_one() == "active"
    with pytest.raises(masters.MasterError, match="only a draft"):
        masters.activate(db, code, boss, today=TODAY)
    assert db.execute(text("select count(*) from audit_event where action like 'contract.%'")).scalar_one() == 4


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"supplier_code": "NOPE"}, "does not exist"),
        ({"type": "lease"}, "must be one of"),
        ({"valid_to": date(2026, 10, 1)}, "cannot end before it starts"),
        ({"max_value": 0}, "more than zero"),
    ],
)
def test_a_contract_that_makes_no_sense_is_refused(db: Session, changes: dict, message: str) -> None:  # noqa: F811
    buyer, _, _ = _people(db)

    with pytest.raises(masters.MasterError, match=message):
        _contract(db, buyer, **changes)


def test_a_contract_with_a_supplier_that_cannot_be_used_is_not_activated(db: Session) -> None:  # noqa: F811
    buyer, boss, _ = _people(db)
    code = _contract(db, buyer)
    masters.add_price(db, code, {"item_code": "A", "price": "90"}, buyer)
    db.execute(text("update supplier set status = 'blocked' where code = 'S1'"))
    db.commit()
    with pytest.raises(masters.MasterError, match="is blocked"):
        masters.activate(db, code, boss, today=TODAY)
    db.execute(text("update supplier set status = 'active' where code = 'S1'"))
    db.execute(text("update supplier_document set verified = false where supplier_code = 'S1' and doc_type = 'pan'"))
    db.commit()
    with pytest.raises(masters.MasterError, match="PAN card is not verified"):
        masters.activate(db, code, boss, today=TODAY)
    db.execute(text("update contract set valid_to = '2026-01-01' where code = :c"), {"c": code})
    db.execute(text("update supplier_document set verified = true"))
    db.commit()
    with pytest.raises(masters.MasterError, match="already ended"):
        masters.activate(db, code, boss, today=TODAY)


def test_a_price_needs_a_real_item_and_a_price_above_zero(db: Session) -> None:  # noqa: F811
    buyer, _, _ = _people(db)
    code = _contract(db, buyer)
    for data, message in (
        ({"item_code": "NOPE", "price": "1"}, "item NOPE"),
        ({"item_code": "A", "price": "0"}, "more than zero"),
    ):
        with pytest.raises(masters.MasterError, match=message):
            masters.add_price(db, code, data, buyer)
    with pytest.raises(masters.MasterError, match="does not exist"):
        masters.add_price(db, "CNT-9999", {"item_code": "A", "price": "1"}, buyer)


def test_the_screens_add_verify_and_store_through_the_api_with_the_right_permissions(db: Session) -> None:  # noqa: F811
    buyer, boss, other = _people(db)
    slug = db.info["slug"]
    h_buyer, h_boss, h_other = (
        _headers(slug, buyer, "Buyer"),
        _headers(slug, boss, "Approver"),
        _headers(slug, other, "Requester"),
    )

    assert client.post("/api/vendors/S1/documents", json={"type": "iso"}, headers=h_other).status_code == 403
    added = client.post(
        "/api/vendors/S1/documents",
        json={"type": "iso", "number": "ISO-1", "expires_on": "2027-01-01"},
        headers=h_buyer,
    )
    assert added.status_code == 200 and added.json()["verified"] is False
    doc = added.json()["id"]
    assert (
        client.post(
            "/api/vendors/S1/documents", json={"type": "iso", "expires_on": "soon"}, headers=h_buyer
        ).status_code
        == 422
    )
    assert (
        client.post(f"/api/vendors/S1/documents/{doc}/verify", json={}, headers=h_buyer).status_code == 403
    )  # a buyer may not verify
    assert client.post(f"/api/vendors/S1/documents/{doc}/verify", json={}, headers=h_boss).status_code == 200
    assert client.post("/api/vendors/S1/documents/9999/verify", json={}, headers=h_boss).status_code == 404

    detail = client.get("/api/vendors/S1", headers=h_boss).json()
    assert detail["can_verify_documents"] is True and detail["can_add_documents"] is True
    assert any(d["id"] == doc and d["verified"] for d in detail["documents"]["on_file"])
    assert client.get("/api/vendors/S1", headers=h_other).json()["can_verify_documents"] is False

    body = {"supplier_code": "S1", "valid_from": "2026-11-01", "valid_to": "2027-10-31", "max_value": "50000"}
    assert client.post("/api/contracts", json=body, headers=h_other).status_code == 403
    assert client.post("/api/contracts", json={"supplier_code": "S1"}, headers=h_buyer).status_code == 422
    made = client.post("/api/contracts", json=body, headers=h_buyer)
    code = made.json()["id"]
    assert made.status_code == 200 and made.json()["status"] == "draft"
    assert (
        client.post(f"/api/contracts/{code}/prices", json={"item_code": "A", "price": "x"}, headers=h_buyer).status_code
        == 422
    )
    assert (
        client.post(
            f"/api/contracts/{code}/prices", json={"item_code": "A", "price": "80", "uom": "NOS"}, headers=h_buyer
        ).status_code
        == 200
    )
    page = client.get(f"/api/contracts/{code}", headers=h_buyer).json()
    assert (page["status"], page["can_manage"], page["can_activate"], page["prices"][0]["price"]) == (
        "draft",
        True,
        False,
        "80.0000",
    )
    assert (
        client.post(f"/api/contracts/{code}/activate", headers=h_buyer).status_code == 403
    )  # a buyer may not activate
    assert client.get(f"/api/contracts/{code}", headers=h_boss).json()["can_activate"] is True
    assert client.post(f"/api/contracts/{code}/activate", headers=h_boss).status_code == 200
    assert (
        client.get(f"/api/contracts/{code}", headers=h_buyer).json()["can_manage"] is True
    )  # an active contract takes new prices only as a draft version someone else activates (D48)
    assert client.post(f"/api/contracts/{code}/activate", headers=h_boss).status_code == 409
    assert (
        client.post("/api/contracts/NOPE/prices", json={"item_code": "A", "price": "1"}, headers=h_buyer).status_code
        == 404
    )
    assert client.post("/api/contracts", json=body).status_code == 401
