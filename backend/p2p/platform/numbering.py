"""Document numbers: REQ-VAD-2627-00001 and the like. The format of each series is data (the number_series table);
this module only counts. The counter is bumped in the caller's transaction, so a rolled-back document gives its
number back and two callers never get the same one."""

from datetime import date

from sqlalchemy import text
from sqlalchemy.orm import Session

from p2p.formulas.fy import fy_start_year


class NoSuchSeries(Exception):
    pass


def fy4(d: date) -> str:
    """'2627' for any date in the financial year 2026-27."""
    y = fy_start_year(d)
    return f"{y % 100:02d}{(y + 1) % 100:02d}"


def next_number(db: Session, document_type: str, on_date: date, *, location_code: str | None = None) -> str:
    series = (
        db.execute(
            text("select prefix, scope, format, reset from number_series where document_type = :t"),
            {"t": document_type},
        )
        .mappings()
        .first()
    )
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
