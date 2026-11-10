"""The store issues stock for use and corrects it after a count: the books move, the shelf limit holds, and it is all audited."""

import uuid
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session
from test_stock import _tenant, shop  # noqa: F401 - fixture

from p2p.formulas.stock import count_difference
from p2p.main import app
from p2p.platform import users
from p2p.platform.tokens import create_access_token
from p2p.receiving.stock import StockError, post_count, post_issue, stock_levels

client = TestClient(app)
D = Decimal
PASSWORD = "correct horse battery"


def _on_hand(db: Session, item: str = "A") -> Decimal:
    return next(line.on_hand for line in stock_levels(db, item=item))


def _audit(db: Session, action: str) -> list[dict]:
    return [
        dict(r)
        for r in db.execute(text("select * from audit_event where action = :a order by id"), {"a": action}).mappings()
    ]


# ---- the arithmetic ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("counted", "on_hand", "difference"),
    [
        ("7.5", "10", "-2.5"),
        ("10", "10", "0"),
        ("12", "10", "2"),
        ("0", "0.0001", "-0.0001"),
        ("0.0001", "0", "0.0001"),
    ],
)
def test_a_count_adds_or_takes_exactly_the_difference(counted: str, on_hand: str, difference: str) -> None:
    assert count_difference(D(counted), D(on_hand)) == D(difference)


# ---- issues -----------------------------------------------------------------------------------


def test_an_issue_takes_stock_out_carries_the_last_cost_and_leaves_an_audit_row(shop: Session) -> None:  # noqa: F811
    shop.execute(
        text(
            "insert into stock_movement (id, item_code, location_code, date, type, qty, unit_cost, source) values ('R1', 'A', 'WH', '2026-02-01', 'receipt', 5, 42.5, 'platform')"
        )
    )
    shop.commit()
    assert _on_hand(shop) == D("105")

    movement = post_issue(shop, 1, "A", "WH", D("20"), reference="Press 3 repair")

    assert _on_hand(shop) == D("85")
    row = shop.execute(
        text("select type, qty, unit_cost, ref_doc, source from stock_movement where id = :i"), {"i": movement}
    ).one()
    assert (row[0], str(row[1]), str(row[2]), row[3], row[4]) == (
        "issue",
        "20.0000",
        "42.5000",
        "Press 3 repair",
        "platform",
    )
    rows = _audit(shop, "stock.issue")
    assert len(rows) == 1 and rows[0]["after"]["on_hand"] == "85.0000" and rows[0]["entity_id"] == "A@WH"


def test_an_issue_cannot_take_more_than_is_on_the_shelf_and_writes_nothing(shop: Session) -> None:  # noqa: F811
    post_issue(shop, 1, "A", "WH", D("60"))

    with pytest.raises(StockError, match="only 40 is on the shelf"):
        post_issue(shop, 1, "A", "WH", D("60"))

    assert _on_hand(shop) == D("40")
    assert len(_audit(shop, "stock.issue")) == 1


def test_an_issue_of_everything_that_is_left_is_allowed_and_one_more_is_not(shop: Session) -> None:  # noqa: F811
    post_issue(shop, 1, "A", "WH", D("100"))

    assert _on_hand(shop) == D("0")
    with pytest.raises(StockError, match="only 0 is on the shelf"):
        post_issue(shop, 1, "A", "WH", D("0.0001"))


@pytest.mark.parametrize(
    ("item", "location", "qty", "message"),
    [
        ("A", "WH", "0", "more than zero"),
        ("A", "WH", "-1", "more than zero"),
        ("NOPE", "WH", "1", "no item NOPE"),
        ("A", "NOWHERE", "1", "not a stock location"),
    ],
)
def test_an_issue_that_makes_no_sense_is_refused(
    shop: Session, item: str, location: str, qty: str, message: str
) -> None:  # noqa: F811
    with pytest.raises(StockError, match=message):
        post_issue(shop, 1, item, location, D(qty))
    assert _on_hand(shop) == D("100")


def test_an_issue_that_takes_stock_to_the_reorder_point_shows_as_a_reorder(shop: Session) -> None:  # noqa: F811
    assert next(line.status for line in stock_levels(shop, item="A")) == "ok"

    post_issue(shop, 1, "A", "WH", D("10"))  # 90 is exactly the reorder point

    assert next(line.status for line in stock_levels(shop, item="A")) == "reorder"


# ---- counts -----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("counted", "after", "difference"), [("93.5", "93.5", "-6.5000"), ("104", "104", "4.0000"), ("0", "0", "-100.0000")]
)
def test_a_count_makes_the_books_match_the_shelf_and_says_by_how_much(
    shop: Session, counted: str, after: str, difference: str
) -> None:  # noqa: F811
    movement = post_count(shop, 1, "A", "WH", D(counted), "Annual stock take")

    assert movement is not None and _on_hand(shop) == D(after)
    rows = _audit(shop, "stock.adjust")
    assert len(rows) == 1 and rows[0]["after"]["difference"] == difference and rows[0]["reason"] == "Annual stock take"
    assert rows[0]["before"]["on_hand"] == "100.0000"


def test_a_count_that_agrees_with_the_books_changes_nothing(shop: Session) -> None:  # noqa: F811
    assert post_count(shop, 1, "A", "WH", D("100"), "Cycle count") is None

    assert shop.execute(text("select count(*) from stock_movement")).scalar_one() == 0
    assert _audit(shop, "stock.adjust") == []


def test_a_count_needs_a_reason_and_cannot_be_below_zero(shop: Session) -> None:  # noqa: F811
    with pytest.raises(StockError, match="say why"):
        post_count(shop, 1, "A", "WH", D("90"), "  ")
    with pytest.raises(StockError, match="below zero"):
        post_count(shop, 1, "A", "WH", D("-1"), "Recount")
    assert _on_hand(shop) == D("100")


# ---- the screens ------------------------------------------------------------------------------


def _login(slug: str, role: str) -> dict[str, str]:
    uid = users.create_user(slug, f"{role.lower()}{uuid.uuid4().hex[:6]}@x.example", PASSWORD, role, role)
    return {"Authorization": f"Bearer {create_access_token(uid, slug, role)}"}


def test_the_store_issues_and_counts_over_the_api_and_nobody_else_may(control_db: str) -> None:
    slug, db = _tenant()
    store, requester = _login(slug, "Store"), _login(slug, "Requester")

    page = client.get("/api/inventory", headers=store).json()
    assert (page["can_issue"], page["can_adjust"]) == (True, True)
    assert client.get("/api/inventory", headers=requester).json()["can_issue"] is False

    body = {"item_code": "A", "location": "WH", "qty": "15", "reference": "Line 2"}
    assert client.post("/api/inventory/issue", json=body, headers=requester).status_code == 403
    assert client.post("/api/inventory/issue", json=body).status_code == 401
    assert client.post("/api/inventory/issue", json=body, headers=store).status_code == 200
    too_much = client.post("/api/inventory/issue", json={**body, "qty": "500"}, headers=store)
    assert too_much.status_code == 409 and "only 85 is on the shelf" in too_much.json()["detail"]
    assert client.post("/api/inventory/issue", json={**body, "qty": "lots"}, headers=store).status_code == 422
    assert (
        client.post("/api/inventory/issue", json={"item_code": "A", "location": "WH"}, headers=store).status_code == 422
    )

    count = {"item_code": "A", "location": "WH", "counted_qty": "80", "reason": "Shelf count"}
    assert client.post("/api/inventory/count", json=count, headers=requester).status_code == 403
    assert client.post("/api/inventory/count", json=count, headers=store).status_code == 200
    assert client.post("/api/inventory/count", json={**count, "reason": ""}, headers=store).status_code == 409
    after = client.get("/api/inventory", headers=store).json()["lines"][0]
    assert after["on_hand"] == "80"
    db.close()
