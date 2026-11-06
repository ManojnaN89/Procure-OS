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
        return Decimal(value)
    if base == "date":
        return date.fromisoformat(value)
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
    "budget": lambda r: "amount must not be negative" if r.get("amount") is not None and r["amount"] < 0 else None,
    "stock_opening": lambda r: "qty must not be negative" if r.get("qty") is not None and r["qty"] < 0 else None,
}


def read_table(path: Path, spec: Table, report: ImportReport) -> list[tuple[int, dict[str, Any]]]:
    """Read one csv file into typed rows. Unreadable rows go to the report."""
    if not path.is_file():
        report.rejected.append(Problem(spec.name, 0, f"file {path.name} is missing"))
        return []
    out: list[tuple[int, dict[str, Any]]] = []
    with path.open(encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        missing = [c for c, _ in spec.columns if c not in (reader.fieldnames or [])]
        if missing:
            report.rejected.append(Problem(spec.name, 1, f"column(s) missing in {path.name}: {', '.join(missing)}"))
            return []
        for line, raw in enumerate(reader, start=2):
            row: dict[str, Any] = {}
            try:
                for col, sql_type in spec.columns:
                    row[col] = _cast(raw[col], sql_type)
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
                    Problem(spec.name, line, f"key column(s) {', '.join(spec.pk)} must not be empty")
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
