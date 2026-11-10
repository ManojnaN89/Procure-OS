"""The master data can come as Excel files instead of CSV files: the same rows load, the same problems are reported, and a dry run writes nothing."""

import csv
import datetime as dt
import shutil
import uuid
from pathlib import Path

import openpyxl
import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session
from test_masters_import import _count, dataset, tenant_db  # noqa: F401 - fixtures

from p2p.masters.importer import import_masters
from p2p.masters.schema import TABLES
from p2p.platform import routing, tenants


def _as_excel(src: Path, dst: Path, *, only: set[str] | None = None) -> None:
    """Copy a dataset, turning its master csv files into Excel files with real numbers, dates and text, as people keep them."""
    shutil.copytree(src / "master", dst / "master")
    for csv_file in sorted((dst / "master").glob("*.csv")):
        if only is not None and csv_file.stem not in only:
            continue
        spec = next((t for t in TABLES if t.csv == csv_file.stem), None)
        if spec is None:  # a file the importer does not read stays as it is
            continue
        types = dict(spec.columns)
        book = openpyxl.Workbook()
        sheet = book.active
        with csv_file.open(encoding="utf-8-sig", newline="") as f:
            rows = list(csv.reader(f))
        sheet.append(rows[0])
        for row in rows[1:]:
            sheet.append([_typed(cell, types.get(col, "text")) for col, cell in zip(rows[0], row, strict=False)])
        book.save(csv_file.with_suffix(".xlsx"))
        csv_file.unlink()


def _typed(cell: str, sql_type: str) -> object:
    """Numbers and dates in the columns that hold them; everything else stays text (a code like 07 keeps its zero)."""
    base = sql_type.split("(")[0]
    if cell == "":
        return None
    if base in ("integer", "bigint"):
        return int(cell)
    if base == "numeric":
        return float(cell)
    if base == "date":
        return dt.date.fromisoformat(cell)
    if base == "boolean":
        return cell.lower() == "true"
    return cell


@pytest.fixture
def fresh(control_db: str) -> Session:
    slug = "x" + uuid.uuid4().hex[:8]
    assert tenants.create_tenant(slug).ok
    db = Session(routing.engine_for(slug))
    yield db
    db.close()


def test_an_excel_dataset_loads_exactly_what_the_csv_dataset_loads(
    fresh: Session, tmp_path: Path, dataset: Path
) -> None:  # noqa: F811
    from_csv = import_masters(fresh, dataset, dry_run=True)
    _as_excel(dataset, tmp_path / "xl")

    from_excel = import_masters(fresh, tmp_path / "xl", dry_run=True)

    assert from_excel.loaded == from_csv.loaded
    assert [(p.table, p.line, p.reason) for p in from_excel.rejected] == [
        (p.table, p.line, p.reason) for p in from_csv.rejected
    ]


def test_an_excel_import_with_a_dry_run_writes_nothing_and_a_real_one_loads_and_matches_the_csv_load(
    fresh: Session, tmp_path: Path, dataset: Path
) -> None:  # noqa: F811
    _as_excel(dataset, tmp_path / "xl")

    import_masters(fresh, tmp_path / "xl", dry_run=True)
    assert _count(fresh, "item") == 0
    report = import_masters(fresh, tmp_path / "xl")

    assert report.loaded["item"] == _count(fresh, "item") > 0
    prices_in_excel = fresh.execute(text("select code, standard_price from item order by code")).all()
    import_masters(fresh, dataset)  # the same data from the csv files replaces it
    assert fresh.execute(text("select code, standard_price from item order by code")).all() == prices_in_excel


def test_csv_and_excel_files_can_be_mixed_in_one_folder(fresh: Session, tmp_path: Path, dataset: Path) -> None:  # noqa: F811
    _as_excel(dataset, tmp_path / "mix", only={"item", "supplier"})

    report = import_masters(fresh, tmp_path / "mix")

    assert report.loaded["item"] > 0 and report.loaded["supplier"] > 0 and not report.rejected


def test_a_bad_row_in_an_excel_file_is_reported_with_its_sheet_line_and_reason(
    fresh: Session, tmp_path: Path, dataset: Path
) -> None:  # noqa: F811
    _as_excel(dataset, tmp_path / "bad")
    path = tmp_path / "bad" / "master" / "supplier.xlsx"
    book = openpyxl.load_workbook(path)
    sheet = book.active
    head = [c.value for c in sheet[1]]
    sheet.cell(row=3, column=head.index("gstin") + 1, value="27AAAAA0000A1Z5X")  # not a valid GSTIN
    book.save(path)

    report = import_masters(fresh, tmp_path / "bad", dry_run=True)

    assert any(p.table == "supplier" and p.line == 3 and "gstin" in p.reason.lower() for p in report.rejected)


def test_an_excel_file_without_a_column_is_refused_by_name(fresh: Session, tmp_path: Path, dataset: Path) -> None:  # noqa: F811
    _as_excel(dataset, tmp_path / "cols")
    path = tmp_path / "cols" / "master" / "item.xlsx"
    book = openpyxl.load_workbook(path)
    book.active.delete_cols(1)  # the first column goes
    book.save(path)

    report = import_masters(fresh, tmp_path / "cols", dry_run=True)

    assert any(p.table == "item" and "column(s) missing in item.xlsx" in p.reason for p in report.rejected)
    assert report.loaded["item"] == 0
