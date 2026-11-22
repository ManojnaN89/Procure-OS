"""Import the master data of a company from a dataset folder (the generated demo data, or a client's own files in the same layout).

Every row is read, converted to its column type and checked: keys present and unique, references to other tables
resolve, GSTIN and PAN are well formed, dates run forward, amounts make sense. A row that fails is left out and reported
with the file, the line and the reason; a row that loads but deserves a look (an HSN code the directory does not know) is
reported as a warning. Nothing is written when a dry run is asked for.

The first import of a company (no import before, no supplier and no item yet: the company being set up) replaces its
masters in one transaction. Every later import matches rows by key and changes only what `import_rules` allows
(`upsert`): it never deletes, and what a second person decides stays as it is (task 0.6, DF-16, D-22).
"""

import csv
import time
from collections.abc import Callable
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from p2p.formulas.gstin import is_valid_gstin, is_valid_pan, state_of_gstin
from p2p.masters import upsert
from p2p.masters.report import ImportReport, Problem, RowChange
from p2p.masters.schema import TABLES, Table
from p2p.platform import audit, company_state
from p2p.platform.sod import Actor

__all__ = ["ImportReport", "Problem", "RowChange", "import_masters", "read_table"]


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
    if r.get("status") and r["status"] not in SUPPLIER_STATUSES:
        return f"status '{r['status']}' must be one of {', '.join(SUPPLIER_STATUSES)}"
    return None


def _check_dates(r: dict[str, Any], start: str, end: str) -> str | None:
    if r.get(start) and r.get(end) and r[end] < r[start]:
        return f"{end} {r[end]} is before {start} {r[start]}"
    return None


def _check_price_record(r: dict[str, Any]) -> str | None:
    if r.get("price") is not None and r["price"] <= 0:
        return f"price {r['price']} must be above zero"
    r["version"] = r.get("version") or 1  # a file without versions: every price is version 1, in force
    r["status"] = r.get("status") or "active"
    if r["status"] not in ("active", "draft"):
        return f"status '{r['status']}' must be active or draft (a draft waits for a second person to activate it)"
    if r["version"] < 1:
        return "version must be 1 or more"
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


def _check_tolerance(r: dict[str, Any]) -> str | None:
    r["version"] = r.get("version") or 1  # a file without versions: the company's tolerances are version 1
    return None


def _check_scope(r: dict[str, Any]) -> str | None:
    if (r.get("location_code") is None) == (r.get("region_code") is None):
        return "a scope row names one location or one region, not both and not neither"
    return None


def _check_benchmark(r: dict[str, Any]) -> str | None:
    if r.get("price") is not None and r["price"] <= 0:
        return f"price {r['price']} must be above zero"
    r["status"] = r.get("status") or "unverified"
    r["basis"] = r.get("basis") or "delivered"
    if r["status"] not in ("unverified", "verified", "rejected"):
        return f"status '{r['status']}' must be unverified, verified or rejected"
    if r["status"] == "verified" and not r.get("verified_by"):
        return "a verified benchmark names who verified it"
    if r.get("verified_by") and r["verified_by"] == r.get("entered_by"):
        return "a benchmark is verified by someone other than who entered it"
    return None


# columns a file may leave out: a price list without versions loads as version 1, in force, for everywhere
OPTIONAL: dict[str, frozenset[str]] = {
    "price_record": frozenset({"version", "status", "location_code", "region_code"}),
    "tolerance": frozenset({"version"}),
    "location": frozenset({"region_code"}),
    "supplier": frozenset({"registered_by"}),
}
# files a dataset may leave out: the table then keeps what it has (tolerance version 1 and the price threshold rule
# from setting up the company; no regions, scopes, flags or benchmarks)
OPTIONAL_FILES = frozenset(
    {
        "tolerance_version",
        "region",
        "supplier_category_scope",
        "supplier_flag",
        "price_threshold_rule",
        "market_benchmark",
    }
)
SUPPLIER_STATUSES = ("draft", "active", "blocked", "prospect")

CHECKS: dict[str, Callable[[dict[str, Any]], str | None]] = {
    "supplier": _check_supplier,
    "location": lambda r: _check_gstin(r.get("gstin"), "gstin") or _check_pincode(r),
    "supplier_address": lambda r: _check_gstin(r.get("gstin"), "gstin") or _check_pincode(r),
    "gst_registration": lambda r: _check_gstin(r.get("gstin"), "gstin"),
    "price_record": _check_price_record,
    "tolerance": _check_tolerance,
    "supplier_category_scope": _check_scope,
    "market_benchmark": _check_benchmark,
    "price_threshold_rule": lambda r: _check_dates(r, "effective_from", "effective_to"),
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
    absent: set[str] | None = None,
) -> list[tuple[int, dict[str, Any]]]:
    """Read one table into typed rows: the csv file, or an Excel file of the same name. Unreadable rows go to the report.
    `rename` maps a client's own column names to ours (a saved column mapping); `optional` columns may be left out, and
    those the file leaves out are added to `absent`."""
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
    if absent is not None:
        absent.update(c for c in optional if c not in fieldnames)
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
    """Load the company's masters from the files in `dataset`/master: the first import replaces them, a later one updates
    by key. Returns what loaded, what did not and why."""
    company_state.require_state(db, "masters")
    state = company_state.state_here(db)
    started = time.monotonic()
    # the first import (every table replaced) only while the company is set up; after that, by key (D-02)
    report = ImportReport(dry_run=dry_run, mode="first" if state == "setup" and _first_import(db) else "update")
    master_dir = dataset / "master"

    # the tables that others point at must be known before their children are checked, including self-references
    parsed: dict[str, list[tuple[int, dict[str, Any]]]] = {}
    absent: dict[str, set[str]] = {}
    kept_as_is = {s.name for s in TABLES if s.name in OPTIONAL_FILES and not _present(master_dir, s.csv)}
    for spec in TABLES:
        if spec.name in kept_as_is:  # a table the dataset leaves out keeps its rows; its keys come from the database
            parsed[spec.name] = [(0, dict(r)) for r in db.execute(text(f"select * from {spec.name}")).mappings()]  # noqa: S608
            continue
        parsed[spec.name] = read_table(
            master_dir / f"{spec.csv}.csv",
            spec,
            report,
            optional=OPTIONAL.get(spec.name, frozenset()),
            absent=absent.setdefault(spec.name, set()),
        )
    company = upsert.load(db, kept_as_is) if report.mode == "update" else None
    accepted = _accept(db, parsed, kept_as_is, company, report)
    _check_scope_approvals(accepted, report, company)
    items = [r for _, r in accepted.get("item", [])]
    _warn_unknown_hsn(db, items, report)
    _warn_gst_rate_differs(db, items, report)
    report.loaded = {name: len(rows) for name, rows in accepted.items()}

    if company is not None:
        locked = upsert.LIVE_LOCKS if state == "live" else {}
        upsert.apply(
            db,
            accepted,
            parsed,
            company,
            absent,
            report,
            actor=actor,
            dry_run=dry_run,
            dataset=dataset.name,
            locked=locked,
        )
    else:
        report.added = dict(report.loaded)
        if not dry_run:
            _replace(db, accepted)
            audit.record(
                db,
                actor,
                "masters.import",
                "dataset",
                dataset.name,
                after={
                    "mode": "first",
                    "rows_loaded": report.rows_loaded,
                    "rejected": len(report.rejected),
                    "warnings": len(report.warnings),
                },
            )
            db.commit()
    report.seconds = round(time.monotonic() - started, 2)
    return report


def _first_import(db: Session) -> bool:
    """The company is being set up: never imported, and no supplier or item made on the screens either. The audit log
    is insert only to the app (D-25), so an earlier import cannot be made to disappear."""
    done = db.execute(text("select 1 from audit_event where action = 'masters.import' limit 1")).first()
    used = db.execute(text("select exists (select 1 from supplier) or exists (select 1 from item)")).scalar()
    return done is None and not used


Lines = list[tuple[int, dict[str, Any]]]


def _accept(
    db: Session, parsed: dict[str, Lines], kept_as_is: set[str], company: upsert.Company | None, report: ImportReport
) -> dict[str, Lines]:
    """Check every row; return the rows that pass, with their lines. In update mode a reference may also point at a row
    the company has and the file leaves out."""
    keys: dict[str, dict[str, set[Any]]] = {}  # table -> column -> values present (for reference checks)
    outside = {
        s.name: company.outside(s, parsed[s.name]) if company and s.name not in kept_as_is else [] for s in TABLES
    }
    for spec in TABLES:
        for col in {c for _, t, c in (fk for s in TABLES for fk in s.foreign_keys) if t == spec.name}:
            values = [r for _, r in parsed[spec.name]] + outside[spec.name]
            keys.setdefault(spec.name, {})[col] = {r[col] for r in values if r.get(col) is not None}

    accepted: dict[str, Lines] = {}
    for spec in TABLES:
        if spec.name in kept_as_is:
            continue
        seen: dict[tuple[Any, ...], int] = {}
        rows: Lines = []
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
            if company and (reason := upsert.refusal(db, spec, row, company, parsed)):
                report.rejected.append(Problem(spec.name, line, reason))
                continue
            seen[key] = line
            rows.append((line, row))
        accepted[spec.name] = rows
        # a parent row that was rejected must not stay known to its children
        for col in keys.get(spec.name, {}):
            values = [r for _, r in rows] + outside[spec.name]
            keys[spec.name][col] = {r[col] for r in values if r.get(col) is not None}
    return accepted


def _replace(db: Session, accepted: dict[str, Lines]) -> None:
    """The first import: every table the files carry is emptied and loaded again."""
    for spec in reversed(TABLES):
        if spec.name in accepted:
            db.execute(text(f"delete from {spec.name}"))  # noqa: S608 - table names come from the schema file
    for spec in TABLES:
        rows = [r for _, r in accepted.get(spec.name, [])]
        if rows:
            cols = [c for c, _ in spec.columns]
            db.execute(
                text(f"insert into {spec.name} ({', '.join(cols)}) values ({', '.join(':' + c for c in cols)})"),  # noqa: S608
                rows,
            )
    _close_tolerance_versions(db)


def _present(master_dir: Path, name: str) -> bool:
    return (master_dir / f"{name}.csv").is_file() or (master_dir / f"{name}.xlsx").is_file()


def _check_scope_approvals(accepted: dict[str, Lines], report: ImportReport, company: upsert.Company | None) -> None:
    """A scope limits an approval: it needs the supplier's approval for that category (in the file, or one the company
    has and the file leaves out)."""
    if "supplier_category_scope" not in accepted:
        return
    approvals = [r for _, r in accepted.get("supplier_category", [])]
    if company:
        approvals += company.outside(_spec("supplier_category"), accepted.get("supplier_category", []))
    approved = {(r["supplier_code"], r["category_code"]) for r in approvals}
    rows = []
    for line, r in accepted["supplier_category_scope"]:
        if (r["supplier_code"], r["category_code"]) in approved:
            rows.append((line, r))
            continue
        why = f"{r['supplier_code']} is not approved for {r['category_code']}: a scope limits an approval"
        report.rejected.append(Problem("supplier_category_scope", line, why))
    accepted["supplier_category_scope"] = rows


def _spec(name: str) -> Table:
    return next(s for s in TABLES if s.name == name)


def _close_tolerance_versions(db: Session) -> None:
    """Each tolerance version ends the day before the next one starts."""
    db.execute(
        text(
            "update tolerance_version v set effective_to = n.effective_from - 1 from tolerance_version n "
            "where n.version = (select min(x.version) from tolerance_version x where x.version > v.version)"
        )
    )


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
