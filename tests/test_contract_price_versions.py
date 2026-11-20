"""Contract price versions (D48): a price is a draft until someone else activates it, prices of one supplier for an item
never apply on the same day, sourcing uses only the version in force, and an order line keeps the version it used."""

import uuid
from datetime import date

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session
from test_orders import _setup
from test_sourcing import PASSWORD, TODAY, Outbox, _headers, _price, _request, db, outbox  # noqa: F401 - fixtures

from p2p.formulas.contracts import periods_overlap
from p2p.main import app
from p2p.masters import documents_and_contracts as masters
from p2p.orders import service as orders
from p2p.platform import routing, tenants, users
from p2p.platform.permissions import PermissionDenied
from p2p.sourcing import service as sourcing

client = TestClient(app)
D = date


@pytest.mark.parametrize(
    ("a", "b", "overlap"),
    [
        ((D(2026, 1, 1), D(2026, 1, 31)), (D(2026, 2, 1), D(2026, 2, 28)), False),  # adjacent: the next day
        ((D(2026, 1, 1), D(2026, 1, 31)), (D(2026, 1, 31), D(2026, 2, 28)), True),  # one shared day
        ((D(2026, 2, 1), D(2026, 2, 28)), (D(2026, 1, 1), D(2026, 2, 1)), True),  # the other way round
        ((D(2026, 1, 1), None), (D(2030, 1, 1), D(2030, 1, 2)), True),  # no end runs on for ever
        ((D(2026, 1, 1), D(2026, 1, 31)), (D(2026, 2, 1), None), False),
        ((D(2026, 1, 1), None), (D(2025, 1, 1), None), True),
    ],
)
def test_two_periods_overlap_only_when_they_share_a_day(a: tuple, b: tuple, overlap: bool) -> None:
    assert periods_overlap(a[0], a[1], b[0], b[1]) is overlap
    assert periods_overlap(b[0], b[1], a[0], a[1]) is overlap


def _people(db_: Session) -> dict[str, int]:
    slug, sfx = db_.info["slug"], db_.info["sfx"]
    return {
        "buyer": users.create_user(slug, f"vb{sfx}@x.example", PASSWORD, "A Buyer", "Buyer"),
        "boss": users.create_user(slug, f"v1{sfx}@x.example", PASSWORD, "First Approver", "Approver"),
        "boss2": users.create_user(slug, f"v2{sfx}@x.example", PASSWORD, "Second Approver", "Approver"),
        "boss3": users.create_user(slug, f"v3{sfx}@x.example", PASSWORD, "Third Approver", "Approver"),
    }


def _contract(db_: Session, user_id: int, **kw: object) -> str:
    data = {"supplier_code": "S1", "valid_from": D(2026, 11, 1), "valid_to": D(2027, 10, 31), **kw}
    return masters.create_contract(db_, data, user_id)


def _prices(db_: Session, item: str, on: date) -> list[str]:
    return [str(o.price) for o in sourcing.contract_options(db_, item, "NOS", on)]


def test_a_new_price_on_an_active_contract_waits_for_someone_else_then_replaces_the_one_in_force(db: Session) -> None:  # noqa: F811
    p = _people(db)
    code = _contract(db, p["buyer"])
    first = masters.add_price(db, code, {"item_code": "A", "price": "90", "uom": "NOS"}, p["buyer"])
    masters.activate(db, code, p["boss"], today=TODAY)

    draft = masters.add_price(
        db, code, {"item_code": "A", "price": "80", "uom": "NOS", "valid_from": D(2026, 12, 1)}, p["boss2"]
    )

    assert db.execute(text("select version, status from price_record where id = :i"), {"i": draft}).one() == (
        2,
        "draft",
    )
    assert _prices(db, "A", D(2026, 12, 5)) == ["90.0000"]  # a draft is never bought at
    masters.submit_version(db, code, p["boss"])
    with pytest.raises(masters.MasterError, match="waiting to be activated"):
        masters.add_price(db, code, {"item_code": "D", "price": "5", "uom": "NOS"}, p["boss2"])
    with pytest.raises(PermissionDenied):
        masters.activate_version(db, code, p["boss"])  # who sent it cannot decide it
    with pytest.raises(PermissionDenied, match="segregation of duties"):
        masters.activate_version(db, code, p["boss2"])  # nor who entered the price
    with pytest.raises(PermissionDenied):
        masters.activate_version(db, code, p["buyer"])  # a buyer may not activate at all

    done = masters.activate_version(db, code, p["boss3"])

    assert done == {"version": 2, "prices": [draft], "ended": [first]}
    rows = db.execute(
        text(
            "select id, version, status, valid_from, valid_to, activated_by from price_record where contract_code = :c order by id"
        ),
        {"c": code},
    ).all()
    assert rows == [
        (first, 1, "active", D(2026, 11, 1), D(2026, 11, 30), str(p["boss"])),  # ends the day before the new one
        (draft, 2, "active", D(2026, 12, 1), D(2027, 10, 31), str(p["boss3"])),
    ]
    assert _prices(db, "A", D(2026, 11, 30)) == ["90.0000"] and _prices(db, "A", D(2026, 12, 1)) == ["80.0000"]
    assert (
        db.execute(
            text("select count(*) from audit_event where action = 'contract.price_version_activate'")
        ).scalar_one()
        == 1
    )


def test_overlapping_prices_are_refused_and_adjacent_ones_and_quantity_breaks_are_not(db: Session) -> None:  # noqa: F811
    p = _people(db)
    code = _contract(db, p["buyer"])
    price = {"item_code": "A", "price": "90", "uom": "NOS"}
    masters.add_price(db, code, {**price, "valid_to": D(2027, 3, 31)}, p["buyer"])

    with pytest.raises(masters.MasterError, match="overlap"):  # one shared day
        masters.add_price(db, code, {**price, "valid_from": D(2027, 3, 31)}, p["buyer"])
    masters.add_price(db, code, {**price, "price": "95", "valid_from": D(2027, 4, 1)}, p["buyer"])  # the next day
    masters.add_price(db, code, {**price, "price": "85", "min_qty": 10}, p["buyer"])  # a quantity break
    masters.add_price(db, code, {**price, "uom": "BOX"}, p["buyer"])  # another unit
    with pytest.raises(masters.MasterError, match="within the contract's dates"):
        masters.add_price(db, code, {**price, "item_code": "D", "valid_to": D(2027, 11, 1)}, p["buyer"])
    masters.activate(db, code, p["boss"], today=TODAY)
    other = _contract(db, p["buyer"], valid_from=D(2027, 1, 1), valid_to=D(2027, 12, 31))
    with pytest.raises(masters.MasterError, match=f"overlap the active price of A on {code}"):
        masters.add_price(db, other, price, p["buyer"])  # the same supplier on two contracts
    masters.add_price(db, other, {**price, "valid_from": D(2027, 11, 1)}, p["buyer"])  # after the first one ends

    assert (
        db.execute(
            text("select count(*) from price_record where contract_code = :c and status = 'active'"), {"c": code}
        ).scalar_one()
        == 4
    )


def test_whoever_stored_the_contract_or_priced_it_cannot_activate_it(db: Session) -> None:  # noqa: F811
    p = _people(db)
    stored = _contract(db, p["boss"])
    masters.add_price(db, stored, {"item_code": "A", "price": "90", "uom": "NOS"}, p["buyer"])
    priced = _contract(db, p["buyer"], valid_from=D(2027, 11, 1), valid_to=D(2028, 3, 31))
    masters.add_price(db, priced, {"item_code": "A", "price": "90", "uom": "NOS"}, p["boss"])

    for code in (stored, priced):
        with pytest.raises(masters.MasterError, match="segregation of duties"):
            masters.activate(db, code, p["boss"], today=TODAY)
        masters.activate(db, code, p["boss2"], today=TODAY)

    assert (
        db.execute(text("select count(*) from contract where status = 'active' and code like 'CNT-%'")).scalar_one()
        == 2
    )


def test_an_order_line_keeps_the_version_it_was_bought_at_after_a_new_one_is_activated(
    db: Session,  # noqa: F811
    outbox: Outbox,  # noqa: F811
) -> None:
    _setup(db)
    p = _people(db)
    _price(db, "S1", "A", "100")  # K-ACTIVE, 2026: version 1
    old = int(
        db.execute(
            text("select id from price_record where contract_code = 'K-ACTIVE' and item_code = 'A'")
        ).scalar_one()
    )
    _request(db, "R-1", [("A", "10", None, "C1")])
    sourcing.source_request(db, db.info["slug"], "R-1", as_of=TODAY, dispatch=False)
    first = orders.order_request(db, db.info["slug"], "R-1", as_of=TODAY)

    new = masters.add_price(
        db, "K-ACTIVE", {"item_code": "A", "price": "90", "uom": "NOS", "valid_from": D(2026, 11, 10)}, p["boss"]
    )
    masters.submit_version(db, "K-ACTIVE", p["boss"])
    masters.activate_version(db, "K-ACTIVE", p["boss2"])
    _request(db, "R-2", [("A", "10", None, "C1")])
    sourcing.source_request(db, db.info["slug"], "R-2", as_of=D(2026, 11, 12), dispatch=False)
    second = orders.order_request(db, db.info["slug"], "R-2", as_of=D(2026, 11, 12))

    lines = db.execute(
        text(
            "select l.po_no, l.unit_price, l.price_record_id, p.version from po_line l "
            "join price_record p on p.id = l.price_record_id order by l.po_no"
        )
    ).all()
    assert [(str(r[1]), r[2], r[3]) for r in lines] == [("100.0000", old, 1), ("90.0000", new, 2)]
    assert [r[0] for r in lines] == first.pos + second.pos
    assert db.execute(text("select valid_to from price_record where id = :i"), {"i": old}).scalar_one() == D(
        2026, 11, 9
    )


def test_the_contract_page_drafts_sends_rejects_and_activates_a_version_with_the_right_people(db: Session) -> None:  # noqa: F811
    p = _people(db)
    slug = db.info["slug"]
    h = {k: _headers(slug, v, "Buyer" if k == "buyer" else "Approver") for k, v in p.items()}
    code = _contract(db, p["buyer"])
    masters.add_price(db, code, {"item_code": "A", "price": "90", "uom": "NOS"}, p["buyer"])
    masters.activate(db, code, p["boss"], today=TODAY)
    base = f"/api/contracts/{code}"

    added = client.post(
        f"{base}/prices",
        json={"item_code": "A", "price": "85", "uom": "NOS", "valid_from": "2027-01-01"},
        headers=h["buyer"],
    )
    page = client.get(base, headers=h["boss"]).json()

    assert added.status_code == 200
    assert (page["can_manage"], page["draft_version"], page["can_submit_version"], page["can_activate_version"]) == (
        True, 2, True, False,
    )  # fmt: skip
    assert [(x["version"], x["status"]) for x in page["prices"]] == [(1, "active"), (2, "draft")]
    assert client.post(f"{base}/versions/submit", headers=h["buyer"]).status_code == 200
    assert client.post(f"{base}/versions/submit", headers=h["buyer"]).status_code == 409  # already waiting
    assert client.post(f"{base}/versions/activate", json={}, headers=h["buyer"]).status_code == 403
    assert client.post(f"{base}/versions/reject", json={"reason": ""}, headers=h["boss"]).status_code == 409
    assert client.post(f"{base}/versions/reject", json={"reason": "Too low to be real"}, headers=h["boss"]).json() == {
        "dropped": 1
    }
    assert client.get(base, headers=h["boss"]).json()["draft_version"] is None

    client.post(
        f"{base}/prices",
        json={"item_code": "A", "price": "86", "uom": "NOS", "valid_from": "2027-01-01"},
        headers=h["boss2"],
    )
    assert client.post(f"{base}/versions/submit", headers=h["buyer"]).status_code == 200
    assert client.get(base, headers=h["boss"]).json()["can_activate_version"] is True
    assert client.post(f"{base}/versions/activate", json={}, headers=h["boss2"]).status_code == 403  # entered the price
    done = client.post(f"{base}/versions/activate", json={}, headers=h["boss3"])
    assert done.status_code == 200 and done.json()["version"] == 3
    assert client.post(f"{base}/versions/activate", json={}, headers=h["boss3"]).status_code == 409  # nothing waits

    client.post(f"{base}/prices", json={"item_code": "D", "price": "5", "uom": "NOS"}, headers=h["buyer"])
    assert client.post(f"{base}/versions/withdraw", json={"reason": "Wrong item"}, headers=h["buyer"]).json() == {
        "dropped": 1
    }
    assert client.post("/api/contracts/NOPE/versions/activate", json={}, headers=h["boss"]).status_code == 404


def test_another_company_cannot_see_or_activate_a_contract_version(db: Session) -> None:  # noqa: F811
    p = _people(db)
    code = _contract(db, p["buyer"])
    masters.add_price(db, code, {"item_code": "A", "price": "90", "uom": "NOS"}, p["buyer"])
    masters.activate(db, code, p["boss"], today=TODAY)
    masters.add_price(
        db, code, {"item_code": "A", "price": "80", "uom": "NOS", "valid_from": D(2027, 1, 1)}, p["buyer"]
    )
    masters.submit_version(db, code, p["buyer"])
    other = "q" + uuid.uuid4().hex[:8]
    assert tenants.create_tenant(other, load_reference=False).ok
    stranger = users.create_user(other, f"x{db.info['sfx']}@y.example", PASSWORD, "Someone Else", "Approver")
    h = _headers(other, stranger, "Approver")

    assert client.get(f"/api/contracts/{code}", headers=h).status_code == 404
    assert client.post(f"/api/contracts/{code}/versions/activate", json={}, headers=h).status_code == 404

    assert db.execute(text("select count(*) from price_record where status = 'draft'")).scalar_one() == 1
    routing.clear_engine_cache()
