"""Demand from the client's ERP: loading its files (production orders and their materials, MRP results, sales orders, the
demand plan), the saved column mapping per kind of file, and the material still wanted for the next step to act on.

Each production order loaded gets a material request (requests/material.py) with a line per component.

A file is read with the company's column mapping for its kind (the latest version), every row is checked and a row that
fails is listed with its line and reason. A row is known by its ERP document number and line: one already loaded is
counted and skipped, so loading the same file again loads nothing; one already loaded with other values is listed as
changed and left out (the loaded demand is never overwritten silently). A dry run checks everything and writes nothing.
"""

import csv
import io
import json
import time
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from p2p.masters.importer import ImportReport, Problem, read_table
from p2p.platform import audit
from p2p.platform.sod import Actor
from p2p.requests import material
from p2p.requests.demand_files import KINDS, DemandFile, Reference, check_qty


class DemandError(Exception):
    """A request that makes no sense (an unknown kind of file, a mapping to columns we do not have)."""


@dataclass
class DemandReport(ImportReport):
    already: dict[str, int] = field(default_factory=dict)  # kind -> rows loaded before and unchanged
    mappings: dict[str, int] = field(default_factory=dict)  # kind -> column mapping version used (0: our template)
    import_id: int | None = None
    material_requests: list[str] = field(default_factory=list)  # made or added to for the production orders loaded

    @property
    def already_loaded(self) -> int:
        return sum(self.already.values())


# ---- the column mapping per kind of file --------------------------------------------------------


def current_mapping(db: Session, kind: str) -> tuple[int, dict[str, str]]:
    """(version, {our column: the client's column}) for a kind of file; version 0 and no mapping when none is saved."""
    row = db.execute(
        text("select version, columns from erp_column_map where kind = :k order by version desc limit 1"), {"k": kind}
    ).first()
    return (int(row[0]), dict(row[1])) if row else (0, {})


def save_mapping(db: Session, actor: Actor, kind: str, columns: dict[str, str]) -> int:
    """Save a new version of the column mapping of a kind of file; earlier versions stay. Returns the new version."""
    spec = KINDS.get(kind)
    if spec is None:
        raise DemandError(f"there is no kind of file called {kind}; choose one of: {', '.join(KINDS)}")
    ours = {c for c, _ in spec.columns}
    unknown = sorted(set(columns) - ours)
    if unknown:
        raise DemandError(f"{', '.join(unknown)} is not a column of {kind}; its columns are {', '.join(sorted(ours))}")
    theirs = [str(v).strip() for v in columns.values()]
    if any(not v for v in theirs):
        raise DemandError("every mapped column needs the client's column name")
    if len(set(theirs)) != len(theirs):
        raise DemandError("two of our columns are mapped to the same client column")
    version = current_mapping(db, kind)[0] + 1
    clean = dict(zip(columns, theirs, strict=True))
    db.execute(
        text("insert into erp_column_map (kind, version, columns, created_by) values (:k, :v, cast(:c as jsonb), :by)"),
        {"k": kind, "v": version, "c": json.dumps(clean), "by": actor.identity},
    )
    audit.record(db, actor, "demand.mapping", "erp_column_map", f"{kind} v{version}", after=clean)
    db.commit()
    return version


def template_csv(kind: str) -> str:
    """Our template for a kind of file: the header and one example row."""
    spec = KINDS.get(kind)
    if spec is None:
        raise DemandError(f"there is no kind of file called {kind}; choose one of: {', '.join(KINDS)}")
    out = io.StringIO(newline="")
    writer = csv.writer(out, lineterminator="\r\n")
    writer.writerow([c for c, _ in spec.columns])
    writer.writerow(spec.example)
    return out.getvalue()


# ---- loading ----------------------------------------------------------------------------------


def _files(source: Path, kind: str | None) -> list[tuple[DemandFile, Path]]:
    """The files to read: one file (its kind named or taken from its name), or every kind found in a dataset folder."""
    if kind is not None and kind not in KINDS:
        raise DemandError(f"there is no kind of file called {kind}; choose one of: {', '.join(KINDS)}")
    if source.is_file():
        name = kind or source.stem
        if name not in KINDS:
            raise DemandError(f"cannot tell what {source.name} holds; name it after its kind ({', '.join(KINDS)})")
        return [(KINDS[name], source)]
    folder = source / "inputs" if (source / "inputs").is_dir() else source
    found = [
        (f, folder / f"{f.kind}.csv")
        for f in KINDS.values()
        if kind in (None, f.kind) and any((folder / f"{f.kind}{ext}").is_file() for ext in (".csv", ".xlsx"))
    ]
    if not found:
        raise DemandError(f"no ERP input file in {folder} (looked for {', '.join(KINDS)} as .csv or .xlsx)")
    return found


def _show(value: Any) -> str:
    if value is None:
        return "(blank)"
    if isinstance(value, Decimal):
        return f"{value.normalize():f}"
    if isinstance(value, date):
        return value.isoformat()
    return str(value)


def _existing(db: Session, f: DemandFile) -> dict[tuple[Any, ...], dict[str, Any]]:
    cols = ", ".join((*f.key, *f.compared))
    return {
        tuple(r[c] for c in f.key): dict(r)
        for r in db.execute(text(f"select {cols} from {f.table}")).mappings()  # noqa: S608 - names come from the classes
    }


def _check_rows(
    db: Session, f: DemandFile, path: Path, ref: Reference, report: DemandReport, rename: dict[str, str]
) -> list[dict[str, Any]]:
    """The records of one file that are new; every other row is counted as loaded before or listed with its reason."""
    existing = _existing(db, f)
    seen: dict[tuple[Any, ...], int] = {}
    records: list[dict[str, Any]] = []
    report.already[f.kind] = 0
    for line, row in read_table(path, f.spec(), report, rename=rename, optional=f.optional):
        missing = [c for c in f.required if row.get(c) is None]
        reason = f"{', '.join(missing)} is required" if missing else check_qty(row["qty"]) or f.check(row, ref)
        if reason is None:
            rec = f.record(row, ref)
            key = tuple(rec[c] for c in f.key)
            if key in seen:
                reason = f"duplicate of line {seen[key]} (same ERP document and line)"
            elif key in existing:
                old = existing[key]
                changes = [f"{c} {_show(old[c])} -> {_show(rec[c])}" for c in f.compared if old[c] != rec[c]]
                if not changes:
                    report.already[f.kind] += 1
                    continue
                reason = "already loaded; changed: " + "; ".join(changes)
        if reason is not None:
            report.rejected.append(Problem(f.kind, line, reason))
            continue
        seen[key] = line
        records.append(rec)
    return records


def _insert(db: Session, f: DemandFile, records: list[dict[str, Any]], import_id: int) -> None:
    for rec in records:
        rec["import_id"] = import_id
        cols = [c for c in rec if c != "id"]
        rec["id"] = db.execute(
            text(
                f"insert into {f.table} ({', '.join(cols)}) values ({', '.join(':' + c for c in cols)}) returning id"  # noqa: S608
            ),
            rec,
        ).scalar_one()


def import_demand(
    db: Session,
    source: Path,
    *,
    actor: Actor = audit.SYSTEM,
    dry_run: bool = False,
    kind: str | None = None,
) -> DemandReport:
    """Load the ERP demand in `source` (one file, or a dataset folder with an inputs/ folder) and report what happened."""
    started = time.monotonic()
    report = DemandReport(dry_run=dry_run)
    try:
        files = _files(source, kind)
    except DemandError as exc:
        report.rejected.append(Problem("demand", 0, str(exc)))
        return report
    ref = Reference.load(db)
    import_id = None
    if not dry_run:
        import_id = db.execute(
            text(
                "insert into demand_import (source, files, loaded, already_loaded, rejected, imported_by) "
                "values (:s, '{}', 0, 0, 0, :by) returning id"
            ),
            {"s": source.name, "by": actor.identity},
        ).scalar_one()
    per_file: dict[str, dict[str, int]] = {}
    for f, path in files:
        version, mapping = current_mapping(db, f.kind)
        report.mappings[f.kind] = version
        rejected_before = len(report.rejected)
        records = _check_rows(db, f, path, ref, report, {theirs: ours for ours, theirs in mapping.items()})
        if import_id is not None:
            _insert(db, f, records, import_id)
        for rec in records:
            f.accepted(rec, ref)
        report.loaded[f.kind] = len(records)
        per_file[f.kind] = {
            "loaded": len(records),
            "already_loaded": report.already[f.kind],
            "rejected": len(report.rejected) - rejected_before,
            "mapping_version": version,
        }
    if import_id is not None:
        counts = {"loaded": report.rows_loaded, "already": report.already_loaded, "rejected": len(report.rejected)}
        db.execute(
            text(
                "update demand_import set files = cast(:f as jsonb), loaded = :loaded, already_loaded = :already, "
                "rejected = :rejected where id = :i"
            ),
            {"f": json.dumps(per_file), "i": import_id, **counts},
        )
        audit.record(db, actor, "demand.import", "demand_import", import_id, after={"source": source.name, **counts})
        db.commit()
        report.import_id = import_id
        report.material_requests = material.make_requests(db, actor)
    report.seconds = round(time.monotonic() - started, 2)
    return report


# ---- what the next step reads -----------------------------------------------------------------


@dataclass(frozen=True)
class Requirement:
    """Material still wanted: from a production order's components or an MRP result."""

    id: int
    source_kind: str  # production_order or mrp
    source_doc_no: str  # the ERP's document number (MO-1, or the MRP plan)
    item_code: str
    qty: Decimal  # in `unit`, as the ERP gave it
    unit: str
    base_qty: Decimal  # in the item's base unit
    need_by: date
    location_code: str


_REQUIREMENT_COLUMNS = "id, source_kind, source_doc_no, item_code, qty, unit, base_qty, need_by, location_code"


def open_requirements(db: Session, *, location: str | None = None, item: str | None = None) -> list[Requirement]:
    """The open material requirements, earliest need-by first (then in the order they were loaded): the order in which
    stock is reserved and material requests are made."""
    clauses = ["status = 'open'"]
    params: dict[str, Any] = {}
    if location:
        clauses.append("location_code = :loc")
        params["loc"] = location
    if item:
        clauses.append("item_code = :item")
        params["item"] = item
    where = " and ".join(clauses)  # fixed clauses; the values are parameters
    sql = f"select {_REQUIREMENT_COLUMNS} from material_requirement where {where} order by need_by, id"  # noqa: S608
    rows = db.execute(text(sql), params).mappings()
    return [Requirement(**r) for r in rows]


def summary(report: DemandReport) -> str:
    """One line for a person: what loaded, what was there already, what was left out."""
    verb = "would load" if report.dry_run else "loaded"
    if report.rows_loaded == 0 and report.already_loaded and not report.rejected:
        return f"nothing new: all {report.already_loaded} rows were loaded before"
    return (
        f"{verb} {report.rows_loaded} row{'' if report.rows_loaded == 1 else 's'}; {report.already_loaded} loaded "
        "before and unchanged; "
        f"{len(report.rejected)} left out"
    )
