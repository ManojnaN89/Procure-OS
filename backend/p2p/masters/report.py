"""What an import says it did: rows loaded, rows left out with their reason, warnings, and for a later masters import
what it added, changed or kept."""

from dataclasses import dataclass, field
from typing import Any


@dataclass
class Problem:
    table: str
    line: int  # line number in the csv file (the header is line 1)
    reason: str


@dataclass
class RowChange:
    table: str
    key: str  # the key's values joined with '/', empty shown as '-'
    kind: str  # added or changed
    before: dict[str, Any]  # changed: the changed columns as they were; added: empty
    after: dict[str, Any]  # changed: the changed columns as they are now; added: the row


@dataclass
class ImportReport:
    loaded: dict[str, int] = field(default_factory=dict)  # rows accepted from the file (added + changed + unchanged)
    rejected: list[Problem] = field(default_factory=list)
    warnings: list[Problem] = field(default_factory=list)
    seconds: float = 0.0
    dry_run: bool = False
    mode: str = "first"  # masters: first (every table replaced) or update (matched by key)
    added: dict[str, int] = field(default_factory=dict)
    changed: dict[str, int] = field(default_factory=dict)
    unchanged: dict[str, int] = field(default_factory=dict)
    not_in_file: dict[str, int] = field(default_factory=dict)  # the company's rows the file leaves out: kept
    changes: list[Any] = field(default_factory=list)  # masters: a RowChange per added or changed row

    @property
    def rows_loaded(self) -> int:
        return sum(self.loaded.values())


def _value(value: Any) -> str:
    if value is None:
        return "-"
    return f"'{value}'" if isinstance(value, str) else str(value)


def _cut(lines: list[str], limit: int) -> list[str]:
    return lines[:limit] + ([f"  ... and {len(lines) - limit} more"] if len(lines) > limit else [])


def update_lines(report: ImportReport) -> list[str]:
    """What a later masters import says on the command line: totals, a line per table with anything added, changed or
    not in the file, then the rows added and changed, the rows left out and the warnings."""
    total = {k: sum(getattr(report, k).values()) for k in ("added", "changed", "unchanged", "not_in_file")}
    tail = (
        f"not in the file {total['not_in_file']} (kept), in {report.seconds} s; "
        f"{len(report.rejected)} left out, {len(report.warnings)} warnings"
    )
    if report.dry_run:
        head = (
            f"update by key (dry run, nothing written): would add {total['added']}, would change {total['changed']}, "
            f"unchanged {total['unchanged']}, {tail}"
        )
    else:
        head = (
            f"update by key: added {total['added']}, changed {total['changed']}, unchanged {total['unchanged']}, {tail}"
        )
    lines = [head]
    for table in report.not_in_file:  # every table compared, parents first
        added, changed, kept = (
            report.added.get(table, 0),
            report.changed.get(table, 0),
            report.not_in_file.get(table, 0),
        )
        if added or changed or kept:
            lines.append(
                f"  {table}: {added} added, {changed} changed, {report.unchanged.get(table, 0)} unchanged, "
                f"{kept} not in the file"
            )
    lines += _cut([f"  added     {c.table} {c.key}" for c in report.changes if c.kind == "added"], 25)
    lines += _cut(
        [
            f"  changed   {c.table} {c.key}: "
            + "; ".join(f"{col} {_value(c.before.get(col))} -> {_value(c.after[col])}" for col in c.after)
            for c in report.changes
            if c.kind == "changed"
        ],
        25,
    )
    lines += _cut([f"  left out  {p.table} line {p.line}: {p.reason}" for p in report.rejected], 25)
    lines += _cut([f"  warning   {p.table}: {p.reason}" for p in report.warnings], 50)
    return lines
