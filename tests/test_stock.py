"""Stock levels: the status formula at its boundaries, balances worked out by hand, the history load, and the screen's data."""

import csv
import shutil
import subprocess
import sys
import uuid
from collections.abc import Iterator
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session

from p2p.formulas.stock import stock_status
from p2p.main import app
from p2p.masters.importer import import_masters
from p2p.platform import routing, tenants, users
from p2p.platform.tokens import create_access_token
from p2p.receiving.stock import import_movements, stock_levels

ROOT = Path(__file__).resolve().parents[1]
APEX = ROOT / "data" / "datasets" / "apex-v1"  # the main demo company
client = TestClient(app)
D = Decimal


def status(on_hand: str, on_order: str = "0", **kw: str | None) -> str:
    plan = {"policy": "minmax", "reorder_point": "100", "safety_stock": "20", "max_qty": "300"} | kw
    return stock_status(
        D(on_hand),
        D(on_order),
        policy=plan["policy"],
        reorder_point=D(plan["reorder_point"]) if plan["reorder_point"] is not None else None,
        safety_stock=D(plan["safety_stock"]) if plan["safety_stock"] is not None else None,
        max_qty=D(plan["max_qty"]) if plan["max_qty"] is not None else None,
    )


@pytest.mark.parametrize(
    ("on_hand", "on_order", "expected"),
    [
        ("-0.001", "0", "negative"),
        ("0", "0", "stockout"),
        ("0", "500", "stockout"),  # nothing on the shelf is a stockout even when an order is coming
        ("19.999", "0", "below_safety"),
        ("20", "0", "reorder"),  # exactly the safety stock is not below it
        ("100", "0", "reorder"),  # exactly the reorder point reorders
        ("100", "0.001", "covered"),  # any order that lifts the position above the point covers it
        ("101", "0", "ok"),
        ("300", "0", "ok"),  # exactly the maximum is not over it
        ("300.001", "0", "over_max"),
    ],
)
def test_the_status_changes_exactly_at_each_threshold(on_hand: str, on_order: str, expected: str) -> None:
    assert status(on_hand, on_order) == expected


def test_an_item_without_a_reorder_policy_is_not_planned_unless_it_is_empty() -> None:
    assert status("50", policy="none") == "not_planned"
    assert status("50", policy=None) == "not_planned"
    assert status("0", policy="none") == "stockout"


# ---- the database -----------------------------------------------------------------------------


def _tenant(rows: bool = True) -> tuple[str, Session]:
    slug = "s" + uuid.uuid4().hex[:8]
    assert tenants.create_tenant(slug, load_reference=False).ok
    db = Session(routing.engine_for(slug))
    if rows:
        db.execute(text("insert into location (code, name, is_stock_location) values ('WH', 'Main store', true)"))
        db.execute(text("insert into item_category (code, name) values ('C1', 'Spares')"))
        db.execute(
            text(
                "insert into item (code, name, category_code, base_uom) values ('A', 'Bearing', 'C1', 'NOS'), ('B', 'Seal', 'C1', 'NOS')"
            )
        )
        db.execute(
            text(
                "insert into stock_opening (item_code, location_code, qty, as_of) values ('A', 'WH', 100, '2026-01-01')"
            )
        )
        db.execute(
            text(
                "insert into item_location (item_code, location_code, reorder_policy, reorder_point, safety_stock, max_qty) "
                "values ('A', 'WH', 'minmax', 90, 10, 300)"
            )
        )
        db.commit()
    return slug, db


@pytest.fixture
def shop(control_db: str) -> Iterator[Session]:
    _, db = _tenant()
    yield db
    db.close()
    routing.clear_engine_cache()


def _move(
    db: Session, ident: str, item: str, kind: str, qty: str, day: str = "2026-02-01", source: str = "import"
) -> None:
    db.execute(
        text(
            "insert into stock_movement (id, item_code, location_code, date, type, qty, source) "
            "values (:i, :it, 'WH', :d, :t, :q, :s)"
        ),
        {"i": ident, "it": item, "d": day, "t": kind, "q": qty, "s": source},
    )


def test_on_hand_is_the_opening_balance_plus_receipts_less_issues(shop: Session) -> None:
    _move(shop, "1", "A", "receipt", "50")
    _move(shop, "2", "A", "issue", "30")
    _move(shop, "3", "A", "return", "5")  # back to the vendor
    _move(shop, "4", "A", "transfer_out", "7")
    _move(shop, "5", "A", "transfer_in", "2")
    _move(shop, "6", "A", "adjust", "-3.5")  # an adjustment carries its own sign
    _move(shop, "7", "A", "issue", "999", day="2025-12-31")  # before the opening date: already in the balance
    shop.commit()

    (line,) = [x for x in stock_levels(shop) if x.item_code == "A"]

    assert line.on_hand == D("100") + 50 - 30 - 5 - 7 + 2 - D("3.5")  # 106.5
    assert line.uom == "NOS" and line.category == "Spares" and line.location_name == "Main store"
    assert line.status == "ok"  # 106.5 is above the reorder point 90


def test_an_item_with_movements_but_no_opening_balance_starts_from_zero(shop: Session) -> None:
    _move(shop, "1", "B", "receipt", "12")
    shop.commit()

    line = next(x for x in stock_levels(shop) if x.item_code == "B")

    assert line.on_hand == D("12")
    assert line.status == "not_planned"  # it has no planning record here


def test_a_balance_that_falls_below_zero_is_flagged_not_hidden(shop: Session) -> None:
    _move(shop, "1", "A", "issue", "101")
    shop.commit()

    assert next(x for x in stock_levels(shop) if x.item_code == "A").status == "negative"


def test_the_list_can_be_limited_to_one_location_or_item(shop: Session) -> None:
    shop.execute(text("insert into location (code, name, is_stock_location) values ('PL', 'Plant', true)"))
    shop.execute(
        text("insert into stock_opening (item_code, location_code, qty, as_of) values ('A', 'PL', 5, '2026-01-01')")
    )
    shop.commit()

    assert {x.location_code for x in stock_levels(shop)} == {"WH", "PL"}
    assert {x.location_code for x in stock_levels(shop, location="PL")} == {"PL"}
    assert [x.item_code for x in stock_levels(shop, item="A", location="WH")] == ["A"]


# ---- the history load -------------------------------------------------------------------------


@pytest.fixture(scope="module")
def dataset() -> Path:
    if not (APEX / "manifest.json").is_file():
        subprocess.run(
            [sys.executable, "-I", str(ROOT / "datagen/generator/company_profile.py"), "apex"], check=True, cwd=ROOT
        )
    return APEX


@pytest.fixture(scope="module")
def loaded(control_db: str, dataset: Path) -> Iterator[Session]:
    slug, db = _tenant(rows=False)
    assert import_masters(db, dataset).rejected == []
    yield db
    db.close()


def _csv(dataset: Path, folder: str, name: str) -> list[dict[str, str]]:
    with (dataset / folder / f"{name}.csv").open(encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def test_every_movement_in_the_file_is_loaded_and_the_balances_match_an_independent_replay(
    loaded: Session, dataset: Path
) -> None:
    report = import_movements(loaded, dataset)

    assert report.rejected == []
    movements = _csv(dataset, "history", "stock_movement")
    assert loaded.execute(text("select count(*) from stock_movement where quality = 'usable'")).scalar_one() == len(
        movements
    )  # held lots (history/stock_held.csv) are loaded beside them in their own quality

    expected = {(r["item_code"], r["location_code"]): D(r["qty"]) for r in _csv(dataset, "master", "stock_opening")}
    for m in movements:
        key = (m["item_code"], m["location_code"])
        expected[key] += D(m["qty"]) if m["type"] in ("receipt", "transfer_in", "adjust") else -D(m["qty"])
    actual = {(x.item_code, x.location_code): x.on_hand for x in stock_levels(loaded)}
    assert actual == expected


def test_loading_the_history_twice_does_not_double_it_and_keeps_what_the_platform_recorded(
    loaded: Session, dataset: Path
) -> None:
    import_movements(loaded, dataset)
    one = _csv(dataset, "master", "stock_opening")[0]
    loaded.execute(
        text(
            "insert into stock_movement (id, item_code, location_code, date, type, qty, source) "
            "values ('PLAT-1', :i, :l, '2026-12-31', 'receipt', 1, 'platform')"
        ),
        {"i": one["item_code"], "l": one["location_code"]},
    )
    loaded.commit()
    before = loaded.execute(text("select count(*) from stock_movement")).scalar_one()

    import_movements(loaded, dataset)

    assert loaded.execute(text("select count(*) from stock_movement")).scalar_one() == before
    assert loaded.execute(text("select count(*) from stock_movement where id = 'PLAT-1'")).scalar_one() == 1


def test_a_dry_run_writes_nothing(loaded: Session, dataset: Path) -> None:
    loaded.execute(text("delete from stock_movement"))
    loaded.commit()

    report = import_movements(loaded, dataset, dry_run=True)

    assert report.rows_loaded > 0
    assert loaded.execute(text("select count(*) from stock_movement")).scalar_one() == 0


def test_the_load_leaves_an_audit_row(loaded: Session, dataset: Path) -> None:
    import_movements(loaded, dataset)

    assert loaded.execute(text("select count(*) from audit_event where action = 'stock.import'")).scalar_one() >= 1


def test_bad_movements_are_left_out_with_line_and_reason(loaded: Session, dataset: Path, tmp_path: Path) -> None:
    rows = _csv(dataset, "history", "stock_movement")
    rows[0]["item_code"] = "NO-SUCH-ITEM"
    rows[1]["qty"] = "-4"
    rows[2]["type"] = "teleport"
    rows.append(dict(rows[5]))  # same id again
    shutil.copytree(dataset / "master", tmp_path / "ds" / "master")
    (tmp_path / "ds" / "history").mkdir()
    with (tmp_path / "ds" / "history" / "stock_movement.csv").open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)

    report = import_movements(loaded, tmp_path / "ds", dry_run=True)

    by_line = {p.line: p.reason for p in report.rejected}
    assert "not found in item" in by_line[2]
    assert "must not be negative" in by_line[3]
    assert "teleport" in by_line[4]
    assert "duplicate" in by_line[len(rows) + 1]
    assert len(report.rejected) == 4


# ---- the screen's data ------------------------------------------------------------------------


def _headers(slug: str) -> dict[str, str]:
    uid = users.create_user(
        slug, f"u{uuid.uuid4().hex[:6]}@example.com", "correct horse battery", "Reader", "Requester"
    )
    return {"Authorization": f"Bearer {create_access_token(uid, slug, 'Requester')}"}


def test_the_inventory_endpoint_summarises_filters_and_keeps_quantities_exact(control_db: str) -> None:
    slug, db = _tenant()
    _move(db, "1", "A", "receipt", "0.0050")  # 100.0050 on hand
    db.commit()
    h = _headers(slug)

    data = client.get("/api/inventory", headers=h).json()
    assert data["summary"] == {"total": 1, "by_status": {"ok": 1}}
    assert data["locations"] == [{"code": "WH", "name": "Main store"}]
    assert data["lines"][0]["on_hand"] == "100.005"  # text, never a float
    assert data["lines"][0]["reorder_point"] == "90"

    assert client.get("/api/inventory?status=stockout", headers=h).json()["lines"] == []
    assert client.get("/api/inventory?location=NOPE", headers=h).json()["summary"]["total"] == 0
    assert client.get("/api/inventory").status_code == 401
    db.close()


def test_one_company_cannot_see_the_stock_of_another(control_db: str) -> None:
    with_stock, db = _tenant()
    other, db2 = _tenant(rows=False)
    db.close()
    db2.close()

    assert client.get("/api/inventory", headers=_headers(with_stock)).json()["summary"]["total"] == 1
    assert client.get("/api/inventory", headers=_headers(other)).json()["summary"]["total"] == 0
