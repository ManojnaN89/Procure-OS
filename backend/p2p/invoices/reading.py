"""Invoices from documents: an email attachment or an upload is read into a draft that AP confirms.

The order of work, and what is a model's and what is code's:
1. The text of the document (the text layer of a PDF, else the existing parser with OCR) and the QR code of an e-invoice, both by code.
2. Who sent it. Mail from anyone who is not a known supplier (a contact's address, or the GSTIN on the invoice) is never shown to a model.
3. An e-invoice whose IRN checks out is read by code alone: the header from the QR code, the lines from rows whose arithmetic holds
   (quantity times price is the amount). If that gives lines that add up to the invoice total, no model is called.
4. Anything else is read by the AI step (every value must be found in the text); a reading the checks refuse goes to a person.
5. The lines are mapped to the lines of the supplier's orders, and the draft is only ever as sure as the checks: a clean draft may be entered
   without a person when the autonomy gate 'invoice.read' allows it; everything else waits for AP.
Whatever is entered goes through enter_invoice, so the duplicate check and the three-way match see it exactly as if it had been typed.
"""

import hashlib
import json
import re
from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from decimal import Decimal
from email.utils import parseaddr
from pathlib import Path
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from p2p.agents import autonomy
from p2p.ai_steps import invoice_reading as step
from p2p.ai_steps.gateway import Gateway
from p2p.ai_steps.invoice_reading import InvoiceReading
from p2p.ai_steps.quote_reading import RfqLine, match_line
from p2p.ai_steps.runner import run_step
from p2p.formulas import matching
from p2p.formulas.calendar import IST
from p2p.formulas.gst import Registration
from p2p.formulas.orders import charge_rate, line_amounts
from p2p.invoices import einvoice
from p2p.invoices import service as invoices
from p2p.platform import audit, worker
from p2p.platform.sod import Actor

SERVICE = Actor("service", "invoice_reading")
DECISION_TYPE = "invoice.read"
SUPPORTED = (".pdf", ".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp")
MIN_TEXT = 40  # a PDF with less text than this is a scan: it goes to the OCR parser
RATES = {Decimal(x) for x in ("0", "0.25", "3", "5", "12", "18", "28")}
D = Decimal


class ReadingError(Exception):
    pass


# ---- the text of a document -------------------------------------------------------------------


def extract_text(data: bytes, filename: str) -> str:
    """The text layer of a PDF if it has one (instant), else the platform's parser, which reads scans and photographs."""
    if filename.lower().endswith(".pdf"):
        try:
            import pypdfium2 as pdfium  # type: ignore[import-untyped]

            pdf = pdfium.PdfDocument(data)
            try:
                found = "\n".join(pdf[i].get_textpage().get_text_range() for i in range(len(pdf)))
            finally:
                pdf.close()
            found = found.replace("\r\n", "\n")
            if len(found.strip()) >= MIN_TEXT:
                return found
        except Exception:  # noqa: BLE001, S110 - no text layer: fall back to the full parser
            pass
    from p2p.platform import parsing

    parsed = parsing.parse_document(data, filename)
    return merge_tables(parsed.text, [cells for t in parsed.tables for cells in t.rows])


def merge_tables(page_text: str, table_rows: list[list[str]]) -> str:
    """The page text plus the table rows the OCR found, leaving out a row the page text already has (OCR reports a ruled
    table both ways, and a repeated row would be read as a second item)."""

    def squash(value: str) -> str:
        return re.sub(r"[\s|]+", "", value)

    seen = squash(page_text)
    extra = [" | ".join(cells) for cells in table_rows if squash(" ".join(cells)) not in seen]
    return page_text + "\n" + "\n".join(extra)


# ---- rows by arithmetic -----------------------------------------------------------------------

_NUMBER = re.compile(r"(?<![\w.])\d[\d,]*(?:\.\d+)?")
_SERIAL = re.compile(r"^\s*(\d{1,3})(?:\s*[.)|]\s*|\s+)(?=[A-Za-z])")
_SKIP = re.compile(
    r"^\s*(sub\s*total|total|grand|cgst|sgst|igst|round|amount in words|invoice|irn|gstin|date|bill|ship|bank|terms|remarks)",
    re.I,
)
_CHARGE = re.compile(r"^\s*(freight|packing)", re.I)


@dataclass
class TextLine:
    description: str
    qty: Decimal
    unit_price: Decimal
    taxable: Decimal
    gst_rate: Decimal | None = None
    po_no: str | None = None
    po_line_no: int | None = None


def _numbers(row: str) -> list[tuple[Decimal, int]]:
    return [(D(m.group().replace(",", "")), m.start()) for m in _NUMBER.finditer(row)]


def parse_rows(source: str, po_numbers: list[str]) -> tuple[list[TextLine], dict[str, tuple[Decimal, Decimal | None]]]:
    """The item rows of an invoice and its charges, found by arithmetic: a row is a description followed by a quantity,
    a price and an amount where quantity times price is the amount. A model is not needed, and a value that does not
    make the arithmetic work is not a row."""
    rows: list[TextLine] = []
    charges: dict[str, tuple[Decimal, Decimal | None]] = {}
    for raw in source.splitlines():
        row = raw.strip()
        if len(row) < 6:
            continue
        po_no: str | None = None
        po_line: int | None = None
        for no in po_numbers:
            m = re.search(re.escape(no) + r"(?:\s*/\s*(\d+))?", row, re.I)
            if m:
                po_no, po_line = no, int(m.group(1)) if m.group(1) else None
                row = row.replace(m.group(0), " ")
                break
        charge = _CHARGE.match(row)
        if charge:
            nums = _numbers(row)
            if nums:
                kind = "freight" if charge.group(1).lower() == "freight" else "packing_forwarding"
                rate = next((v for v, _ in nums[1:] if v in RATES), None)
                charges[kind] = (nums[0][0], rate)
            continue
        if _SKIP.match(row):
            continue
        row = _SERIAL.sub("", row)
        nums = _numbers(row)
        # A number in the description ("1/2 inch") can make a small sum work by chance: the real row is the one with
        # the largest amount, and the last of those when two read the same (the quantity sits next to the price).
        found: tuple[int, int, int] | None = None
        for i in range(len(nums)):
            for j in range(i + 1, len(nums)):
                for k in range(j + 1, len(nums)):
                    q, p, a = nums[i][0], nums[j][0], nums[k][0]
                    if q > 0 and p > 0 and abs(q * p - a) <= D("0.02") and (found is None or a >= nums[found[2]][0]):
                        found = (i, j, k)
        if found is None:
            continue
        i, j, k = found
        description = row[: nums[i][1]].strip(" |:-,;")
        if len(re.findall(r"[A-Za-z]", description)) < 2:
            continue
        rate = next((v for idx, (v, _) in enumerate(nums) if idx > j and idx != k and v in RATES), None)
        rows.append(TextLine(description, nums[i][0], nums[j][0], nums[k][0], rate, po_no, po_line))
    return rows, charges


# ---- the draft --------------------------------------------------------------------------------


@dataclass
class DraftLine:
    po_no: str
    po_line_no: int
    description: str
    qty: str
    unit_price: str
    gst_rate: str | None
    taxable: str | None


@dataclass
class Draft:
    supplier_code: str | None = None
    invoice_number: str | None = None
    invoice_date: str | None = None
    doc_type: str = "invoice"
    supplier_gstin: str | None = None
    buyer_gstin: str | None = None
    irn: str | None = None
    eway_bill: str | None = None
    ref_invoice_no: str | None = None
    taxable: str | None = None
    total: str | None = None
    cgst: str | None = None
    sgst: str | None = None
    igst: str | None = None
    lines: list[DraftLine] = field(default_factory=list)
    charges: list[dict[str, Any]] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)
    assumptions: list[str] = field(default_factory=list)


def _json(value: Any) -> str:
    return json.dumps(value, default=str)


def _norm(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", value.casefold())


# ---- who sent it ------------------------------------------------------------------------------


def supplier_by_sender(db: Session, from_addr: str) -> str | None:
    address = parseaddr(from_addr)[1].strip().lower()
    if not address:
        return None
    row = db.execute(
        text("select supplier_code from supplier_contact where lower(email) = :a order by is_primary desc limit 1"),
        {"a": address},
    ).first()
    return str(row[0]) if row else None


def supplier_by_gstin(db: Session, gstin: str | None) -> str | None:
    if not gstin:
        return None
    row = db.execute(
        text(
            "select code from supplier where upper(gstin) = :g union "
            "select supplier_code from supplier_address where upper(gstin) = :g limit 1"
        ),
        {"g": gstin.strip().upper()},
    ).first()
    return str(row[0]) if row else None


def _gstins_in(source: str) -> list[str]:
    return [g for g in dict.fromkeys(re.findall(r"\b\d{2}[A-Z]{5}\d{4}[A-Z]\d[A-Z\d][A-Z\d]\b", source.upper()))]


def _own_gstins(db: Session, supplier: str) -> set[str]:
    rows = db.execute(
        text(
            "select upper(gstin) from supplier where code = :c and gstin is not null union "
            "select upper(gstin) from supplier_address where supplier_code = :c and gstin is not null union "
            "select upper(supplier_gstin) from po where supplier_code = :c and supplier_gstin is not null"
        ),
        {"c": supplier},
    )
    return {r[0] for r in rows}


# ---- mapping lines to order lines -------------------------------------------------------------


@dataclass(frozen=True)
class Candidate:
    po_no: str
    po_line_no: int
    item_code: str | None
    description: str
    available: Decimal
    price: Decimal
    gst_rate: Decimal


def candidates(db: Session, supplier: str) -> list[Candidate]:
    rows = db.execute(
        text(
            "select l.po_no, l.line_no, l.item_code, l.description, l.received_qty - l.invoiced_qty, l.unit_price, l.gst_rate "
            "from po_line l join po on po.no = l.po_no where po.supplier_code = :s and l.status <> 'cancelled' "
            "and po.status not in ('draft', 'submitted', 'cancelled') order by l.po_no, l.line_no"
        ),
        {"s": supplier},
    ).all()
    return [Candidate(r[0], r[1], r[2], r[3], D(r[4]), D(r[5]), D(r[6])) for r in rows]


def _po_numbers_in(source: str, pool: list[Candidate]) -> list[str]:
    flat = _norm(source)
    return [no for no in dict.fromkeys(c.po_no for c in pool) if _norm(no) in flat]


def map_line(
    written: str, po_hints: list[str], po_line: int | None, pool: list[Candidate]
) -> tuple[Candidate | None, str | None, bool]:
    """The order line an invoice line is for: (candidate, problem, assumed). An order number and line number written on the
    line decide; otherwise the words of the description must line up with exactly one line of the orders named, or of the
    supplier's orders when none is named. A line that leaves two equally good choices is not guessed."""
    scope = [c for c in pool if not po_hints or c.po_no in po_hints]
    if po_line is not None:
        exact = [c for c in scope if c.po_line_no == po_line]
        if len(exact) == 1:
            return exact[0], None, False
    if not scope:
        return None, "the order named on the invoice is not an order of this supplier", False
    shown = [RfqLine(i, c.item_code, c.description, c.available) for i, c in enumerate(scope)]
    hit = match_line(written, shown)
    if hit is not None:
        return scope[hit.line_no], None, False
    if len(scope) == 1:
        return scope[0], None, True
    return None, f"could not tell which order line '{written}' is", False


# ---- the checks that make a draft ------------------------------------------------------------


def _supplier_facts(db: Session, supplier: str) -> tuple[str | None, str | None, bool]:
    row = db.execute(
        text("select gst_reg_type, state_code, e_invoice_applicable from supplier where code = :c"), {"c": supplier}
    ).first()
    return (row[0], row[1], bool(row[2])) if row else (None, None, False)


def _expected_total(db: Session, d: Draft) -> Decimal | None:
    """What the invoice should come to by our rules for tax: the lines and charges, their tax, rounded to the rupee."""
    if not d.supplier_code or not d.lines:
        return None
    reg_type, state, _ = _supplier_facts(db, d.supplier_code)
    state = state or (d.supplier_gstin or "")[:2] or None
    place = (d.buyer_gstin or "")[:2] or None
    if not state or not place:
        return None
    registration = Registration(reg_type or "regular")
    total = D(0)
    for ln in d.lines:
        calc = line_amounts(
            D(ln.qty), D(ln.unit_price), D(ln.gst_rate) if ln.gst_rate is not None else D(0), state, place, registration
        )
        total += calc.taxable + calc.cgst + calc.sgst + calc.igst
    for ch in d.charges:
        calc = line_amounts(1, D(ch["amount"]), D(ch["rate"] or 0), state, place, registration)
        total += calc.taxable + calc.cgst + calc.sgst + calc.igst
    return total


def _finish(db: Session, d: Draft, pool: list[Candidate], printed_total: Decimal | None) -> Draft:
    """The checks every draft passes through, whichever way it was read."""
    unrated = [c for c in d.charges if c["rate"] is None]
    if (
        unrated
    ):  # freight and packing charged with the goods take the highest rate among the lines (the rule for a mixed supply)
        top = charge_rate([D(ln.gst_rate) for ln in d.lines if ln.gst_rate is not None])
        for c in unrated:
            c["rate"] = str(top)
        d.assumptions.append(
            f"the {unrated[0]['type'].replace('_', ' ')} was taxed at {top:f}%, the highest rate of the lines"
        )
    if not d.invoice_number:
        d.problems.append("the invoice number could not be read")
    if not d.invoice_date:
        d.problems.append("the invoice date could not be read")
    if d.doc_type == "invoice" and not d.lines:
        d.problems.append("no line of the invoice could be matched to an order line")
    if d.doc_type == "credit_note" and not d.ref_invoice_no:
        d.problems.append("a credit note must say which invoice it is for")
    if d.supplier_code and d.supplier_gstin:
        own = _own_gstins(db, d.supplier_code)
        if own and d.supplier_gstin.upper() not in own:
            other = supplier_by_gstin(db, d.supplier_gstin)
            d.problems.append(
                f"the GSTIN {d.supplier_gstin} is "
                + (
                    f"that of {other}, not of {d.supplier_code}"
                    if other
                    else f"not one registered for {d.supplier_code}"
                )
            )
    if d.supplier_code and d.doc_type == "invoice" and not (d.irn or "").strip():
        if _supplier_facts(db, d.supplier_code)[2] and _supplier_facts(db, d.supplier_code)[0] == "regular":
            d.assumptions.append("this supplier must issue e-invoices and the document shows no IRN")
    if printed_total is not None and d.lines:
        expected = _expected_total(db, d)
        if expected is None:  # a total that cannot be checked is not a clean draft
            d.assumptions.append("the total could not be checked against the lines (the buyer's GSTIN was not read)")
        elif abs(expected.quantize(D("1")) - printed_total) > D("1.00"):
            d.problems.append(
                f"the lines come to {expected.quantize(D('1')):,.0f} with GST by our rules, the invoice says {printed_total:,.2f}"
            )
    return d


def _build_lines(
    db: Session,
    d: Draft,
    rows: list[dict[str, Any]],
    pool: list[Candidate],
    doc_pos: list[str],
) -> None:
    """Map each read row to an order line. `rows` have description, qty, unit_price, taxable, gst_rate, po_no, po_line_no."""
    seen: dict[tuple[str, int], str] = {}
    for r in rows:
        po_hints = [r["po_no"]] if r.get("po_no") else doc_pos
        hit, problem, assumed = map_line(r["description"], po_hints, r.get("po_line_no"), pool)
        if hit is None:
            d.problems.append(problem or f"could not map '{r['description']}'")
            continue
        key = (hit.po_no, hit.po_line_no)
        if key in seen:
            d.problems.append(
                f"{hit.po_no} line {hit.po_line_no} appears twice on the invoice ('{seen[key]}' and '{r['description']}')"
            )
            continue
        seen[key] = r["description"]
        if assumed:
            d.assumptions.append(
                f"'{r['description']}' was taken to be {hit.po_no} line {hit.po_line_no}, the only line open"
            )
        qty, price, taxable = r.get("qty"), r.get("unit_price"), r.get("taxable")
        if qty is None or price is None:
            d.problems.append(f"the quantity or price of '{r['description']}' is not on the invoice")
            continue
        if taxable is not None and abs(D(qty) * D(price) - D(taxable)) > D("0.02"):
            d.problems.append(f"'{r['description']}': {qty} x {price} is not the amount {taxable} printed")
            continue
        rate = r.get("gst_rate")
        if rate is None:
            rate = hit.gst_rate
            d.assumptions.append(f"'{r['description']}': no GST rate on the invoice; the order's {rate:f}% was assumed")
        d.lines.append(
            DraftLine(
                hit.po_no,
                hit.po_line_no,
                r["description"],
                str(qty),
                str(price),
                str(rate),
                str(taxable) if taxable is not None else None,
            )
        )


def _ref_invoice(db: Session, supplier: str, written: str | None) -> str | None:
    if not written:
        return None
    row = db.execute(
        text(
            "select no from invoice where supplier_code = :s and dedupe_key = :k and type = 'invoice' order by created_at limit 1"
        ),
        {"s": supplier, "k": matching.normalize_invoice_no(written)},
    ).first()
    return str(row[0]) if row else None


def draft_from_qr(
    db: Session, supplier: str, qr: einvoice.EInvoice, source: str, pool: list[Candidate]
) -> Draft | None:
    """The whole invoice by code: header from the QR code, lines from rows that add up. None if that is not enough."""
    if not qr.irn_ok or qr.doc_type not in ("INV", "CRN") or not (qr.doc_no and qr.doc_date and qr.total is not None):
        return None
    doc_pos = _po_numbers_in(source, pool)
    rows, charges = parse_rows(source, doc_pos)
    if not rows or (qr.item_count is not None and len(rows) != qr.item_count):
        return None
    d = Draft(
        supplier_code=supplier,
        invoice_number=qr.doc_no,
        invoice_date=qr.doc_date.isoformat(),
        doc_type="credit_note" if qr.doc_type == "CRN" else "invoice",
        supplier_gstin=qr.seller_gstin,
        buyer_gstin=qr.buyer_gstin,
        irn=qr.irn,
        total=str(qr.total),
    )
    d.charges = [
        {"type": k, "amount": str(v[0]), "rate": str(v[1]) if v[1] is not None else None} for k, v in charges.items()
    ]
    _build_lines(db, d, [asdict(r) for r in rows], pool, doc_pos)
    if d.doc_type == "credit_note":
        d.ref_invoice_no = None  # the QR code does not say which invoice; a person (or the printed text) must
    return _finish(db, d, pool, qr.total)


def draft_from_reading(
    db: Session,
    supplier: str,
    reading: InvoiceReading,
    qr: einvoice.EInvoice | None,
    source: str,
    pool: list[Candidate],
) -> Draft:
    d = Draft(
        supplier_code=supplier,
        invoice_number=reading.invoice_number,
        invoice_date=reading.invoice_date.isoformat() if reading.invoice_date else None,
        doc_type="credit_note" if reading.document_type == "credit_note" else "invoice",
        supplier_gstin=(reading.supplier_gstin or "").upper() or None,
        buyer_gstin=(reading.buyer_gstin or "").upper() or None,
        irn=reading.irn,
        eway_bill=reading.eway_bill,
        taxable=str(reading.taxable_total) if reading.taxable_total is not None else None,
        total=str(reading.total) if reading.total is not None else None,
        cgst=str(reading.cgst) if reading.cgst is not None else None,
        sgst=str(reading.sgst) if reading.sgst is not None else None,
        igst=str(reading.igst) if reading.igst is not None else None,
    )
    if reading.document_type == "other":
        d.problems.append("the model does not take this document for an invoice")
    for kind, amount in (("freight", reading.freight), ("packing_forwarding", reading.packing_forwarding)):
        if amount:
            d.charges.append({"type": kind, "amount": str(amount), "rate": None})
    if qr is not None:
        if qr.doc_no and reading.invoice_number and _norm(qr.doc_no) != _norm(reading.invoice_number):
            d.problems.append(
                f"the invoice number {reading.invoice_number} differs from the one in the QR code, {qr.doc_no}"
            )
        if qr.total is not None and reading.total is not None and abs(qr.total - reading.total) > D("1.00"):
            d.problems.append(f"the total {reading.total} differs from the QR code's {qr.total}")
        if qr.seller_gstin and d.supplier_gstin and qr.seller_gstin != d.supplier_gstin:
            d.problems.append("the supplier GSTIN printed differs from the one in the QR code")
        if not d.irn and qr.irn:
            d.irn = qr.irn
    doc_pos = list(
        dict.fromkeys(
            [*[n for n in reading.po_numbers if any(c.po_no == n for c in pool)], *_po_numbers_in(source, pool)]
        )
    )
    rows = [
        {
            "description": ln.description, "qty": ln.qty, "unit_price": ln.unit_price, "taxable": ln.taxable,
            "gst_rate": ln.gst_rate, "po_no": ln.po_no if ln.po_no and any(c.po_no == ln.po_no for c in pool) else None,
            "po_line_no": None,
        }
        for ln in reading.lines
    ]  # fmt: skip
    _build_lines(db, d, rows, pool, doc_pos)
    if reading.ref_invoice_no and d.doc_type == "credit_note":
        d.ref_invoice_no = _ref_invoice(db, supplier, reading.ref_invoice_no)
        if d.ref_invoice_no is None:
            d.problems.append(f"the invoice {reading.ref_invoice_no} that the credit note refers to is not on file")
    return _finish(db, d, pool, reading.total)


# ---- storing, entering --------------------------------------------------------------------------


def _store(
    db: Session,
    *,
    source: str,
    email_id: int | None,
    document_id: int | None,
    filename: str | None,
    document_hash: str,
    supplier: str | None,
    status: str,
    path: str,
    draft: Draft | None = None,
    reading: InvoiceReading | None = None,
    qr: einvoice.EInvoice | None = None,
    problems: list[str] | None = None,
    confidence: float | None = None,
    model: str | None = None,
    created_by: str | None = None,
) -> int:
    all_problems = [*(problems or []), *(draft.problems if draft else [])]
    return int(
        db.execute(
            text(
                "insert into invoice_draft (source, email_id, document_id, filename, document_hash, supplier_code, status, path, "
                "reading, qr, proposal, problems, assumptions, confidence, model, prompt_version, created_by) values "
                "(:s, :e, :d, :f, :h, :sup, :st, :p, cast(:rd as jsonb), cast(:q as jsonb), cast(:pr as jsonb), cast(:pb as jsonb), "
                "cast(:a as jsonb), :c, :m, :pv, :by) returning id"
            ),
            {
                "s": source, "e": email_id, "d": document_id, "f": filename, "h": document_hash, "sup": supplier, "st": status,
                "p": path, "rd": _json(reading.model_dump(mode="json")) if reading else None,
                "q": _json(qr.as_json()) if qr else None, "pr": _json(asdict(draft)) if draft else None,
                "pb": _json(all_problems), "a": _json(draft.assumptions if draft else []), "c": confidence, "m": model,
                "pv": step.PROMPT_VERSION if reading else None, "by": created_by,
            },
        ).scalar_one()
    )  # fmt: skip


def read_document(
    db: Session,
    tenant: str,
    data: bytes,
    filename: str,
    *,
    source: str,
    gateway: Gateway,
    as_of: date,
    email_id: int | None = None,
    document_id: int | None = None,
    sender: str | None = None,
    supplier_code: str | None = None,
    user_id: int = 0,
    received_at: datetime | None = None,
) -> int:
    """Read one document into a draft. Returns the draft's id (the earlier one if this file was read before)."""
    digest = hashlib.sha256(data).hexdigest()
    earlier = db.execute(
        text(
            "select id from invoice_draft where document_hash = :h and status in ('pending', 'needs_person', 'applied')"
        ),
        {"h": digest},
    ).scalar()
    if earlier:
        return int(earlier)
    common: dict[str, Any] = {
        "source": source, "email_id": email_id, "document_id": document_id, "filename": filename, "document_hash": digest,
        "created_by": str(user_id) if user_id else None,
    }  # fmt: skip

    def finish(**kw: Any) -> int:
        draft_id = _store(db, **common, **kw)
        audit.record(
            db, SERVICE, "invoice_draft.create", "invoice_draft", draft_id,
            after={"status": kw["status"], "path": kw["path"], "supplier": kw.get("supplier"), "file": filename},
            model=kw.get("model"), prompt_version=step.PROMPT_VERSION if kw.get("reading") else None,
        )  # fmt: skip
        db.commit()
        return draft_id

    try:
        source_text = extract_text(data, filename)
    except Exception as exc:  # noqa: BLE001 - an unreadable file becomes a task for a person, not a failure
        return finish(
            supplier=supplier_code,
            status="needs_person",
            path="none",
            problems=[f"the document could not be read: {type(exc).__name__}"],
        )
    qr = einvoice.find_einvoice(data, filename)

    supplier = supplier_code or (supplier_by_sender(db, sender) if sender else None)
    via = "chosen" if supplier_code else "sender"
    if supplier is None:
        found = supplier_by_gstin(db, qr.seller_gstin if qr else None) or next(
            (s for g in _gstins_in(source_text) if (s := supplier_by_gstin(db, g))), None
        )
        supplier, via = found, "GSTIN"
    if supplier is None:
        return finish(
            supplier=None, status="needs_person", path="none", qr=qr,
            problems=["the sender is not a known supplier and the GSTIN on the document is not one on file, so no model read it"],
        )  # fmt: skip
    if qr and qr.seller_gstin and supplier_by_gstin(db, qr.seller_gstin) not in (None, supplier):
        return finish(
            supplier=supplier, status="needs_person", path="none", qr=qr,
            problems=[f"the QR code names {qr.seller_gstin}, the GSTIN of another supplier; it came from {supplier} ({via})"],
        )  # fmt: skip
    if qr and not qr.irn_ok:
        return finish(
            supplier=supplier, status="needs_person", path="none", qr=qr,
            problems=["the IRN in the QR code is not the one this invoice's own details give: it was altered or is another document's"],
        )  # fmt: skip

    pool = candidates(db, supplier)
    draft = draft_from_qr(db, supplier, qr, source_text, pool) if qr else None
    reading: InvoiceReading | None = None
    model: str | None = None
    confidence: float | None = None
    path = "qr"
    if draft is None or draft.problems:
        outcome = run_step(
            db, tenant, step.STEP, source_text[: step.MAX_SOURCE_CHARS], gateway=gateway, review_assignee=_ap_user(db),
            entity="invoice_document", entity_id=digest[:12],
        )  # fmt: skip
        model = f"{outcome.completion.provider}/{outcome.completion.model}" if outcome.completion else None
        if outcome.status != "ok" or not isinstance(outcome.value, InvoiceReading):
            return finish(
                supplier=supplier, status="needs_person", path="model", qr=qr, problems=outcome.failures, model=model,
                reading=outcome.value if isinstance(outcome.value, InvoiceReading) else None,
            )  # fmt: skip
        reading, path = outcome.value, "model"
        confidence = reading.confidence
        draft = draft_from_reading(db, supplier, reading, qr, source_text, pool)
    else:
        confidence = 1.0
    status = "pending" if not draft.problems else "needs_person"
    draft_id = finish(
        supplier=supplier,
        status=status,
        path=path,
        draft=draft,
        reading=reading,
        qr=qr,
        confidence=confidence,
        model=model,
    )
    if via != "GSTIN":  # a document from someone who is not a known contact of the supplier is always seen by a person
        _maybe_apply(db, tenant, draft_id, draft, confidence, as_of, received_at)
    return draft_id


def _ap_user(db: Session) -> int:
    """Someone to give the review task to when a reading is not reliable: an AP user, else anyone."""
    found = db.execute(text("select user_id from user_profile where role = 'AP' order by user_id limit 1")).scalar()
    if not found:
        found = db.execute(text("select user_id from user_profile order by user_id limit 1")).scalar()
    return int(found or 0)


def to_invoice_in(d: dict[str, Any], channel: str, received_at: datetime | None) -> invoices.InvoiceIn:
    def dec(v: Any) -> Decimal | None:
        return D(str(v)) if v not in (None, "") else None

    return invoices.InvoiceIn(
        supplier_code=d["supplier_code"],
        supplier_invoice_no=d["invoice_number"],
        invoice_date=date.fromisoformat(d["invoice_date"]),
        type=d.get("doc_type") or "invoice",
        supplier_gstin=d.get("supplier_gstin"),
        buyer_gstin=d.get("buyer_gstin"),
        irn=d.get("irn"),
        eway_bill_no=d.get("eway_bill"),
        ref_invoice_no=d.get("ref_invoice_no"),
        channel=channel,
        received_at=received_at,
        taxable=dec(d.get("taxable")),
        cgst=dec(d.get("cgst")),
        sgst=dec(d.get("sgst")),
        igst=dec(d.get("igst")),
        total=dec(d.get("total")),
        lines=[
            invoices.InvLine(
                po_no=ln["po_no"], po_line_no=int(ln["po_line_no"]), qty=D(str(ln["qty"])), unit_price=D(str(ln["unit_price"])),
                gst_rate=dec(ln.get("gst_rate")), taxable=dec(ln.get("taxable")), description=ln.get("description"),
            )
            for ln in d.get("lines", [])
        ],
        charges=[invoices.InvCharge(c["type"], D(str(c["amount"])), D(str(c.get("rate") or 0))) for c in d.get("charges", [])],
    )  # fmt: skip


def _maybe_apply(
    db: Session,
    tenant: str,
    draft_id: int,
    draft: Draft,
    confidence: float | None,
    as_of: date,
    received_at: datetime | None,
) -> None:
    """A clean draft is entered without a person only when the autonomy gate allows it. Anything in doubt waits for AP."""
    if draft.problems or draft.assumptions or not draft.lines:
        return
    total = D(draft.total) if draft.total else sum((D(ln.qty) * D(ln.unit_price) for ln in draft.lines), D(0))
    if not autonomy.gate(db, DECISION_TYPE, value=total, confidence=confidence).allowed:
        return
    confirm_draft(db, draft_id, 0, as_of=as_of, automatic=True, received_at=received_at)
    autonomy.record_outcome(db, DECISION_TYPE, auto=True, ref=str(draft_id))


# ---- the person's decision --------------------------------------------------------------------


def _locked(db: Session, draft_id: int) -> dict[str, Any]:
    row = db.execute(text("select * from invoice_draft where id = :i for update"), {"i": draft_id}).mappings().first()
    if row is None:
        raise ReadingError(f"draft {draft_id} does not exist")
    return dict(row)


def confirm_draft(
    db: Session,
    draft_id: int,
    user_id: int,
    *,
    as_of: date,
    edits: dict[str, Any] | None = None,
    automatic: bool = False,
    received_at: datetime | None = None,
) -> invoices.Entered:
    """Enter the invoice of a draft. `edits` lets AP correct what was read: header fields (supplier_code, invoice_number,
    invoice_date, ref_invoice_no) and lines by position (qty, unit_price, po_no, po_line_no). The three-way match does the rest."""
    row = _locked(db, draft_id)
    if row["status"] not in ("pending", "needs_person"):
        raise ReadingError(f"draft {draft_id} is {row['status']}; only an open draft can be confirmed")
    proposal = dict(row["proposal"] or {})
    if not proposal:
        raise ReadingError("nothing was read from this document: enter the invoice by hand")
    for key in ("supplier_code", "invoice_number", "invoice_date", "ref_invoice_no", "doc_type"):
        if edits and edits.get(key) not in (None, ""):
            proposal[key] = edits[key]
    lines = proposal.get("lines", [])
    for pos, change in ((edits or {}).get("lines") or {}).items():
        ln = lines[int(pos)]
        for key in ("qty", "unit_price", "po_no", "po_line_no"):
            if change.get(key) not in (None, ""):
                ln[key] = change[key]
        ln["taxable"] = None  # the amount printed no longer describes the corrected line
    if edits and edits.get("lines"):
        proposal["total"] = None
    if row["status"] == "needs_person" and not edits and not automatic and proposal.get("problems"):
        raise ReadingError("this draft has problems: correct them, or enter the invoice by hand")
    if not proposal.get("supplier_code") or not proposal.get("invoice_number") or not proposal.get("invoice_date"):
        raise ReadingError("the supplier, the invoice number and the date are needed")
    data = to_invoice_in(proposal, "email" if row["source"] == "email" else "upload", received_at)
    try:
        entered = invoices.enter_invoice(db, data, user_id, as_of=as_of)
    except invoices.InvoiceError:
        db.rollback()
        raise
    who = "service:invoice_reading" if automatic else f"user:{user_id}"
    if automatic:  # no person entered it: the invoice says so (the audit row of this confirmation explains why)
        db.execute(text("update invoice set entered_by = 'invoice_reading' where no = :n"), {"n": entered.no})
    db.execute(
        text(
            "update invoice_draft set status = 'applied', invoice_no = :n, decided_by = :by, decided_at = now() where id = :i"
        ),
        {"n": entered.no, "by": who, "i": draft_id},
    )
    audit.record(
        db, SERVICE if automatic else Actor("user", str(user_id)), "invoice_draft.confirm", "invoice_draft", draft_id,
        after={"invoice": entered.no, "status": entered.status, "edited": bool(edits), "automatic": automatic},
    )  # fmt: skip
    db.commit()
    return entered


def dismiss_draft(db: Session, draft_id: int, user_id: int, note: str | None = None) -> None:
    row = _locked(db, draft_id)
    if row["status"] in ("applied", "dismissed"):
        raise ReadingError(f"draft {draft_id} is already {row['status']}")
    db.execute(
        text(
            "update invoice_draft set status = 'dismissed', decided_by = :by, decided_at = now(), decision_note = :n where id = :i"
        ),
        {"by": f"user:{user_id}", "n": note, "i": draft_id},
    )
    audit.record(db, Actor("user", str(user_id)), "invoice_draft.dismiss", "invoice_draft", draft_id, reason=note)
    db.commit()


# ---- email ------------------------------------------------------------------------------------


def is_invoice_mailbox(db: Session, to_addrs: list[str]) -> bool:
    box = db.execute(text("select value from org_setting where key = 'invoice_receipt_mailbox'")).scalar()
    if not box:
        return False
    return str(box).strip().lower() in {parseaddr(a)[1].strip().lower() for a in to_addrs}


def read_email(db: Session, tenant: str, email_id: int, gateway: Gateway, *, as_of: date) -> list[int]:
    """Read every document attached to a mail sent to the invoice mailbox. Returns the drafts' ids."""
    from p2p.platform import storage

    mail = (
        db.execute(
            text(
                "select id, from_addr, subject, sent_at, attachment_ids from email_message where id = :i and direction = 'in'"
            ),
            {"i": email_id},
        )
        .mappings()
        .first()
    )
    if mail is None:
        return []
    ids: list[int] = []
    sent = mail["sent_at"]
    documents = [int(x) for x in (mail["attachment_ids"] or [])]
    readable = 0
    for doc_id in documents:
        row = db.execute(text("select filename, storage_key from document where id = :i"), {"i": doc_id}).first()
        if row is None or Path(row[0]).suffix.lower() not in SUPPORTED:
            continue
        if db.execute(
            text("select 1 from invoice_draft where email_id = :e and document_id = :d"), {"e": email_id, "d": doc_id}
        ).first():
            continue
        readable += 1
        ids.append(
            read_document(
                db,
                tenant,
                storage.get_storage().get(tenant, row[1]),
                row[0],
                source="email",
                gateway=gateway,
                as_of=as_of,
                email_id=email_id,
                document_id=doc_id,
                sender=mail["from_addr"],
                received_at=sent,
            )
        )
    already = db.execute(text("select 1 from invoice_draft where email_id = :e limit 1"), {"e": email_id}).first()
    if readable == 0 and not already:
        draft = _store(
            db, source="email", email_id=email_id, document_id=None, filename=None, document_hash=None,  # type: ignore[arg-type]
            supplier=supplier_by_sender(db, mail["from_addr"] or ""), status="needs_person", path="none",
            problems=[f"the mail '{mail['subject']}' has no invoice document (PDF or image) attached"],
        )  # fmt: skip
        db.commit()
        ids.append(draft)
    return ids


@worker.register("read_invoice_email")
def _read_email_job(ctx: worker.JobContext) -> None:
    from p2p.ai_steps.gateway import Gateway as RealGateway
    from p2p.platform import routing

    with Session(routing.engine_for(ctx.tenant)) as db:  # a person sees every draft that needs one on the Invoices page
        read_email(db, ctx.tenant, int(ctx.payload["email_id"]), RealGateway(), as_of=ctx.now.astimezone(IST).date())
