"""ERP demand import: Apex's input files load, bad rows are listed with their reasons, the same order never loads twice, a
changed order is reported and not overwritten, a dry run writes nothing, a client's column names map to ours, every
import is audited, and one company never sees another's imports."""

import csv
import subprocess
import sys
import uuid
from collections.abc import Iterator
from decimal import Decimal
from pathlib import Path

import openpyxl
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session

from p2p.main import app
from p2p.masters.importer import import_masters
from p2p.platform import routing, tenants, users
from p2p.platform.sod import user
from p2p.platform.tokens import create_access_token
from p2p.requests import demand
from p2p.requests.demand_files import KINDS

ROOT = Path(__file__).resolve().parents[1]
APEX = ROOT / "data" / "datasets" / "apex-v1"
DEMAND_TABLES = (
    *("material_request_buy", "material_request_line", "material_request"),  # made from the demand
    *("material_requirement", "production_order", "product_demand", "demand_import", "erp_column_map"),
)
client = TestClient(app)
D = Decimal


@pytest.fixture(scope="module")
def dataset() -> Path:
    if not (APEX / "manifest.json").is_file():
        subprocess.run(
            [sys.executable, "-I", str(ROOT / "datagen/generator/company_profile.py"), "apex"], check=True, cwd=ROOT
        )
    return APEX


@pytest.fixture(scope="module")
def company(control_db: str, dataset: Path) -> Iterator[tuple[str, Session]]:
    slug = "dm" + uuid.uuid4().hex[:8]
    assert tenants.create_tenant(slug, load_reference=False).ok
    db = Session(routing.engine_for(slug))
    assert import_masters(db, dataset).rejected == []
    yield slug, db
    db.close()


@pytest.fixture
def db(company: tuple[str, Session]) -> Session:
    """The Apex company with no demand loaded yet."""
    session = company[1]
    session.rollback()
    for table in DEMAND_TABLES:
        session.execute(text(f"delete from {table}"))  # noqa: S608
    session.execute(text("delete from uom_conversion"))
    session.commit()
    return session


def _rows(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def _write(path: Path, header: list[str], rows: list[list[str]]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(header)
        w.writerows(rows)
    return path


def _count(db: Session, table: str) -> int:
    return int(db.execute(text(f"select count(*) from {table}")).scalar_one())  # noqa: S608


def _reasons(report: demand.DemandReport) -> dict[tuple[str, int], str]:
    return {(p.table, p.line): p.reason for p in report.rejected}


def test_apex_input_files_load_every_order_and_material(db: Session, dataset: Path) -> None:
    # given Apex's ERP files; when they are imported
    report = demand.import_demand(db, dataset)

    # then every row is in, MO-1 is 50 pumps by 5 December and needs 1.8 kg of copper wire per pump
    inputs = dataset / "inputs"
    assert report.rejected == []
    assert report.loaded == {
        "production_order": len(_rows(inputs / "production_order.csv")),
        "production_order_line": len(_rows(inputs / "production_order_line.csv")),
        "sales_order": len(_rows(inputs / "sales_order.csv")),
        "demand_plan": len(_rows(inputs / "demand_plan.csv")),
    }
    mo1 = db.execute(text("select id, qty, need_by, location_code from production_order where order_no = 'MO-1'")).one()
    assert (mo1.qty, mo1.need_by.isoformat(), mo1.location_code) == (D(50), "2026-12-05", "PLT-CKN")
    wire = db.execute(
        text(
            "select qty, unit, base_qty, production_order_id from material_requirement "
            "where source_doc_no = 'MO-1' and item_code = 'RM-08'"
        )
    ).one()
    assert (wire.qty, wire.unit, wire.base_qty, wire.production_order_id) == (D(90), "KGS", D(90), mo1.id)
    bearings = db.execute(
        text("select qty from material_requirement where source_doc_no = 'MO-1' and item_code = 'RM-06'")
    ).scalar_one()
    assert bearings == 2 * 50


def test_bad_rows_are_listed_with_their_reasons(db: Session, tmp_path: Path) -> None:
    # given ERP files with one fault per row
    folder = tmp_path / "erp"
    _write(
        folder / "production_order.csv",
        ["order_no", "product_code", "qty", "need_by", "reason"],
        [
            ["MO-7", "FG-01", "10", "2026-12-20", ""],  # line 2: good
            ["MO-8", "FG-01", "10", "20/12/2026", ""],  # line 3: a date the wrong way round
            ["MO-9", "FG-01", "0", "2026-12-20", ""],  # line 4: nothing to make
            ["", "FG-01", "5", "2026-12-20", ""],  # line 5: no order number
            ["MO-7", "FG-01", "10", "2026-12-20", ""],  # line 6: the same order twice
        ],
    )
    _write(
        folder / "production_order_line.csv",
        ["order_no", "item_code", "qty", "unit", "need_by"],
        [
            ["MO-7", "RM-01", "10", "NOS", "2026-12-20"],  # line 2: good
            ["MO-7", "XX-99", "10", "NOS", "2026-12-20"],  # line 3: no such item
            ["MO-7", "RM-08", "18", "BOX", "2026-12-20"],  # line 4: a unit with no conversion
            ["MO-7", "RM-03", "-1", "NOS", "2026-12-20"],  # line 5: negative quantity
            ["MO-8", "RM-01", "10", "NOS", "2026-12-20"],  # line 6: its order was left out
            ["MO-7", "RM-05", "ten", "NOS", "2026-12-20"],  # line 7: not a number
            ["MO-7", "RM-06", "1.23456", "NOS", ""],  # line 8: more decimals than kept
        ],
    )
    _write(folder / "demand_plan.csv", ["plan_no", "product_code", "qty"], [["DP-1", "FG-01", "5"]])  # no period column

    # when they are imported
    report = demand.import_demand(db, folder)

    # then the good rows load and every bad one is listed with its line and reason
    assert report.loaded == {"production_order": 1, "production_order_line": 1, "demand_plan": 0}
    reasons = _reasons(report)
    assert reasons[("production_order", 3)] == "need_by: '20/12/2026' is not a date like 2026-12-05"
    assert reasons[("production_order", 4)] == "qty 0 must be above zero"
    assert reasons[("production_order", 5)] == "order_no is required"
    assert reasons[("production_order", 6)] == "duplicate of line 2 (same ERP document and line)"
    assert reasons[("production_order_line", 3)] == "item_code 'XX-99' is not in the item master"
    assert (
        reasons[("production_order_line", 4)] == "unit 'BOX' is not RM-08's unit (KGS) and no conversion from it is set"
    )
    assert reasons[("production_order_line", 5)] == "qty -1 must be above zero"
    assert "MO-8 has no header" in reasons[("production_order_line", 6)]
    assert reasons[("production_order_line", 7)] == "qty: 'ten' is not a number"
    assert reasons[("production_order_line", 8)] == "qty 1.23456 has more than four decimals"
    assert reasons[("demand_plan", 1)] == "column missing in demand_plan.csv: period"
    assert len(report.rejected) == 11


def test_a_second_import_of_the_same_files_loads_nothing_and_says_so(db: Session, dataset: Path) -> None:
    # given Apex's files already loaded
    first = demand.import_demand(db, dataset)
    before = {t: _count(db, t) for t in ("production_order", "material_requirement", "product_demand")}

    # when the same files are imported again
    second = demand.import_demand(db, dataset)

    # then nothing new is loaded, every row is counted as loaded before, and the person is told
    assert second.rows_loaded == 0 and second.rejected == []
    assert second.already_loaded == first.rows_loaded
    assert demand.summary(second) == f"nothing new: all {first.rows_loaded} rows were loaded before"
    assert {t: _count(db, t) for t in before} == before
    assert _count(db, "demand_import") == 2  # both runs are on record


def test_a_changed_order_is_listed_as_changed_and_not_overwritten(db: Session, dataset: Path, tmp_path: Path) -> None:
    # given MO-1 loaded for 50 pumps
    demand.import_demand(db, dataset)
    rows = _rows(dataset / "inputs" / "production_order.csv")
    rows[0]["qty"], rows[0]["need_by"] = "60", "2026-12-08"
    path = _write(tmp_path / "production_order.csv", list(rows[0]), [list(r.values()) for r in rows])

    # when the ERP sends MO-1 again for 60 pumps three days later
    report = demand.import_demand(db, path)

    # then it is listed as changed with what changed, and the loaded order stays as it was
    assert report.rows_loaded == 0 and report.already_loaded == 1
    assert _reasons(report) == {
        ("production_order", 2): "already loaded; changed: qty 50 -> 60; need_by 2026-12-05 -> 2026-12-08"
    }
    assert db.execute(text("select qty from production_order where order_no = 'MO-1'")).scalar_one() == 50


def test_a_new_line_of_a_loaded_order_attaches_to_it(db: Session, dataset: Path, tmp_path: Path) -> None:
    # given MO-1 loaded; when the ERP adds a spare part line to it in a file of lines alone
    demand.import_demand(db, dataset)
    path = _write(tmp_path / "lines.csv", ["order_no", "item_code", "qty"], [["MO-1", "SP-01", "5"]])
    report = demand.import_demand(db, path, kind="production_order_line")

    # then it loads under MO-1 with the order's date and location
    assert report.rejected == [] and report.rows_loaded == 1
    line = db.execute(
        text(
            "select r.need_by, r.location_code, r.unit, p.order_no from material_requirement r "
            "join production_order p on p.id = r.production_order_id where r.item_code = 'SP-01'"
        )
    ).one()
    assert (line.need_by.isoformat(), line.location_code, line.unit, line.order_no) == (
        "2026-12-05",
        "PLT-CKN",
        "NOS",
        "MO-1",
    )


def test_a_dry_run_reports_but_writes_nothing(db: Session, dataset: Path) -> None:
    audits = int(db.execute(text("select count(*) from audit_event where action = 'demand.import'")).scalar_one())

    report = demand.import_demand(db, dataset, dry_run=True)

    assert report.dry_run and report.rows_loaded > 0 and report.rejected == []
    for table in DEMAND_TABLES:
        assert _count(db, table) == 0, table
    after = int(db.execute(text("select count(*) from audit_event where action = 'demand.import'")).scalar_one())
    assert after == audits


def test_every_import_is_audited_with_its_counts(db: Session, dataset: Path) -> None:
    report = demand.import_demand(db, dataset, actor=user(42))

    row = db.execute(
        text("select actor_type, actor_id, after from audit_event where action = 'demand.import' and entity_id = :i"),
        {"i": str(report.import_id)},
    ).one()
    assert (row.actor_type, row.actor_id) == ("user", "42")
    assert row.after["loaded"] == report.rows_loaded and row.after["rejected"] == 0
    files = db.execute(text("select files from demand_import where id = :i"), {"i": report.import_id}).scalar_one()
    assert files["production_order_line"]["loaded"] == report.loaded["production_order_line"]


def test_a_client_s_column_names_load_through_the_saved_mapping(db: Session, tmp_path: Path) -> None:
    # given a client whose ERP calls the columns differently, and units in boxes of 10
    db.execute(
        text("insert into uom_conversion (item_code, from_uom, to_uom, factor) values ('PK-01', 'BOX', 'NOS', 10)")
    )
    db.commit()
    names = {"order_no": "Order", "product_code": "Material", "qty": "Order Qty", "need_by": "Finish Date"}
    assert demand.save_mapping(db, user(1), "production_order", {"order_no": "Ord"}) == 1
    assert demand.save_mapping(db, user(1), "production_order", names) == 2  # the latest version is used
    demand.save_mapping(db, user(1), "production_order_line", {"order_no": "Order", "item_code": "Component"})
    _write(tmp_path / "production_order.csv", list(names.values()), [["SAP-100", "FG-01", "20", "2026-12-22"]])
    book = openpyxl.Workbook()
    sheet = book.active
    assert sheet is not None
    sheet.append(["Order", "Component", "qty", "unit"])
    sheet.append(["SAP-100", "PK-01", 2, "BOX"])
    book.save(tmp_path / "production_order_line.xlsx")

    # when the files are imported
    report = demand.import_demand(db, tmp_path)

    # then they load through the mapping, and boxes become pieces
    assert report.rejected == []
    assert report.mappings == {"production_order": 2, "production_order_line": 1}
    assert db.execute(
        text("select base_qty from material_requirement where source_doc_no = 'SAP-100'")
    ).scalar_one() == D(20)


def test_a_mapping_to_a_column_we_do_not_have_is_refused(db: Session) -> None:
    with pytest.raises(demand.DemandError, match="colour is not a column"):
        demand.save_mapping(db, user(1), "production_order", {"colour": "Farbe"})
    with pytest.raises(demand.DemandError, match="same client column"):
        demand.save_mapping(db, user(1), "production_order", {"order_no": "X", "product_code": "X"})


def test_every_template_loads_as_it_is(db: Session, tmp_path: Path) -> None:
    # given our template of every kind, with its example row; when they are imported together
    for kind in KINDS:
        (tmp_path / f"{kind}.csv").write_text(demand.template_csv(kind), encoding="utf-8")
    report = demand.import_demand(db, tmp_path)

    # then each example row loads
    assert report.rejected == []
    assert report.loaded == dict.fromkeys(KINDS, 1)


def test_open_requirements_come_earliest_need_by_first(db: Session, dataset: Path, tmp_path: Path) -> None:
    demand.import_demand(db, dataset)
    _write(
        tmp_path / "mrp_result.csv",
        ["plan_no", "line_no", "item_code", "qty", "unit", "need_by"],
        [["MRP-1", "1", "RM-06", "40", "NOS", "2026-11-30"]],
    )
    demand.import_demand(db, tmp_path)

    wanted = demand.open_requirements(db, item="RM-06")

    assert [(r.source_doc_no, r.need_by.isoformat()) for r in wanted] == [
        ("MRP-1", "2026-11-30"),
        ("MO-1", "2026-12-05"),
        ("MO-2", "2026-12-15"),
    ]
    assert wanted[0].source_kind == "mrp" and wanted[0].base_qty == D(40)


# ---- through the API ---------------------------------------------------------------------------


def _headers(slug: str, role: str) -> dict[str, str]:
    uid = users.create_user(slug, f"u{uuid.uuid4().hex[:6]}@example.com", "correct horse battery", "Person", role)
    return {"Authorization": f"Bearer {create_access_token(uid, slug, role)}"}


def test_an_upload_loads_for_its_own_company_only(company: tuple[str, Session], db: Session, dataset: Path) -> None:
    # given two companies; when the store of the first uploads its production orders
    slug, _ = company
    other = "do" + uuid.uuid4().hex[:8]
    assert tenants.create_tenant(other, load_reference=False).ok
    store = _headers(slug, "Store")
    body = (dataset / "inputs" / "production_order.csv").read_bytes()
    checked = client.post(
        "/api/demand/import/production_order?dry_run=true",
        files={"file": ("orders.csv", body, "text/csv")},
        headers=store,
    )
    loaded = client.post(
        "/api/demand/import/production_order", files={"file": ("orders.csv", body, "text/csv")}, headers=store
    )

    # then the check writes nothing, the load does, and only the first company sees it
    assert checked.status_code == 200 and checked.json()["import_id"] is None
    assert loaded.status_code == 200 and loaded.json()["loaded"] == 2 and loaded.json()["rejected"] == []
    assert [i["id"] for i in client.get("/api/demand/imports", headers=store).json()] == [loaded.json()["import_id"]]
    assert client.get("/api/demand/imports", headers=_headers(other, "Store")).json() == []
    with Session(routing.engine_for(other)) as other_db:
        assert _count(other_db, "production_order") == 0


def test_only_the_store_may_import(company: tuple[str, Session], db: Session) -> None:
    # the administrator does setup only (E22), so the import is the store's alone
    slug, _ = company
    for role, allowed in (("Store", True), ("Admin", False), ("Requester", False), ("Buyer", False)):
        resp = client.get("/api/demand/templates/production_order", headers=_headers(slug, role))
        assert (resp.status_code == 200) == allowed, role
    resp = client.get("/api/demand/templates/production_order", headers=_headers(slug, "Store"))
    assert resp.text.lstrip("﻿").splitlines()[0] == "order_no,product_code,qty,need_by,reason,location_code"
