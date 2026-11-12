"""Import the master data of a company from a dataset folder (the generated demo data, or a client's own files in the same layout).

Every row is read, converted to its column type and checked: keys present and unique, references to other tables
resolve, GSTIN and PAN are well formed, dates run forward, amounts make sense. A row that fails is left out and reported
with the file, the line and the reason; a row that loads but deserves a look (an HSN code the directory does not know) is
reported as a warning. Nothing is written when a dry run is asked for. A real import replaces the company's masters in
one transaction, so a failure leaves the old data untouched.
"""

import csv
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from p2p.formulas.gstin import is_valid_gstin, is_valid_pan, state_of_gstin
from p2p.masters.schema import TABLES, Table
from p2p.platform import audit
from p2p.platform.sod import Actor


@dataclass
class Problem:
    table: str
    line: int  # line number in the csv file (the header is line 1)
    reason: str


@dataclass
class ImportReport:
    loaded: dict[str, int] = field(default_factory=dict)
    rejected: list[Problem] = field(default_factory=list)
    warnings: list[Problem] = field(default_factory=list)
    seconds: float = 0.0
    dry_run: bool = False

    @property
    def rows_loaded(self) -> int:
        return sum(self.loaded.values())


def _cast(value: str, sql_type: str) -> Any:
    value = value.strip()
    if value == "":
        return None
    base = sql_type.split("(")[0]
    if base == "text":
        return value
    if base == "boolean":
        if value.lower() not in ("true", "false"):
            raise ValueError(f"'{value}' is not true or false")
        return value.lower() == "true"
    if base in ("integer", "bigint"):
        return int(value)
    if base == "numeric":
        try:
            number = Decimal(value)
        except InvalidOperation:
            raise ValueError(f"'{value}' is not a number") from None
        if not number.is_finite():
            raise ValueError(f"'{value}' is not a number")
        return number
    if base == "date":
        try:
            return date.fromisoformat(value)
        except ValueError:
            raise ValueError(f"'{value}' is not a date like 2026-12-05") from None
    if base == "timestamptz":
        return datetime.fromisoformat(value)
    raise ValueError(f"unknown column type {sql_type}")


# ---- business checks, per table: return a reason to reject the row, or None -----------------


def _check_gstin(value: str | None, who: str) -> str | None:
    if value and not is_valid_gstin(value):
        return f"{who} '{value}' is not a valid GSTIN"
    return None


def _check_supplier(r: dict[str, Any]) -> str | None:
    if reason := _check_gstin(r.get("gstin"), "gstin"):
        return reason
    if r.get("gstin") and r.get("state_code") and state_of_gstin(r["gstin"]) != r["state_code"]:
        return f"state_code {r['state_code']} does not match the GSTIN state {state_of_gstin(r['gstin'])}"
    if r.get("pan") and not is_valid_pan(r["pan"]):
        return f"pan '{r['pan']}' is not a valid PAN"
    return None


def _check_dates(r: dict[str, Any], start: str, end: str) -> str | None:
    if r.get(start) and r.get(end) and r[end] < r[start]:
        return f"{end} {r[end]} is before {start} {r[start]}"
    return None


def _check_price_record(r: dict[str, Any]) -> str | None:
    if r.get("price") is not None and r["price"] <= 0:
        return f"price {r['price']} must be above zero"
    return _check_dates(r, "valid_from", "valid_to")


def _check_item_location(r: dict[str, Any]) -> str | None:
    if r.get("min_qty") is not None and r.get("max_qty") is not None and r["max_qty"] < r["min_qty"]:
        return f"max_qty {r['max_qty']} is below min_qty {r['min_qty']}"
    for col in ("reorder_point", "safety_stock", "order_qty"):
        if r.get(col) is not None and r[col] < 0:
            return f"{col} must not be negative"
    return None


def _check_pincode(r: dict[str, Any]) -> str | None:
    if r.get("pincode") and not (len(r["pincode"]) == 6 and r["pincode"].isdigit()):
        return f"pincode '{r['pincode']}' is not 6 digits"
    return None


CHECKS: dict[str, Callable[[dict[str, Any]], str | None]] = {
    "supplier": _check_supplier,
    "location": lambda r: _check_gstin(r.get("gstin"), "gstin") or _check_pincode(r),
    "supplier_address": lambda r: _check_gstin(r.get("gstin"), "gstin") or _check_pincode(r),
    "gst_registration": lambda r: _check_gstin(r.get("gstin"), "gstin"),
    "price_record": _check_price_record,
    "contract": lambda r: _check_dates(r, "valid_from", "valid_to"),
    "item_location": _check_item_location,
    "budget": lambda r: (
        "amount must not be negative"
        if r.get("amount") is not None and r["amount"] < 0
        else "a budget belongs to a cost centre or to a location, not both and not neither"
        if (r.get("cost_centre_code") is None) == (r.get("location_code") is None)
        else None
    ),
    "stock_opening": lambda r: "qty must not be negative" if r.get("qty") is not None and r["qty"] < 0 else None,
}


_MIDNIGHT = datetime.min.time()


def _cell_text(value: Any) -> str:
    """An Excel cell as the text a CSV file would hold, so one reading code serves both and money never passes through a float
    more than Excel itself stored it (its own shortest text, e.g. 129.99)."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, datetime):
        return value.date().isoformat() if value.time() == _MIDNIGHT else value.isoformat(sep=" ")
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, float) and value == int(value) and abs(value) < 1e15:
        return str(int(value))
    return str(value).strip()


def _read_xlsx(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    """The first sheet of an Excel file: the first row names the columns, the rows after it are the data."""
    import openpyxl

    book = openpyxl.load_workbook(path, read_only=True, data_only=True)
    try:
        rows = list(book.worksheets[0].iter_rows(values_only=True))
    finally:
        book.close()
    if not rows:
        return [], []
    names = [_cell_text(c) for c in rows[0]]
    body = [
        dict(zip(names, (_cell_text(c) for c in row), strict=False))
        for row in rows[1:]
        if any(c is not None for c in row)
    ]
    return names, body


def _read_csv(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    with path.open(encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        return list(reader.fieldnames or []), list(reader)


def read_table(
    path: Path,
    spec: Table,
    report: ImportReport,
    *,
    rename: dict[str, str] | None = None,
    optional: frozenset[str] = frozenset(),
) -> list[tuple[int, dict[str, Any]]]:
    """Read one table into typed rows: the csv file, or an Excel file of the same name. Unreadable rows go to the report.
    `rename` maps a client's own column names to ours (a saved column mapping); `optional` columns may be left out."""
    excel = path.with_suffix(".xlsx")
    if not path.is_file() and excel.is_file():
        path = excel
    if not path.is_file():
        report.rejected.append(Problem(spec.name, 0, f"file {path.name} is missing"))
        return []
    fieldnames, raw_rows = _read_xlsx(path) if path.suffix == ".xlsx" else _read_csv(path)
    if rename:
        fieldnames = [rename.get(c, c) for c in fieldnames]
        raw_rows = [{rename.get(k, k): v for k, v in r.items()} for r in raw_rows]
    missing = [c for c, _ in spec.columns if c not in fieldnames and c not in optional]
    if missing:
        report.rejected.append(
            Problem(
                spec.name,
                1,
                f"{'column' if len(missing) == 1 else 'columns'} missing in {path.name}: {', '.join(missing)}",
            )
        )
        return []
    out: list[tuple[int, dict[str, Any]]] = []
    for line, raw in enumerate(raw_rows, start=2):
        row: dict[str, Any] = {}
        try:
            for col, sql_type in spec.columns:
                row[col] = _cast(raw.get(col) or "", sql_type)
        except (ValueError, InvalidOperation) as exc:
            report.rejected.append(Problem(spec.name, line, f"{col}: {exc}"))
            continue
        out.append((line, row))
    return out


def import_masters(db: Session, dataset: Path, *, actor: Actor = audit.SYSTEM, dry_run: bool = False) -> ImportReport:
    """Replace the company's masters with the files in `dataset`/master. Returns what loaded and what did not."""
    started = time.monotonic()
    report = ImportReport(dry_run=dry_run)
    master_dir = dataset / "master"
    accepted: dict[str, list[dict[str, Any]]] = {}
    keys: dict[str, dict[str, set[Any]]] = {}  # table -> column -> values present (for reference checks)

    # the tables that others point at must be known before their children are checked, including self-references
    parsed: dict[str, list[tuple[int, dict[str, Any]]]] = {}
    for spec in TABLES:
        parsed[spec.name] = read_table(master_dir / f"{spec.csv}.csv", spec, report)
    for spec in TABLES:
        for col in {c for _, t, c in (fk for s in TABLES for fk in s.foreign_keys) if t == spec.name}:
            keys.setdefault(spec.name, {})[col] = {r[col] for _, r in parsed[spec.name] if r.get(col) is not None}

    for spec in TABLES:
        seen: dict[tuple[Any, ...], int] = {}
        rows: list[dict[str, Any]] = []
        for line, row in parsed[spec.name]:
            natural = spec.pk or tuple(c for c, _ in spec.columns)
            key = tuple(row[c] for c in natural)
            if spec.pk and any(v is None for v in key):
                report.rejected.append(
                    Problem(
                        spec.name,
                        line,
                        f"key {'column' if len(spec.pk) == 1 else 'columns'} {', '.join(spec.pk)} must not be empty",
                    )
                )
                continue
            if key in seen:
                report.rejected.append(
                    Problem(
                        spec.name,
                        line,
                        f"duplicate of line {seen[key]} (same {', '.join(natural) if spec.pk else 'values'})",
                    )
                )
                continue
            check = CHECKS.get(spec.name)
            if check and (reason := check(row)):
                report.rejected.append(Problem(spec.name, line, reason))
                continue
            bad_ref = next(
                (
                    f"{col} '{row[col]}' not found in {ref_table}"
                    for col, ref_table, ref_col in spec.foreign_keys
                    if row[col] is not None and row[col] not in keys.get(ref_table, {}).get(ref_col, set())
                ),
                None,
            )
            if bad_ref:
                report.rejected.append(Problem(spec.name, line, bad_ref))
                continue
            seen[key] = line
            rows.append(row)
        accepted[spec.name] = rows
        # a parent row that was rejected must not stay known to its children
        for col in keys.get(spec.name, {}):
            keys[spec.name][col] = {r[col] for r in rows if r.get(col) is not None}
        report.loaded[spec.name] = len(rows)

    _warn_unknown_hsn(db, accepted.get("item", []), report)
    _warn_gst_rate_differs(db, accepted.get("item", []), report)

    if not dry_run:
        for spec in reversed(TABLES):
            db.execute(text(f"delete from {spec.name}"))  # noqa: S608 - table names come from the schema file
        for spec in TABLES:
            rows = accepted[spec.name]
            if rows:
                cols = [c for c, _ in spec.columns]
                db.execute(
                    text(f"insert into {spec.name} ({', '.join(cols)}) values ({', '.join(':' + c for c in cols)})"),  # noqa: S608
                    rows,
                )
        audit.record(
            db,
            actor,
            "masters.import",
            "dataset",
            dataset.name,
            after={
                "rows_loaded": report.rows_loaded,
                "rejected": len(report.rejected),
                "warnings": len(report.warnings),
            },
        )
        db.commit()
    report.seconds = round(time.monotonic() - started, 2)
    return report


def _warn_unknown_hsn(db: Session, items: list[dict[str, Any]], report: ImportReport) -> None:
    known = {r[0] for r in db.execute(text("select code from ref_hsn_sac"))}
    if not known:
        return  # no HSN directory loaded in this company: nothing to compare with
    for i, item in enumerate(items, start=1):
        if item.get("hsn_sac") and item["hsn_sac"] not in known:
            report.warnings.append(
                Problem("item", i, f"{item['code']}: HSN/SAC {item['hsn_sac']} is not in the HSN directory")
            )


def _warn_gst_rate_differs(db: Session, items: list[dict[str, Any]], report: ImportReport) -> None:
    """An item whose GST rate today is not the rate the notified rate table gives for its HSN or SAC code is flagged, so
    a wrong rate in the item master is seen when it is loaded and not when an order is priced. A code the table is unsure
    about (needs review) is not compared. The item's rate stays as given: it is the buyer's master data."""
    from p2p.formulas.calendar import business_today
    from p2p.formulas.orders import current_gst_rate
    from p2p.platform import reference

    today = business_today()
    for i, item in enumerate(items, start=1):
        code = item.get("hsn_sac")
        if not code:
            continue
        mine = current_gst_rate(
            item.get("gst_rate_before"), item.get("gst_rate_after"), item.get("gst_rate_change_date"), today
        )
        listed = reference.gst_rate_for(db, str(code), today)
        if mine is None or listed is None or listed.needs_review or mine == listed.igst:
            continue
        report.warnings.append(
            Problem(
                "item",
                i,
                f"{item['code']}: GST rate {mine.normalize():f}% differs from the rate table's {listed.igst.normalize():f}% for {code} today",
            )
        )
