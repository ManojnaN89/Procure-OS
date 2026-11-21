"""Reference data loader and lookups (tax rates, HSN/SAC codes, holidays, TDS sections).

The files in data/reference are loaded into every tenant database when the tenant is created. GST rates found
here are a fallback: a rate stored on the item wins. Rows that came from a rule with exceptions ("other than ...")
are marked needs_review.
"""

import csv
import json
import re
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from p2p.config import REPO_ROOT, get_settings
from p2p.formulas.calendar import BusinessCalendar

GOODS_RATE_START = date(2025, 9, 22)
EARLIEST = date(2017, 7, 1)  # GST began on this date
_EXCEPTION = re.compile(r"other than|except|\bany\b|\[|\(", re.IGNORECASE)


def reference_dir() -> Path:
    configured = get_settings().reference_dir
    return Path(configured) if configured else REPO_ROOT / "data" / "reference"


def _rows(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def _dec(value: str | None) -> Decimal | None:
    value = (value or "").strip()
    return Decimal(value) if value else None


def _rate(value: str) -> tuple[Decimal, bool]:
    """First number in a rate cell such as '5|12' or '5 or 18'; the flag says the cell offered alternatives."""
    numbers = re.findall(r"\d+(?:\.\d+)?", value)
    if not numbers:
        raise ValueError(f"no rate in '{value}'")
    return Decimal(numbers[0]), len(numbers) > 1


def _date(value: str | None) -> date | None:
    value = (value or "").strip()
    return date.fromisoformat(value) if value else None


def parse_hsn_prefixes(raw: str) -> tuple[list[str], bool]:
    """Turn the HSN text of a GST notification line into code prefixes.

    Returns the prefixes and whether the text carried an exception or condition, in which case a person should review.
    """
    review = bool(_EXCEPTION.search(raw))
    cleaned = re.sub(r"[\[(].*?[\])]|[\[(].*$", " ", raw)
    cleaned = re.sub(r"\bor\s*any\b.*|\bany\s+chapter\b", " ", cleaned, flags=re.IGNORECASE)
    prefixes: list[str] = []

    def take_range(m: re.Match[str]) -> str:
        a, b = re.sub(r"\D", "", m.group(1)), re.sub(r"\D", "", m.group(2))
        if len(a) == len(b) and len(a) <= 4 and a <= b:
            prefixes.extend(str(n).zfill(len(a)) for n in range(int(a), int(b) + 1))
        else:
            prefixes.extend(p for p in (a, b) if p)
        return " "

    cleaned = re.sub(r"(\d[\d ]*?)\s+to\s+(\d[\d ]*)", take_range, cleaned, flags=re.IGNORECASE)
    for part in re.split(r",|\bor(?=\s|\d)", cleaned, flags=re.IGNORECASE):
        digits = re.sub(r"\D", "", part)
        if len(digits) in (2, 4, 6, 8):
            prefixes.append(digits)
    return list(dict.fromkeys(prefixes)), review


def load_reference(db: Session, directory: Path | None = None) -> dict[str, int]:
    """Replace the reference tables of one tenant database with the contents of the reference files."""
    d = directory or reference_dir()
    counts: dict[str, int] = {}
    for table in (
        "ref_gst_state",
        "ref_uom",
        "ref_hsn_sac",
        "ref_gst_rate",
        "ref_holiday",
        "ref_tds_section",
        "ref_rbi_bank_rate",
    ):
        db.execute(text(f"delete from {table}"))  # noqa: S608 - fixed table names

    def insert(table: str, cols: list[str], rows: list[dict[str, Any]]) -> None:
        if rows:
            names = ", ".join(cols)
            values = ", ".join(f":{c}" for c in cols)
            db.execute(text(f"insert into {table} ({names}) values ({values})"), rows)  # noqa: S608 - fixed names
        counts[table] = len(rows)

    insert("ref_gst_state", ["code", "name"], _rows(d / "gst_state_codes.csv"))
    insert("ref_uom", ["code", "name"], _rows(d / "gst_uqc_units.csv"))
    hsn = {(r["code"], r["kind"]): r for r in _rows(d / "hsn_sac_codes.csv")}
    insert(
        "ref_hsn_sac",
        ["code", "kind", "level", "description"],
        [{k: r[k] for k in ("code", "kind", "level", "description")} for r in hsn.values()],
    )

    rates: list[dict[str, Any]] = []
    for r in _rows(d / "gst_goods_rates_2025-09-22.csv"):
        igst = _dec(r["igst_rate"])
        if igst is None:
            continue
        prefixes, review = parse_hsn_prefixes(r["hsn_raw"])
        for p in prefixes:
            rates.append(
                dict(
                    kind="goods",
                    code_prefix=p,
                    description=r["description"][:300],
                    cgst=_dec(r["cgst_rate"]) or igst / 2,
                    sgst=_dec(r["sgst_rate"]) or igst / 2,
                    igst=igst,
                    effective_from=GOODS_RATE_START,
                    effective_to=None,
                    reverse_charge=False,
                    needs_review=review,
                    source=r["source"][:200],
                )
            )
    for r in _rows(d / "gst_rate_history_catalogue.csv"):
        kind = "services" if r["kind"].startswith("service") else "goods"
        rc = r["reverse_charge"].strip().lower().startswith("yes")
        review = r["confidence"].strip().upper() != "CONFIRMED"
        before, alt_b = _rate(r["rate_before_pct"])
        after, alt_a = _rate(r["rate_after_pct"])
        review = review or alt_b or alt_a
        common = dict(
            kind=kind,
            code_prefix=r["hsn_sac"],
            description=r["example_item"][:300],
            reverse_charge=rc,
            needs_review=review,
            source=r["source"][:200],
        )
        if r["changed"].strip().lower() == "yes":
            rates.append(
                dict(
                    common,
                    cgst=before / 2,
                    sgst=before / 2,
                    igst=before,
                    effective_from=EARLIEST,
                    effective_to=date(2025, 9, 21),
                )
            )
            rates.append(
                dict(
                    common,
                    cgst=after / 2,
                    sgst=after / 2,
                    igst=after,
                    effective_from=GOODS_RATE_START,
                    effective_to=None,
                )
            )
        else:
            rates.append(
                dict(common, cgst=after / 2, sgst=after / 2, igst=after, effective_from=EARLIEST, effective_to=None)
            )
    for r in _rows(d / "gst_services_rates.csv"):
        rate, alternatives = _rate(r["gst_rate"])
        m = re.match(r"\s*(\d+)", r["sac"])
        if not m:
            continue
        rates.append(
            dict(
                kind="services",
                code_prefix=m.group(1),
                description=r["service"][:300],
                cgst=rate / 2,
                sgst=rate / 2,
                igst=rate,
                effective_from=_date(r["effective_from"]) or EARLIEST,
                effective_to=_date(r["effective_to"]),
                reverse_charge=r["reverse_charge"].strip().lower().startswith("yes"),
                needs_review=alternatives or r["status"].strip().upper() != "CONFIRMED",
                source=r["source"][:200],
            )
        )
    insert(
        "ref_gst_rate",
        [
            "kind",
            "code_prefix",
            "description",
            "cgst",
            "sgst",
            "igst",
            "effective_from",
            "effective_to",
            "reverse_charge",
            "needs_review",
            "source",
        ],
        rates,
    )

    holidays: dict[tuple[date, str, str], dict[str, Any]] = {}
    for fname, scope_col in (
        ("holidays_central_2024_2025.csv", None),
        ("holidays_2026_central_gazetted.csv", None),
        ("holidays_state_MH_GJ_KA_2024_2026.csv", "state_code"),
    ):
        for r in _rows(d / fname):
            scope = r[scope_col] if scope_col else "central"
            when = date.fromisoformat(r["date"])
            holidays[(when, scope, r["name"][:200])] = dict(
                holiday_date=when, name=r["name"][:200], kind=r.get("type") or "closed", scope=scope
            )
    insert("ref_holiday", ["holiday_date", "name", "kind", "scope"], list(holidays.values()))

    tds = [
        dict(
            act=r["act"],
            section=r["section"],
            nature=r["nature"],
            payee_type=r["payee_type"],
            rate_pct=_dec(r["rate_pct"]),
            rate_no_pan_pct=_dec(r["rate_no_pan_pct"]),
            threshold_single=_dec(r["threshold_single_inr"]),
            threshold_annual=_dec(r["threshold_annual_inr"]),
            base_rule=r["base_rule"],
            effective_from=_date(r["effective_from"]),
            effective_to=_date(r["effective_to"]),
            status=r["status"],
        )
        for r in _rows(d / "tds_sections.csv")
    ]
    insert(
        "ref_tds_section",
        [
            "act",
            "section",
            "nature",
            "payee_type",
            "rate_pct",
            "rate_no_pan_pct",
            "threshold_single",
            "threshold_annual",
            "base_rule",
            "effective_from",
            "effective_to",
            "status",
        ],
        tds,
    )
    bank_rates = d / "rbi_bank_rate.csv"
    if bank_rates.exists():
        insert(
            "ref_rbi_bank_rate",
            ["effective_from", "rate_pct"],
            [
                {"effective_from": _date(r["effective_from"]), "rate_pct": _dec(r["rate_pct"])}
                for r in _rows(bank_rates)
            ],
        )
    db.execute(text("insert into ref_load (files) values (cast(:f as jsonb))"), {"f": json.dumps(counts)})
    db.commit()
    return counts


@dataclass(frozen=True)
class RateLookup:
    igst: Decimal
    cgst: Decimal
    sgst: Decimal
    reverse_charge: bool
    needs_review: bool
    code_prefix: str


def gst_rate_for(db: Session, code: str, on: date, kind: str | None = None) -> RateLookup | None:
    """The rate for an HSN or SAC code on a date: the longest matching prefix wins, then the latest start date."""
    row = db.execute(
        text(
            "select igst, cgst, sgst, reverse_charge, needs_review, code_prefix from ref_gst_rate "
            "where :code like code_prefix || '%' and effective_from <= :d "
            "and (effective_to is null or effective_to >= :d) "
            "and (cast(:k as text) is null or kind = :k) "
            "order by length(code_prefix) desc, needs_review asc, effective_from desc limit 1"
        ),
        {"code": code, "d": on, "k": kind},
    ).first()
    if row is None:
        return None
    return RateLookup(Decimal(row[0]), Decimal(row[1]), Decimal(row[2]), bool(row[3]), bool(row[4]), str(row[5]))


def holidays_for(db: Session, state_code: str | None = None) -> set[date]:
    """Dates a location is closed: central holidays plus its state's. Half days and optional days do not count."""
    rows = db.execute(
        text("select holiday_date from ref_holiday where kind = 'closed' and (scope = 'central' or scope = :s)"),
        {"s": state_code},
    )
    return {r[0] for r in rows}


def calendar_for(db: Session, state_code: str | None = None, *, saturday_working: bool = False) -> BusinessCalendar:
    return BusinessCalendar.of(holidays_for(db, state_code), saturday_working=saturday_working)


def calendar_for_location(db: Session, location_code: str | None) -> BusinessCalendar:
    """The business calendar of one of the company's places: its working week (Mon-Sat or Mon-Fri) and the central and
    its state's holidays. A place not on record falls back to the default calendar."""
    row = db.execute(
        text("select state_code, working_days from location where code = :c"), {"c": location_code}
    ).first()
    if row is None:
        return calendar_for(db)
    return calendar_for(db, row[0], saturday_working="sat" in str(row[1] or "").lower())
