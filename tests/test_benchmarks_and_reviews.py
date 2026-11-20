"""Market benchmarks (task 13.1, D52) and strategic reviews (task 12.5, D40, D41).

A benchmark is entered or uploaded unverified and verified by someone else; only a verified, fresh, in-region benchmark
in a convertible unit counts, and none is ever used as an estimate, a route, a contract or an order price. A contract
price above the market opens a review only above every threshold; a plan, an expiry or a buyer always opens one. Every
evaluation is recorded, and the contract stays valid."""

import io
from collections.abc import Iterator
from datetime import date, timedelta
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session
from test_sourcing import PASSWORD, TODAY, _headers, _price, db  # noqa: F401 - fixtures

from p2p.formulas.calendar import business_today
from p2p.main import app
from p2p.platform import users
from p2p.requests import estimates
from p2p.sourcing import benchmarks, price_review, price_view, service

D = Decimal
client = TestClient(app)


@pytest.fixture
def market(db: Session) -> Iterator[Session]:  # noqa: F811
    """A Karnataka store, a contract price of 62,000 for laptops (item A) and the people who enter and verify."""
    x = db.execute
    x(text("insert into region (code, name) values ('KA', 'Karnataka'), ('TS', 'Telangana')"))
    x(text("update location set region_code = 'KA' where code = 'WH-BLR'"))
    x(text("update contract set valid_to = '2027-03-31' where code = 'K-ACTIVE'"))
    db.commit()
    slug, sfx = db.info["slug"], db.info["sfx"]
    db.info["buyer"] = users.create_user(slug, f"mb{sfx}@x.example", PASSWORD, "A Buyer", "Buyer")
    db.info["boss"] = users.create_user(slug, f"m1{sfx}@x.example", PASSWORD, "An Approver", "Approver")
    db.info["admin"] = users.create_user(slug, f"ma{sfx}@x.example", PASSWORD, "Admin One", "Admin")
    db.info["admin2"] = users.create_user(slug, f"mc{sfx}@x.example", PASSWORD, "Admin Two", "Admin")
    _price(db, "S1", "A", "62000", valid_to="2027-03-31")
    yield db


def _bench(
    db_: Session, price: str, on: date, *, region: str | None = None, source: str = "OEM list", uom: str = "NOS"
) -> int:
    data = {
        "item_code": "A", "price": D(price), "uom": uom, "effective_date": on, "region_code": region,
        "source_kind": "price_list", "source_name": source,
    }  # fmt: skip
    return benchmarks.add(db_, data, db_.info["buyer"], today=on + timedelta(days=60))


def _verify(db_: Session, bid: int) -> None:
    benchmarks.verify(db_, bid, db_.info["boss"], "checked against the list")


def _price_id(db_: Session) -> int:
    return int(db_.execute(text("select max(id) from price_record where item_code = 'A'")).scalar_one())


def _ordered(db_: Session, qty: str) -> None:
    """Some of the item ordered in the last year, for the saving."""
    db_.execute(
        text(
            "insert into request (no, ship_to_location, type, channel, status, total_value) "
            "values ('R-0', 'WH-BLR', 'goods', 'planning', 'closed', 0)"
        )
    )
    db_.execute(
        text(
            "insert into po (no, supplier_code, ship_to_location, status, created_at, total) "
            "values ('PO-1', 'S1', 'WH-BLR', 'approved', :at, 0)"
        ),
        {"at": TODAY - timedelta(days=30)},
    )
    db_.execute(
        text(
            "insert into po_line (po_no, line_no, request_no, request_line_no, item_code, description, qty, uom, "
            "unit_price, gst_rate, taxable, status, source) values ('PO-1', 1, 'R-0', 1, 'A', 'Laptop', :q, 'NOS', 62000, "
            "18, 0, 'open', 'direct')"
        ),
        {"q": qty},
    )
    db_.commit()


# ---- benchmarks ----------------------------------------------------------------------------------


def test_a_benchmark_is_verified_only_by_someone_other_than_who_entered_it(market: Session) -> None:
    bid = _bench(market, "58900", date(2026, 10, 31))

    with pytest.raises(benchmarks.BenchmarkError) as refused:
        benchmarks.verify(market, bid, market.info["buyer"], None)
    assert refused.value.status == 403
    _verify(market, bid)
    with pytest.raises(benchmarks.BenchmarkError) as twice:
        _verify(market, bid)
    assert twice.value.status == 409
    with pytest.raises(benchmarks.BenchmarkError) as edited:
        benchmarks.edit(market, bid, {"price": D("58000")}, market.info["buyer"])
    assert edited.value.status == 409  # a verified record is never edited
    actions = (
        market.execute(text("select action from audit_event where entity = 'market_benchmark' order by id"))
        .scalars()
        .all()
    )
    assert actions == ["benchmark.add", "benchmark.verify"]


def test_a_correction_is_a_new_record_and_the_old_one_is_rejected_as_replaced(market: Session) -> None:
    old = _bench(market, "58900", date(2026, 10, 31))
    _verify(market, old)

    data = {
        "item_code": "A", "price": D("59900"), "uom": "NOS", "effective_date": date(2026, 10, 31),
        "source_kind": "price_list", "source_name": "OEM list", "replaces": old,
    }  # fmt: skip
    new = benchmarks.add(market, data, market.info["buyer"], today=date(2026, 11, 20))

    rows = dict(market.execute(text("select id, status from market_benchmark")).all())
    assert rows == {old: "rejected", new: "unverified"}
    reason = market.execute(text("select rejected_reason from market_benchmark where id = :i"), {"i": old}).scalar()
    assert reason == f"replaced by #{new}"


@pytest.mark.parametrize(
    ("change", "reason"),
    [
        ({"item_code": "NOPE"}, "unknown item NOPE"),
        ({"price": D(0)}, "price must be more than zero"),
        ({"uom": "XYZ"}, "unit XYZ is not known"),
        ({"effective_date": date(2030, 1, 1)}, "the effective date is in the future"),
        ({"source_name": " "}, "the source is missing"),
    ],
)
def test_a_benchmark_needs_an_item_a_price_a_unit_a_past_date_and_a_source(
    market: Session, change: dict, reason: str
) -> None:
    data = {
        "item_code": "A", "price": D("1"), "uom": "NOS", "effective_date": date(2026, 10, 31),
        "source_kind": "price_list", "source_name": "List", **change,
    }  # fmt: skip
    with pytest.raises(benchmarks.BenchmarkError, match=reason):
        benchmarks.add(market, data, market.info["buyer"], today=date(2026, 11, 20))


def _sheet(rows: list[str]) -> bytes:
    head = "item_code,price,uom,basis,effective_date,region_code,source_kind,source_name,source_ref,note"
    return ("\n".join([head, *rows]) + "\n").encode()


def test_an_upload_loads_the_good_rows_unverified_lists_the_bad_and_a_second_upload_loads_nothing(
    market: Session,
) -> None:
    slug = market.info["slug"]
    h = _headers(slug, market.info["buyer"], "Buyer")
    sheet = _sheet(
        [
            "A,64800.00,NOS,delivered,2025-11-30,,price_list,OEM list,,synthetic",
            "A,64300.00,NOS,delivered,2025-12-31,,price_list,OEM list,,synthetic",
            "ZZ,1.00,NOS,delivered,2025-12-31,,price_list,OEM list,,",
            "A,0,NOS,delivered,2025-12-31,,price_list,Other list,,",
            "A,100.00,NOS,delivered,2025-12-31,,price_list,,,",
        ]
    )

    first = client.post("/api/benchmarks/upload", files={"file": ("b.csv", sheet, "text/csv")}, headers=h)
    again = client.post("/api/benchmarks/upload", files={"file": ("b.csv", sheet, "text/csv")}, headers=h)
    changed = client.post(
        "/api/benchmarks/upload",
        files={"file": ("b.csv", _sheet(["A,64000.00,NOS,delivered,2025-11-30,,price_list,OEM list,,"]), "text/csv")},
        headers=h,
    )
    wrong = client.post("/api/benchmarks/upload", files={"file": ("b.pdf", b"%PDF", "application/pdf")}, headers=h)
    other = client.post("/api/benchmarks/upload", files={"file": ("b.csv", b"a,b\n1,2\n", "text/csv")}, headers=h)

    assert first.status_code == 200, first.text
    assert first.json()["loaded"] == 2
    assert [r["line"] for r in first.json()["rejected"]] == [4, 5, 6]
    assert [r["reason"] for r in first.json()["rejected"]] == [
        "unknown item ZZ", "price must be more than zero", "the source is missing",
    ]  # fmt: skip
    assert (again.json()["loaded"], again.json()["skipped"]) == (0, 2)
    assert changed.json()["changed"][0]["reason"] == "already loaded; changed"
    assert wrong.status_code == 415 and other.status_code == 422 and "use the template" in other.text
    assert set(market.execute(text("select status from market_benchmark")).scalars()) == {"unverified"}


def test_a_benchmark_is_evidence_only_and_never_changes_an_estimate_or_a_contract_price(market: Session) -> None:
    before = estimates.for_item(market, "A", "NOS", D(1), TODAY, D("61000"), "WH-BLR")
    bid = _bench(market, "100", date(2026, 10, 31))  # absurdly cheap
    _verify(market, bid)

    after = estimates.for_item(market, "A", "NOS", D(1), TODAY, D("61000"), "WH-BLR")
    assert before == after
    assert [o.price for o in service.contract_options(market, "A", "NOS", TODAY, "WH-BLR")] == [D("62000.0000")]


def test_the_api_lists_benchmarks_with_age_and_whether_each_counts(market: Session) -> None:
    slug = market.info["slug"]
    on = business_today()
    fresh = _bench(market, "58900", on - timedelta(days=45))
    stale = _bench(market, "58800", on - timedelta(days=46), source="Other list")
    _verify(market, fresh)
    _verify(market, stale)
    _bench(market, "58000", on - timedelta(days=10), source="Unverified list")

    listed = client.get("/api/benchmarks?item=A", headers=_headers(slug, market.info["boss"], "Approver")).json()

    why = {b["source_name"]: (b["counts"], b["why"], b["age_days"]) for b in listed["benchmarks"]}
    assert why == {
        "OEM list": (True, "fresh", 45),
        "Other list": (False, "stale: 46 days old", 46),
        "Unverified list": (False, "unverified", 10),
    }
    assert client.get("/api/benchmarks", headers=_headers(slug, market.info["admin"], "Admin")).status_code == 403


def test_the_price_view_keeps_each_price_type_in_its_own_section(market: Session) -> None:
    bid = _bench(market, "58900", business_today() - timedelta(days=5))
    _verify(market, bid)
    slug = market.info["slug"]

    view = client.get(
        "/api/items/A/prices?location=WH-BLR", headers=_headers(slug, market.info["buyer"], "Buyer")
    ).json()

    assert [c["price"] for c in view["contract_prices"]] == ["62000.0000"]
    assert view["contract_prices"][0]["market_gap"]["label"] == "market gap, evidence only"
    assert [b["price"] for b in view["benchmarks"]] == ["58900.0000"]
    assert view["budget_price"] == "not kept per item" and view["last_paid"] is None
    assert (
        client.get("/api/items/NOPE/prices", headers=_headers(slug, market.info["buyer"], "Buyer")).status_code == 404
    )
    assert price_view.for_item(market, "A", "WH-BLR", business_today())["standard_price"] is None  # type: ignore[index]


# ---- the price-gap test and reviews --------------------------------------------------------------


def test_exactly_five_percent_below_the_contract_opens_no_review_but_records_the_evaluation(market: Session) -> None:
    _ordered(market, "40")
    on = date(2026, 11, 20)
    bid = _bench(market, "58900", date(2026, 10, 31))
    _verify(market, bid)

    found = price_review.run(market, market.info["slug"], on)

    assert found.opened == [] and len(found.below) == 1
    row = market.execute(text("select output from rule_evaluation where key = 'price_threshold'")).scalar_one()
    assert (row["gap_pct"], row["goes_ahead"]) == ("5.0000", False)


def test_just_above_every_threshold_a_review_opens_once_and_the_contract_stays_in_use(market: Session) -> None:
    _ordered(market, "40")
    on = date(2026, 11, 20)
    bid = _bench(market, "58899", date(2026, 10, 31))  # 5.0016%
    _verify(market, bid)

    first = price_review.run(market, market.info["slug"], on)
    second = price_review.run(market, market.info["slug"], on)

    assert len(first.opened) == 1 and second.opened == [] and len(second.joined) == 1
    (review,) = price_review.listing(market)
    assert (review["trigger"], review["gap_pct"], review["rule"]) == ("price_gap", "5.0016", "price_threshold v1")
    assert len(review["triggers"]) == 1  # the same finding again adds nothing
    assert review["allows"]["note"].startswith("The contract stays valid")
    labels = market.execute(text("select rule_version from audit_event where action = 'review.open'")).scalars().all()
    assert labels == ["price_threshold v1"]  # the rule version that decided is on the audit row
    assert [o.price for o in service.contract_options(market, "A", "NOS", on, "WH-BLR")] == [D("62000.0000")]


def test_stale_unverified_and_other_region_benchmarks_trigger_nothing(market: Session) -> None:
    _ordered(market, "40")
    on = date(2026, 11, 20)
    stale = _bench(market, "50000", on - timedelta(days=46), source="Stale")
    other = _bench(market, "50000", date(2026, 11, 1), region="TS", source="Other region")
    _bench(market, "50000", date(2026, 11, 1), source="Unverified")
    _verify(market, stale)
    _verify(market, other)
    market.execute(text("update price_record set region_code = 'KA' where item_code = 'A'"))
    market.commit()

    found = price_review.run(market, market.info["slug"], on)

    assert found.opened == [] and "no counting benchmark" in found.below[0]


def test_a_contract_opens_an_expiry_review_sixty_days_before_its_end(market: Session) -> None:
    slug = market.info["slug"]
    assert price_review.run(market, slug, date(2027, 1, 29)).opened == []
    opened = price_review.run(market, slug, date(2027, 1, 30)).opened
    assert "K-ACTIVE expiry" in opened
    labels = market.execute(text("select rule_version from audit_event where action = 'review.open'")).scalars()
    assert set(labels) == {"price_threshold v1"}


def test_a_buyer_always_opens_a_review_and_a_second_request_joins_it(market: Session) -> None:
    slug = market.info["slug"]
    h = _headers(slug, market.info["buyer"], "Buyer")

    first = client.post(
        "/api/reviews", json={"trigger": "buyer", "contract_code": "K-ACTIVE", "reason": "renewal"}, headers=h
    )
    second = client.post("/api/reviews", json={"contract_code": "K-ACTIVE", "reason": "again"}, headers=h)
    unknown = client.post("/api/reviews", json={"contract_code": "K-NONE", "reason": "x"}, headers=h)
    listed = client.get("/api/reviews?status=open", headers=_headers(slug, market.info["boss"], "Approver")).json()
    no_reason = client.post(f"/api/reviews/{first.json()['task_id']}/close", json={"outcome": "no_action"}, headers=h)
    closed = client.post(
        f"/api/reviews/{first.json()['task_id']}/close", json={"outcome": "no_action", "reason": "fine"}, headers=h
    )

    assert first.json()["joined"] is False and second.json() == {"task_id": first.json()["task_id"], "joined": True}
    assert unknown.status_code == 422
    assert len(listed) == 1 and len(listed[0]["triggers"]) == 2
    assert no_reason.status_code == 422 and closed.status_code == 200
    actions = (
        market.execute(text("select action from audit_event where action like 'review.%' order by id")).scalars().all()
    )
    assert actions == ["review.open", "review.joined", "review.closed"]


def test_a_threshold_change_waits_for_a_second_administrator_and_is_used_from_its_date(market: Session) -> None:
    slug = market.info["slug"]
    ha, hb = _headers(slug, market.info["admin"], "Admin"), _headers(slug, market.info["admin2"], "Admin")
    start = (business_today() + timedelta(days=1)).isoformat()

    bad = client.put(
        "/api/setup/price-thresholds",
        json={"values": {"gap_pct": "150"}, "effective_from": start, "reason": "x"},
        headers=ha,
    )
    past = client.put(
        "/api/setup/price-thresholds",
        json={"values": {"gap_pct": "3"}, "effective_from": "2026-01-01", "reason": "x"},
        headers=ha,
    )
    asked = client.put(
        "/api/setup/price-thresholds",
        json={"values": {"gap_pct": "3"}, "effective_from": start, "reason": "validated with the company"},
        headers=ha,
    )

    assert bad.status_code == 422 and past.status_code == 422
    assert asked.status_code == 202
    own = client.post(f"/api/admin/proposals/{asked.json()['proposal_id']}/approve", json={}, headers=ha)
    done = client.post(f"/api/admin/proposals/{asked.json()['proposal_id']}/approve", json={}, headers=hb)
    assert own.status_code == 403 and done.json()["execution"] == "ok", done.text
    rules_now = client.get("/api/setup/price-thresholds", headers=hb).json()
    assert [v["version"] for v in rules_now["versions"]] == [2, 1]
    assert "needs validation" in rules_now["banner"].lower() or "need validation" in rules_now["banner"].lower()
    assert price_review.rule_for(market, None, business_today()).version == "price_threshold v1"
    assert price_review.rule_for(market, None, business_today() + timedelta(days=1)).gap_pct == D("3")


def test_another_company_cannot_read_the_benchmarks(market: Session, control_db: str) -> None:
    import uuid

    from p2p.platform import tenants

    other = "o" + uuid.uuid4().hex[:8]
    assert tenants.create_tenant(other, load_reference=False).ok
    them = users.create_user(other, f"ob{uuid.uuid4().hex[:6]}@x.example", PASSWORD, "Their Buyer", "Buyer")
    _bench(market, "58900", date(2026, 10, 31))

    assert client.get("/api/benchmarks", headers=_headers(other, them, "Buyer")).json()["benchmarks"] == []
    tenants.drop_tenant(other)


def test_the_upload_template_has_our_columns(market: Session) -> None:
    got = client.get("/api/benchmarks/template", headers=_headers(market.info["slug"], market.info["buyer"], "Buyer"))
    first = io.StringIO(got.content.decode("utf-8-sig")).readline().strip()
    assert first == "item_code,price,uom,basis,effective_date,region_code,source_kind,source_name,source_ref,note"
