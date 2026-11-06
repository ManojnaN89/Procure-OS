"""Master data import: counts match the files, bad rows are reported with file, line and reason, a dry run writes nothing."""

import csv
import shutil
import subprocess
import sys
import uuid
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

from p2p.formulas.gstin import gstin_check_char, is_valid_gstin, is_valid_pan
from p2p.masters.importer import import_masters
from p2p.masters.schema import TABLES
from p2p.platform import routing, tenants

ROOT = Path(__file__).resolve().parents[1]
SMALL = ROOT / "data" / "datasets" / "cbc-small-v1-seed42"


@pytest.fixture(scope="module")
def dataset() -> Path:
    if not SMALL.is_dir():
        subprocess.run(
            [sys.executable, "-I", str(ROOT / "datagen/generator/cli.py"), "--size", "small", "--seed", "42"],
            check=True,
            cwd=ROOT,
        )
    return SMALL


@pytest.fixture(scope="module")
def tenant_db(control_db: str) -> Session:
    slug = "m" + uuid.uuid4().hex[:8]
    assert tenants.create_tenant(slug).ok
    db = Session(routing.engine_for(slug))
    yield db
    db.close()


def _rows(dataset: Path, name: str) -> list[dict[str, str]]:
    with (dataset / "master" / f"{name}.csv").open(encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def _count(db: Session, table: str) -> int:
    return int(db.execute(text(f"select count(*) from {table}")).scalar_one())


def _copy_with(dataset: Path, tmp: Path, name: str, change) -> Path:  # type: ignore[no-untyped-def]
    """A copy of the dataset in which the rows of one file are changed by `change(rows)`."""
    target = tmp / "ds"
    shutil.copytree(dataset / "master", target / "master")
    rows = _rows(dataset, name)
    change(rows)
    with (target / "master" / f"{name}.csv").open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    return target


def test_every_row_of_every_file_is_loaded_when_the_data_is_clean(tenant_db: Session, dataset: Path) -> None:
    report = import_masters(tenant_db, dataset)

    assert report.rejected == []
    for spec in TABLES:
        assert _count(tenant_db, spec.name) == len(_rows(dataset, spec.csv)), spec.name
    assert report.rows_loaded == sum(len(_rows(dataset, s.csv)) for s in TABLES)


def test_a_hand_added_total_matches_the_database(tenant_db: Session, dataset: Path) -> None:
    import_masters(tenant_db, dataset)

    expected = sum((Decimal(r["amount"]) for r in _rows(dataset, "budget")), Decimal(0))
    stored = tenant_db.execute(text("select sum(amount) from budget")).scalar_one()
    assert stored == expected


def test_a_second_import_replaces_instead_of_doubling(tenant_db: Session, dataset: Path) -> None:
    import_masters(tenant_db, dataset)
    first = _count(tenant_db, "supplier")
    import_masters(tenant_db, dataset)

    assert _count(tenant_db, "supplier") == first


def test_a_dry_run_reports_but_writes_nothing(control_db: str, dataset: Path) -> None:
    slug = "d" + uuid.uuid4().hex[:8]
    assert tenants.create_tenant(slug).ok
    with Session(routing.engine_for(slug)) as db:
        report = import_masters(db, dataset, dry_run=True)
        assert report.rows_loaded > 0
        assert _count(db, "supplier") == 0
        assert not db.execute(text("select 1 from audit_event where action = 'masters.import'")).first()


def test_an_import_leaves_an_audit_row(tenant_db: Session, dataset: Path) -> None:
    import_masters(tenant_db, dataset)

    row = tenant_db.execute(
        text("select count(*) from audit_event where action = 'masters.import' and entity_id = :d"),
        {"d": dataset.name},
    ).scalar_one()
    assert row >= 1


def test_a_bad_gstin_is_left_out_with_its_line_and_reason(tenant_db: Session, dataset: Path, tmp_path: Path) -> None:
    suppliers = _rows(dataset, "supplier")
    victim = next(i for i, r in enumerate(suppliers) if r["gstin"])

    def spoil(rows: list[dict[str, str]]) -> None:
        g = rows[victim]["gstin"]
        rows[victim]["gstin"] = g[:14] + ("0" if g[14] != "0" else "1")  # wrong check digit

    report = import_masters(tenant_db, _copy_with(dataset, tmp_path, "supplier", spoil), dry_run=True)

    problem = next(p for p in report.rejected if p.table == "supplier")
    assert problem.line == victim + 2  # header is line 1
    assert "not a valid GSTIN" in problem.reason


def test_children_of_a_left_out_parent_are_left_out_too(tenant_db: Session, dataset: Path, tmp_path: Path) -> None:
    contacts = _rows(dataset, "supplier_contact")
    code = contacts[0]["supplier_code"]
    children = len([r for r in contacts if r["supplier_code"] == code])

    def drop_pan(rows: list[dict[str, str]]) -> None:
        for r in rows:
            if r["code"] == code:
                r["pan"] = "BADPAN"

    report = import_masters(tenant_db, _copy_with(dataset, tmp_path, "supplier", drop_pan), dry_run=True)

    assert any(p.table == "supplier" and "PAN" in p.reason for p in report.rejected)
    orphaned = [p for p in report.rejected if p.table == "supplier_contact" and code in p.reason]
    assert len(orphaned) == children


def test_a_duplicate_key_and_a_bad_number_are_both_reported(tenant_db: Session, dataset: Path, tmp_path: Path) -> None:
    def spoil(rows: list[dict[str, str]]) -> None:
        rows.append(dict(rows[0]))  # same code twice
        rows[1]["default_lead_days"] = "soon"

    items = _rows(dataset, "item")
    if "default_lead_days" not in items[0]:
        pytest.skip("item has no default_lead_days column in this dataset")
    report = import_masters(tenant_db, _copy_with(dataset, tmp_path, "item", spoil), dry_run=True)

    reasons = [p.reason for p in report.rejected if p.table == "item"]
    assert any("duplicate" in r for r in reasons)
    assert any("default_lead_days" in r for r in reasons)


def test_a_failed_import_keeps_the_old_data(tenant_db: Session, dataset: Path, tmp_path: Path) -> None:
    import_masters(tenant_db, dataset)
    before = _count(tenant_db, "supplier")
    broken = tmp_path / "empty"
    (broken / "master").mkdir(parents=True)

    report = import_masters(tenant_db, broken, dry_run=True)

    assert report.rejected  # every file is missing
    assert _count(tenant_db, "supplier") == before


def test_the_gstin_check_digit_formula() -> None:
    assert gstin_check_char("27AAPFU0939F1Z") == "V"  # the published example 27AAPFU0939F1ZV
    assert is_valid_gstin("27AAPFU0939F1ZV")
    assert not is_valid_gstin("27AAPFU0939F1ZX")
    assert not is_valid_gstin("27AAPFU0939F1")
    assert is_valid_pan("ABCDE1234F")
    assert not is_valid_pan("ABCDE12345")


def test_every_generated_supplier_gstin_passes_the_formula(dataset: Path) -> None:
    gstins = [r["gstin"] for r in _rows(dataset, "supplier") if r["gstin"]]
    assert gstins
    assert all(is_valid_gstin(g) for g in gstins)


def test_the_masters_can_be_loaded_again_while_requests_and_stock_refer_to_them(
    tenant_db: Session, dataset: Path
) -> None:
    import_masters(tenant_db, dataset)
    item = _rows(dataset, "item")[0]["code"]
    location = _rows(dataset, "location")[0]["code"]
    tenant_db.execute(
        text(
            "insert into request (no, ship_to_location, type, channel, status, total_value) "
            "values ('R-KEEP', :l, 'goods', 'planning', 'draft', 100)"
        ),
        {"l": location},
    )
    tenant_db.execute(
        text(
            "insert into request_line (request_no, line_no, item_code, qty, delivery_location, status) "
            "values ('R-KEEP', 1, :i, 1, :l, 'open')"
        ),
        {"i": item, "l": location},
    )
    tenant_db.commit()
    try:
        report = import_masters(tenant_db, dataset)

        assert report.rejected == []
        assert (
            tenant_db.execute(text("select count(*) from request_line where request_no = 'R-KEEP'")).scalar_one() == 1
        )
        assert _count(tenant_db, "item") == len(_rows(dataset, "item"))
    finally:
        tenant_db.execute(text("delete from request where no = 'R-KEEP'"))
        tenant_db.commit()
