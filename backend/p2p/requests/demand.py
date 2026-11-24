"""Demand from the client's ERP: loading its files (production orders and their materials, MRP results, sales orders, the
demand plan), the saved column mapping per kind of file, and the material still wanted for the next step to act on.

Each production order loaded gets a material request (requests/material.py) with a line per component; so does each
MRP plan per location, and each joiner plan per department.

A file is read with the company's column mapping for its kind (the latest version), every row is checked and a row that
fails is listed with its line and reason. A row is known by its ERP document number and line: one already loaded with
the same values is counted and skipped, so loading the same file again loads nothing; one already loaded with other
values (or `status` cancelled) is a change the ERP made, applied by requests/demand_changes.py (rule Q21) and listed
with what it did. A dry run checks everything, lists the changes and writes nothing.

After the rows: material requests for the new lines, the stock a change released set aside for the lines that need it,
and the store check again for the items and places that now need more or need it earlier. The import holds the store
check's lock while it applies the changes.
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
from p2p.platform import audit, company_state, tenants
from p2p.platform.sod import Actor
from p2p.requests import material, reservation
from p2p.requests.demand_changes import Applier, Change, classify
from p2p.requests.demand_files import KINDS, TEMPLATES, DemandFile, Reference, check_qty, status_of


class DemandError(Exception):
    """A request that makes no sense (an unknown kind of file, a mapping to columns we do not have)."""


@dataclass
class DemandReport(ImportReport):
    already: dict[str, int] = field(default_factory=dict)  # kind -> rows loaded before and unchanged
    mappings: dict[str, int] = field(default_factory=dict)  # kind -> column mapping version used (0: our template)
    import_id: int | None = None
    material_requests: list[str] = field(default_factory=list)  # made or added to for the production orders loaded
    changed: dict[str, int] = field(default_factory=dict)  # kind -> rows that changed what was loaded
    changes: list[Change] = field(default_factory=list)

    @property
    def already_loaded(self) -> int:
        return sum(self.already.values())

    @property
    def rows_changed(self) -> int:
        return sum(self.changed.values())


# ---- the column mapping per kind of file --------------------------------------------------------


def current_mapping(db: Session, kind: str) -> tuple[int, dict[str, str]]:
    """(version, {our column: the client's column}) for a kind of file; version 0 and no mapping when none is saved."""
    row = db.execute(
        text("select version, columns from erp_column_map where kind = :k order by version desc limit 1"), {"k": kind}
    ).first()
    return (int(row[0]), dict(row[1])) if row else (0, {})


def save_mapping(db: Session, actor: Actor, kind: str, columns: dict[str, str]) -> int:
    """Save a new version of the column mapping of a kind of file; earlier versions stay. Returns the new version."""
    spec = TEMPLATES.get(kind)
    if spec is None:
        raise DemandError(f"there is no kind of file called {kind}; choose one of: {', '.join(TEMPLATES)}")
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
    spec = TEMPLATES.get(kind)
    if spec is None:
        raise DemandError(f"there is no kind of file called {kind}; choose one of: {', '.join(TEMPLATES)}")
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


def _existing(db: Session, f: DemandFile) -> dict[tuple[Any, ...], dict[str, Any]]:
    return {
        tuple(r[c] for c in f.key): dict(r)
        for r in db.execute(text(f"select * from {f.table}")).mappings()  # noqa: S608 - names come from the classes
    }


@dataclass
class Checked:
    """One file read: the new records, how many rows gave them, the changes, and the orders its lines named."""

    records: list[dict[str, Any]] = field(default_factory=list)
    rows: int = 0
    changes: list[Change] = field(default_factory=list)
    docs: set[str] = field(default_factory=set)


def _check_rows(
    db: Session, f: DemandFile, path: Path, ref: Reference, report: DemandReport, rename: dict[str, str]
) -> Checked:
    """The records of one file that are new and the changes to what was loaded; every other row is counted as loaded
    before or listed with its reason. A row that becomes several records (a joiner plan) is taken or left out whole."""
    existing = _existing(db, f)
    seen: dict[tuple[Any, ...], int] = {}
    out = Checked()
    report.already[f.kind] = 0
    for line, row in read_table(path, f.spec(), report, rename=rename, optional=f.optional):
        missing = [c for c in f.required if row.get(c) is None]
        reason = (
            f"{', '.join(missing)} is required"
            if missing
            else check_qty(row[f.qty_column], f.qty_column) or f.check(row, ref)
        )
        if not missing and f.kind == "production_order_line":
            out.docs.add(row["order_no"])
        new: list[dict[str, Any]] = []
        pairs: list[tuple[dict[str, Any], dict[str, Any]]] = []
        change = None
        if reason is None:
            for rec in f.records(row, ref):
                key = tuple(rec[c] for c in f.key)
                if key in seen:
                    reason = f"duplicate of line {seen[key]} (same ERP document and line)"
                    break
                if key in existing:
                    pairs.append((existing[key], rec))
                else:
                    new.append(rec)
        if reason is None and not pairs and status_of(row) == "cancelled":
            reason = f"nothing to cancel: {f.label(row)} was never loaded"
        if reason is None and pairs:
            change, reason = classify(f, row, pairs, ref)
            if change is None and reason is None and not new:
                report.already[f.kind] += 1
                continue
        if reason is not None:
            report.rejected.append(Problem(f.kind, line, reason))
            continue
        for rec in [*new, *(n for _, n in pairs)]:
            seen[tuple(rec[c] for c in f.key)] = line
        if change is not None:
            out.changes.append(change)
        if new and status_of(row) != "cancelled":
            out.records += new
            out.rows += 1
    return out


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
    tenant: str | None = None,
) -> DemandReport:
    """Load the ERP demand in `source` (one file, or a dataset folder with an inputs/ folder) and report what happened."""
    company_state.require_state(db, "demand")
    started = time.monotonic()
    report = DemandReport(dry_run=dry_run)
    try:
        files = _files(source, kind)
    except DemandError as exc:
        report.rejected.append(Problem("demand", 0, str(exc)))
        return report
    tenant = tenant or tenants.slug_of(str(db.get_bind().engine.url.database or ""))
    import_id = None
    if not dry_run:
        db.execute(text("select pg_advisory_xact_lock(hashtext('store_check'))"))  # changes move stock and requests
        import_id = db.execute(
            text(
                "insert into demand_import (source, files, loaded, already_loaded, rejected, imported_by) "
                "values (:s, '{}', 0, 0, 0, :by) returning id"
            ),
            {"s": source.name, "by": actor.identity},
        ).scalar_one()
    ref = Reference.load(db)
    applier = Applier(db, tenant, actor, import_id) if import_id is not None else None
    per_file: dict[str, dict[str, int]] = {}
    line_orders: set[str] = set()
    for f, path in files:
        version, mapping = current_mapping(db, f.kind)
        report.mappings[f.kind] = version
        rejected_before = len(report.rejected)
        got = _check_rows(db, f, path, ref, report, {theirs: ours for ours, theirs in mapping.items()})
        line_orders |= got.docs
        if import_id is not None:
            _insert(db, f, got.records, import_id)
        for rec in got.records:
            f.accepted(rec, ref)
        if applier is not None:
            for change in got.changes:
                applier.apply(change)
        report.loaded[f.kind] = got.rows
        report.changed[f.kind] = len(got.changes)
        report.changes += got.changes
        per_file[f.kind] = {
            "loaded": got.rows,
            "changed": len(got.changes),
            "already_loaded": report.already[f.kind],
            "rejected": len(report.rejected) - rejected_before,
            "mapping_version": version,
        }
    for change in report.changes:  # the ERP explodes the bill of materials: a header's quantity alone moves no line
        if "header_qty" in change.what and change.doc_no not in line_orders:
            report.warnings.append(
                Problem(
                    "production_order", 0,
                    f"{change.doc_no}: quantity {change.before['qty']} -> {change.after['qty']}; its components were "
                    "not in this import, so its material request is unchanged",
                )
            )  # fmt: skip
    if import_id is not None and applier is not None:
        counts = {
            "loaded": report.rows_loaded, "changed": report.rows_changed, "already": report.already_loaded,
            "rejected": len(report.rejected),
        }  # fmt: skip
        db.execute(
            text(
                "update demand_import set files = cast(:f as jsonb), loaded = :loaded, changed = :changed, "
                "already_loaded = :already, rejected = :rejected where id = :i"
            ),
            {"f": json.dumps(per_file), "i": import_id, **counts},
        )
        audit.record(db, actor, "demand.import", "demand_import", import_id, after={"source": source.name, **counts})
        db.commit()
        report.import_id = import_id
        applier.finish()
        report.material_requests = material.make_requests(db, actor)
        why = f"the ERP's demand changed (import {import_id})"
        reservation.stock_rose(db, tenant, sorted(applier.released), why=why)
        if applier.to_check:
            material.store_check(db, audit.SYSTEM, tenant=tenant, places=sorted(applier.to_check))
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
    """One line for a person: what loaded, what changed, what was there already, what was left out."""
    verb = "would load" if report.dry_run else "loaded"
    if report.rows_loaded == 0 and not report.rows_changed and report.already_loaded and not report.rejected:
        return f"nothing new: all {report.already_loaded} rows were loaded before"
    return (
        f"{verb} {report.rows_loaded} row{'' if report.rows_loaded == 1 else 's'}; {report.rows_changed} changed; "
        f"{report.already_loaded} loaded before and unchanged; {len(report.rejected)} left out"
    )


def change_line(change: dict[str, Any]) -> str:
    """A change for a person: "MO-2 RM-10: 100 -> 60; released 40; REQ-... line 8 90 -> 50; case 14 for ... due 25 Nov"."""
    before, after = change["before"], change["after"]
    head = change["doc_no"] + (f" {change['item_code']}" if change.get("item_code") else "")
    if "cancelled" in change["what"]:
        parts = [f"{head}: cancelled"]
    else:
        keys = ("qty", "need_by", "unit", "basis", "customer", "period", "status")
        moved = [f"{before.get(k)} -> {after.get(k)}" for k in keys if before.get(k) != after.get(k)]
        parts = [f"{head}: {'; '.join(moved) or ', '.join(change['what'])}"]
    released = change.get("released") or {}
    if released:
        parts.append("released " + ", ".join(v if len(released) == 1 else f"{k} {v}" for k, v in released.items()))
    parts += [
        f"{c['request_no']} line {c['line_no']} {c['qty_before']} -> {c['qty_after']}"
        for c in change.get("requests_cut", [])
    ]
    parts += [f"{no} cancelled" for no in change.get("requests_cancelled", [])]
    parts += [f"{m['request_no']} line {m['line_no']} need-by {m['to']}" for m in change.get("need_by_moved", [])]
    for case in change.get("cases", []):
        due = date.fromisoformat(case["due_at"][:10])
        parts.append(f"case {case['task_id']} for {case['assignee']} due {due:%d %b}")
    if change.get("store_check"):
        parts.append("store check run again")
    return "; ".join(parts)


def list_changes(db: Session, *, import_id: int | None = None, doc_no: str | None = None) -> list[dict[str, Any]]:
    """The changes applied, newest first (at most 200), as the import answer shows them."""
    rows = db.execute(
        text(
            "select c.id, c.import_id, c.kind, c.doc_no, c.line_key, c.what, c.before, c.after, c.effects, c.applied_at, "
            "q.item_code, coalesce(q.location_code, p.location_code) as location_code from demand_change c "
            "left join material_requirement q on q.id = c.requirement_id "
            "left join production_order p on c.kind = 'production_order' and p.order_no = c.doc_no "
            "where (cast(:i as bigint) is null or c.import_id = :i) and (cast(:d as text) is null or c.doc_no = :d) "
            "order by c.id desc limit 200"
        ),
        {"i": import_id, "d": doc_no},
    ).mappings()
    empty: dict[str, Any] = {
        "released": {},
        "requests_cut": [],
        "requests_cancelled": [],
        "need_by_moved": [],
        "cases": [],
    }
    return [
        {
            "id": r["id"], "import_id": r["import_id"], "kind": r["kind"], "doc_no": r["doc_no"],
            "line_key": r["line_key"], "item_code": r["item_code"], "location_code": r["location_code"],
            "what": list(r["what"]), "before": r["before"], "after": r["after"],
            **empty, "store_check": False, **(r["effects"] or {}), "applied_at": r["applied_at"].isoformat(),
        }
        for r in rows
    ]  # fmt: skip
