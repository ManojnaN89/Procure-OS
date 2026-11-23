"""Shared pieces of the block 7 evals (E128-E135): purchase order versions (flow step 7). An acknowledgement bound to the
version (DF-07), a stored document per issued version, the differences between versions, header changes (delivery
place, payment terms, delivery terms, the contract price in force) approved again when material and carried when not, a
respond link per version end to end, and the order that keeps its contract price version (D48). Written from the block
7 spec, the plan and the dataset only, never from the implementation.

Two halves, as in `block6`:

- **The oracle** (`Oracle7`, built on `block5.Oracle5`): GST on the place of supply (CGST = SGST = taxable x half the
  rate, each half up to paise, when the supplier's state is the delivery place's state, else IGST = taxable x rate half
  up), the buyer's GSTIN of a place (the location's own, else the default registration), the approval band and its
  person with makers skipped (`block4.Oracle.approver`), the material reasons measured from the last approved version
  (`block4.material`, Q19 in the spec's order), the contract price in force on a day (the raw `price_record.csv`, a
  draft only once activated, version 1 ending the day before version 2 starts), the version snapshots and their
  differences (the eval's own `diff`), sha256 of the bytes. `SPEC` holds section 7's figures as a self-check, and
  `truth/truth_po_versions.csv` (the generator's own arithmetic) is compared row by row.
- **The platform** (`Co7`, built on `block6.Co6`): a demo company made by `demo reset`, its people signed in with their
  own logins, the API section 8 names (`/revise` with `version` and `header`, `/acknowledge` with `version`,
  `/versions`, `/versions/compare`, `/versions/{v}/pdf`, `/pdf`, the supplier's `/supplier/{token}` and its `/pdf`), the
  worker run in this process, mail caught in this process (`lib.capture_mail`: the PDF attached to an order mail is
  read from the captured call), PDF text read with `pypdfium2`, and read-only views of the tables section 8 lets an eval
  read.

Where the spec leaves a shape open (a list wrapped in an object or not; a difference's fields as a list or a mapping; a
field named `qty` or `quantity`; money as text or number; a person as code, user id, email or name) the readers accept
every reasonable shape and say so in a comment.
"""

import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

import block1 as b
import block3 as b3
import block4 as k
import block5 as f
import block6 as s
from block1 import D, js

TODAY = s.TODAY  # Mon 23 Nov 2026 (spec 7)
JAN4 = date(2027, 1, 4)  # Mon 4 Jan 2027, P8's re-pricing day
NEED_BY = date(2026, 12, 14)
DEC19 = date(2026, 12, 19)  # need-by moved 5 days: beyond the 3 accepted, material
P8_NEED = date(2027, 1, 15)
LOC, SND = "PLT-CKN", "PLT-SND"
BLR, HYD = "OFF-BLR", "OFF-HYD"
WIRE, SHAFT, CASTING, LAPTOP = "RM-08", "RM-05", "RM-01", "LP-01"
CONTRACT = "CNT-0001"
REFUSED = (400, 403, 404, 409, 410, 422)

# section 7's figures: the oracle must give the same, else the eval or the spec is wrong
SPEC: dict[str, dict[str, str]] = {
    "P1v1": {"taxable": "108600.00", "cgst": "9774.00", "sgst": "9774.00", "igst": "0.00", "total": "128148.00",
             "rule": "PO-02", "approver": "U08"},
    "P1v2": {"taxable": "90500.00", "cgst": "8145.00", "sgst": "8145.00", "igst": "0.00", "total": "106790.00"},
    "P4v1": {"taxable": "124000.00", "cgst": "11160.00", "sgst": "11160.00", "igst": "0.00", "total": "146320.00",
             "rule": "PO-02", "approver": "U09"},
    "P4v2": {"taxable": "124000.00", "cgst": "0.00", "sgst": "0.00", "igst": "22320.00", "total": "146320.00",
             "buyer_gstin": "36BRTCB0000T1ZC"},
    "P5v1": {"taxable": "1800.00", "cgst": "45.00", "sgst": "45.00", "igst": "0.00", "total": "1890.00", "rule": "PO-01"},
    "P5v2": {"total": "1890.00", "approver": "U09"},
    "P8v1": {"taxable": "130000.00", "cgst": "11700.00", "sgst": "11700.00", "igst": "0.00", "total": "153400.00",
             "rule": "PO-02", "approver": "U08", "unit_price": "650.00"},
    "P8v2": {"taxable": "134400.00", "cgst": "12096.00", "sgst": "12096.00", "igst": "0.00", "total": "158592.00",
             "rule": "PO-02", "approver": "U08", "unit_price": "672.00"},
}  # fmt: skip


# ---- words and numbers -------------------------------------------------------------------------------------------------


def long_date(d: date) -> str:
    return k.long_date(d)  # '14 Dec 2026', the spec's <dd Mon yyyy>


def norm(x: Any) -> str:
    return f.norm(x)


def inr(x: Any) -> str:
    return b3.inr(D(x))  # '1,06,790.00'


def sha(data: bytes | None) -> str | None:
    return hashlib.sha256(data).hexdigest() if data else None


def flat(x: Any) -> str:
    """A PDF's text (or any words) with every run of spaces and line breaks as one space."""
    return re.sub(r"\s+", " ", str(x or "")).strip()


def pdf_text(data: bytes | None) -> str:
    """The text of a PDF, read with pypdfium2 (spec 9.2); empty for anything that is not a PDF."""
    if not data or data[:5] != b"%PDF-":
        return ""
    import pypdfium2 as pdfium

    pdf = pdfium.PdfDocument(data)
    try:
        return flat("\n".join(pdf[i].get_textpage().get_text_range() for i in range(len(pdf))))
    finally:
        pdf.close()


def as_rows(value: Any, *keys: str) -> list[dict[str, Any]]:
    """A list of objects as sent: the list itself or wrapped in an object under one of `keys`."""
    value = k.as_json(value)
    if isinstance(value, dict):
        for key in keys:
            if isinstance(value.get(key), list):
                value = value[key]
                break
    return [x for x in value or [] if isinstance(x, dict)] if isinstance(value, list) else []


def attached_pdfs(mail: dict[str, Any] | None) -> list[bytes]:
    """The PDFs attached to a captured mail. Seen shape: `attachments` a list of (name, type, bytes); a dict with the
    bytes under any key, or a stored file's path, is accepted too."""
    found: list[bytes] = []

    def walk(x: Any) -> None:
        if isinstance(x, (bytes, bytearray)):
            if bytes(x[:5]) == b"%PDF-":
                found.append(bytes(x))
        elif isinstance(x, dict):
            for v in x.values():
                walk(v)
        elif isinstance(x, (list, tuple)):
            for v in x:
                walk(v)
        elif isinstance(x, str) and x.lower().endswith(".pdf") and len(x) < 400:
            p = Path(x)
            if p.is_file():
                walk(p.read_bytes())

    walk((mail or {}).get("attachments"))
    return found


# ---- values as the platform may send them --------------------------------------------------------------------------------

FIELD_ALIASES = {
    "quantity": "qty", "ship_to": "ship_to_location", "delivery_location": "ship_to_location", "location": "ship_to_location",
    "delivery_place": "ship_to_location", "place": "ship_to_location", "payment_terms": "payment_terms_code",
    "price": "unit_price", "contract": "contract_version", "contract_price_version": "contract_version",
    "price_record_version": "contract_version", "need_by_date": "need_by", "gstin": "buyer_gstin",
}  # fmt: skip


def field_name(x: Any) -> str:
    name = re.sub(r"[^a-z0-9]+", "_", str(x or "").strip().lower()).strip("_")
    return FIELD_ALIASES.get(name, name)


def value_of(x: Any) -> Any:
    """A value compared by meaning: numbers as Decimal (650 equals 650.00), dates by their ISO day, codes as text. A
    {code, version} object counts as its version (a contract version shown as an object)."""
    if isinstance(x, dict):
        for key in ("version", "value", "code"):
            if key in x:
                return value_of(x[key])
        return json.dumps(x, sort_keys=True, default=str)
    if x is None or x == "":
        return None
    if isinstance(x, bool):
        return x
    if isinstance(x, date):
        return x.isoformat()
    text_ = str(x).strip()
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}([T ].*)?", text_):
        return text_[:10]
    try:
        return Decimal(text_.replace(",", "")).normalize()
    except (InvalidOperation, ValueError):
        return text_


# ======================================================================================================================
# The oracle
# ======================================================================================================================


@dataclass
class Version:
    """One version of a one-line order as the eval works it out (the fields the scenarios change or depend on)."""

    version: int
    item: str
    qty: Decimal
    price: Decimal
    need_by: date
    supplier: str
    place: str
    terms: str = ""
    delivery_terms: str | None = None
    price_record_id: Any = None
    contract_version: int | None = None
    amounts: dict[str, Decimal] = field(default_factory=dict)
    buyer_gstin: str = ""

    def header(self) -> dict[str, Any]:
        out = {"ship_to_location": self.place, "buyer_gstin": self.buyer_gstin, "payment_terms_code": self.terms or None,
               "delivery_terms": self.delivery_terms}  # fmt: skip
        out.update({x: self.amounts[x] for x in ("taxable", "cgst", "sgst", "igst", "total")})
        return out

    def line(self) -> dict[str, Any]:
        out = {"qty": self.qty, "unit_price": self.price, "need_by": self.need_by, "price_record_id": self.price_record_id,
               "contract_version": self.contract_version}  # fmt: skip
        out.update({x: self.amounts[x] for x in ("taxable", "cgst", "sgst", "igst")})
        return out


def diff(a: Version, b_: Version) -> dict[tuple[str, int, str], tuple[Any, Any]]:
    """Spec R9 over the eval's own versions: (scope, line, field) -> (before, after) for every value that differs."""
    out: dict[tuple[str, int, str], tuple[Any, Any]] = {}
    for scope, line_no, x, y in (("header", 0, a.header(), b_.header()), ("line", 1, a.line(), b_.line())):
        for key in x:
            if value_of(x[key]) != value_of(y[key]):
                out[(scope, line_no, key)] = (x[key], y[key])
    return out


class Oracle7(f.Oracle5):
    """Amounts on the place of supply, bands and people, the contract price in force, material reasons."""

    def __init__(self, co: "Co7") -> None:
        super().__init__(co)
        self.registrations = co.rows("master", "gst_registration")
        self.regions = {r["code"]: r["name"] for r in co.rows("master", "region")}
        self.terms = {r["code"]: r for r in co.rows("master", "payment_terms")}

    def buyer_gstin(self, loc: str) -> str:
        own = self.locations[loc].get("gstin")
        if own:
            return own
        return next(r["gstin"] for r in self.registrations if r.get("is_default") == "true")

    def amounts_at(self, item: str, qty: Any, price: Any, supplier: str, loc: str) -> dict[str, Decimal]:
        return self.award_value([(item, qty, price)], supplier, loc, self.gst_rate(item))

    def version(self, n: int, item: str, qty: Any, price: Any, need_by: date, supplier: str, loc: str, *,
                terms: str = "", delivery_terms: str | None = None, price_record_id: Any = None,
                contract_version: int | None = None) -> Version:  # fmt: skip
        return Version(n, item, D(qty), D(price), need_by, supplier, loc, terms, delivery_terms, price_record_id,
                       contract_version, self.amounts_at(item, qty, price, supplier, loc), self.buyer_gstin(loc))  # fmt: skip

    def as_snap(self, v: Version) -> k.Snap:
        """The block 4 material rule's view of a version (the band of its total)."""
        total = v.amounts["total"]
        return k.Snap(v.version, v.qty, v.price, v.need_by, total, self.band_max(total), self.band(total)["rule_code"],
                      v.supplier, v.place, v.terms)  # fmt: skip

    def reasons(self, base: Version, new: Version) -> list[str]:
        """Q19 from the last approved version, in the spec's order (block 4 formula, unchanged by block 7)."""
        return k.material(self.as_snap(base), self.as_snap(new), self.as_snap(base), self.date_days())

    def region_name(self, loc: str) -> str:
        return self.regions.get(self.region_of(loc), "")

    def contract_rows(self, item: str) -> list[dict[str, str]]:
        return [r for r in self.all_prices if r["item_code"] == item and r.get("contract_code") == CONTRACT]

    def contract_price(self, item: str, on: date = TODAY, activated: bool = False) -> tuple[Decimal, int] | None:  # type: ignore[override]
        """(price, version) of CNT-0001 in force on a day. The draft version counts only once activated; it then ends
        the version it replaces the day before it starts (D48)."""
        rows = [r for r in self.contract_rows(item) if activated or r.get("status", "active") != "draft"]
        starts = {int(r["version"]): r["valid_from"] for r in rows}
        best = None
        for r in rows:
            v = int(r["version"])
            later = [st for vv, st in starts.items() if vv > v]
            end = r.get("valid_to") or "9999-12-31"
            if later:
                end = min(end, (date.fromisoformat(min(later)) - timedelta(days=1)).isoformat())
            if r["valid_from"] <= on.isoformat() <= end and (best is None or v > best[1]):
                best = (D(r["price"]), v)
        return best


def self_check(check: b.Checker, key: str, mine: dict[str, Any]) -> None:
    """The oracle's figures against section 7's for the keys both name."""
    wrong = []
    for name, want in SPEC[key].items():
        if name not in mine or mine[name] is None:
            continue
        got = mine[name]
        same = D(got) == D(want) if b.dec(want) is not None and b.dec(got) is not None else str(got) == want
        if not same:
            wrong.append(f"{name} {got} (spec {want})")
    check(not wrong, f"oracle self-check {key}: {'; '.join(wrong)}", "medium")


def figures(v: Version, rule: str = "", approver: str = "") -> dict[str, Any]:
    return {**{x: str(y) for x, y in v.amounts.items()}, "unit_price": str(v.price), "qty": str(v.qty),
            "buyer_gstin": v.buyer_gstin, "rule": rule, "approver": approver}  # fmt: skip


# ---- the truth file -----------------------------------------------------------------------------------------------------

TRUTH = "truth/truth_po_versions.csv"


def compare_truth(check: b.Checker, co: "Co7", mine: dict[tuple[str, int], dict[str, Any]]) -> int:
    """`truth/truth_po_versions.csv` (spec 3.1: scenario, version, location, place, qty, unit_price,
    price_record_version, taxable, cgst, sgst, igst, total, rule, approver, material) against the oracle's rows of the
    same (scenario, version). Returns how many rows were compared; the file missing is a medium finding."""
    path = co.file(TRUTH)
    if not check(path is not None, f"the dataset has no {TRUTH} (spec 3.1: the generator writes it)", "medium"):
        return 0
    compared = 0
    for r in b.read_csv(path):
        try:
            key = (r.get("scenario", "").strip().upper(), int(r.get("version") or 0))
        except ValueError:
            continue
        m = mine.get(key)
        if m is None:
            continue
        compared += 1
        wrong = []
        for col in ("qty", "unit_price", "taxable", "cgst", "sgst", "igst", "total", "price_record_version"):
            if b.dec(r.get(col)) is not None and m.get(col) is not None and D(r[col]) != D(m[col]):
                wrong.append(f"{col} {r[col]} (oracle {m[col]})")
        for col in ("rule", "location"):
            if r.get(col) and m.get(col) and str(r[col]).strip() != str(m[col]):
                wrong.append(f"{col} {r[col]} (oracle {m[col]})")
        if r.get("approver") and m.get("approver") and co.code_of(r["approver"].strip()) not in (m["approver"],) \
                and r["approver"].strip() != m["approver"]:
            wrong.append(f"approver {r['approver']} (oracle {m['approver']})")
        if k.truthy(r.get("material")) is not None and m.get("material") is not None and k.truthy(r["material"]) != m["material"]:
            wrong.append(f"material {r['material']} (oracle {m['material']})")
        check(not wrong, f"truth file {key}: {'; '.join(wrong)}", "medium")
    check(compared > 0, f"truth file: no row of {sorted(mine)} could be compared with the oracle's", "medium")
    return compared


def truth_row(v: Version, rule: str = "", approver: str = "", material: bool | None = None) -> dict[str, Any]:
    return {**v.amounts, "qty": v.qty, "unit_price": v.price, "rule": rule, "approver": approver, "material": material,
            "location": v.place, "price_record_version": v.contract_version}  # fmt: skip


# ======================================================================================================================
# The platform
# ======================================================================================================================


def pin(d: date) -> None:
    s.pin(d)


class Co7(s.Co6):
    """A demo company with the block 7 calls."""

    # ---- reading ----
    def rows(self, folder: str, name: str) -> list[dict[str, str]]:
        """The reset's own dataset file, else the generated `data/datasets/<name>-v1/` copy (the reset writes its copy
        to a temporary folder, which a cleanup on the shared machine removed once during a batch run)."""
        path = self.file(f"{folder}/{name}.csv")
        return b.read_csv(path) if path else []

    def po_versions(self, no: str) -> dict[int, dict[str, Any]]:
        """`po_version` rows by version (none before block 7: the checks then fail, they do not crash)."""
        rows = self.qd("select * from po_version where po_no = :n order by version", n=no)
        for r in rows:
            r["snapshot"] = k.as_json(r.get("snapshot")) or {}
        return {int(r["version"]): r for r in rows}

    def links(self, no: str, after_id: int = 0) -> list[dict[str, Any]]:
        return self.qd("select * from supplier_link where po_no = :n and id > :i order by id", n=no, i=after_id)

    def link_mark(self) -> int:
        return int(self.one("select coalesce(max(id), 0) from supplier_link") or 0)

    def respond_links(self, no: str, after_id: int = 0) -> list[dict[str, Any]]:
        return [x for x in self.links(no, after_id) if str(x.get("purpose") or "respond") == "respond"]

    def document(self, doc_id: Any) -> dict[str, Any]:
        found = self.qd("select * from document where id = :i", i=int(doc_id)) if doc_id else []
        return found[0] if found else {}

    def audit_mark(self) -> int:
        return self.last_audit_id()

    def footprint(self, no: str) -> tuple[Any, ...]:
        """What a refused change must leave as it was: the order's version, status, header and total, its audit rows,
        links, version rows, revisions and approval steps (spec 7 P9 'nothing written')."""
        p = self.po_row(no)
        return (p.get("version"), p.get("status"), p.get("ship_to_location"), p.get("payment_terms_code"),
                p.get("delivery_terms"), str(p.get("total")), p.get("acknowledged_at"), self.entity_audits(no),
                len(self.links(no)), len(self.po_versions(no)), len(self.revisions(no)), len(self.steps(no)),
                tuple((str(x.get("qty")), str(x.get("unit_price")), str(x.get("need_by")), x.get("price_record_id"))
                      for x in self.qd("select * from po_line where po_no = :n order by line_no", n=no)))  # fmt: skip

    # ---- the API of section 8 ----
    def revise7(self, no: str, *, by: str | None = None, lines: list[dict[str, Any]] | None = None,
                header: dict[str, Any] | None = None, version: int | None = None, reason: str = "eval revision",
                extra: dict[str, Any] | None = None) -> Any:  # fmt: skip
        body: dict[str, Any] = {"lines": lines or [], "reason": reason}
        if header is not None:
            body["header"] = header
        if version is not None:
            body["version"] = version
        body.update(extra or {})
        r = self.call(by or self.buyer(), "POST", f"/api/purchase-orders/{no}/revise", body)
        self.work()
        return r

    def ack_app(self, no: str, version: int | None, by: str | None = None) -> Any:
        body: dict[str, Any] = {"promised_date": NEED_BY.isoformat(), "note": "eval: acknowledged on the phone"}
        if version is not None:
            body["version"] = version
        r = self.call(by or self.buyer(), "POST", f"/api/purchase-orders/{no}/acknowledge", body)
        self.work()
        return r

    def versions_api(self, no: str, by: str | None = None) -> tuple[int, list[dict[str, Any]]]:
        r = self.call(by or self.buyer(), "GET", f"/api/purchase-orders/{no}/versions")
        return r.status_code, as_rows(js(r) if r.status_code == 200 else [], "versions", "items")

    def compare(self, no: str, a: Any, b_: Any, by: str | None = None) -> Any:
        return self.call(by or self.buyer(), "GET", f"/api/purchase-orders/{no}/versions/compare?from={a}&to={b_}")

    def version_pdf(self, no: str, v: Any, by: str | None = None) -> Any:
        return self.call(by or self.buyer(), "GET", f"/api/purchase-orders/{no}/versions/{v}/pdf")

    def pdf(self, no: str, by: str | None = None) -> Any:
        return self.call(by or self.buyer(), "GET", f"/api/purchase-orders/{no}/pdf")

    def supplier_pdf(self, token: str) -> Any:
        return self.link_get(f"/supplier/{token}/pdf")

    def accept(self, token: str | None) -> Any:
        r = self.supplier(token or "", action="accept")
        self.work()
        return r

    # ---- mail ----
    def order_mails(self, no: str, since: int) -> list[dict[str, Any]]:
        """Mails about this order to a supplier contact (the order mail and its new versions)."""
        addresses = {r["email"].lower() for r in self.rows("master", "supplier_contact")}
        return [m for m in self.mails(since) if str(m.get("to", "")).lower() in addresses
                and no in str(m.get("subject") or "") + str(m.get("body") or "")]  # fmt: skip

    def last_order_mail(self, no: str, since: int) -> dict[str, Any]:
        found = [m for m in self.order_mails(no, since) if "/supplier/" in str(m.get("body") or "") + str(m.get("html") or "")]
        return found[-1] if found else {}

    def token_in(self, mail: dict[str, Any]) -> str | None:
        found = re.search(r"/supplier/([^\s<>\"'/]+)", str(mail.get("body") or "") + " " + str(mail.get("html") or ""))
        return found.group(1).rstrip(".,)") if found else None

    # ---- people and setup ----
    def grant(self, check: b.Checker, code: str, role: str, label: str) -> bool:
        """A second job through two administrators (as E54): the existing people API and a proposal."""
        admin, admin2 = self.holders("admin")[0], self.holders("admin")[1]
        r = self.call(admin, "GET", "/api/setup/people")
        people = {p["person"]["code"]: p for p in (js(r).get("people") or []) if isinstance(p, dict) and p.get("person")}
        if not check(code in people, f"{label}: {code} is not on /api/setup/people ({r.status_code})", "medium"):
            return False
        r = self.call(admin, "POST", f"/api/setup/people/{people[code]['user_id']}/roles", {"role": role, "reason": label})
        pid = js(r).get("proposal_id")
        if pid:
            self.call(admin2, "POST", f"/api/admin/proposals/{pid}/approve", {"note": label})
        self._headers.pop(code, None)  # a fresh login carries the new role
        r = self.call(admin, "GET", "/api/setup/people")
        roles = [x.get("role") for x in next((p.get("roles") or [] for p in (js(r).get("people") or [])
                                              if isinstance(p, dict) and (p.get("person") or {}).get("code") == code), [])]  # fmt: skip
        return check(role in roles, f"{label}: {code}'s roles after the grant {roles}; expected {role}", "medium")

    def activate_v2(self, check: b.Checker, label: str) -> bool:
        """CNT-0001's dataset draft version 2 sent by the plant head and activated by the director (D48, as E50)."""
        base = f"/api/contracts/{CONTRACT}"
        sent = self.call(self.login("planthead"), "POST", f"{base}/versions/submit")
        done = self.call(self.login("director"), "POST", f"{base}/versions/activate", {"note": label})
        self.work()
        return check(done.status_code == 200, f"{label}: activating {CONTRACT} version 2 answered {done.status_code} "
                     f"{done.text[:200]} (sent {sent.status_code} {sent.text[:120]})", "medium")  # fmt: skip

    def price_id(self, item: str, version: int) -> Any:
        return self.one("select id from price_record where contract_code = :c and item_code = :i and version = :v "
                        "order by id limit 1", c=CONTRACT, i=item, v=version)  # fmt: skip

    # ---- the scenarios' orders (spec 7) ----
    def q_order(self, check: b.Checker, label: str, kg: int = 120, price: str = "905.00") -> str:
        """Q(kg, price) as block 6 left it (block 4's steps; the business date 23 Nov)."""
        return self.quotation_order(check, kg, price, label)

    def k_order(self, check: b.Checker, item: str, qty: int, need_by: date, label: str, *, loc: str = LOC,
                by: str | None = None) -> str:  # fmt: skip
        """K(item, qty, need-by): a request approved through its chain, sourced on the contract by the worker, and the
        order it made."""
        req = self.ask(check, item, qty, need_by, label, by=by, loc=loc)
        orders = sorted({x["po_no"] for x in self.lines_ordered(req)}) if req else []
        check(len(orders) == 1, f"{label}: request {req} gave orders {orders}, expected one on the contract")
        return orders[0] if orders else ""

    def send_v(self, check: b.Checker, no: str, label: str) -> tuple[str | None, dict[str, Any], int]:
        """The buyer sends; returns (the respond link, the order mail, the supplier_link mark before)."""
        lm = self.link_mark()
        since = self.mark()
        r = self.send(no)
        check(r.status_code == 200, f"{label}: sending {no} answered {r.status_code} {r.text[:200]}")
        mail = self.last_order_mail(no, since)
        token = self.token_in(mail) if mail else None
        check(token is not None, f"{label}: the mail sending {no} carries no respond link")
        return token, mail, lm


# ======================================================================================================================
# Checks shared by the evals
# ======================================================================================================================


def amounts_ok(check: b.Checker, co: Co7, no: str, want: Version, label: str, sev: str = "high") -> bool:
    """The order's header amounts, line quantity and price against the oracle's version."""
    p, ln = co.po_row(no), co.po_line1(no)
    got = {x: D(p.get(x)) for x in ("taxable", "cgst", "sgst", "igst", "total")}
    want_a = {x: want.amounts[x] for x in got}
    return check(got == want_a and D(ln.get("qty")) == want.qty and D(ln.get("unit_price")) == want.price,
                 f"{label}: {no} amounts {got}, qty {ln.get('qty')}, price {ln.get('unit_price')}; oracle {want_a} for "
                 f"{want.qty} at {want.price}", sev)  # fmt: skip


def refusal(check: b.Checker, r: Any, codes: int | tuple[int, ...], words: str | None, label: str,
            sev_words: str = "medium") -> bool:  # fmt: skip
    codes = (codes,) if isinstance(codes, int) else codes
    ok = check(r.status_code in codes, f"{label}: answered {r.status_code} {r.text[:200]}, the spec says {codes}")
    if words is not None:
        check(norm(words) in norm(r.text), f"{label}: the refusal does not say '{words}': {r.text[:250]}", sev_words)
    return ok


def unchanged(check: b.Checker, co: Co7, no: str, before: tuple[Any, ...], label: str) -> None:
    after = co.footprint(no)
    check(after == before, f"{label}: a refused change wrote something: {before} -> {after}")


def link_versions(co: Co7, no: str, after_id: int) -> list[int]:
    return [int(x.get("po_version") or 0) for x in co.respond_links(no, after_id)]


def diff_set(body: Any) -> dict[tuple[str, int, str], tuple[Any, Any]]:
    """The compare endpoint's changes as (scope, line, field) -> (before, after) (spec section 8: header [{field, label,
    before, after}], lines [{line_no, change, fields: [...]}], charges [...]). A line's fields may also come as a
    mapping field -> {before, after}; an added or dropped line counts as one entry 'line'."""
    out: dict[tuple[str, int, str], tuple[Any, Any]] = {}
    body = k.as_json(body) if body is not None else {}
    if not isinstance(body, dict):
        return out

    def fields_of(x: Any) -> list[tuple[str, Any, Any]]:
        x = k.as_json(x)
        if isinstance(x, dict):
            return [(n, (v or {}).get("before"), (v or {}).get("after")) for n, v in x.items() if isinstance(v, dict)]
        return [(str(e.get("field") or e.get("name")), e.get("before"), e.get("after")) for e in x or []
                if isinstance(e, dict)]  # fmt: skip

    for name, before, after in fields_of(body.get("header")):
        out[("header", 0, field_name(name))] = (before, after)
    for ln in k.as_json(body.get("lines")) or []:
        if not isinstance(ln, dict):
            continue
        no = int(ln.get("line_no") or ln.get("line") or 0)
        change = str(ln.get("change") or "changed")
        if change in ("added", "dropped"):
            out[("line", no, change)] = (None, None)
        for name, before, after in fields_of(ln.get("fields") if ln.get("fields") is not None else ln.get("changes")):
            out[("line", no, field_name(name))] = (before, after)
    for i, ch in enumerate(k.as_json(body.get("charges")) or []):
        out[("charge", i, json.dumps(ch, default=str)[:60])] = (None, None)
    return out


def diff_matches(got: dict[tuple[str, int, str], tuple[Any, Any]], want: dict[tuple[str, int, str], tuple[Any, Any]],
                 ) -> tuple[list[str], list[str], list[str]]:  # fmt: skip
    """(missing, extra, wrong values) of the platform's differences against the oracle's."""
    missing = [f"{key}" for key in want if key not in got]
    extra = [f"{key}" for key in got if key not in want]
    wrong = [f"{key}: {got[key]} (oracle {want[key]})" for key in want if key in got
             and (value_of(got[key][0]), value_of(got[key][1])) != (value_of(want[key][0]), value_of(want[key][1]))]  # fmt: skip
    return missing, extra, wrong


def version_row(rows: list[dict[str, Any]], v: int) -> dict[str, Any]:
    return next((x for x in rows if int(x.get("version") or 0) == v), {})


def tenant_check7(check: b.Checker, mine: Co7, other: Co7, orders: list[str], tokens: list[str | None], since: int) -> None:
    """Spec 9.2: the other company gets 404 for the order, its versions, comparison and documents, and the order's
    links forged for it open nothing; no mail about this company's orders goes to the other's people."""
    by = other.buyer()
    for no in [x for x in orders if x]:
        own = other.one("select count(*) from po where no = :n", n=no)
        for path in (f"/api/purchase-orders/{no}", f"/api/purchase-orders/{no}/versions",
                     f"/api/purchase-orders/{no}/versions/compare?from=1&to=2", f"/api/purchase-orders/{no}/versions/1/pdf",
                     f"/api/purchase-orders/{no}/pdf"):  # fmt: skip
            r = other.call(by, "GET", path)
            if own and r.status_code == 200:  # the other's own order of the same number: never this company's figures
                theirs = js(r) if "pdf" not in path else {}
                check(not isinstance(theirs, dict) or D(theirs.get("total")) != D(mine.po_row(no).get("total"))
                      or theirs.get("supplier_code") != mine.po_row(no).get("supplier_code"),
                      f"tenant check: {other.name} reads {mine.name}'s {no} at {path}")  # fmt: skip
            else:
                check(r.status_code == 404, f"tenant check: {other.name}'s buyer asking {path} of {mine.name}'s {no} "
                      f"answered {r.status_code}", "high" if path.endswith(no) else "medium")  # fmt: skip
        before = mine.footprint(no)
        r = other.call(by, "POST", f"/api/purchase-orders/{no}/revise",
                       {"lines": [], "header": {"delivery_terms": "eval: from the other company"}, "reason": "x"})  # fmt: skip
        other.work()
        check(r.status_code in (403, 404) or own, f"tenant check: {other.name}'s buyer revising {mine.name}'s {no} "
              f"answered {r.status_code}")  # fmt: skip
        unchanged(check, mine, no, before, f"tenant check: {other.name}'s revise of {no}")
    for tok in [t for t in tokens if t]:
        forged = f.foreign_token(tok, mine.slug, other.slug)
        r = mine.link_get(f"/supplier/{forged}")
        check(r.status_code in REFUSED, f"tenant check: {mine.name}'s link forged for {other.name} answered "
              f"{r.status_code}", "medium")  # fmt: skip
        r = mine.link_get(f"/supplier/{forged}/pdf")
        check(r.status_code in REFUSED and r.content[:5] != b"%PDF-", f"tenant check: the forged link's PDF answered "
              f"{r.status_code}", "medium")  # fmt: skip
    nos = [x for x in orders if x]
    to_other = [m for m in mine.mails(since) if str(m.get("to", "")).lower().endswith("@" + other.domain)
                and any(no in str(m.get("subject") or "") + str(m.get("body") or "") for no in nos)]  # fmt: skip
    check(not to_other, f"tenant check: {len(to_other)} mail(s) about {mine.name}'s orders went to {other.name}: "
          f"{[m.get('subject') for m in to_other[:3]]}")  # fmt: skip


def start(co: Co7) -> None:
    pin(TODAY)
    co.work()
