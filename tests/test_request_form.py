"""The New request screen: items and quantities typed on the screen become a draft, with no model involved."""

from datetime import date
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session
from test_intake_agent import _all, _asker, world  # noqa: F401 - fixtures
from test_orders import _headers, shop  # noqa: F401 - fixtures
from test_sourcing import Outbox, db, outbox  # noqa: F401 - fixtures

from p2p.agents import intake
from p2p.main import app

client = TestClient(app)
TODAY = date(2026, 11, 6)
NEED = date(2026, 11, 20)


def test_a_request_typed_on_the_screen_becomes_a_draft_with_its_lines_and_an_audit_row(world: Session) -> None:  # noqa: F811
    result = intake.create_from_form(
        world,
        _asker(world),
        [("B1", Decimal(10)), ("F1", Decimal(2))],
        NEED,
        None,
        "For the line 3 repair",
        today=TODAY,
    )

    assert result.status == "drafted" and result.request_nos
    rows = world.execute(
        text(
            "select r.status, r.channel, r.justification, l.item_code, l.qty from request r "
            "join request_line l on l.request_no = r.no where r.no = any(:n) order by l.item_code"
        ),
        {"n": result.request_nos},
    ).all()
    assert {(r[3], int(r[4])) for r in rows} == {("B1", 10), ("F1", 2)}
    assert {r[0] for r in rows} == {"draft"} and {r[1] for r in rows} == {"form"}
    assert any("line 3 repair" in (r[2] or "") for r in rows)
    audited = world.execute(
        text("select count(*) from audit_event where action = 'request.create' and entity_id = any(:n)"),
        {"n": result.request_nos},
    ).scalar_one()
    assert audited == len(result.request_nos)


@pytest.mark.parametrize(
    ("lines", "need", "message"),
    [
        ([], NEED, "at least one item"),
        ([("NOPE", Decimal(1))], NEED, "no item NOPE"),
        ([("B1", Decimal(0))], NEED, "more than zero"),
        ([("B1", Decimal(1)), ("B1", Decimal(2))], NEED, "listed twice"),
        ([("B1", Decimal(1))], date(2026, 11, 1), "in the past"),
    ],
)
def test_a_form_that_makes_no_sense_is_refused_and_nothing_is_saved(
    world: Session,
    lines: list,
    need: date,
    message: str,  # noqa: F811
) -> None:
    before = world.execute(text("select count(*) from request")).scalar_one()

    with pytest.raises(intake.FormError, match=message):
        intake.create_from_form(world, _asker(world), lines, need, None, None, today=TODAY)

    assert world.execute(text("select count(*) from request")).scalar_one() == before


def test_the_screen_loads_the_catalogue_and_saves_through_the_api_for_people_who_may_raise_requests(
    world: Session,
    outbox: Outbox,  # noqa: F811
) -> None:
    slug = world.info["slug"]
    logins = _all(world)
    requester = _headers(slug, logins["keeper"], "Requester")
    buyer = _headers(slug, logins["buyer"], "Buyer")

    form = client.get("/api/requisition-form", headers=requester)
    assert form.status_code == 200
    body = form.json()
    assert {"B1", "F1"} <= {i["code"] for i in body["items"]} and body["locations"] and body["need_by"]
    assert client.get("/api/requisition-form", headers=buyer).status_code == 403  # a buyer does not raise requests

    made = client.post(
        "/api/requisitions",
        json={"lines": [{"item_code": "B1", "qty": "4"}], "need_by": "2099-01-01", "reason": "spare"},
        headers=requester,
    )
    assert made.status_code == 200 and made.json()["id"].startswith("REQ-")
    listed = {r["id"]: r for r in client.get("/api/requisitions", headers=requester).json()}
    row = listed[made.json()["id"]]
    assert row["title"] == "Ball bearing 6205" and row["mine"] is True and row["status"] == "draft"

    assert (
        client.post("/api/requisitions", json={"lines": "x", "need_by": "2099-01-01"}, headers=requester).status_code
        == 422
    )
    assert (
        client.post(
            "/api/requisitions", json={"lines": [{"item_code": "B1"}], "need_by": "2099-01-01"}, headers=requester
        ).status_code
        == 422
    )
    assert (
        client.post(
            "/api/requisitions",
            json={"lines": [{"item_code": "B1", "qty": "x"}], "need_by": "2099-01-01"},
            headers=requester,
        ).status_code
        == 422
    )
    refused = client.post(
        "/api/requisitions",
        json={"lines": [{"item_code": "NOPE", "qty": "1"}], "need_by": "2099-01-01"},
        headers=requester,
    )
    assert refused.status_code == 422 and "NOPE" in refused.json()["detail"]
    assert (
        client.post(
            "/api/requisitions",
            json={"lines": [{"item_code": "B1", "qty": "1"}], "need_by": "2099-01-01"},
            headers=buyer,
        ).status_code
        == 403
    )
    assert client.post("/api/requisitions", json={}).status_code == 401


def test_a_place_chosen_on_the_form_may_be_a_location_that_is_not_a_store_yet(world: Session) -> None:  # noqa: F811
    world.execute(
        text(
            "insert into location (code, name, type, state_code, is_stock_location) "
            "values ('PLT-NEW', 'Plant 2 (opening)', 'plant', '24', false)"
        )
    )
    world.commit()

    result = intake.create_from_form(world, _asker(world), [("B1", Decimal(1))], NEED, "PLT-NEW", None, today=TODAY)

    place = world.execute(
        text("select ship_to_location from request where no = any(:n)"), {"n": result.request_nos}
    ).scalar_one()
    assert place == "PLT-NEW"
    with pytest.raises(intake.FormError, match="Choose where"):
        intake.create_from_form(world, _asker(world), [("B1", Decimal(1))], NEED, "NO-SUCH", None, today=TODAY)
