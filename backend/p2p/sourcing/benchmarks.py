"""Market benchmarks (D52, task 13.1): entered one by one or uploaded in our template, verified by a second person.

Every record is evidence only. It carries its source, effective date, region and unit, and stays unverified until a
person with `benchmark.verify` who did not enter it verifies or rejects it. A verified record is never edited: a
correction is a new record, and the old one is rejected as replaced. Whether a record counts on a date is
`formulas/benchmarks.standing`. A newly verified record asks the price-gap detector to look at its item.
"""

import csv
import io
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from p2p.approvals.workflow import org_code_of
from p2p.formulas import benchmarks as rules
from p2p.formulas.calendar import business_today
from p2p.formulas.scope import Scope
from p2p.masters import regions
from p2p.masters.importer import ImportReport, read_table
from p2p.masters.schema import Table
from p2p.platform import audit
from p2p.platform.sod import user

SOURCE_KINDS = ("index", "price_list", "market_survey", "other")
BASES = ("delivered", "ex_works")
COLUMNS = (
    ("item_code", "text"),
    ("price", "numeric(18,4)"),
    ("uom", "text"),
    ("basis", "text"),
    ("effective_date", "date"),
    ("region_code", "text"),
    ("source_kind", "text"),
    ("source_name", "text"),
    ("source_ref", "text"),
    ("note", "text"),
)
TEMPLATE = Table("market_benchmark", "market_benchmark", COLUMNS, None, ())
OPTIONAL = frozenset({"basis", "region_code", "source_ref", "note"})


class BenchmarkError(Exception):
    def __init__(self, message: str, status: int = 422) -> None:
        super().__init__(message)
        self.status = status


def person(db: Session, user_id: int) -> str:
    """Who acted, as the organisation knows them (their person code), else their login."""
    return org_code_of(db, user_id) or f"user:{user_id}"


def _problem(db: Session, r: dict[str, Any], today: date) -> str | None:
    """Why a benchmark cannot be stored, or None."""
    item = db.execute(
        text("select base_uom, purchase_uom from item where code = :c"), {"c": r.get("item_code")}
    ).first()
    if item is None:
        return f"unknown item {r.get('item_code')}"
    price = r.get("price")
    if price is None or price <= 0:
        return "price must be more than zero"
    units = {str(u[0]) for u in db.execute(text("select code from ref_uom"))} | {str(item[0]), str(item[1])}
    if not r.get("uom") or r["uom"] not in units:
        return f"unit {r.get('uom') or '(none)'} is not known"
    if r.get("effective_date") is None:
        return "an effective date is needed"
    if r["effective_date"] > today:
        return "the effective date is in the future"
    if not (r.get("source_name") or "").strip():
        return "the source is missing"
    if r.get("source_kind") not in SOURCE_KINDS:
        return f"source kind must be one of {', '.join(SOURCE_KINDS)}"
    if (r.get("basis") or "delivered") not in BASES:
        return "basis must be delivered or ex_works"
    if (
        r.get("region_code")
        and db.execute(text("select 1 from region where code = :c"), {"c": r["region_code"]}).first() is None
    ):
        return f"unknown region {r['region_code']}"
    return None


def _existing(db: Session, r: dict[str, Any]) -> dict[str, Any] | None:
    row = (
        db.execute(
            text(
                "select id, price, status from market_benchmark where item_code = :i and source_name = :s "
                "and effective_date = :d and region_code is not distinct from :r and status <> 'rejected'"
            ),
            {"i": r["item_code"], "s": r["source_name"].strip(), "d": r["effective_date"], "r": r.get("region_code")},
        )
        .mappings()
        .first()
    )
    return dict(row) if row else None


def _insert(db: Session, r: dict[str, Any], by: str, replaces: int | None = None) -> int:
    return int(
        db.execute(
            text(
                "insert into market_benchmark (item_code, price, uom, basis, effective_date, region_code, source_kind, "
                "source_name, source_ref, document_id, note, status, entered_by, replaces) values (:i, :p, :u, :b, :d, :r, "
                ":k, :s, :ref, :doc, :n, 'unverified', :by, :rep) returning id"
            ),
            {
                "i": r["item_code"],
                "p": Decimal(r["price"]),
                "u": r["uom"],
                "b": r.get("basis") or "delivered",
                "d": r["effective_date"],
                "r": r.get("region_code") or None,
                "k": r["source_kind"],
                "s": r["source_name"].strip(),
                "ref": r.get("source_ref") or None,
                "doc": r.get("document_id"),
                "n": r.get("note") or None,
                "by": by,
                "rep": replaces,
            },  # fmt: skip
        ).scalar_one()
    )


def add(db: Session, data: dict[str, Any], user_id: int, *, today: date | None = None) -> int:
    """Enter one benchmark (unverified). With `replaces`, it corrects an earlier record, which is rejected as replaced."""
    today = today or business_today()
    if problem := _problem(db, data, today):
        raise BenchmarkError(problem)
    old_id = data.get("replaces")
    old = None
    if old_id is not None:
        old = (
            db.execute(text("select * from market_benchmark where id = :i for update"), {"i": old_id})
            .mappings()
            .first()
        )
        if old is None:
            raise BenchmarkError(f"benchmark {old_id} does not exist", 404)
        if old["status"] == "rejected":
            raise BenchmarkError(f"benchmark {old_id} is already rejected", 409)
    elif _existing(db, data) is not None:
        raise BenchmarkError("that benchmark (item, source, date and region) is already recorded", 409)
    by = person(db, user_id)
    if old is not None:  # the old record leaves the key free for its correction
        db.execute(
            text("update market_benchmark set status = 'rejected', rejected_reason = :r where id = :i"),
            {"r": "replaced", "i": old_id},
        )
    bid = _insert(db, data, by, old_id)
    if old is not None:
        db.execute(
            text("update market_benchmark set rejected_reason = :r where id = :i"),
            {"r": f"replaced by #{bid}", "i": old_id},
        )
        audit.record(
            db, user(user_id), "benchmark.reject", "market_benchmark", old_id,
            before={"status": old["status"]}, after={"status": "rejected"}, reason=f"replaced by #{bid}",
        )  # fmt: skip
    audit.record(
        db, user(user_id), "benchmark.add", "market_benchmark", bid,
        after={k: data.get(k) for k in ("item_code", "price", "uom", "effective_date", "region_code", "source_name")},
    )  # fmt: skip
    db.commit()
    return bid


def edit(db: Session, bid: int, data: dict[str, Any], user_id: int) -> None:
    """Correct an unverified record. A verified (or rejected) one is never edited: enter a correction instead."""
    old = db.execute(text("select * from market_benchmark where id = :i for update"), {"i": bid}).mappings().first()
    if old is None:
        raise BenchmarkError(f"benchmark {bid} does not exist", 404)
    if old["status"] != "unverified":
        raise BenchmarkError(f"benchmark {bid} is {old['status']}: it is never edited; enter a correction", 409)
    merged = {**dict(old), **{k: v for k, v in data.items() if k in dict(COLUMNS)}}
    if problem := _problem(db, merged, business_today()):
        raise BenchmarkError(problem)
    db.execute(
        text(
            "update market_benchmark set price = :p, uom = :u, basis = :b, effective_date = :d, region_code = :r, "
            "source_kind = :k, source_name = :s, source_ref = :ref, note = :n where id = :i"
        ),
        {
            "p": merged["price"],
            "u": merged["uom"],
            "b": merged["basis"],
            "d": merged["effective_date"],
            "r": merged["region_code"],
            "k": merged["source_kind"],
            "s": merged["source_name"],
            "ref": merged["source_ref"],
            "n": merged["note"],
            "i": bid,
        },  # fmt: skip
    )
    audit.record(db, user(user_id), "benchmark.edit", "market_benchmark", bid, before=dict(old), after=merged)
    db.commit()


def _decide(db: Session, bid: int, user_id: int) -> dict[str, Any]:
    row = db.execute(text("select * from market_benchmark where id = :i for update"), {"i": bid}).mappings().first()
    if row is None:
        raise BenchmarkError(f"benchmark {bid} does not exist", 404)
    if row["status"] != "unverified":
        raise BenchmarkError(f"benchmark {bid} is already {row['status']}", 409)
    if row["entered_by"] in (person(db, user_id), f"user:{user_id}"):
        raise BenchmarkError("segregation of duties: whoever entered a benchmark does not verify it", 403)
    return dict(row)


def verify(db: Session, bid: int, user_id: int, note: str | None) -> str:
    """Verify a benchmark: who, when and a note. Returns its item, for the price-gap detector."""
    row = _decide(db, bid, user_id)
    db.execute(
        text(
            "update market_benchmark set status = 'verified', verified_by = :by, verified_at = now(), verify_note = :n "
            "where id = :i"
        ),
        {"by": person(db, user_id), "n": (note or "").strip() or None, "i": bid},
    )
    audit.record(
        db, user(user_id), "benchmark.verify", "market_benchmark", bid,
        before={"status": "unverified"}, after={"status": "verified"}, reason=note,
    )  # fmt: skip
    db.commit()
    return str(row["item_code"])


def reject(db: Session, bid: int, user_id: int, reason: str) -> None:
    if not reason.strip():
        raise BenchmarkError("a reason is needed")
    _decide(db, bid, user_id)
    db.execute(
        text(
            "update market_benchmark set status = 'rejected', rejected_reason = :r, verified_by = :by, verified_at = now() where id = :i"
        ),
        {"r": reason.strip(), "by": person(db, user_id), "i": bid},
    )
    audit.record(
        db, user(user_id), "benchmark.reject", "market_benchmark", bid,
        before={"status": "unverified"}, after={"status": "rejected"}, reason=reason,
    )  # fmt: skip
    db.commit()


# ---- the upload ----------------------------------------------------------------------------------


@dataclass
class UploadReport:
    loaded: int = 0
    skipped: int = 0  # the same row loaded before
    changed: list[dict[str, Any]] = field(default_factory=list)  # loaded before with another price: not applied
    rejected: list[dict[str, Any]] = field(default_factory=list)


def template_csv() -> str:
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\n")
    writer.writerow([c for c, _ in COLUMNS])
    writer.writerow(["RM-01", "628.00", "NOS", "delivered", "2026-10-31", "MH", "price_list", "Source name", "", ""])
    return out.getvalue()


def upload(db: Session, path: Path, user_id: int, *, today: date | None = None) -> UploadReport:
    """Load a sheet in our template: every good row becomes an unverified record; bad rows come back with the reason.
    A row loaded before is skipped; one loaded before with a different price is listed as changed and not applied."""
    today = today or business_today()
    report = ImportReport()
    rows = read_table(path, TEMPLATE, report, optional=OPTIONAL)
    if report.rejected and report.rejected[0].line <= 1:
        raise BenchmarkError(f"use the template: {report.rejected[0].reason}")
    result = UploadReport(rejected=[{"line": p.line, "reason": p.reason} for p in report.rejected])
    by = person(db, user_id)
    seen: set[tuple[Any, ...]] = set()
    for line, r in rows:
        if problem := _problem(db, r, today):
            result.rejected.append({"line": line, "reason": problem})
            continue
        key = (r["item_code"], r["source_name"].strip(), r["effective_date"], r.get("region_code"))
        before = _existing(db, r)
        if before is not None or key in seen:
            if before is not None and Decimal(before["price"]) != Decimal(r["price"]):
                result.changed.append({"line": line, "reason": "already loaded; changed", "id": before["id"]})
            else:
                result.skipped += 1
            continue
        seen.add(key)
        _insert(db, r, by)
        result.loaded += 1
    audit.record(
        db, user(user_id), "benchmark.upload", "market_benchmark", path.name,
        after={"loaded": result.loaded, "skipped": result.skipped, "changed": len(result.changed),
               "rejected": len(result.rejected)},
    )  # fmt: skip
    db.commit()
    return result


# ---- reading -------------------------------------------------------------------------------------


def conversions(db: Session, item: str) -> dict[tuple[str, str], Decimal]:
    return {
        (str(r[0]), str(r[1])): Decimal(r[2])
        for r in db.execute(
            text("select from_uom, to_uom, factor from uom_conversion where item_code = :i and factor > 0"), {"i": item}
        )
    }


def as_rule(r: dict[str, Any]) -> rules.Benchmark:
    return rules.Benchmark(
        int(r["id"]), Decimal(r["price"]), str(r["uom"]), r["effective_date"], r["region_code"], str(r["status"])
    )


def records(
    db: Session, *, item: str | None = None, status: str | None = None, region: str | None = None
) -> list[dict[str, Any]]:
    clauses, params = ["true"], {}
    if item:
        clauses.append("b.item_code = :item")
        params["item"] = item
    if status:
        clauses.append("b.status = :status")
        params["status"] = status
    if region:
        clauses.append("b.region_code = :region")
        params["region"] = region
    return [
        dict(r)
        for r in db.execute(
            text(
                "select b.*, i.name as item_name from market_benchmark b join item i on i.code = b.item_code "  # noqa: S608
                f"where {' and '.join(clauses)} order by b.item_code, b.effective_date desc, b.id desc"
            ),
            params,
        ).mappings()
    ]


def described(
    db: Session, rows: list[dict[str, Any]], *, on: date, max_age_days: int, scope: Scope | None, uom: str | None
) -> list[dict[str, Any]]:
    """Each record for the screen, with its age and whether it counts (for a price of `uom` in `scope`)."""
    region_of, words = regions.region_of(db), regions.names(db)
    out = []
    for r in rows:
        b = as_rule(r)
        conv = conversions(db, str(r["item_code"]))
        s = rules.standing(
            b, on=on, max_age_days=max_age_days, scope=scope or Scope(), region_of=region_of,
            uom=uom or b.uom, conversions=conv,
        )  # fmt: skip
        out.append(
            {
                "id": r["id"], "item_code": r["item_code"], "item_name": r.get("item_name"),
                "price": str(r["price"]), "uom": r["uom"], "basis": r["basis"],
                "effective_date": r["effective_date"].isoformat(), "region": r["region_code"],
                "region_name": words.get(r["region_code"]) if r["region_code"] else "National",
                "source_kind": r["source_kind"], "source_name": r["source_name"], "source_ref": r["source_ref"],
                "note": r["note"], "status": r["status"], "entered_by": r["entered_by"],
                "entered_at": _iso(r["entered_at"]), "verified_by": r["verified_by"],
                "verified_at": _iso(r["verified_at"]), "rejected_reason": r["rejected_reason"],
                "replaces": r["replaces"], "age_days": s.age_days, "counts": s.counts, "why": s.reason,
            }
        )  # fmt: skip
    return out


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def parse_price(value: Any) -> Decimal | None:
    try:
        return Decimal(str(value)) if value not in (None, "") else None
    except InvalidOperation:
        return None
