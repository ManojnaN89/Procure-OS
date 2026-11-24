"""Synthetic documents from a generated dataset: supplier invoices (PDF and pictures) and supplier replies to requests for
quotation (.eml), with a manifest that says what each document should be read as.

    python -I datagen/generator/documents.py --dataset data/datasets/apex-v1 --seed 42 [--limit N]

Run it with the backend's Python (backend/.venv): it needs Pillow, numpy and zxing-cpp for the pictures and the QR codes.
Output goes to <dataset>/documents/ (git-ignored with the rest of data/datasets/):

    invoices/<invoice no>.pdf|png|jpg     one file per invoice of the dataset, in one of six looks
    quotes/<email id>_<rfq>_<supplier>.eml  one file per supplier reply to an RFQ: a quote or a refusal
    manifest.json                          the answer key, taken from the dataset's own tables

The key is never computed from the rendered document: every field comes from the CSV rows, so a reader can be scored
against it independently. Same dataset and seed give the same files. Everything is made up: PANs and GSTINs carry 0000,
phones are +91 00000 ..., emails end in .example (`unsafe_text` checks every document before it is written).
"""

import argparse
import csv
import hashlib
import json
import re
import shutil
import sys
from collections import defaultdict
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal
from email import policy
from email.message import EmailMessage
from email.utils import format_datetime
from pathlib import Path
from random import Random
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
import pagekit as pk  # noqa: E402
from pagekit import Page, Picture, Rule, Text  # noqa: E402

D = Decimal
GENERATOR_VERSION = "documents-v1"

# Six looks for an invoice, interleaved so that even a small --limit shows several of them.
INVOICE_LOOKS = [
    "text_pdf", "scan_pdf", "multipage_pdf", "photo_png", "text_pdf", "skewed_jpg", "low_contrast_pdf", "text_pdf",
    "multipage_pdf", "scan_pdf", "text_pdf", "photo_png", "text_pdf", "skewed_jpg", "low_contrast_pdf", "text_pdf",
    "multipage_pdf", "scan_pdf", "text_pdf", "text_pdf",
]  # fmt: skip
DIFFICULTY = {
    "text_pdf": "clean", "multipage_pdf": "medium", "scan_pdf": "medium",
    "photo_png": "hard", "skewed_jpg": "hard", "low_contrast_pdf": "hard",
}  # fmt: skip
QUOTE_FORMATS = ["pdf_attachment", "body_text", "body_table"]
# Kept back to measure on: two layouts of each kind of document, and every document of the suppliers that the dataset's
# splits.json holds out. Tune on the 'dev' documents, report on the 'hold_out' ones.
HELD_OUT_LAYOUTS = {"invoice": ("skewed_jpg", "low_contrast_pdf"), "quote_reply": ("body_table", "decline")}
QUOTE_QUIRKS = [
    "none",
    "none",
    "partial",
    "none",
    "unit_differs",
    "none",
    "incl_gst",
    "none",
    "relative_validity",
    "none",
]


# ---- reading the dataset ------------------------------------------------------------------------


def split_of(entry: dict[str, Any], hold_out_suppliers: set[str]) -> str:
    """'hold_out' for a held-out layout or a held-out supplier, else 'dev'."""
    supplier = entry.get("supplier_code") or entry["expected"]["supplier_code"]  # an invoice keeps it in its key
    if supplier in hold_out_suppliers or entry["variant"] in HELD_OUT_LAYOUTS[entry["kind"]]:
        return "hold_out"
    return "dev"


def table(ds: Path, folder: str, name: str) -> list[dict[str, str]]:
    path = ds / folder / f"{name}.csv"
    if not path.exists():
        return []
    with path.open(encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def money(x: Decimal) -> str:
    """Rupees with Indian grouping: 12,34,567.89."""
    s = f"{abs(x.quantize(D('0.01'), ROUND_HALF_UP)):.2f}"
    whole, frac = s.split(".")
    if len(whole) > 3:
        head, tail, groups = whole[:-3], whole[-3:], []
        while len(head) > 2:
            groups.insert(0, head[-2:])
            head = head[:-2]
        if head:
            groups.insert(0, head)
        whole = ",".join([*groups, tail])
    return f"{'-' if x < 0 else ''}{whole}.{frac}"


def qty_text(q: str) -> str:
    return f"{D(q).normalize():f}"


_ONES = ("zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen seventeen "
         "eighteen nineteen").split()  # fmt: skip
_TENS = "_ _ twenty thirty forty fifty sixty seventy eighty ninety".split()


def _words(n: int) -> str:
    if n < 20:
        return _ONES[n]
    if n < 100:
        return _TENS[n // 10] + (f"-{_ONES[n % 10]}" if n % 10 else "")
    if n < 1000:
        return f"{_ONES[n // 100]} hundred" + (f" {_words(n % 100)}" if n % 100 else "")
    for size, name in ((10_000_000, "crore"), (100_000, "lakh"), (1000, "thousand")):
        if n >= size:
            return f"{_words(n // size)} {name}" + (f" {_words(n % size)}" if n % size else "")
    return ""


def amount_in_words(total: Decimal) -> str:
    total = total.quantize(D("0.01"), ROUND_HALF_UP)
    rupees, paise = int(total), int((total - int(total)) * 100)
    out = f"Rupees {_words(rupees)}" + (f" and {_words(paise)} paise" if paise else "") + " only"
    return out[0].upper() + out[1:]


# ---- keeping it safe ----------------------------------------------------------------------------

_PAN = re.compile(r"\b[A-Z]{5}\d{4}[A-Z]\b")
_GSTIN = re.compile(r"\b\d{2}([A-Z]{5}\d{4}[A-Z])[1-9A-Z]Z[0-9A-Z]\b")
_PHONE = re.compile(r"(?<!\d)(?:\+91|0091)[ -]?\d{5}[ -]?\d{5}(?!\d)")
_EMAIL = re.compile(r"[\w.+-]+@([\w-]+(?:\.[\w-]+)*)")


def unsafe_text(text: str) -> list[str]:
    """What in a text could belong to a real person or company: a PAN or GSTIN without 0000, a phone not +91 00000, an
    email outside .example. Empty means safe."""
    bad = [f"PAN {m}" for m in _PAN.findall(text) if "0000" not in m]
    bad += [f"GSTIN {m.group(0)}" for m in _GSTIN.finditer(text) if "0000" not in m.group(1)]
    bad += [f"phone {m}" for m in _PHONE.findall(text) if not re.sub(r"^(\+91|0091)[ -]?", "", m).startswith("00000")]
    bad += [f"email {m.group(0)}" for m in _EMAIL.finditer(text) if not m.group(1).endswith(".example")]
    return bad


# ---- the e-invoice QR code ----------------------------------------------------------------------


def qr_payload(inv: dict[str, str], lines: list[dict[str, str]]) -> str:
    """The text of the e-invoice QR code: a signed token (the signature here is a placeholder) holding the supplier and
    buyer GSTINs, the number, type and date, the total and the IRN."""
    import base64

    on = date.fromisoformat(inv["invoice_date"])
    doc_type = "CRN" if inv["type"] == "credit_note" else "DBN" if inv["type"] == "debit_note" else "INV"
    claims = {
        "SellerGstin": inv["supplier_gstin"], "BuyerGstin": inv["buyer_gstin"], "DocNo": inv["supplier_invoice_no"],
        "DocTyp": doc_type, "DocDt": on.strftime("%d/%m/%Y"),
        "TotInvVal": float(D(inv["total"])), "ItemCnt": len(lines), "MainHsnCode": lines[0]["hsn_sac"] if lines else "",
        "Irn": inv["irn"], "IrnDt": f"{inv['ack_date'] or inv['invoice_date']} 10:00:00",
    }  # fmt: skip

    def b64(raw: bytes) -> str:
        return base64.urlsafe_b64encode(raw).decode().rstrip("=")

    head = b64(json.dumps({"alg": "RS256", "typ": "JWT"}).encode())
    body = b64(json.dumps({"data": json.dumps(claims), "iss": "NIC"}).encode())
    return f"{head}.{body}.{b64(b'placeholder-signature')}"


# ---- invoice pages ------------------------------------------------------------------------------

LM, RM = 40.0, 555.0
COL = {"sl": 42.0, "desc": 56.0, "hsn": 280.0, "qty": 345.0, "uom": 351.0, "rate": 432.0, "amt": 503.0, "gst": 553.0}
DESC_WIDTH = 218.0
HEADINGS = {"sl": "Sl", "desc": "Description of goods", "hsn": "HSN/SAC", "qty": "Qty", "uom": "UOM", "rate": "Rate",
            "amt": "Amount", "gst": "GST %"}  # fmt: skip


class Ctx:
    """The dataset's tables, loaded once."""

    def __init__(self, ds: Path) -> None:
        self.ds = ds
        self.invoices = table(ds, "history", "invoice")
        self.lines: dict[str, list[dict[str, str]]] = defaultdict(list)
        for r in table(ds, "history", "invoice_line"):
            self.lines[r["invoice_no"]].append(r)
        self.charges: dict[str, list[dict[str, str]]] = defaultdict(list)
        for r in table(ds, "history", "invoice_charge"):
            self.charges[r["invoice_no"]].append(r)
        self.supplier = {r["code"]: r for r in table(ds, "master", "supplier")}
        self.address = {
            r["supplier_code"]: r for r in table(ds, "master", "supplier_address") if r["type"] == "registered"
        }
        self.bank = {r["supplier_code"]: r for r in table(ds, "master", "supplier_bank_account")}
        self.contact = {
            r["supplier_code"]: r for r in table(ds, "master", "supplier_contact") if r["is_primary"] == "true"
        }
        self.terms = {r["code"]: r for r in table(ds, "master", "payment_terms")}
        self.items = {r["code"]: r for r in table(ds, "master", "item")}
        companies = table(ds, "master", "company")
        self.company = companies[0] if companies else {"legal_name": "Buyer", "email_domain": "buyer.example"}
        self.registration = {r["gstin"]: r for r in table(ds, "master", "gst_registration")}
        self.planted: dict[str, list[str]] = defaultdict(list)
        for r in table(ds, "truth", "truth_case"):
            for doc in r["documents"].split(";"):
                self.planted[doc.strip()].append(r["type"])
        splits = ds / "splits.json"
        self.hold_out_suppliers: set[str] = (
            set(json.loads(splits.read_text(encoding="utf-8")).get("hold_out_suppliers", []))
            if splits.is_file()
            else set()
        )
        self.supplier_no = {r["no"]: r["supplier_invoice_no"] for r in self.invoices}
        self.quotes = table(ds, "history", "quote")
        self.quote_lines: dict[str, list[dict[str, str]]] = defaultdict(list)
        for r in table(ds, "history", "quote_line"):
            self.quote_lines[r["quote_no"]].append(r)
        self.rfq_lines: dict[str, list[dict[str, str]]] = defaultdict(list)
        for r in table(ds, "history", "rfq_line"):
            self.rfq_lines[r["rfq_no"]].append(r)
        self.rfq_supplier = {(r["rfq_no"], r["supplier_code"]): r for r in table(ds, "history", "rfq_supplier")}
        self.emails = table(ds, "history", "email_message")


def _table_header(y: float) -> list[Text | Rule]:
    ops: list[Text | Rule] = [Rule(LM, y - 11, RM, y - 11, 0.8), Rule(LM, y + 4, RM, y + 4, 0.8)]
    for key, label in HEADINGS.items():
        align = "r" if key in ("qty", "rate", "amt", "gst") else "l"
        ops.append(Text(COL[key], y, label, 8, True, align))
    return ops


def _row(
    y: float, sl: str, desc: str, hsn: str, qty: str, uom: str, rate: str, amt: str, gst: str
) -> tuple[list[Text], float]:
    """One table row, and the y where the next one starts. A description too long for its cell is set smaller, and
    then, if still too long, continues on a line below."""
    size, rest = 8.0, ""
    for size in (8.0, 7.0, 6.5):
        if pk.text_width(desc, size) <= DESC_WIDTH:
            break
    else:
        words, first = desc.split(" "), ""
        while words and pk.text_width((first + " " + words[0]).strip(), size) <= DESC_WIDTH:
            first = (first + " " + words.pop(0)).strip()
        desc, rest = first or desc, " ".join(words)
    ops = [
        Text(COL["sl"], y, sl, 8), Text(COL["desc"], y, desc, size), Text(COL["hsn"], y, hsn, 8),
        Text(COL["qty"], y, qty, 8, align="r"), Text(COL["uom"], y, uom, 8), Text(COL["rate"], y, rate, 8, align="r"),
        Text(COL["amt"], y, amt, 8, align="r"), Text(COL["gst"], y, gst, 8, align="r"),
    ]  # fmt: skip
    if rest:
        ops.append(Text(COL["desc"], y + 9, rest, size))
        return ops, y + 22
    return ops, y + 14


def invoice_pages(
    ctx: Ctx, inv: dict[str, str], *, qr: Any | None, rows_per_page: int | None, terms_page: bool, po_style: str
) -> list[Page]:
    """The invoice as pages. `po_style`: 'inline' puts 'PO-x/1' in front of the description, 'sub' puts it on a small
    line under it, 'header' leaves it to the purchase order line at the top."""
    no = inv["no"]
    lines, charges = ctx.lines[no], ctx.charges[no]
    sup, addr = ctx.supplier[inv["supplier_code"]], ctx.address.get(inv["supplier_code"], {})
    bank = ctx.bank.get(inv["supplier_code"])
    reg = ctx.registration.get(inv["buyer_gstin"], {})
    credit = inv["type"] == "credit_note"
    debit = inv["type"] == "debit_note"
    note = credit or debit
    pos = list(dict.fromkeys(ln["po_no"] for ln in lines if ln.get("po_no")))
    chunks = (
        [lines] if not rows_per_page else [lines[i : i + rows_per_page] for i in range(0, len(lines), rows_per_page)]
    )
    total_pages = len(chunks) + (1 if terms_page else 0)
    pages: list[Page] = []

    for pno, chunk in enumerate(chunks, 1):
        p: Page = []
        if pno == 1:
            p += [Text(LM, 50, sup["legal_name"], 14, True)]
            p += [
                Text(LM, 64, addr.get("address", ""), 8),
                Text(
                    LM,
                    74,
                    f"{addr.get('city', '')} {addr.get('pincode', '')}, state code {addr.get('state_code', '')}".strip(),
                    8,
                ),
            ]
            p += [Text(LM, 88, f"GSTIN: {inv['supplier_gstin']}    PAN: {sup['pan']}", 8.5)]
            p += [
                Text(RM, 50, "CREDIT NOTE" if credit else "DEBIT NOTE" if debit else "TAX INVOICE", 15, True, "r"),
                Text(RM, 62, "Original for recipient", 7.5, False, "r", 0.4),
            ]
            p.append(Rule(LM, 100, RM, 100, 1.0))
            label = "Credit note No" if credit else "Debit note No" if debit else "Invoice No"
            y = 118.0
            p += [Text(LM, y, f"{label}: {inv['supplier_invoice_no']}", 9.5, True)]
            p += [Text(LM, y + 13, f"Date: {date.fromisoformat(inv['invoice_date']):%d/%m/%Y}", 9)]
            p += [Text(LM, y + 26, f"Place of supply: {inv['place_of_supply']}", 9)]
            y += 39
            if pos:
                p += [Text(LM, y, f"Purchase order: {', '.join(pos)}", 9)]
                y += 13
            if inv["ref_invoice_no"]:
                p += [
                    Text(
                        LM,
                        y,
                        f"Against invoice: {ctx.supplier_no.get(inv['ref_invoice_no'], inv['ref_invoice_no'])}",
                        9,
                    )
                ]
                y += 13
            y += 8
            p += [Text(LM, y, "Bill to:", 8.5, True), Text(LM, y + 12, ctx.company["legal_name"], 9)]
            address = reg.get("legal_address") or ctx.company.get("registered_address", "")
            half = address.rfind(",", 0, 70) if len(address) > 70 else -1
            p += [Text(LM, y + 23, address[: half + 1] if half > 0 else address, 8)]
            if half > 0:
                p += [Text(LM, y + 33, address[half + 1 :].strip(), 8)]
            p += [Text(LM, y + 46, f"Bill to GSTIN: {inv['buyer_gstin']}", 9)]
            y += 62
            if qr is not None:
                p.append(Picture(RM - 130, 108, 130, 130, qr))
                p += [
                    Text(LM, y, f"IRN: {inv['irn']}", 7.5),
                    Text(LM, y + 11, f"Ack No: {inv['ack_no']}    Ack Date: {inv['ack_date']}", 7.5),
                ]
                y += 22
            top = max(y, 250.0) + 14
        else:
            p += [
                Text(LM, 50, sup["legal_name"], 12, True),
                Text(RM, 50, f"Page {pno} of {total_pages}", 9, False, "r"),
            ]
            p += [
                Text(
                    LM,
                    64,
                    f"{'Credit note' if credit else 'Debit note' if debit else 'Invoice'} No: {inv['supplier_invoice_no']}    Date: {date.fromisoformat(inv['invoice_date']):%d/%m/%Y}",
                    9,
                )
            ]
            p.append(Rule(LM, 74, RM, 74, 1.0))
            top = 100.0
        p += _table_header(top)
        y = top + 20
        for ln in chunk:
            idx = lines.index(ln) + 1
            desc, sub = ln["description"], ""
            ref = f"{ln['po_no']}/{ln['po_line_no']}"
            if ln.get("po_no") and po_style == "inline" and pk.text_width(f"{ref} {desc}", 8) <= DESC_WIDTH:
                desc = f"{ref} {desc}"
            elif ln.get("po_no") and po_style == "sub":
                sub = f"PO {ln['po_no']} line {ln['po_line_no']}"
            ops, y = _row(
                y,
                str(idx),
                desc,
                ln["hsn_sac"],
                qty_text(ln["qty"]),
                ln["uom"],
                money(D(ln["unit_price"])),
                money(D(ln["taxable"])),
                ln["gst_rate"],
            )
            p += ops
            if sub:
                p.append(Text(COL["desc"], y - 4, sub, 6.5, False, "l", 0.35))
                y += 8
        if pno < len(chunks):
            p += [Rule(LM, y + 2, RM, y + 2, 0.5), Text(RM, y + 16, "Continued on next page", 8, False, "r", 0.3)]
        else:
            for c in charges:
                name = "Freight" if c["type"] == "freight" else "Packing and forwarding"
                p += [
                    Text(COL["desc"], y, name, 8),
                    Text(COL["amt"], y, money(D(c["amount"])), 8, align="r"),
                    Text(COL["gst"], y, c["gst_rate"], 8, align="r"),
                ]
                y += 14
            p.append(Rule(LM, y, RM, y, 0.8))
            y += 16
            tots: list[tuple[str, str, bool]] = [("Taxable value", money(D(inv["taxable"])), False)]
            for name in ("igst", "cgst", "sgst"):
                if D(inv[name]):
                    tots.append((name.upper(), money(D(inv[name])), False))
            if D(inv["round_off"]):
                tots.append(("Round off", money(D(inv["round_off"])), False))
            tots.append(("Total", money(D(inv["total"])), True))
            for label, value, bold in tots:
                p += [
                    Text(410, y, label, 10 if bold else 8.5, bold),
                    Text(RM, y, value, 10 if bold else 8.5, bold, "r"),
                ]
                y += 14
            p += [
                Text(LM, y - 14 * len(tots) + 4, "Amount in words:", 8, True),
                Text(LM, y - 14 * len(tots) + 15, amount_in_words(D(inv["total"])), 8),
            ]
            y += 14
            terms = ctx.terms.get(inv.get("payment_terms_code", ""), {}) or ctx.terms.get(sup["payment_terms_code"], {})
            p += [Text(LM, y, f"Payment terms: {terms.get('name', sup['payment_terms_code'])}", 8)]
            if bank and not terms_page:
                p += [Text(LM, y + 12, f"Bank: {bank['bank_code']}  A/c {bank['account_no']}  IFSC {bank['ifsc']}", 8)]
            p += [
                Text(RM, y + 60, f"For {sup['legal_name']}", 8.5, True, "r"),
                Text(RM, y + 100, "Authorised signatory", 8, False, "r"),
            ]
            p += [Text(LM, y + 100, "This is a computer generated document. E&OE.", 7, False, "l", 0.4)]
        p.append(Text(PAGE_CENTER, 820, f"Page {pno} of {total_pages}", 7.5, False, "c", 0.4))
        pages.append(p)

    if terms_page:
        p = [
            Text(LM, 50, sup["legal_name"], 12, True),
            Text(RM, 50, f"Page {total_pages} of {total_pages}", 9, False, "r"),
            Rule(LM, 62, RM, 62, 1.0),
        ]
        p += [Text(LM, 84, "Terms and conditions of sale", 11, True)]
        clauses = [
            "1. Goods once sold will not be taken back or exchanged except for manufacturing defects.",
            "2. Interest at 18% per annum is charged on payments delayed beyond the agreed credit period.",
            "3. Claims for shortage or damage must reach us within 7 days of receipt of the goods.",
            "4. Warranty, where applicable, is 12 months from the date of supply as stated in the contract.",
            f"5. All disputes are subject to the jurisdiction of the courts at {addr.get('city', 'our city')}.",
        ]
        for i, c in enumerate(clauses):
            p.append(Text(LM, 108 + i * 15, c, 8.5))
        if bank:
            p += [
                Text(LM, 215, "Our bank details", 10, True),
                Text(LM, 232, f"Bank: {bank['bank_code']}", 8.5),
                Text(LM, 245, f"Account number: {bank['account_no']}", 8.5),
                Text(LM, 258, f"IFSC: {bank['ifsc']}", 8.5),
            ]
        p.append(Text(PAGE_CENTER, 820, f"Page {total_pages} of {total_pages}", 7.5, False, "c", 0.4))
        pages.append(p)
    return pages


PAGE_CENTER = pk.PAGE_W / 2


def invoice_expected(ctx: Ctx, inv: dict[str, str]) -> dict[str, Any]:
    no = inv["no"]
    lines, charges = ctx.lines[no], ctx.charges[no]
    credit = inv["type"] == "credit_note"
    return {
        "supplier_code": inv["supplier_code"], "supplier_name": ctx.supplier[inv["supplier_code"]]["legal_name"],
        "supplier_gstin": inv["supplier_gstin"], "buyer_gstin": inv["buyer_gstin"],
        "document_type": "credit_note" if credit else "debit_note" if inv["type"] == "debit_note" else "invoice",
        "invoice_number": inv["supplier_invoice_no"],
        "invoice_date": inv["invoice_date"], "irn": inv["irn"] or None,
        "po_numbers": list(dict.fromkeys(ln["po_no"] for ln in lines if ln.get("po_no"))),
        "ref_invoice_number": ctx.supplier_no.get(inv["ref_invoice_no"]) if inv["ref_invoice_no"] else None,
        "lines": [
            {"line_no": int(ln["line_no"]), "po_no": ln["po_no"] or None,
             "po_line_no": int(ln["po_line_no"]) if ln["po_line_no"] else None, "description": ln["description"],
             "hsn": ln["hsn_sac"], "qty": ln["qty"], "uom": ln["uom"], "unit_price": ln["unit_price"], "taxable": ln["taxable"],
             "gst_rate": ln["gst_rate"]}
            for ln in lines
        ],
        "charges": [{"type": c["type"], "amount": c["amount"], "gst_rate": c["gst_rate"]} for c in charges],
        "taxable": inv["taxable"], "cgst": inv["cgst"], "sgst": inv["sgst"], "igst": inv["igst"], "round_off": inv["round_off"],
        "total": inv["total"],
    }  # fmt: skip


def make_invoice(ctx: Ctx, inv: dict[str, str], look: str, seed: int) -> tuple[bytes, str, dict[str, Any], list[str]]:
    """(file bytes, extension, manifest entry, every text printed on it)."""
    no = inv["no"]
    rng = Random(f"{seed}|invoice|{no}")
    lines = ctx.lines[no]
    has_qr = bool(inv["irn"]) and inv["type"] in ("invoice", "credit_note", "debit_note")
    qr = pk.qr_pixels(qr_payload(inv, lines)) if has_qr else None
    po_style = rng.choice(["inline", "sub", "header"])
    multi = look == "multipage_pdf"
    pages = invoice_pages(
        ctx, inv, qr=qr, rows_per_page=2 if multi and len(lines) >= 3 else None, terms_page=multi, po_style=po_style
    )
    printed = [op.s for page in pages for op in page if isinstance(op, Text)]
    if look in ("text_pdf", "multipage_pdf"):
        data, ext = pk.pdf_from_pages(pages), "pdf"
    else:
        img = pk.render(pages[0], 150)
        if look == "scan_pdf":
            img = pk.scan(img, rng)
        elif look == "photo_png":
            img = pk.photo(img, rng)
        elif look == "skewed_jpg":
            img = pk.skewed(img, rng)
        else:
            img = pk.low_contrast(img, rng)
        if look == "photo_png":
            data, ext = pk.to_png(img), "png"
        elif look == "skewed_jpg":
            data, ext = pk.to_jpeg(img, 70), "jpg"
        else:
            data, ext = pk.pdf_from_pictures([(pk.to_jpeg(img, 80), img.width, img.height)]), "pdf"
    entry = {
        "file": f"invoices/{no}.{ext}", "kind": "invoice", "variant": look, "difficulty": DIFFICULTY[look],
        "pages": len(pages) if look in ("text_pdf", "multipage_pdf") else 1,
        "has_text_layer": look in ("text_pdf", "multipage_pdf"), "has_qr": has_qr, "po_style": po_style,
        "dataset_invoice": no, "channel": inv["channel"], "dataset_status": inv["status"], "planted_cases": ctx.planted.get(no, []),
        "expected": invoice_expected(ctx, inv),
        "qr": {"irn": inv["irn"], "seller_gstin": inv["supplier_gstin"], "buyer_gstin": inv["buyer_gstin"], "doc_no": inv["supplier_invoice_no"],
               "doc_type": "CRN" if inv["type"] == "credit_note" else "DBN" if inv["type"] == "debit_note" else "INV",
               "doc_date": inv["invoice_date"], "total": inv["total"], "item_count": len(lines)} if has_qr else None,
    }  # fmt: skip
    return data, ext, entry, printed


# ---- bills without an order and debit notes (block 10) ------------------------------------------


def _fy4(on: date) -> str:
    start = on.year if on.month >= 4 else on.year - 1
    return f"{start}-{(start + 1) % 100:02d}"


def _irn(gstin: str, on: date, doc_type: str, doc_no: str) -> str:
    return hashlib.sha256(f"{gstin}{_fy4(on)}{doc_type}{doc_no}".encode()).hexdigest().upper()


def extra_invoices(ctx: Ctx) -> list[dict[str, str]]:
    """A bill without an order (with and without a QR code) and a debit note, against a bill or an invoice of the history."""
    made: list[dict[str, str]] = []
    if "SUP-010" in ctx.supplier:  # Apex: the internet provider bills without an order
        sup = ctx.supplier["SUP-010"]
        buyer = next((r["gstin"] for r in ctx.registration.values() if r["is_default"] == "true"), "")
        for number, with_qr in (("SNL/2627/0458", True), ("SNL/2627/0459", False)):
            on = date(2026, 11, 23)
            no = f"BILL-{number.replace('/', '-')}"
            doc_date = on
            ctx.lines[no] = [
                {
                    "invoice_no": no, "line_no": "1", "po_no": "", "po_line_no": "",
                    "description": "Internet lease line, 100 Mbps, November 2026", "hsn_sac": "998422",
                    "qty": "1", "uom": "OTH", "unit_price": "15000.00", "discount": "0.00", "taxable": "15000.00",
                    "gst_rate": "18", "cgst": "1350.00", "sgst": "1350.00", "igst": "0.00",
                }
            ]  # fmt: skip
            made.append(
                {
                    "no": no, "type": "invoice", "supplier_code": "SUP-010", "supplier_gstin": sup["gstin"],
                    "buyer_gstin": buyer, "place_of_supply": buyer[:2], "supplier_invoice_no": number,
                    "invoice_date": doc_date.isoformat(), "fy": _fy4(doc_date), "irn": _irn(sup["gstin"], doc_date, "INV", number) if with_qr else "",
                    "ack_no": "ACK-0000" if with_qr else "", "ack_date": doc_date.isoformat() if with_qr else "",
                    "eway_bill_no": "", "reverse_charge": "false", "received_at": f"{doc_date.isoformat()}T10:00:00+05:30",
                    "received_on": doc_date.isoformat(), "channel": "email", "taxable": "15000.00", "cgst": "1350.00",
                    "sgst": "1350.00", "igst": "0.00", "cess": "0.00", "round_off": "0.00", "total": "17700.00",
                    "ref_invoice_no": "", "due_date": "", "payment_block": "none", "status": "approved",
                    "duplicate_of": "", "block_reason": "", "cancel_reason": "", "document_ref": "",
                    "entered_by": "U04", "created_at": f"{doc_date.isoformat()}T10:00:00+05:30",
                    "payment_terms_code": "NET30", "_qr": "true" if with_qr else "false",
                }
            )  # fmt: skip
    refs = [r for r in ctx.invoices if r["supplier_code"] == "SUP-003" and r["type"] == "invoice"]
    if refs:  # a debit note of Shree Wires against its latest invoice on file
        ref = sorted(refs, key=lambda r: r["no"])[-1]
        sup = ctx.supplier["SUP-003"]
        on = date(2026, 11, 20)
        for number, with_qr in (("SW/DBN/2627/014", True), ("SW/DBN/2627/015", False)):
            no = f"DBN-{number.replace('/', '-')}"
            ctx.lines[no] = [
                {
                    "invoice_no": no, "line_no": "1", "po_no": "", "po_line_no": "",
                    "description": "Short supply of enamelled copper wire, debit raised", "hsn_sac": "854411",
                    "qty": "1", "uom": "KGS", "unit_price": "2500.00", "discount": "0.00", "taxable": "2500.00",
                    "gst_rate": "18", "cgst": "225.00", "sgst": "225.00", "igst": "0.00",
                }
            ]  # fmt: skip
            made.append(
                {
                    "no": no, "type": "debit_note", "supplier_code": "SUP-003", "supplier_gstin": sup["gstin"],
                    "buyer_gstin": ref["buyer_gstin"], "place_of_supply": ref["place_of_supply"],
                    "supplier_invoice_no": number, "invoice_date": on.isoformat(), "fy": _fy4(on),
                    "irn": _irn(sup["gstin"], on, "DBN", number) if with_qr else "",
                    "ack_no": "ACK-0000" if with_qr else "", "ack_date": on.isoformat() if with_qr else "",
                    "eway_bill_no": "", "reverse_charge": "false", "received_at": f"{on.isoformat()}T10:00:00+05:30",
                    "received_on": on.isoformat(), "channel": "email", "taxable": "2500.00", "cgst": "225.00",
                    "sgst": "225.00", "igst": "0.00", "cess": "0.00", "round_off": "0.00", "total": "2950.00",
                    "ref_invoice_no": ref["no"], "due_date": "", "payment_block": "none", "status": "approved",
                    "duplicate_of": "", "block_reason": "", "cancel_reason": "", "document_ref": "",
                    "entered_by": "U04", "created_at": f"{on.isoformat()}T10:00:00+05:30",
                    "payment_terms_code": sup["payment_terms_code"], "_qr": "true" if with_qr else "false",
                }
            )  # fmt: skip
    return made


# ---- quotation replies --------------------------------------------------------------------------

STYLE_SUBJECT = {
    "formal": "Re: Request for quotation {rfq}",
    "terse": "Quote - {rfq}",
    "chatty": "Our rates for {rfq}, thanks for asking",
}


def _date_text(d: date, rng: Random) -> str:
    return rng.choice([d.strftime("%d/%m/%Y"), d.strftime("%d %b %Y"), d.strftime("%d-%m-%Y")])


def quote_expected(
    ctx: Ctx,
    quote: dict[str, str],
    rfq: str,
    quirk: str,
    kept: list[dict[str, str]],
    missing: list[dict[str, str]],
    written: dict[str, str],
) -> dict[str, Any]:
    out_lines = []
    for ql in kept:
        rl = next(r for r in ctx.rfq_lines[rfq] if r["line_no"] == ql["rfq_line_no"])
        price: str | None = ql["unit_price"]
        item: dict[str, Any] = {
            "rfq_line_no": int(ql["rfq_line_no"]), "item_code": ql["item_code"], "item_written": written[ql["rfq_line_no"]],
            "rfq_qty": rl["qty"], "qty": ql["qty"], "unit_price": price, "discount_pct": ql["discount_pct"] if D(ql["discount_pct"]) else None,
            "gst_rate": ql["gst_rate"], "delivery_days": int(ql["delivery_days"]) if ql["delivery_days"] else None,
            "price_includes_gst": None,
        }  # fmt: skip
        if quirk == "unit_differs" and ql is kept[0]:
            item.update(
                unit_price=None,
                printed_price=str(D(price) * 10),
                printed_per="box of 10",
                derivable_unit_price=ql["unit_price"],
            )
        elif quirk == "incl_gst":
            inclusive = (D(price) * (100 + D(ql["gst_rate"])) / 100).quantize(D("0.01"), ROUND_HALF_UP)
            item.update(unit_price=str(inclusive), price_includes_gst=True, unit_price_ex_gst=ql["unit_price"])
        out_lines.append(item)
    valid = quote["valid_until"] or None
    return {
        "intent": "quote", "valid_until": None if quirk == "relative_validity" else valid,
        "relative_validity_days": (date.fromisoformat(valid) - date.fromisoformat(quote["received_at"][:10])).days
        if quirk == "relative_validity" and valid else None,
        "freight": quote["freight"] if D(quote["freight"]) else None,
        "packing_forwarding": quote["packing_forwarding"] if D(quote["packing_forwarding"]) else None,
        "prices_exclude_gst": quote["taxes_extra"] == "true" and quirk != "incl_gst", "payment_terms_code": quote["payment_terms_code"],
        "delivery_terms": quote["delivery_terms"], "lines": out_lines,
        "missing_rfq_line_nos": [int(m["line_no"]) for m in missing],
    }  # fmt: skip


def _line_text(ql: dict[str, str], name: str, quirk: str, first: bool, uom: str, expected: dict[str, Any]) -> str:
    price = D(ql["unit_price"])
    shown = f"Rs {money(price)} per {uom}"
    if quirk == "unit_differs" and first:
        shown = f"Rs {money(price * 10)} per box of 10 {uom}"
    elif quirk == "incl_gst":
        shown = f"Rs {money(D(expected['unit_price']))} per {uom} (inclusive of GST {ql['gst_rate']}%)"
    gst = "" if quirk == "incl_gst" else f", GST {ql['gst_rate']}% extra"
    disc = f", discount {D(ql['discount_pct']).normalize():f}%" if D(ql["discount_pct"]) else ""
    days = f", delivery {ql['delivery_days']} days" if ql["delivery_days"] else ""
    return f"{name} - {qty_text(ql['qty'])} {uom} @ {shown}{gst}{disc}{days}"


def quote_page(
    ctx: Ctx,
    quote: dict[str, str],
    rfq: str,
    kept: list[dict[str, str]],
    rows: list[dict[str, Any]],
    inclusive: bool,
    valid_text: str,
) -> Page:
    sup = ctx.supplier[quote["supplier_code"]]
    addr = ctx.address.get(quote["supplier_code"], {})
    p: Page = [Text(LM, 50, sup["legal_name"], 14, True), Text(LM, 64, addr.get("address", ""), 8),
               Text(LM, 74, f"{addr.get('city', '')} {addr.get('pincode', '')}", 8), Text(LM, 88, f"GSTIN: {sup['gstin']}", 8.5),
               Text(RM, 50, "QUOTATION", 15, True, "r"), Rule(LM, 100, RM, 100, 1.0)]  # fmt: skip
    p += [Text(LM, 120, f"Quotation No: {quote['no']}", 9.5, True), Text(LM, 133, f"Date: {date.fromisoformat(quote['received_at'][:10]):%d/%m/%Y}", 9),
          Text(LM, 146, f"Your enquiry: {rfq}", 9)]  # fmt: skip
    if valid_text:
        p.append(Text(LM, 159, valid_text, 9))
    cols = {"sl": 42.0, "item": 56.0, "qty": 330.0, "rate": 410.0, "gst": 460.0, "days": 553.0}
    y = 190.0
    p += [Rule(LM, y - 11, RM, y - 11, 0.8), Rule(LM, y + 4, RM, y + 4, 0.8)]
    for key, label in (
        ("sl", "Sl"),
        ("item", "Item"),
        ("qty", "Qty"),
        ("rate", "Rate (Rs)"),
        ("gst", "GST %"),
        ("days", "Delivery days"),
    ):
        p.append(Text(cols[key], y, label, 8, True, "l" if key in ("sl", "item") else "r"))
    y += 20
    for i, (ql, row) in enumerate(zip(kept, rows, strict=True), 1):
        price = row["unit_price"] if row["unit_price"] is not None else row["printed_price"]
        p += [Text(cols["sl"], y, str(i), 8), Text(cols["item"], y, row["item_written"], 8), Text(cols["qty"], y, qty_text(ql["qty"]), 8, align="r"),
              Text(cols["rate"], y, money(D(price)), 8, align="r"), Text(cols["gst"], y, ql["gst_rate"], 8, align="r"),
              Text(cols["days"], y, ql["delivery_days"] or "", 8, align="r")]  # fmt: skip
        if row["unit_price"] is None:
            p.append(Text(cols["item"], y + 9, f"(price is per {row['printed_per']})", 6.5, False, "l", 0.35))
            y += 9
        y += 16
    terms = ctx.terms.get(quote["payment_terms_code"], {})
    y += 10
    for line in (
        "Prices are inclusive of GST." if inclusive else "Prices are exclusive of GST.",
        f"Payment terms: {terms.get('name', quote['payment_terms_code'])}",
        f"Delivery terms: {quote['delivery_terms'].replace('_', ' ')}",
        *([f"Freight: Rs {money(D(quote['freight']))} extra"] if D(quote["freight"]) else []),
        *(
            [f"Packing and forwarding: Rs {money(D(quote['packing_forwarding']))} extra"]
            if D(quote["packing_forwarding"])
            else []
        ),
    ):
        p.append(Text(LM, y, line, 8.5))
        y += 13
    return p


def make_quote_mail(
    ctx: Ctx, mail: dict[str, str], position: int, seed: int
) -> tuple[bytes, dict[str, Any], list[str]]:
    """A supplier's reply to an RFQ as an .eml, with its manifest entry and every text in it."""
    rfq, sup_code = mail["linked_doc_no"], mail["supplier_code"]
    rng = Random(f"{seed}|quote|{mail['id']}")
    style = mail["style"]
    sup, contact = ctx.supplier[sup_code], ctx.contact.get(sup_code, {})
    out_mail = next(
        (
            m
            for m in ctx.emails
            if m["kind"] == "rfq_send" and m["linked_doc_no"] == rfq and m["supplier_code"] == sup_code
        ),
        None,
    )
    msg = EmailMessage(policy=policy.SMTP)
    msg["Message-ID"] = mail["message_id"]
    msg["From"] = f"{contact.get('name', sup['legal_name'])} <{mail['from_addr']}>"
    msg["To"] = f"rfq+{rfq}@{ctx.company['email_domain']}"
    msg["Subject"] = STYLE_SUBJECT[style].format(rfq=rfq)
    msg["Date"] = format_datetime(datetime.fromisoformat(mail["at"]))
    if out_mail:
        msg["In-Reply-To"] = out_mail["message_id"]
        msg["References"] = out_mail["message_id"]
    sign = f"{contact.get('name', '')}\n{sup['trade_name']}\n{contact.get('phone', '')}".strip()
    greet = {"formal": "Dear Sir/Madam,", "terse": "Hi,", "chatty": "Hello team,"}[style]
    bye = {"formal": "Thanking you,\nYours faithfully,", "terse": "Regards", "chatty": "Cheers and thanks!"}[style]
    entry: dict[str, Any] = {"kind": "quote_reply", "style": style, "rfq_no": rfq, "supplier_code": sup_code, "email_id": mail["id"],
                             "message_id": mail["message_id"], "quirks": []}  # fmt: skip
    name = f"{mail['id']}_{rfq}_{sup_code}"

    if mail["kind"] == "decline_rfq":
        reason = ctx.rfq_supplier[(rfq, sup_code)]["decline_reason"]
        text = {"formal": f"We thank you for the enquiry {rfq}. We regret that we are unable to quote. Reason: {reason}.",
                "terse": f"Sorry, no quote for {rfq}. {reason}.",
                "chatty": f"Thanks for thinking of us for {rfq}! Unfortunately we have to pass this time. {reason}."}[style]  # fmt: skip
        body = f"{greet}\n\n{text}\n\n{bye}\n{sign}\n"
        msg.set_content(body)
        entry.update(
            variant="decline",
            file=f"quotes/{name}.eml",
            attachment=None,
            expected={"intent": "decline", "decline_reason": reason},
        )
        return msg.as_bytes(), entry, [body]

    quote = next(
        q
        for q in ctx.quotes
        if q["rfq_no"] == rfq and q["supplier_code"] == sup_code and q["received_at"] == mail["at"]
    )
    ql_all = ctx.quote_lines[quote["no"]]
    fmt = QUOTE_FORMATS[position % len(QUOTE_FORMATS)]
    quirk = QUOTE_QUIRKS[position % len(QUOTE_QUIRKS)]
    if quirk == "partial" and len(ql_all) < 2:
        quirk = "none"
    kept = ql_all[:-1] if quirk == "partial" else ql_all
    missing = [r for r in ctx.rfq_lines[rfq] if r["line_no"] not in {q["rfq_line_no"] for q in kept}]
    written = {}
    for q in ql_all:
        rl = next(r for r in ctx.rfq_lines[rfq] if r["line_no"] == q["rfq_line_no"])
        written[q["rfq_line_no"]] = (
            rng.choice([rl["description"], ctx.items[q["item_code"]]["name"], q["item_code"]])
            if style != "terse"
            else rl["description"]
        )
    expected = quote_expected(ctx, quote, rfq, quirk, kept, missing, written)
    valid = quote["valid_until"]
    valid_text = (
        f"Valid for {expected['relative_validity_days']} days from the date of this quotation."
        if quirk == "relative_validity" and valid
        else f"Valid till: {_date_text(date.fromisoformat(valid), rng)}"
        if valid
        else ""
    )
    uoms = {r["line_no"]: r["uom"] for r in ctx.rfq_lines[rfq]}
    extras = []
    if D(quote["freight"]):
        extras.append(f"Freight: Rs {money(D(quote['freight']))} extra")
    if D(quote["packing_forwarding"]):
        extras.append(f"Packing and forwarding: Rs {money(D(quote['packing_forwarding']))} extra")
    terms = ctx.terms.get(quote["payment_terms_code"], {}).get("name", quote["payment_terms_code"])
    partial_note = "We are not able to offer the remaining item(s) at present." if quirk == "partial" else ""
    printed: list[str] = []
    attachment: bytes | None = None
    if fmt == "pdf_attachment":
        page = quote_page(ctx, quote, rfq, kept, expected["lines"], quirk == "incl_gst", valid_text)
        attachment = pk.pdf_from_pages([page])
        printed += [op.s for op in page if isinstance(op, Text)]
        intro = {
            "formal": f"With reference to your enquiry {rfq}, please find our quotation attached.",
            "terse": "Quote attached.",
            "chatty": "Please find our best prices in the attached quotation.",
        }[style]
        parts = [greet, "", intro]
        if valid_text and rng.random() < 0.5:
            parts += ["", valid_text]
        if partial_note:
            parts += ["", partial_note]
        parts += ["", bye, sign, ""]
        entry["attachment"] = f"{quote['no']}.pdf"
    else:
        if fmt == "body_text":
            item_lines = [
                _line_text(q, written[q["rfq_line_no"]], quirk, i == 0, uoms[q["rfq_line_no"]], row)
                for i, (q, row) in enumerate(zip(kept, expected["lines"], strict=True))
            ]
            listing = "\n".join(f"{i}. {t}" for i, t in enumerate(item_lines, 1))
        else:
            rows = ["Item | Qty | Rate (Rs) | GST % | Delivery days"]
            for q, row in zip(kept, expected["lines"], strict=True):
                shown = row["unit_price"] if row["unit_price"] is not None else row["printed_price"]
                rows.append(
                    f"{row['item_written']} | {qty_text(q['qty'])} | {money(D(shown))} | {q['gst_rate']} | {q['delivery_days']}"
                )
            if quirk == "unit_differs":
                rows.append("Note: the first rate is per box of 10.")
            if quirk == "incl_gst":
                rows.append("All rates include GST.")
            listing = "\n".join(rows)
        parts = [
            greet,
            "",
            {
                "formal": f"With reference to your enquiry {rfq}, our offer is as follows.",
                "terse": f"{rfq} rates:",
                "chatty": f"Here are our prices for {rfq}.",
            }[style],
            "",
            listing,
            "",
        ]
        parts += [*extras, f"Payment terms: {terms}", f"Delivery terms: {quote['delivery_terms'].replace('_', ' ')}"]
        if valid_text:
            parts.append(valid_text)
        if partial_note:
            parts.append(partial_note)
        parts += ["", bye, sign, ""]
        entry["attachment"] = None
    body = "\n".join(parts)
    printed.append(body)
    msg.set_content(body)
    if attachment is not None:
        msg.add_attachment(attachment, maintype="application", subtype="pdf", filename=f"{quote['no']}.pdf")
        msg.set_boundary(f"=_boundary_{mail['id']}")
    entry.update(
        variant=fmt,
        file=f"quotes/{name}.eml",
        quirks=[] if quirk == "none" else [quirk],
        quote_no=quote["no"],
        expected=expected,
    )
    return msg.as_bytes(), entry, printed


# ---- the run ------------------------------------------------------------------------------------


def generate(dataset: Path, seed: int = 42, limit: int | None = None, out: Path | None = None) -> dict[str, Any]:
    ctx = Ctx(dataset)
    out = out or dataset / "documents"
    for sub in ("invoices", "quotes"):
        shutil.rmtree(out / sub, ignore_errors=True)
        (out / sub).mkdir(parents=True, exist_ok=True)
    rng = Random(f"{seed}|order")
    invoices = sorted((i for i in ctx.invoices if ctx.lines[i["no"]]), key=lambda r: r["no"])
    rng.shuffle(invoices)
    mails = sorted(
        (m for m in ctx.emails if m["kind"] in ("quote_cover", "decline_rfq") and m["direction"] == "in"),
        key=lambda m: m["id"],
    )
    rng.shuffle(mails)
    if limit is not None:
        invoices, mails = invoices[:limit], mails[:limit]
    documents: list[dict[str, Any]] = []
    unsafe: list[str] = []
    for i, inv in enumerate(invoices):
        data, _, entry, printed = make_invoice(ctx, inv, INVOICE_LOOKS[i % len(INVOICE_LOOKS)], seed)
        entry["split"] = split_of(entry, ctx.hold_out_suppliers)
        (out / entry["file"]).write_bytes(data)
        unsafe += [f"{entry['file']}: {b}" for b in unsafe_text("\n".join(printed))]
        documents.append(entry)
    for i, inv in enumerate(extra_invoices(ctx) if limit is None else []):
        inv.pop("_qr", None)
        data, _, entry, printed = make_invoice(ctx, inv, "text_pdf", seed)
        entry["split"] = split_of(entry, ctx.hold_out_suppliers)
        (out / entry["file"]).write_bytes(data)
        unsafe += [f"{entry['file']}: {b}" for b in unsafe_text("\n".join(printed))]
        documents.append(entry)
    for i, mail in enumerate(mails):
        data, entry, printed = make_quote_mail(ctx, mail, i, seed)
        entry["split"] = split_of(entry, ctx.hold_out_suppliers)
        (out / entry["file"]).write_bytes(data)
        unsafe += [f"{entry['file']}: {b}" for b in unsafe_text("\n".join(printed))]
        documents.append(entry)
    if unsafe:
        raise SystemExit("documents carry something that could be real:\n" + "\n".join(unsafe[:20]))
    counts: dict[str, int] = defaultdict(int)
    for d in documents:
        counts[f"{d['kind']}:{d['variant']}"] += 1
    manifest = {
        "generator": GENERATOR_VERSION, "dataset": dataset.name, "seed": seed, "limit": limit,
        "note": "Everything is made up. The 'expected' of each document comes from the dataset's tables, not from the document.",
        "counts": dict(sorted(counts.items())),
        "splits": {k: sum(1 for d in documents if d["split"] == k) for k in ("dev", "hold_out")},
        "hold_out_rule": {"layouts": {k: list(v) for k, v in HELD_OUT_LAYOUTS.items()}, "suppliers": sorted(ctx.hold_out_suppliers)},
        "documents": documents,
    }  # fmt: skip
    (out / "manifest.json").write_text(json.dumps(manifest, indent=1, ensure_ascii=False), encoding="utf-8")
    return manifest


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dataset", required=True, type=Path)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--limit", type=int, default=None, help="at most this many invoices and this many quote replies")
    ap.add_argument("--out", type=Path, default=None, help="default: <dataset>/documents")
    args = ap.parse_args()
    m = generate(args.dataset, args.seed, args.limit, args.out)
    print(f"wrote {len(m['documents'])} documents to {args.out or args.dataset / 'documents'}")
    for k, v in m["counts"].items():
        print(f"  {k}: {v}")


if __name__ == "__main__":
    main()
