"""GSTR-2B: read the portal's file or our CSV, compare it with our books, keep the result and raise one task.

The comparison changes nothing: no invoice, block, due date or run (D-19, "no holding"). A second file for the same
GSTIN and month is kept and becomes the current comparison; the same file twice returns the first result.
"""

import csv
import hashlib
import io
import json
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from p2p.formulas import gstr2b as rules
from p2p.formulas.calendar import business_today
from p2p.formulas.money import format_inr
from p2p.formulas.words import count
from p2p.invoices import tolerances
from p2p.platform import audit, tasks, users
from p2p.platform.sod import user

TEMPLATE = "supplier_gstin,type,doc_no,doc_date,taxable,igst,cgst,sgst,cess,reverse_charge,itc_available,reason\n"
MAX_LINES = 5000
KIND = "gstr2b_differences"
SLA_HOURS = 40


class Gstr2bError(Exception):
    def __init__(self, message: str, status: int = 422) -> None:
        super().__init__(message)
        self.status = status


@dataclass
class Parsed:
    gstin: str
    period: str
    generated_on: date | None
    docs: list[rules.Doc] = field(default_factory=list)
    unreadable: list[dict[str, Any]] = field(default_factory=list)


def _d(value: Any) -> Decimal:
    if value in (None, ""):
        return Decimal(0)
    if isinstance(value, bool):
        return Decimal(1 if value else 0)
    try:
        return Decimal(str(value))
    except InvalidOperation:
        return Decimal(0)


def _day(value: Any) -> date | None:
    if value in (None, ""):
        return None
    text_value = str(value).strip()
    for fmt in ("%d-%m-%Y", "%Y-%m-%d", "%d/%m/%Y", "%d-%b-%Y", "%Y%m%d"):
        try:
            return datetime.strptime(text_value[:10], fmt).date()  # noqa: DTZ007 - a calendar date
        except ValueError:
            continue
    return None


def _type(value: Any) -> str:
    raw = str(value or "R").upper()
    return {"R": "invoice", "C": "credit_note", "D": "debit_note", "CR": "credit_note", "DR": "debit_note"}.get(
        raw, "invoice"
    )


def _period(value: Any) -> str:
    raw = str(value or "").strip()
    if len(raw) == 6 and raw.isdigit():  # mmyyyy
        return f"{raw[2:]}-{raw[:2]}"
    if len(raw) == 6 and raw[:4].isdigit():  # yyyymm
        return f"{raw[:4]}-{raw[4:]}"
    return ""


def _invoice(
    ctin: str, inv: dict[str, Any], unreadable: list[dict[str, Any]], n: int, amendments: bool
) -> rules.Doc | None:
    number = inv.get("inum") or (inv.get("oinum") if amendments else None)
    on = _day(inv.get("dt") or (inv.get("oidt") if amendments else None))
    if not number or on is None:
        unreadable.append({"row": n, "why": "the document has no number or date"})
        return None
    return rules.Doc(
        gstin=str(ctin),
        doc_type=_type(inv.get("typ") or inv.get("doc_type") or "R"),
        doc_no=str(number),
        on=on,
        taxable=_d(inv.get("txval")),
        igst=_d(inv.get("igst")),
        cgst=_d(inv.get("cgst")),
        sgst=_d(inv.get("sgst")),
        cess=_d(inv.get("cess")),
        itc_available=str(inv.get("itcavl", "Y")).upper() != "N",
        itc_reason=str(inv.get("rsn") or "") or None,
        reverse_charge=str(inv.get("rchrg") or "N").upper() == "Y",
    )


def parse_json(data: bytes) -> Parsed:
    try:
        raw = json.loads(data.decode("utf-8-sig"), parse_float=Decimal, parse_int=Decimal)
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise Gstr2bError("the file is not JSON in the portal's shape") from None
    body = raw.get("data") if isinstance(raw, dict) and isinstance(raw.get("data"), dict) else raw
    if not isinstance(body, dict):
        raise Gstr2bError("the file is not JSON in the portal's shape")
    gstin = str(body.get("gstin") or "").strip()
    if not gstin:
        raise Gstr2bError("the file does not name our GSTIN")
    parsed = Parsed(gstin, _period(body.get("rtnprd")), _day(body.get("gendt")))
    docdata = body.get("docdata") or {}
    n = 0
    for section, key, amendments in (("b2b", "inv", False), ("b2ba", "inv", True)):
        for party in docdata.get(section) or []:
            ctin = str(party.get("ctin") or party.get("gstin") or "")
            for inv in party.get(key) or []:
                n += 1
                doc = _invoice(ctin, inv, parsed.unreadable, n, amendments)
                if doc:
                    parsed.docs.append(doc)
    for party in docdata.get("cdnr") or []:
        ctin = str(party.get("ctin") or party.get("gstin") or "")
        for note in party.get("nt") or []:
            n += 1
            doc = _invoice(ctin, note, parsed.unreadable, n, False)
            if doc:
                doc = rules.Doc(
                    doc.gstin, _type(note.get("ntty")), doc.doc_no, doc.on, doc.taxable, doc.igst, doc.cgst,
                    doc.sgst, doc.cess, doc.itc_available, doc.itc_reason,
                )  # fmt: skip
                parsed.docs.append(doc)
    if len(parsed.docs) > MAX_LINES:
        raise Gstr2bError(f"a GSTR-2B file can have at most {MAX_LINES} documents")
    if not parsed.docs:
        raise Gstr2bError("no document of the file could be read")
    if not parsed.period:
        parsed.period = f"{parsed.docs[-1].on.year}-{parsed.docs[-1].on.month:02d}"
    return parsed


def parse_csv(data: bytes) -> Parsed:
    text_value = data.decode("utf-8-sig")
    rows = list(csv.DictReader(io.StringIO(text_value)))
    if not rows:
        raise Gstr2bError("the file has no rows")
    header = {k.strip().lower() for k in rows[0]}
    if not {"supplier_gstin", "doc_no", "doc_date", "taxable"} <= header:
        raise Gstr2bError(f"the file needs the columns of the template: {TEMPLATE.strip()}")
    parsed = Parsed("", "", None)
    for n, row in enumerate(rows, start=2):
        on = _day(row.get("doc_date"))
        number = (row.get("doc_no") or "").strip()
        if on is None or not number:
            parsed.unreadable.append({"row": n, "why": "the document has no number or date"})
            continue
        gstin = (row.get("supplier_gstin") or "").strip()
        if not gstin:
            parsed.unreadable.append({"row": n, "why": "the supplier GSTIN is missing"})
            continue
        parsed.gstin = parsed.gstin or ""
        itc = (row.get("itc_available") or "Y").strip().upper() != "N"
        parsed.docs.append(
            rules.Doc(
                gstin=gstin,
                doc_type=str(row.get("type") or "invoice").strip().lower().replace(" ", "_"),
                doc_no=number,
                on=on,
                taxable=_d(row.get("taxable")),
                igst=_d(row.get("igst")),
                cgst=_d(row.get("cgst")),
                sgst=_d(row.get("sgst")),
                cess=_d(row.get("cess")),
                itc_available=itc,
                itc_reason=(row.get("reason") or "").strip() or None,
                reverse_charge=(row.get("reverse_charge") or "false").strip().lower() in ("true", "y", "yes", "1"),
            )
        )
    if not parsed.docs:
        raise Gstr2bError("no document of the file could be read")
    parsed.period = f"{parsed.docs[-1].on.year}-{parsed.docs[-1].on.month:02d}"
    return parsed


def parse(data: bytes, filename: str) -> Parsed:
    return parse_json(data) if Path(filename).suffix.lower() in (".json", "") else parse_csv(data)


def _matched_before(db: Session, gstin: str) -> set[str]:
    return {
        str(r[0])
        for r in db.execute(
            text(
                "select our_invoice_no from gstr2b_line l join gstr2b_import i on i.id = l.import_id "
                "where i.gstin = :g and i.current and l.outcome in ('matched', 'differs') "
                "and l.our_invoice_no is not null"
            ),
            {"g": gstin},
        )
    }


def books(db: Session, gstin: str, period: str, *, exclude: set[str] | None = None) -> list[rules.Doc]:
    """Our invoices, credit and debit notes to this GSTIN, dated by the month's end, not yet matched before."""
    year, month = int(period[:4]), int(period[5:])
    first_next = date(year + 1, 1, 1) if month == 12 else date(year, month + 1, 1)
    end = first_next - timedelta(days=1)
    rows = db.execute(
        text(
            "select i.no, i.type, i.supplier_invoice_no, i.invoice_date, i.taxable, i.igst, i.cgst, i.sgst, i.cess, "
            "coalesce(i.supplier_gstin, s.gstin) as supplier_gstin from invoice i join supplier s on s.code = i.supplier_code "
            "where i.buyer_gstin = :g and i.status <> 'cancelled' and i.invoice_date <= :end "
            "and i.type in ('invoice', 'credit_note', 'debit_note') and coalesce(i.supplier_gstin, s.gstin) is not null "
            "order by i.invoice_date, i.no"
        ),
        {"g": gstin, "end": end},
    ).mappings()
    skip = exclude or set()
    return [
        rules.Doc(
            str(r["supplier_gstin"]),
            str(r["type"]),
            str(r["supplier_invoice_no"]),
            r["invoice_date"],
            Decimal(r["taxable"]),
            Decimal(r["igst"]),
            Decimal(r["cgst"]),
            Decimal(r["sgst"]),
            Decimal(r["cess"]),
            our_invoice_no=str(r["no"]),
        )
        for r in rows
        if str(r["no"]) not in skip
    ]


def _summary(db: Session, import_id: int) -> dict[str, Any]:
    head = dict(db.execute(text("select * from gstr2b_import where id = :i"), {"i": import_id}).mappings().one())
    rows = list(
        db.execute(
            text(
                "select supplier_gstin, doc_type, doc_no, doc_date, taxable, igst, cgst, sgst, cess, reverse_charge, "
                "itc_available, itc_reason, our_invoice_no, outcome, differences, reason from gstr2b_line "
                "where import_id = :i order by id"
            ),
            {"i": import_id},
        ).mappings()
    )
    return {**head, "results": [dict(r) for r in rows]}


def import_file(
    db: Session, data: bytes, filename: str, user_id: int, *, tenant: str | None = None, today: date | None = None
) -> dict[str, Any]:
    """Read, compare and keep one GSTR-2B file; one task for AP when anything is not matched (R4.1-R4.6)."""
    today = today or business_today()
    digest = hashlib.sha256(data).hexdigest()
    earlier = db.execute(text("select id from gstr2b_import where file_hash = :h"), {"h": digest}).scalar()
    if earlier:
        return {**_summary(db, int(earlier)), "already_imported": True, "task_id": None}
    parsed = parse(data, filename)
    if not parsed.gstin:
        parsed.gstin = str(
            db.execute(text("select gstin from gst_registration order by is_default desc, gstin limit 1")).scalar()
            or ""
        )
    if not parsed.gstin:
        raise Gstr2bError("the file does not name our GSTIN and the company has no GST registration")
    known = db.execute(text("select 1 from gst_registration where gstin = :g"), {"g": parsed.gstin}).first()
    if known is None:
        raise Gstr2bError(f"{parsed.gstin} is not one of our GSTINs")
    tolerance_set = tolerances.in_force(db, today)
    tolerance = tolerance_set.decimal("gstr2b_diff_abs", "1.00")
    ours = books(db, parsed.gstin, parsed.period, exclude=_matched_before(db, parsed.gstin))
    results = rules.compare(ours, parsed.docs, tolerance=tolerance, period=parsed.period)
    counts = rules.counts(results)
    import_id = db.execute(
        text(
            "insert into gstr2b_import (gstin, period, generated_on, file_name, file_hash, tolerance_version, "
            "documents, outcomes, current, imported_by) values (:g, :p, :d, :f, :h, :v, :n, cast(:o as jsonb), true, :u) "
            "returning id"
        ),
        {
            "g": parsed.gstin, "p": parsed.period, "d": parsed.generated_on or today, "f": filename[:200],
            "h": digest, "v": tolerance_set.version, "n": len(parsed.docs), "o": json.dumps(counts),
            "u": str(user_id),
        },
    ).scalar_one()  # fmt: skip
    db.execute(
        text("update gstr2b_import set current = false where gstin = :g and period = :p and id <> :i"),
        {"g": parsed.gstin, "p": parsed.period, "i": import_id},
    )
    for r in results:
        doc = r.doc
        db.execute(
            text(
                "insert into gstr2b_line (import_id, supplier_gstin, doc_type, doc_no, doc_date, taxable, igst, cgst, "
                "sgst, cess, reverse_charge, itc_available, itc_reason, our_invoice_no, outcome, differences, reason) "
                "values (:i, :g, :t, :n, :d, :tx, :ig, :cg, :sg, :ce, :rc, :itc, :why, :our, :o, cast(:df as jsonb), "
                ":reason)"
            ),
            {
                "i": import_id, "g": doc.gstin, "t": doc.doc_type, "n": doc.doc_no, "d": doc.on,
                "tx": doc.taxable, "ig": doc.igst, "cg": doc.cgst, "sg": doc.sgst, "ce": doc.cess,
                "rc": doc.reverse_charge, "itc": doc.itc_available, "why": doc.itc_reason, "our": r.our_invoice_no,
                "o": r.outcome, "df": json.dumps(list(r.differences)), "reason": r.reason,
            },
        )  # fmt: skip
    task_id = None
    if any(r.outcome != "matched" for r in results) and tenant:
        listed = sorted({r.doc.gstin for r in results if r.outcome != "matched"})
        title = (
            f"GSTR-2B {parsed.period}: {count(sum(v for k, v in counts.items() if k != 'matched'), 'difference')}"
            if results
            else "GSTR-2B"
        )
        task_id = tasks.create_task(
            db, tenant, kind=KIND, title=title, assignee=users.first_with_role(db, "AP", "Finance"), sla_hours=SLA_HOURS,
            payload={
                "import_id": int(import_id), "gstin": parsed.gstin, "period": parsed.period, "counts": counts,
                "suppliers": listed,
                "next_step": "ask " + ", ".join(listed[:5]) + " to file or correct the documents",
            }, actor=user(user_id),
        )  # fmt: skip
    audit.record(
        db, user(user_id), "gstr2b.import", "gstr2b_import", str(import_id),
        after={"gstin": parsed.gstin, "period": parsed.period, "documents": len(parsed.docs), **counts},
    )  # fmt: skip
    db.commit()
    return {**_summary(db, int(import_id)), "already_imported": False, "unreadable": parsed.unreadable,
            "task_id": task_id}  # fmt: skip


def current(db: Session, gstin: str | None = None, period: str | None = None) -> list[dict[str, Any]]:
    return [
        dict(r)
        for r in db.execute(
            text(
                "select id, gstin, period, generated_on, file_name, documents, outcomes, current, imported_by, "
                "imported_at from gstr2b_import where current "
                "and (cast(:g as text) is null or gstin = :g) and (cast(:p as text) is null or period = :p) "
                "order by period desc, gstin"
            ),
            {"g": gstin, "p": period},
        ).mappings()
    ]


def one(db: Session, import_id: int) -> dict[str, Any] | None:
    found = db.execute(text("select 1 from gstr2b_import where id = :i"), {"i": import_id}).first()
    return _summary(db, import_id) if found else None


def not_in_2b(db: Session, as_of: date, gstin: str | None = None) -> list[dict[str, Any]]:
    """Our invoices not yet in any current GSTR-2B, oldest first, with how many months they have waited (R4.7)."""
    out: list[dict[str, Any]] = []
    for r in db.execute(
        text(
            "select i.no, i.supplier_code, coalesce(s.trade_name, s.legal_name) as supplier, "
            "i.invoice_date, i.total, i.buyer_gstin "
            "from invoice i join supplier s on s.code = i.supplier_code where i.type = 'invoice' "
            "and i.status <> 'cancelled' and i.invoice_date <= :d "
            "and (cast(:g as text) is null or i.buyer_gstin = :g) "
            "and not exists (select 1 from gstr2b_line l join gstr2b_import x on x.id = l.import_id "
            "where l.our_invoice_no = i.no and l.outcome in ('matched', 'differs') and x.current) "
            "order by i.invoice_date, i.no"
        ),
        {"d": as_of, "g": gstin},
    ).mappings():
        months = max((as_of.year * 12 + as_of.month) - (r["invoice_date"].year * 12 + r["invoice_date"].month), 0)
        out.append(
            {
                "invoice": r["no"], "supplier": r["supplier"], "supplier_code": r["supplier_code"],
                "date": r["invoice_date"], "total": r["total"], "months": months,
                "gstin": r["buyer_gstin"],
            }
        )  # fmt: skip
    return out


def words(summary: dict[str, Any]) -> str:
    return format_inr(summary.get("total") or 0)
