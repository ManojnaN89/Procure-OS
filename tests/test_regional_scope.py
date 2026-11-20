"""Regional scope (task 12.4, D44): a supplier approval or a contract price limited to locations or regions is never used
outside its scope. The delivery location of the line decides the contract price, the estimate, who is asked to quote,
the must-have on a quote and the RFQ (one per category and delivery location)."""

from collections.abc import Iterator
from datetime import date
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session
from test_sourcing import PASSWORD, TODAY, Outbox, _headers, _price, _request, db, outbox  # noqa: F401 - fixtures

from p2p.main import app
from p2p.masters import categories, regions
from p2p.masters import documents_and_contracts as masters
from p2p.platform import proposals, users
from p2p.platform.permissions import PermissionDenied
from p2p.platform.sod import user
from p2p.requests import estimates
from p2p.sourcing import service

D = Decimal
client = TestClient(app)


@pytest.fixture
def places(db: Session) -> Iterator[Session]:  # noqa: F811
    """Two plants in two regions: Bengaluru (KA) where the history is, and a new one in Hyderabad (TS)."""
    x = db.execute
    x(text("insert into region (code, name) values ('KA', 'Karnataka'), ('TS', 'Telangana')"))
    x(text("update location set region_code = 'KA' where code = 'WH-BLR'"))
    x(
        text(
            "insert into location (code, name, is_stock_location, region_code) values ('WH-HYD', 'Hyderabad store', true, 'TS')"
        )
    )
    db.commit()
    yield db


def _scope(
    db_: Session, supplier: str, category: str, *, region: str | None = None, location: str | None = None
) -> None:
    db_.execute(
        text(
            "insert into supplier_category_scope (supplier_code, category_code, location_code, region_code) "
            "values (:s, :c, :l, :r)"
        ),
        {"s": supplier, "c": category, "l": location, "r": region},
    )
    db_.commit()


def _scoped_price(db_: Session, supplier: str, item: str, price: str, region: str | None) -> None:
    _price(db_, supplier, item, price)
    db_.execute(
        text("update price_record set region_code = :r where id = (select max(id) from price_record)"), {"r": region}
    )
    db_.commit()


def _found(db_: Session, item: str, location: str | None) -> list[str]:
    return [f"{o.supplier_code}:{o.price}" for o in service.contract_options(db_, item, "NOS", TODAY, location)]


def test_a_regional_contract_price_is_used_only_inside_its_region(places: Session) -> None:
    _scoped_price(places, "S1", "A", "650", "KA")

    assert _found(places, "A", "WH-BLR") == ["S1:650.0000"]
    assert _found(places, "A", "WH-HYD") == []
    assert _found(places, "A", None) == ["S1:650.0000"]  # a reader naming no place (a report) sees every price
    est_here = estimates.for_item(places, "A", "NOS", D(1), TODAY, D("700"), "WH-BLR")
    est_there = estimates.for_item(places, "A", "NOS", D(1), TODAY, D("700"), "WH-HYD")
    assert (est_here.source, est_here.price) == ("contract K-ACTIVE", D("650"))  # type: ignore[union-attr]
    assert not est_there.source.startswith("contract")  # type: ignore[union-attr]
    assert service.scoped_out(places, "A", "NOS", TODAY, "WH-HYD") == ["K-ACTIVE applies in Karnataka only"]


def test_a_line_for_the_other_region_goes_to_quotation_with_the_reason(places: Session, outbox: Outbox) -> None:  # noqa: F811
    _scoped_price(places, "S1", "A", "650", "KA")
    no = _request(places, "R-HYD", [("A", "2", None, "C1")])
    places.execute(text("update request set ship_to_location = 'WH-HYD' where no = :n"), {"n": no})
    places.commit()

    result = service.source_request(places, places.info["slug"], no, as_of=TODAY, dispatch=False)

    (line,) = result.lines
    assert line.method == "rfq" and "No contract here: K-ACTIVE applies in Karnataka only" in line.reason
    assert places.execute(text("select delivery_location from rfq")).scalar_one() == "WH-HYD"


def test_who_is_asked_to_quote_depends_on_where_the_goods_go(places: Session) -> None:
    _scope(places, "S1", "C1", region="KA")  # S1 serves Karnataka only
    _scope(places, "S2", "C1", location="WH-BLR")

    def asked(location: str | None) -> list[str]:
        found, _ = service.eligible_suppliers(places, "C1", ["A"], TODAY, location)
        return [s.code for s in found]

    assert "S1" in asked("WH-BLR") and "S2" in asked("WH-BLR")
    assert "S1" not in asked("WH-HYD") and "S2" not in asked("WH-HYD")
    assert {"S3", "S4"} <= set(asked("WH-HYD"))  # approved everywhere
    assert regions.approved_at(places, "S1", "C1", "WH-BLR", TODAY)
    assert not regions.approved_at(places, "S1", "C1", "WH-HYD", TODAY)


def test_rfqs_are_made_per_category_and_delivery_location(places: Session, outbox: Outbox) -> None:  # noqa: F811
    no = _request(places, "R-TWO", [("A", "2", None, "C1"), ("B", "3", None, "C1")])
    places.execute(
        text("update request_line set delivery_location = 'WH-HYD' where request_no = :n and line_no = 2"), {"n": no}
    )
    places.commit()

    result = service.source_request(places, places.info["slug"], no, as_of=TODAY, dispatch=False)

    assert len(result.rfqs) == 2
    where = places.execute(text("select delivery_location, min_quotes, min_quotes_rule from rfq order by no")).all()
    assert {w[0] for w in where} == {"WH-BLR", "WH-HYD"}
    assert all(w[1] == 2 and w[2] == "category C1" for w in where)  # the count and the rule it came from are kept


def _people(db_: Session) -> dict[str, int]:
    slug, sfx = db_.info["slug"], db_.info["sfx"]
    return {
        "buyer": users.create_user(slug, f"rb{sfx}@x.example", PASSWORD, "A Buyer", "Buyer"),
        "boss": users.create_user(slug, f"r1{sfx}@x.example", PASSWORD, "First Approver", "Approver"),
        "boss2": users.create_user(slug, f"r2{sfx}@x.example", PASSWORD, "Second Approver", "Approver"),
    }


def test_two_prices_of_one_supplier_for_one_item_may_coexist_only_in_separate_places(places: Session) -> None:
    p = _people(places)
    code = masters.create_contract(
        places, {"supplier_code": "S1", "valid_from": date(2026, 11, 1), "valid_to": date(2027, 10, 31)}, p["buyer"]
    )
    masters.add_price(places, code, {"item_code": "A", "price": "650", "uom": "NOS", "region_code": "KA"}, p["buyer"])
    masters.add_price(places, code, {"item_code": "A", "price": "685", "uom": "NOS", "region_code": "TS"}, p["buyer"])

    with pytest.raises(masters.MasterError, match="same place"):  # everywhere meets Karnataka
        masters.add_price(places, code, {"item_code": "A", "price": "700", "uom": "NOS"}, p["buyer"])
    with pytest.raises(masters.MasterError, match="segregation"):  # its maker never activates it
        masters.activate(places, code, p["buyer"], today=TODAY)
    masters.activate(places, code, p["boss"], today=TODAY)
    assert _found(places, "A", "WH-BLR") == ["S1:650.0000"]
    assert _found(places, "A", "WH-HYD") == ["S1:685.0000"]
    audit = (
        places.execute(text("select after->>'region' from audit_event where action = 'contract.price_add' order by id"))
        .scalars()
        .all()
    )
    assert audit == ["KA", "TS"]


def test_the_price_api_refuses_a_location_and_a_region_together(places: Session) -> None:
    p = _people(places)
    slug = places.info["slug"]
    code = masters.create_contract(
        places, {"supplier_code": "S1", "valid_from": date(2026, 11, 1), "valid_to": date(2027, 10, 31)}, p["buyer"]
    )
    h = _headers(slug, p["buyer"], "Buyer")

    both = client.post(
        f"/api/contracts/{code}/prices",
        json={"item_code": "A", "price": "650", "uom": "NOS", "location_code": "WH-BLR", "region_code": "KA"},
        headers=h,
    )
    unknown = client.post(
        f"/api/contracts/{code}/prices", json={"item_code": "A", "price": "650", "region_code": "XX"}, headers=h
    )

    assert both.status_code == 422 and unknown.status_code == 422
    regions_seen = client.get("/api/regions", headers=h).json()
    assert regions_seen == [
        {"code": "KA", "name": "Karnataka", "locations": [{"code": "WH-BLR", "name": "Bengaluru store"}]},
        {"code": "TS", "name": "Telangana", "locations": [{"code": "WH-HYD", "name": "Hyderabad store"}]},
    ]


def test_a_category_approval_with_a_scope_waits_for_someone_other_than_who_asked(places: Session) -> None:
    p = _people(places)
    slug = places.info["slug"]
    asked = client.post(
        "/api/vendors/S3/categories",
        json={"category": "C2", "approved_from": "2026-11-01", "scope": {"regions": ["TS"]}, "reason": "new plant"},
        headers=_headers(slug, p["boss"], "Approver"),
    )
    assert asked.status_code == 202, asked.text
    again = client.post(
        "/api/vendors/S3/categories",
        json={"category": "C2", "scope": {"regions": ["KA"]}, "reason": "again"},
        headers=_headers(slug, p["boss"], "Approver"),
    )
    assert again.status_code == 409
    unknown = client.post(
        "/api/vendors/S3/categories",
        json={"category": "C9", "reason": "x"},
        headers=_headers(slug, p["boss"], "Approver"),
    )
    assert unknown.status_code == 422

    with pytest.raises(PermissionDenied):
        proposals.approve(places, asked.json()["proposal_id"], p["boss"])
    assert not regions.approved_at(places, "S3", "C2", "WH-HYD", TODAY)
    proposals.approve(places, asked.json()["proposal_id"], p["boss2"])

    assert regions.approved_at(places, "S3", "C2", "WH-HYD", TODAY)
    assert not regions.approved_at(places, "S3", "C2", "WH-BLR", TODAY)
    (c2,) = [c for c in categories.categories_of(places, "S3") if c["code"] == "C2"]
    assert c2["where"] == "Telangana" and c2["scope"] == {"locations": [], "regions": ["TS"]}
    profile = client.get("/api/vendors/S3/profile", headers=_headers(slug, p["boss"], "Approver")).json()
    assert {
        "code": "C2",
        "approved_from": "2026-11-01",
        "scope": {"locations": [], "regions": ["TS"]},
        "where": "Telangana",
    } in profile["categories"]
    assert (
        places.execute(text("select count(*) from audit_event where action = 'supplier.category_scope'")).scalar_one()
        == 1
    )


def test_a_quote_from_a_supplier_not_approved_where_the_goods_go_fails_the_must_have(
    places: Session, outbox: Outbox
) -> None:  # noqa: F811
    from p2p.orders import quotes

    _scope(places, "S1", "C1", region="KA")
    no = _request(places, "R-Q", [("A", "2", None, "C1")])
    places.execute(text("update request set ship_to_location = 'WH-HYD' where no = :n"), {"n": no})
    places.commit()
    service.source_request(places, places.info["slug"], no, as_of=TODAY, dispatch=False)
    rfq = places.execute(text("select no from rfq")).scalar_one()
    places.execute(text("update rfq set status = 'sent' where no = :n"), {"n": rfq})
    places.execute(
        text("insert into rfq_supplier (rfq_no, supplier_code, email) values (:r, 'S1', 's1@x.example')"), {"r": rfq}
    )
    places.commit()
    quotes.enter_quote(
        places, rfq, quotes.QuoteInput("S1", [quotes.QuoteLineInput(1, D("100"), D("18"), delivery_days=1)]), user(1)
    )

    comp = quotes.comparison(places, rfq, TODAY)

    (offer,) = comp.lines[0].offers
    assert "not approved for this category at Hyderabad store" in offer.flags


def test_an_rfq_made_before_the_location_was_kept_is_judged_at_its_requests_place(
    places: Session, outbox: Outbox
) -> None:  # noqa: F811
    from p2p.orders import quotes

    _scope(places, "S1", "C1", region="KA")
    no = _request(places, "R-OLD", [("A", "2", None, "C1")])  # delivered at WH-BLR, inside KA
    service.source_request(places, places.info["slug"], no, as_of=TODAY, dispatch=False)
    rfq = places.execute(text("select no from rfq")).scalar_one()
    places.execute(text("update rfq set status = 'sent', delivery_location = null where no = :n"), {"n": rfq})
    places.execute(
        text(
            "insert into rfq_supplier (rfq_no, supplier_code, email) values (:r, 'S1', 's1@x.example') "
            "on conflict do nothing"
        ),
        {"r": rfq},
    )
    places.commit()
    quotes.enter_quote(
        places, rfq, quotes.QuoteInput("S1", [quotes.QuoteLineInput(1, D("100"), D("18"), delivery_days=1)]), user(1)
    )

    comp = quotes.comparison(places, rfq, TODAY)

    offer = next(o for o in comp.lines[0].offers if o.supplier_code == "S1")
    assert not any("not approved" in f for f in offer.flags)
