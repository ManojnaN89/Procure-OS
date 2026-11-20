"""The company's tolerances, versioned as a whole (task 3.2, D-17).

Version N is every (key, category) value in force from its start date. Every reader asks for the version in force on
the date that decides (an invoice's received date, a receipt's business date, the day a buyer decides a supplier's
change) and keeps the version number with the decision, so the same inputs give the same result later. A change is the
next version from a date (today or later): a copy of the version in force then with one value changed; the version in
force ends the day before. An old version is never edited.
"""

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from p2p.platform import audit
from p2p.platform.sod import Actor


class ToleranceError(Exception):
    pass


@dataclass(frozen=True)
class ToleranceSet:
    version: int
    effective_from: date
    rows: list[dict[str, str]]  # key, value, category_code

    @property
    def label(self) -> str:
        return f"tolerance v{self.version}"

    def value(self, key: str, category: str | None = None) -> str | None:
        """The category's own value, else the company's ('ANY')."""
        for scope in (category, "ANY"):
            for r in self.rows:
                if (
                    scope is not None
                    and r["key"] == key
                    and r["category_code"] == scope
                    and r["value"] not in (None, "")
                ):
                    return str(r["value"])
        return None

    def decimal(self, key: str, default: str, category: str | None = None) -> Decimal:
        return Decimal(self.value(key, category) or default)


def _version_on(db: Session, on: date) -> dict[str, Any] | None:
    row = (
        db.execute(
            text(
                "select version, effective_from from tolerance_version where effective_from <= :d "
                "and (effective_to is null or effective_to >= :d) order by version desc limit 1"
            ),
            {"d": on},
        )
        .mappings()
        .first()
    )
    return dict(row) if row else None


def in_force(db: Session, on: date) -> ToleranceSet:
    """The tolerance set in force on the date. A version replaced before its start date is never used."""
    found = _version_on(db, on)
    if found is None:
        raise ToleranceError(f"no tolerance version is in force on {on:%d %b %Y}")
    return of_version(db, int(found["version"]), found["effective_from"])


def of_version(db: Session, version: int, effective_from: date | None = None) -> ToleranceSet:
    """One version's values (to re-judge a decision under the version it used)."""
    if effective_from is None:
        effective_from = db.execute(
            text("select effective_from from tolerance_version where version = :v"), {"v": version}
        ).scalar_one()
    rows = [
        {"key": str(r[0]), "value": str(r[1]) if r[1] is not None else "", "category_code": str(r[2])}
        for r in db.execute(
            text("select key, value, category_code from tolerance where version = :v order by key, category_code"),
            {"v": version},
        )
    ]
    return ToleranceSet(version, effective_from, rows)


def versions(db: Session) -> list[dict[str, Any]]:
    """Every version, newest first, with its values."""
    out = []
    for r in db.execute(text("select * from tolerance_version order by version desc")).mappings():
        values = [
            {"key": str(k), "category": str(c), "value": str(v)}
            for k, c, v in db.execute(
                text("select key, category_code, value from tolerance where version = :v order by key, category_code"),
                {"v": r["version"]},
            )
        ]
        out.append({**dict(r), "values": values})
    return out


def check_start(db: Session, effective_from: date, today: date) -> None:
    """A new version starts today or later, and not before the latest version's start."""
    if effective_from < today:
        raise ToleranceError(
            "a new version starts today or later: decisions already made keep the version they were made under"
        )
    latest = db.execute(text("select max(effective_from) from tolerance_version")).scalar()
    if latest is not None and effective_from < latest:
        raise ToleranceError(f"the latest version starts on {latest:%d %b %Y}; a new one cannot start before it")


def save_version(
    db: Session,
    actor: Actor,
    *,
    key: str,
    category: str,
    value: str,
    effective_from: date,
    today: date,
    note: str | None = None,
    apply: bool = True,
) -> int:
    """Make the next version from a date: the set in force then, with one value changed (or added for a category).
    The version in force until then ends the day before (a version starting that same day is so replaced before its
    start and never used). Returns the new version; with apply False only checks."""
    check_start(db, effective_from, today)
    current = in_force(db, effective_from)
    version = int(db.execute(text("select coalesce(max(version), 0) + 1 from tolerance_version")).scalar_one())
    if not apply:
        return version
    db.execute(
        text("update tolerance_version set effective_to = :t where version = :v"),
        {"t": effective_from - timedelta(days=1), "v": current.version},
    )
    db.execute(
        text("insert into tolerance_version (version, effective_from, note, created_by) values (:v, :f, :n, :by)"),
        {"v": version, "f": effective_from, "n": note, "by": f"{actor.kind}:{actor.id}"},
    )
    rows = {(r["key"], r["category_code"]): r["value"] for r in current.rows}
    before = rows.get((key, category))
    rows[(key, category)] = value
    db.execute(
        text("insert into tolerance (version, key, value, category_code) values (:v, :k, :val, :c)"),
        [{"v": version, "k": k, "val": v, "c": c} for (k, c), v in sorted(rows.items())],
    )
    audit.record(
        db,
        actor,
        "tolerance.change",
        "tolerance",
        f"{key}:{category}",
        before={"value": before, "version": current.version},
        after={"value": value, "version": version, "effective_from": effective_from},
        reason=note,
        rule_version=f"tolerance v{version}",
    )
    return version
