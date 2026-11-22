"""A later masters import (update mode, task 0.6): each file row is matched to the company's row by its key
(`import_rules`), new rows are added, existing ones change only where the table's policy allows, and nothing is ever
deleted. What a second person decides (a supplier's status, its bank accounts, policies, rules, roles, versions) is kept,
or arrives as a draft or unverified, each with a warning. One audit row per added or changed row."""

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from p2p.masters import import_rules as rules
from p2p.masters.report import ImportReport, Problem, RowChange
from p2p.masters.schema import TABLES, Table
from p2p.masters.suppliers import _duplicate_of, mask
from p2p.platform import audit
from p2p.platform.sod import Actor

Row = dict[str, Any]
Lines = list[tuple[int, Row]]  # (line in the file, row)


def _norm(value: Any) -> Any:
    """A value as it is compared: a time without a zone is taken as UTC, every time in UTC."""
    if isinstance(value, datetime):
        return (value if value.tzinfo else value.replace(tzinfo=UTC)).astimezone(UTC)
    return value


def key_of(spec: Table, row: Row) -> tuple[Any, ...]:
    return tuple(_norm(row.get(c)) for c in rules.key_columns(spec))


def key_text(spec: Table, row: Row) -> str:
    """The key as people read it: values joined with '/', empty as '-', an account number masked."""
    return "/".join(
        mask(row[c]) if c == "account_no" and row.get(c) is not None else rules.shown(row.get(c))
        for c in rules.key_columns(spec)
    )


@dataclass
class Company:
    """The company's master rows before the import (the columns the files carry), indexed by key."""

    rows: dict[str, list[Row]]
    index: dict[str, dict[tuple[Any, ...], list[Row]]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for spec in TABLES:
            for row in self.rows.get(spec.name, []):
                self.index.setdefault(spec.name, {}).setdefault(key_of(spec, row), []).append(row)

    @property
    def suppliers(self) -> set[str]:
        return {str(r["code"]) for r in self.rows.get("supplier", [])}

    def outside(self, spec: Table, file_rows: Lines) -> list[Row]:
        """The company's rows whose key the file does not have: they stay, and their children may point at them."""
        in_file = {key_of(spec, r) for _, r in file_rows}
        return [r for k, rs in self.index.get(spec.name, {}).items() if k not in in_file for r in rs]


def load(db: Session, skip: set[str]) -> Company:
    rows: dict[str, list[Row]] = {}
    for spec in TABLES:
        if spec.name not in skip:
            cols = ", ".join(c for c, _ in spec.columns)
            rows[spec.name] = [dict(r) for r in db.execute(text(f"select {cols} from {spec.name}")).mappings()]  # noqa: S608 - names from the schema file
    return Company(rows)


def refusal(db: Session, spec: Table, row: Row, company: Company, parsed: dict[str, Lines]) -> str | None:
    """Why a file row is left out in update mode: its key is not one row of the company, or it is a new supplier that
    another supplier already is (same GSTIN, PAN or bank account, D-03 R7)."""
    found = company.index.get(spec.name, {}).get(key_of(spec, row), [])
    if len(found) > 1:
        return (
            f"{key_text(spec, row)}: the company has {len(found)} rows with this key; correct them on the screen first"
        )
    if spec.name == "supplier" and not found:
        code = row["code"]
        accounts = [r["account_no"] for _, r in parsed.get("supplier_bank_account", []) if r["supplier_code"] == code]
        for data in ({"gstin": row.get("gstin"), "pan": row.get("pan")}, *({"account_no": a} for a in accounts)):
            if dup := _duplicate_of(db, data, own=code):
                return f"possible duplicate: {dup}"
    return None


@dataclass
class _Table:
    """One table's comparison: what to insert and update, and its counts."""

    spec: Table
    inserts: list[Row] = field(default_factory=list)
    updates: list[tuple[Row, Row]] = field(default_factory=list)  # (the company's row, the columns to set)
    counts: dict[str, int] = field(default_factory=lambda: {"added": 0, "changed": 0, "unchanged": 0})


class _Comparison:
    """Compares one table's accepted file rows with the company's rows, by the table's policy."""

    def __init__(self, spec: Table, company: Company, absent: set[str], report: ImportReport, actor: Actor) -> None:
        self.spec, self.company, self.absent, self.report, self.actor = spec, company, absent, report, actor
        self.policy = rules.policy(spec.name)
        self.out = _Table(spec)
        self._warned: set[str] = set()  # suppliers already warned about (bank details, status history)

    def run(self, rows: Lines) -> _Table:
        index = self.company.index.get(self.spec.name, {})
        for line, row in rows:
            old = index.get(key_of(self.spec, row))
            kind = self._new(line, row) if old is None else self._existing(line, row, old[0])
            self.out.counts[kind] += 1
        return self.out

    def _warn(self, line: int, key: str, reason: str) -> None:
        self.report.warnings.append(Problem(self.spec.name, line, f"{key}: {reason}"))

    def _warn_supplier(self, line: int, code: str, reason: str) -> None:
        if code not in self._warned:
            self._warned.add(code)
            self._warn(line, code, reason)

    def _new(self, line: int, row: Row) -> str:
        name, key = self.spec.name, key_text(self.spec, row)
        existing = self.company.suppliers
        if self.policy.kind == "C":
            self._warn(line, key, rules.control_reason(name, new=True))
            return "unchanged"
        if name == "supplier_status_history" and row["supplier_code"] in existing:
            self._warn_supplier(line, row["supplier_code"], rules.HISTORY_NOT_ADDED)
            return "unchanged"
        if name == "supplier_bank_account" and row["supplier_code"] in existing:
            self._warn_supplier(line, row["supplier_code"], rules.BANK_KEPT)
            return "unchanged"
        row = self._second_person(line, key, dict(row))
        self.out.inserts.append(row)
        self.report.changes.append(RowChange(name, key, "added", {}, _shown_row(row)))
        return "added"

    def _second_person(self, line: int, key: str, row: Row) -> Row:
        """A new row arrives in the state a second person moves on (2P), and the report says so."""
        name = self.spec.name
        if name == "supplier" and row.get("status") == "active":
            row["status"] = "draft"
            self._warn(line, key, rules.DRAFT_SUPPLIER)
        elif name == "supplier_bank_account":
            if row.get("verified"):
                self._warn(line, key, rules.UNVERIFIED_BANK)
            row.update(verified=False, verified_on=None, submitted_by=self.actor.id)
        elif name == "supplier_document" and row.get("verified"):
            row["verified"] = False
            self._warn(line, key, rules.UNVERIFIED_DOCUMENT)
        elif name == "contract" and row.get("status") == "active":
            row["status"] = "draft"
            self._warn(line, key, rules.DRAFT_CONTRACT)
        elif name == "price_record" and row.get("contract_code") and row.get("status") == "active":
            row["status"] = "draft"
            self._warn(line, key, rules.DRAFT_PRICE)
        elif name == "market_benchmark" and row.get("status") != "unverified":
            row.update(status="unverified", verified_by=None, verified_at=None, verify_note=None)
            self._warn(line, key, rules.UNVERIFIED_BENCHMARK)
        return row

    def _existing(self, line: int, row: Row, old: Row) -> str:
        name, key = self.spec.name, key_text(self.spec, row)
        columns = [c for c, _ in self.spec.columns if not (c in self.absent and row[c] is None)]
        diff = [c for c in columns if _norm(old[c]) != _norm(row[c])]
        if not diff:
            return "unchanged"
        if self.policy.kind == "C":
            self._warn(line, key, rules.control_reason(name, new=False))
        elif name == "supplier_bank_account":
            self._warn_supplier(line, row["supplier_code"], rules.BANK_KEPT)
        elif self.policy.kind in ("K", "A"):
            self._warn(line, key, self.policy.why)
        else:
            return self._update(line, key, row, old, diff)
        return "unchanged"

    def _update(self, line: int, key: str, row: Row, old: Row, diff: list[str]) -> str:
        protected = set(self.policy.protected)
        if self.spec.name == "supplier" and old["status"] != "draft":
            protected |= set(rules.SUPPLIER_FROZEN)
        for column in (c for c in diff if c in protected):
            reason = rules.protected_reason(self.spec.name, column, old[column], old.get("status"))
            self._warn(line, key, reason)
        applied = [c for c in diff if c not in protected]
        if not applied:
            return "unchanged"
        self.out.updates.append((old, {c: row[c] for c in applied}))
        before, after = _shown_row({c: old[c] for c in applied}), _shown_row({c: row[c] for c in applied})
        self.report.changes.append(RowChange(self.spec.name, key, "changed", before, after))
        return "changed"


def _shown_row(row: Row) -> Row:
    """A row as the report and the audit log keep it: an account number masked."""
    return {k: mask(v) if k == "account_no" and v is not None else v for k, v in row.items()}


def apply(
    db: Session,
    accepted: dict[str, Lines],
    parsed: dict[str, Lines],
    company: Company,
    absent: dict[str, set[str]],
    report: ImportReport,
    *,
    actor: Actor,
    dry_run: bool,
    dataset: str,
) -> None:
    """Compare every table, then (unless a dry run) write the rows and their audit in one transaction."""
    tables = []
    for spec in TABLES:
        if spec.name not in company.rows:
            continue  # a file the dataset may leave out: the table keeps its rows
        compared = _Comparison(spec, company, absent.get(spec.name, set()), report, actor).run(accepted[spec.name])
        report.not_in_file[spec.name] = len(company.outside(spec, parsed[spec.name]))
        for kind, n in compared.counts.items():
            getattr(report, kind)[spec.name] = n
        tables.append(compared)
    if dry_run:
        return
    for table in tables:
        _write(db, table)
    audit.record(
        db,
        actor,
        "masters.import",
        "dataset",
        dataset,
        after={
            "mode": "update",
            "added": report.added,
            "changed": report.changed,
            "unchanged": report.unchanged,
            "not_in_file": report.not_in_file,
            "rejected": len(report.rejected),
            "warnings": len(report.warnings),
        },
    )
    for change in report.changes:
        action = "masters.row_add" if change.kind == "added" else "masters.row_change"
        audit.record(db, actor, action, change.table, change.key, before=change.before or None, after=change.after)
    db.commit()


def _write(db: Session, table: _Table) -> None:
    name = table.spec.name
    for row in table.inserts:
        cols = list(row)
        db.execute(
            text(f"insert into {name} ({', '.join(cols)}) values ({', '.join(':' + c for c in cols)})"),  # noqa: S608 - names from the schema file
            row,
        )
    keys = rules.key_columns(table.spec)
    where = " and ".join(f"{k} is not distinct from :k_{k}" for k in keys)
    for old, values in table.updates:
        sets = ", ".join(f"{c} = :v_{c}" for c in values)
        db.execute(
            text(f"update {name} set {sets} where {where}"),  # noqa: S608 - names from the schema file
            {**{f"v_{c}": v for c, v in values.items()}, **{f"k_{k}": old[k] for k in keys}},
        )
