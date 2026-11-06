"""Automatic replenishment: the quantity formulas at their boundaries, document numbers, a scenario worked out by hand,
protection against duplicates, and the screen's data."""

import uuid
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session

from p2p.formulas.money import format_qty
from p2p.formulas.replenishment import purchase_quantity, shortfall
from p2p.main import app
from p2p.platform import numbering, routing, tenants, users, worker
from p2p.platform.tokens import create_access_token
from p2p.receiving.stock import stock_levels
from p2p.requests import jobs, replenishment

D = Decimal
client = TestClient(app)
FRIDAY = date(2026, 11, 6)


# ---- the formulas -----------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("policy", "position", "expected"),
    [
        ("minmax", "30", "70"),  # up to the maximum of 100
        ("minmax", "99.5", "0.5"),
        ("minmax", "100", "0"),
        ("minmax", "120", "0"),  # above the maximum: nothing, never a negative order
        ("rop", "5", "25"),  # a fixed lot, whatever the position
        ("rop", "500", "25"),
        ("none", "0", "0"),
        (None, "0", "0"),
    ],
)
def test_the_shortfall_follows_the_planning_policy(policy: str | None, position: str, expected: str) -> None:
    assert shortfall(policy, D(position), max_qty=D("100"), order_qty=D("25")) == D(expected)


def test_a_policy_without_its_numbers_orders_nothing() -> None:
    assert shortfall("minmax", D(5), max_qty=None, order_qty=None) == 0
    assert shortfall("rop", D(5), max_qty=None, order_qty=None) == 0


@pytest.mark.parametrize(
    ("need", "factor", "moq", "expected"),
    [
        ("7", "5", "1", "2"),  # 1.4 cartons rounds up to 2
        ("10", "5", "1", "2"),  # exactly two cartons is two, not three
        ("10.001", "5", "1", "3"),  # a sliver over two cartons is a third carton
        ("0.4", "5", "1", "1"),  # never a part carton
        ("1", "50", "2", "2"),  # the supplier's minimum lifts a small need
        ("500", "50", "2", "10"),
        ("5096.332", "1", "0", "5096.332"),  # a weight is not rounded to whole kilos
        ("20.8651", "1", "0", "20.866"),  # bought in its stock unit: to 0.001
        ("0", "5", "10", "0"),  # nothing needed: the minimum order does not apply
        ("-3", "5", "10", "0"),
    ],
)
def test_the_request_quantity_is_in_purchase_units_rounded_up_and_lifted_to_the_minimum(
    need: str, factor: str, moq: str, expected: str
) -> None:
    assert purchase_quantity(D(need), base_per_purchase_unit=D(factor), moq=D(moq)) == D(expected)


@pytest.mark.parametrize(
    ("value", "shown"),
    [("20.0000", "20"), ("5096.3320", "5096.332"), ("0.5", "0.5"), ("100", "100"), ("0", "0"), ("-3.0", "-3")],
)
def test_quantities_are_shown_without_trailing_zeros(value: str, shown: str) -> None:
    assert format_qty(D(value)) == shown


def test_a_purchase_unit_must_hold_something() -> None:
    with pytest.raises(ValueError, match="positive"):
        purchase_quantity(D(5), base_per_purchase_unit=D(0), moq=D(1))


# ---- a company to work in ---------------------------------------------------------------------


def _company(*, setup: bool = True) -> tuple[str, Session]:
    slug = "r" + uuid.uuid4().hex[:8]
    assert tenants.create_tenant(slug, load_reference=False).ok
    db = Session(routing.engine_for(slug))
    db.execute(
        text(
            "insert into number_series (document_type, prefix, scope, format, reset) values "
            "('request', 'REQ', 'location', '{prefix}-{loc}-{fy4}-{seq:05d}', 'fy'), "
            "('rfq', 'RFQ', 'company', '{prefix}-{fy4}-{seq:05d}', 'fy')"
        )
    )
    if setup:
        db.execute(
            text("insert into location (code, name, is_stock_location) values ('WH-BLR', 'Bengaluru store', true)")
        )
        db.execute(text("insert into item_category (code, name) values ('C1', 'Spares'), ('C2', 'Packaging')"))
        db.execute(
            text(
                "insert into item (code, name, category_code, base_uom, purchase_uom, pack_size, moq, standard_price, "
                "default_lead_days, active, stocked) values "
                "('A', 'Bearing', 'C1', 'NOS', 'NOS', 1, 1, 100, 3, true, true), "
                "('B', 'Cable tie', 'C2', 'PAC', 'CTN', 5, 2, 1000, 3, true, true), "
                "('C', 'Washer', 'C1', 'NOS', 'NOS', 1, 1, 10, 3, true, true), "
                "('D', 'Gasket', 'C1', 'NOS', 'NOS', 1, 30, 7.50, 3, true, true), "
                "('E', 'Unpriced', 'C1', 'NOS', 'NOS', 1, 1, null, 3, true, true), "
                "('F', 'Overdrawn', 'C1', 'NOS', 'NOS', 1, 1, 10, 3, true, true), "
                "('G', 'Retired', 'C1', 'NOS', 'NOS', 1, 1, 10, 3, false, true)"
            )
        )
        db.execute(
            text("insert into uom_conversion (item_code, from_uom, to_uom, factor) values ('B', 'CTN', 'PAC', 5)")
        )
        # item, opening, policy, reorder point, safety, max, lot, lead override
        plans = [
            ("A", 5, "minmax", 20, 2, 100, None, None),  # reorder: up to 100
            ("B", 0, "minmax", 10, 2, 40, None, None),  # stockout, bought in cartons of 5
            ("C", 500, "minmax", 10, 2, 600, None, None),  # plenty
            ("D", 3, "rop", 10, 1, None, 25, 1),  # reorder-point policy, fixed lot, minimum order 30
            ("E", 1, "minmax", 10, 1, 50, None, None),
            ("F", 0, "minmax", 10, 1, 50, None, None),
            ("G", 1, "minmax", 10, 1, 50, None, None),
        ]
        for item, opening, policy, rop, safety, mx, lot, lead in plans:
            db.execute(
                text(
                    "insert into stock_opening (item_code, location_code, qty, as_of) values (:i, 'WH-BLR', :q, '2026-01-01')"
                ),
                {"i": item, "q": opening},
            )
            db.execute(
                text(
                    "insert into item_location (item_code, location_code, reorder_policy, reorder_point, safety_stock, max_qty, "
                    "order_qty, lead_days_override) values (:i, 'WH-BLR', :p, :r, :s, :m, :o, :l)"
                ),
                {"i": item, "p": policy, "r": rop, "s": safety, "m": mx, "o": lot, "l": lead},
            )
        db.execute(
            text(
                "insert into stock_movement (id, item_code, location_code, date, type, qty) "
                "values ('M1', 'F', 'WH-BLR', '2026-02-01', 'issue', 5)"
            )
        )
        db.commit()
    return slug, db


@pytest.fixture
def company(control_db: str) -> Iterator[tuple[str, Session]]:
    slug, db = _company()
    yield slug, db
    db.close()
    routing.clear_engine_cache()


# ---- document numbers -------------------------------------------------------------------------


def test_numbers_follow_the_series_format_count_up_and_start_again_each_financial_year(control_db: str) -> None:
    _, db = _company(setup=False)
    nxt = numbering.next_number

    assert nxt(db, "request", date(2026, 11, 6), location_code="WH-BLR") == "REQ-BLR-2627-00001"
    assert nxt(db, "request", date(2026, 11, 7), location_code="WH-BLR") == "REQ-BLR-2627-00002"
    assert nxt(db, "request", date(2026, 11, 7), location_code="PLT-VAD") == "REQ-VAD-2627-00001"  # its own count
    assert nxt(db, "request", date(2027, 3, 31), location_code="WH-BLR") == "REQ-BLR-2627-00003"
    assert nxt(db, "request", date(2027, 4, 1), location_code="WH-BLR") == "REQ-BLR-2728-00001"  # new year
    assert nxt(db, "rfq", date(2026, 11, 6)) == "RFQ-2627-00001"  # a company-wide series ignores the location
    assert nxt(db, "rfq", date(2026, 11, 6), location_code="WH-BLR") == "RFQ-2627-00002"
    db.close()


def test_a_number_given_back_by_a_rollback_is_used_again(control_db: str) -> None:
    _, db = _company(setup=False)
    numbering.next_number(db, "request", FRIDAY, location_code="WH-BLR")
    db.commit()
    taken = numbering.next_number(db, "request", FRIDAY, location_code="WH-BLR")
    db.rollback()

    assert numbering.next_number(db, "request", FRIDAY, location_code="WH-BLR") == taken
    db.close()


def test_an_unknown_series_or_a_missing_location_is_refused(control_db: str) -> None:
    _, db = _company(setup=False)
    with pytest.raises(numbering.NoSuchSeries):
        numbering.next_number(db, "po", FRIDAY, location_code="WH-BLR")
    with pytest.raises(ValueError, match="per location"):
        numbering.next_number(db, "request", FRIDAY)
    db.close()


# ---- the run ----------------------------------------------------------------------------------


def _requests(db: Session) -> list[dict]:
    return [dict(r) for r in db.execute(text("select * from request order by no")).mappings()]


def _lines(db: Session, no: str) -> dict[str, dict]:
    rows = db.execute(text("select * from request_line where request_no = :n order by line_no"), {"n": no}).mappings()
    return {r["item_code"]: dict(r) for r in rows}


def test_the_run_creates_one_draft_per_category_and_urgency_with_quantities_worked_out_by_hand(
    company: tuple[str, Session],
) -> None:
    _, db = company

    result = replenishment.run(db, as_of=FRIDAY)

    # A and D are normal spares; B is a stockout in packaging; C is fine; E, F, G cannot be ordered
    assert [(r.no, r.category_code, r.priority) for r in result.requests] == [
        ("REQ-BLR-2627-00001", "C1", "normal"),
        ("REQ-BLR-2627-00002", "C2", "urgent"),
    ]
    spares, packaging = _requests(db)
    assert spares["status"] == "draft" and spares["channel"] == "planning" and spares["requester"] is None
    assert spares["type"] == "goods" and spares["ship_to_location"] == "WH-BLR"
    assert packaging["priority"] == "urgent"

    lines = _lines(db, "REQ-BLR-2627-00001")
    assert set(lines) == {"A", "D"}
    assert lines["A"]["qty"] == D("95")  # 100 - 5
    assert lines["A"]["base_qty"] == D("95") and lines["A"]["uom"] == "NOS"
    assert lines["D"]["qty"] == D("30")  # the lot of 25 is lifted to the supplier's minimum of 30
    assert spares["total_value"] == D("95") * 100 + D("30") * D("7.50")  # 9500 + 225 = 9725.00
    assert spares["need_by"] == date(2026, 11, 9)  # D has a one-day lead and is the earliest: Friday + 1 working day
    assert lines["A"]["need_by"] == date(2026, 11, 11)  # three working days from Friday
    assert lines["A"]["reason"] == "On hand 5 NOS is at or below the reorder point 20; order up to the maximum 100"
    assert lines["D"]["reason"] == "On hand 3 NOS is at or below the reorder point 10; order the standard lot 25"

    cartons = _lines(db, "REQ-BLR-2627-00002")["B"]
    assert cartons["qty"] == D("8") and cartons["uom"] == "CTN"  # 40 packs / 5 per carton
    assert cartons["base_qty"] == D("40") and cartons["est_price"] == D("1000")
    assert packaging["total_value"] == D("8000.00")


def test_what_cannot_be_ordered_is_reported_with_the_reason(company: tuple[str, Session]) -> None:
    _, db = company

    result = replenishment.run(db, as_of=FRIDAY, dry_run=True)

    reasons = {s.item_code: s.reason for s in result.skipped}
    assert "standard price" in reasons["E"]
    assert "negative" in reasons["F"]
    assert reasons["G"] == "Item is inactive"
    assert "C" not in reasons  # healthy stock is not a problem to report


def test_every_request_and_the_run_itself_leave_audit_rows(company: tuple[str, Session]) -> None:
    _, db = company

    result = replenishment.run(db, as_of=FRIDAY)

    rows = db.execute(
        text("select action, entity_id from audit_event where action in ('request.create', 'replenishment.run')")
    ).all()
    assert sorted(r[1] for r in rows if r[0] == "request.create") == ["REQ-BLR-2627-00001", "REQ-BLR-2627-00002"]
    assert [r[1] for r in rows if r[0] == "replenishment.run"] == [result.run_id]


def test_a_dry_run_writes_nothing_and_does_not_use_up_numbers(company: tuple[str, Session]) -> None:
    _, db = company

    preview = replenishment.run(db, as_of=FRIDAY, dry_run=True)
    assert len(preview.requests) == 2 and all(r.no is None for r in preview.requests)
    assert _requests(db) == []
    assert db.execute(text("select count(*) from number_counter")).scalar_one() == 0

    assert replenishment.run(db, as_of=FRIDAY).requests[0].no == "REQ-BLR-2627-00001"


def test_running_again_does_not_ask_for_the_same_items_twice(company: tuple[str, Session]) -> None:
    _, db = company
    replenishment.run(db, as_of=FRIDAY)

    again = replenishment.run(db, as_of=FRIDAY)

    assert again.requests == []
    assert len(_requests(db)) == 2
    assert any(s.item_code == "A" and s.reason.startswith("Already requested") for s in again.skipped)


def test_open_requests_count_as_stock_on_the_way(company: tuple[str, Session]) -> None:
    _, db = company
    before = next(x for x in stock_levels(db) if x.item_code == "A")
    assert before.on_order == 0 and before.status == "reorder"

    replenishment.run(db, as_of=FRIDAY)

    after = next(x for x in stock_levels(db) if x.item_code == "A")
    assert after.on_order == D("95") and after.status == "covered"
    # the cartons are counted in the stock unit: 8 cartons of 5 packs
    assert next(x for x in stock_levels(db) if x.item_code == "B").on_order == D("40")


def test_a_cancelled_request_frees_its_items_for_the_next_run(company: tuple[str, Session]) -> None:
    _, db = company
    replenishment.run(db, as_of=FRIDAY)
    db.execute(text("update request set status = 'cancelled' where no = 'REQ-BLR-2627-00001'"))
    db.commit()

    again = replenishment.run(db, as_of=FRIDAY)

    assert [r.category_code for r in again.requests] == ["C1"]
    assert {line.item_code for line in again.requests[0].lines} == {"A", "D"}


def test_two_runs_at_the_same_moment_still_create_each_request_once(company: tuple[str, Session]) -> None:
    slug, db = company

    def go() -> int:
        with Session(routing.engine_for(slug)) as mine:
            return len(replenishment.run(mine, as_of=FRIDAY).requests)

    with ThreadPoolExecutor(2) as pool:
        created = sorted(f.result() for f in [pool.submit(go), pool.submit(go)])

    assert created == [0, 2]
    assert len(_requests(db)) == 2


def test_a_run_can_be_limited_to_one_location(company: tuple[str, Session]) -> None:
    _, db = company
    db.execute(text("insert into location (code, name, is_stock_location) values ('PLT-VAD', 'Plant', true)"))
    db.commit()

    assert replenishment.run(db, as_of=FRIDAY, dry_run=True, location="PLT-VAD").requests == []
    assert len(replenishment.run(db, as_of=FRIDAY, dry_run=True, location="WH-BLR").requests) == 2


# ---- the daily job ----------------------------------------------------------------------------


def test_the_next_run_is_six_in_the_morning_india_time_the_next_day() -> None:
    assert jobs.next_run(datetime(2026, 11, 6, 10, 0, tzinfo=UTC)) == datetime(2026, 11, 7, 0, 30, tzinfo=UTC)
    # 19:00 UTC is already the next morning in India: the next run is the day after
    assert jobs.next_run(datetime(2026, 11, 6, 19, 0, tzinfo=UTC)) == datetime(2026, 11, 8, 0, 30, tzinfo=UTC)


def _queued(slug: str) -> list[datetime]:
    with tenants.control_engine().connect() as c:
        rows = c.execute(
            text("select run_at from job where tenant_slug = :t and kind = 'replenishment' and status = 'queued'"),
            {"t": slug},
        )
        return [r[0] for r in rows]


def test_the_daily_job_creates_requests_only_when_switched_on_and_always_queues_the_next_run(
    company: tuple[str, Session],
) -> None:
    slug, db = company
    now = datetime(2026, 11, 6, 3, 0, tzinfo=UTC)  # 08:30 in India

    jobs._run(worker.JobContext(id=-1, tenant=slug, payload={}, now=now))
    assert _requests(db) == []  # off by default
    assert _queued(slug) == [jobs.next_run(now)]

    db.execute(text("insert into app_setting (key, value) values ('auto_replenishment', 'true')"))
    db.commit()
    jobs._run(worker.JobContext(id=-1, tenant=slug, payload={}, now=now))
    assert len(_requests(db)) == 2
    assert len(_queued(slug)) == 1  # still only one waiting, not two


# ---- the screen's data ------------------------------------------------------------------------


def _headers(slug: str, role: str) -> dict[str, str]:
    uid = users.create_user(
        slug, f"{role.lower()}{uuid.uuid4().hex[:6]}@example.com", "correct horse battery", role, role
    )
    return {"Authorization": f"Bearer {create_access_token(uid, slug, role)}"}


def test_only_a_buyer_or_admin_may_run_it_and_a_preview_is_the_default(company: tuple[str, Session]) -> None:
    slug, db = company

    assert client.post("/api/replenishment/run", json={}, headers=_headers(slug, "Requester")).status_code == 403
    assert client.post("/api/replenishment/run", json={}).status_code == 401

    preview = client.post("/api/replenishment/run", json={}, headers=_headers(slug, "Buyer")).json()
    assert preview["dry_run"] is True and preview["line_count"] == 3 and preview["total_value"] == "17725.00"
    assert [r["no"] for r in preview["requests"]] == [None, None]
    assert preview["skipped"]["Item is inactive"] == 1
    assert _requests(db) == []

    done = client.post("/api/replenishment/run", json={"dry_run": False}, headers=_headers(slug, "Admin")).json()
    assert len(done["requests"]) == 2 and all(r["no"].startswith("REQ-BLR-") for r in done["requests"])
    assert len(_requests(db)) == 2
    actor = db.execute(text("select actor_type from audit_event where action = 'request.create' limit 1")).scalar_one()
    assert actor == "user"  # a person pressed the button, and the audit log says so


def test_the_requisition_pages_show_the_drafts_with_their_lines_and_amounts_as_exact_text(
    company: tuple[str, Session],
) -> None:
    slug, db = company
    replenishment.run(db, as_of=datetime.now(UTC).date())
    h = _headers(slug, "Requester")

    listed = client.get("/api/requisitions", headers=h).json()
    assert len(listed) == 2
    assert {r["status"] for r in listed} == {"draft"}
    assert {r["requested_by"] for r in listed} == {"Automatic replenishment"}
    assert sorted(r["amount"] for r in listed) == ["8000.00", "9725.00"]

    detail = client.get(f"/api/requisitions/{listed[0]['id']}", headers=h).json()
    assert detail["ship_to"] == "WH-BLR" and detail["channel"] == "planning"
    assert detail["lines"][0]["name"] in {"Bearing", "Cable tie"}
    assert detail["lines"][0]["value"] in {"9500.00", "8000.00"}
    assert client.get("/api/requisitions/NOPE", headers=h).status_code == 404
    assert client.get("/api/requisitions").status_code == 401


def test_switching_automatic_replenishment_on_queues_the_daily_job(company: tuple[str, Session]) -> None:
    slug, _ = company
    admin = _headers(slug, "Admin")

    bad = client.post("/api/settings", json={"auto_replenishment": "maybe"}, headers=admin).json()
    assert "true or false" in bad["error"]
    assert _queued(slug) == []

    assert client.post("/api/settings", json={"auto_replenishment": "true"}, headers=admin).json() == {
        "updated": ["auto_replenishment"]
    }
    assert len(_queued(slug)) == 1


def test_an_item_with_an_open_order_is_not_requested_again_even_if_the_order_leaves_it_below_its_reorder_point(
    company: tuple[str, Session],
) -> None:
    _, db = company  # item A has 5 on hand, a reorder point of 20 and a maximum of 100
    db.execute(
        text(
            "insert into request (no, ship_to_location, type, channel, status, total_value) "
            "values ('R-OLD', 'WH-BLR', 'goods', 'planning', 'closed', 1)"
        )
    )
    db.execute(text("insert into supplier (code, legal_name, status) values ('S9', 'Nine', 'active')"))
    db.execute(
        text(
            "insert into po (no, supplier_code, ship_to_location, request_no, status) values ('PO-OLD', 'S9', 'WH-BLR', 'R-OLD', 'approved')"
        )
    )
    db.execute(
        text(
            "insert into po_line (po_no, line_no, request_no, request_line_no, item_code, qty, uom, base_qty, unit_price, gst_rate, taxable, "
            "status, source) values ('PO-OLD', 1, 'R-OLD', 1, 'A', 3, 'NOS', 3, 100, 18, 300, 'open', 'contract')"
        )
    )
    db.commit()  # 3 are on order: 5 + 3 = 8 is still below the reorder point of 20

    first = replenishment.run(db, as_of=FRIDAY, dry_run=True)

    assert "A" not in {ln.item_code for r in first.requests for ln in r.lines}
    reason = next(s.reason for s in first.skipped if s.item_code == "A")
    assert reason == "Already requested or on order (PO-OLD)"

    db.execute(text("update po_line set status = 'received', received_qty = 3 where po_no = 'PO-OLD'"))
    db.commit()  # delivered: the stock is still short, so it is asked for again

    second = replenishment.run(db, as_of=FRIDAY, dry_run=True)

    assert "A" in {ln.item_code for r in second.requests for ln in r.lines}
