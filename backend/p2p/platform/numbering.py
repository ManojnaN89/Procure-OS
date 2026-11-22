"""Document numbers: REQ-VAD-2627-00001 and the like. The format of each series is data (the number_series table);
this module only counts. The counter is bumped in the caller's transaction, so a rolled-back document gives its
number back and two callers never get the same one."""

import re
import string
from datetime import date

from sqlalchemy import text
from sqlalchemy.orm import Session

from p2p.formulas.fy import fy_start_year


class NoSuchSeries(Exception):
    pass


# Series a company's master data may not have yet (it came later than its number_series file); a row in the table wins.
DEFAULT_SERIES: dict[str, dict[str, str]] = {
    "material_request": {
        "prefix": "MR",
        "scope": "location",
        "format": "{prefix}-{loc}-{fy4}-{seq:05d}",
        "reset": "fy",
    },
}


def fy4(d: date) -> str:
    """'2627' for any date in the financial year 2026-27."""
    y = fy_start_year(d)
    return f"{y % 100:02d}{(y + 1) % 100:02d}"


def _series(db: Session, document_type: str) -> dict[str, str] | None:
    row = (
        db.execute(
            text("select prefix, scope, format, reset from number_series where document_type = :t"),
            {"t": document_type},
        )
        .mappings()
        .first()
    )
    return dict(row) if row else DEFAULT_SERIES.get(document_type)


def prefix(db: Session, document_type: str) -> str | None:
    """The prefix of a series (MR for material requests), or None when there is no such series."""
    series = _series(db, document_type)
    return str(series["prefix"]) if series else None


def next_number(db: Session, document_type: str, on_date: date, *, location_code: str | None = None) -> str:
    series = _series(db, document_type)
    if series is None:
        raise NoSuchSeries(f"no number series for {document_type}")
    if series["scope"] == "location" and not location_code:
        raise ValueError(f"the {document_type} series is per location; say which location")
    scope_key = (location_code or "") if series["scope"] == "location" else ""
    fy = fy4(on_date) if series["reset"] == "fy" else "all"
    seq = db.execute(
        text(
            "insert into number_counter (document_type, scope_key, fy, last_seq) values (:t, :s, :f, 1) "
            "on conflict (document_type, scope_key, fy) do update set last_seq = number_counter.last_seq + 1 "
            "returning last_seq"
        ),
        {"t": document_type, "s": scope_key, "f": fy},
    ).scalar_one()
    loc = location_code.split("-")[-1] if location_code else ""
    return str(series["format"]).format(prefix=series["prefix"], loc=loc, fy4=fy4(on_date), seq=seq)


_FIELDS = {"loc": r"(?P<loc>[A-Za-z0-9]+)", "fy4": r"(?P<fy4>\d{4})", "seq": r"(?P<seq>\d+)"}


def _pattern(series: dict[str, str]) -> re.Pattern[str]:
    parts = []
    for literal, name, _, _ in string.Formatter().parse(str(series["format"])):
        parts.append(re.escape(literal))
        if name == "prefix":
            parts.append(re.escape(str(series["prefix"])))
        elif name:
            parts.append(_FIELDS.get(name, ".+?"))
    return re.compile("".join(parts) + "$")


def keep_above(db: Session, document_type: str, number: str) -> bool:
    """A number already used (a document of the old system loaded at cutover): raise the series' counter to it, so the
    platform never issues it again. False when the number does not fit the company's format for the series."""
    series = _series(db, document_type)
    found = _pattern(series).match(number) if series else None
    if series is None or found is None:
        return False
    parts = found.groupdict()
    scope_key = ""
    if series["scope"] == "location":
        codes = [str(r[0]) for r in db.execute(text("select code from location order by code"))]
        scope_key = next((c for c in codes if c.split("-")[-1] == parts.get("loc")), "")
        if not scope_key:
            return False
    fy = parts.get("fy4") or "all" if series["reset"] == "fy" else "all"
    db.execute(
        text(
            "insert into number_counter (document_type, scope_key, fy, last_seq) values (:t, :s, :f, :q) "
            "on conflict (document_type, scope_key, fy) do update set last_seq = greatest(number_counter.last_seq, :q)"
        ),
        {"t": document_type, "s": scope_key, "f": fy, "q": int(parts["seq"])},
    )
    return True
