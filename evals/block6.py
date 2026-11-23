"""Shared pieces of the block 6 evals (E120-E127): quotes and supplier selection (flow step 6). Late bids and the
closing date, the no-response case at closing, quote validity at selection and at the order (DF-33), registered contacts
and sender authentication (D53), the path A/B case (D40, D43), the RFQ draft and its revalidation, the ranking and the
negotiation brief, and sourcing notes that never invent. Written from the block 6 spec, the plan and the dataset only,
never from the implementation.

Two halves, as in `block5`:

- **The oracle** (`Oracle6`, built on `block5.Oracle5`): closing dates on the company calendar (`block1.Calendar` over
  the holiday files), the last price paid per item and location from `history/po.csv` and `po_line.csv` (latest by date
  then order number, within 365 days), counting benchmarks from `market_benchmark.csv` (verified, at most
  `evidence_max_age_days` old, in the location's region or national, the line's unit), the gap test on the versioned
  `price_threshold_rule.csv` (every threshold must be passed: percent, rupees per unit, saving), the brief's target and
  saving, the A/B case figures, the order amounts (`Oracle5.award_value`) and its approver. `SPEC` holds the spec's own
  figures (section 8.3) as a self-check, and `truth/truth_quotes_selection.csv` (the generator's own arithmetic) is
  compared figure by figure.
- **The platform** (`Co6`, built on `block5.Co5`): a demo company made by `demo reset`, its people signed in with their
  own logins, the API section 9 names, mail caught in this process, incoming mail stored through the mailbox's own
  `emails.ingest` and read by the worker with a scripted model (`aif.Model`, never a live one unless `--live`), the
  daily `rfq_reminders` job run at a chosen business date, and read-only views of the tables section 9 lets an eval read.

Where the spec leaves a shape open (a person as code, user id, email or name; money as text or number; a figure's JSON
key; a list of problems as strings or objects) the readers accept every reasonable shape and say so in a comment.
"""

import contextlib
import json
import re
import uuid
from collections.abc import Iterator
from datetime import UTC, date, datetime, time, timedelta
from decimal import ROUND_HALF_UP, Decimal
from email.message import EmailMessage
from email.utils import format_datetime
from typing import Any

import aif
import block1 as b
import block3 as b3
import block4 as k
import block5 as f
from block1 import D, js

TODAY = f.TODAY  # Mon 23 Nov 2026, every scenario's business date unless it says (spec 8.3)
LOC = "PLT-CKN"
WIRE, CASTING = "RM-08", "RM-01"
R_QTY = 500
R_NEED = date(2026, 12, 21)
A_NEED = date(2027, 1, 15)
URGENT_NEED = date(2026, 11, 30)
VALID_UNTIL = "2026-12-31"
CENT, Q4 = Decimal("0.01"), Decimal("0.0001")
IST = b.IST
REFUSED = (403, 404, 409, 422)
AUTHSERV = "mx.apexpumps.example"
REMINDER_JOB = "rfq_reminders"

# the spec's figures (8.3); the oracle must give the same, else the eval or the spec is wrong
SPEC: dict[str, dict[str, str]] = {
    "N1": {"l1": "960.00", "last_paid": "905.91", "last_paid_order": "PO-CKN-2627-00199", "last_paid_date": "2026-10-05",
           "gap": "54.0900", "gap_pct": "5.6344", "saving": "27045.00", "target": "905.91",
           "saving_at_target": "27045.00", "l2": "975.00"},
    "N2": {"market": "897.0000", "market_gap": "63.0000", "market_gap_pct": "6.5625", "market_saving": "31500.00",
           "target": "897.00", "saving_at_target": "31500.00"},
    "N4": {"saving": "21636.00"},
    "V1": {"taxable": "487500.00", "cgst": "43875.00", "sgst": "43875.00", "igst": "0.00", "total": "575250.00",
           "rule": "PO-03", "approver": "U09"},
    "A1": {"contract_price": "650.00", "market": "600.0000", "gap": "50.0000", "gap_pct": "7.6923", "saving": "30000.00"},
    "A2": {"taxable": "390000.00", "cgst": "35100.00", "sgst": "35100.00", "igst": "0.00", "total": "460200.00",
           "rule": "PO-02", "approver": "U08"},
}  # fmt: skip


# ---- words and numbers -------------------------------------------------------------------------------------------------


def long_date(d: date) -> str:
    return k.long_date(d)  # '05 Oct 2026', the spec's <dd Mon yyyy>


def norm(s: Any) -> str:
    return f.norm(s)


def canon(s: Any) -> str:
    """Words compared without case or spacing, the rupee written one way, every number by its value (so '₹30,000.00',
    'Rs 30000' and '30,000' read alike, and '05 Oct' like '5 Oct'), and no closing full stop. Used to compare a
    template's words with the spec's when the spec does not fix how a number is printed."""
    words = norm(s).rstrip(".")
    words = words.replace("rs ", "rs")

    def value(m: re.Match[str]) -> str:
        try:
            v = Decimal(m.group(0).replace(",", ""))
        except ArithmeticError:
            return m.group(0)
        return format(v.normalize(), "f")

    return re.sub(r"\d[\d,]*(?:\.\d+)?", value, words)


def rupees(x: Any) -> str:
    """'Rs 30,000.00' (norm() reads '₹' the same way)."""
    return "Rs " + b3.inr(D(x))


def pct2(x: Decimal) -> str:
    return str(Decimal(x).quantize(CENT, rounding=ROUND_HALF_UP))


def q4(x: Decimal) -> Decimal:
    return Decimal(x).quantize(Q4, rounding=ROUND_HALF_UP)


def money(x: Any) -> Decimal:
    return k.money(x)


def text_of(value: Any) -> str:
    """Problems, assumptions or a payload as one lower-case text (a list of strings or of objects, or JSON text)."""
    value = k.as_json(value)
    if isinstance(value, str):
        return value.lower()
    return json.dumps(value, default=str, ensure_ascii=False).lower()


def held(record: Any, *values: Any) -> list[str]:
    """The values (numbers) a record does NOT hold anywhere in its JSON (rounded to 0, 2 and 4 places too)."""
    nums = b3.record_numbers(k.as_json(record) or {})
    return [str(v) for v in values if D(v).normalize() not in nums]


# ======================================================================================================================
# The oracle
# ======================================================================================================================


class Oracle6(f.Oracle5):
    """Closing dates, last prices paid, counting benchmarks, the gap test and the figures of briefs and A/B cases."""

    def __init__(self, co: "Co6") -> None:
        super().__init__(co)
        self.cal = co.calendar()
        self.benchmarks = co.rows("master", "market_benchmark")
        self.threshold_rows = co.rows("master", "price_threshold_rule")
        self.company = co.rows("master", "company")[0]
        self.contacts_all = co.rows("master", "supplier_contact")

    # ---- closing dates ----
    def due_from(self, d: date) -> date:
        """`default_rfq_response_days` working days after a business date (spec 5.1 rule 5)."""
        return self.cal.add(d, int(D(self.settings.get("default_rfq_response_days", "5"))))

    def next_working(self, d: date, n: int = 1) -> date:
        return self.cal.add(d, n)

    def non_working_after(self, d: date) -> date:
        x = d + timedelta(days=1)
        while self.cal.working(x):
            x += timedelta(days=1)
        return x

    # ---- names ----
    def supplier_name(self, code: str) -> str:
        s = self.suppliers[code]
        return s.get("trade_name") or s["legal_name"]

    def contact(self, code: str) -> dict[str, str]:
        return self.contacts[code]

    def contact_email(self, code: str) -> str:
        return self.contacts[code]["email"].lower()

    def item_name(self, item: str) -> str:
        return self.items[item]["name"]

    def location_name(self, loc: str) -> str:
        return self.locations[loc]["name"]

    # ---- the threshold rule (versioned; the version in force on the day, category first) ----
    def threshold(self, category: str, on: date) -> dict[str, str]:
        found = [r for r in self.threshold_rows if r["effective_from"] <= on.isoformat()
                 and (not r.get("effective_to") or r["effective_to"] >= on.isoformat())]  # fmt: skip
        own = [r for r in found if r.get("category_code") == category]
        pick = own or [r for r in found if not r.get("category_code")]
        return max(pick, key=lambda r: int(r["version"]))

    def gap_test(self, price: Decimal, ref: Decimal, qty: Decimal, category: str, on: date,
                 on_price: Decimal | None = None) -> dict[str, Any]:  # fmt: skip
        """`price_gap.test` as the spec describes it: the gap per unit (4 places), the percent (4 places) on `on_price`
        (the L1 price for a brief, the contract price for an A/B case), the saving on the quantity (paise); triggered
        when every threshold is passed (above, not equal)."""
        rule = self.threshold(category, on)
        gap = q4(price - ref)
        base = on_price if on_price is not None else price
        pct = q4((price - ref) * 100 / base) if base else Decimal(0)
        saving = money((price - ref) * qty)
        go = gap > 0 and pct > D(rule["gap_pct"]) and gap > D(rule["gap_inr_per_unit"]) and saving > D(rule["min_saving_inr"])
        return {"gap": gap, "pct": pct, "saving": saving, "go": go, "rule": rule}

    # ---- references ----
    def last_paid(self, item: str, loc: str, on: date) -> dict[str, Any] | None:
        """Spec 6.4 rule 2a: the last price paid for the item at the location within 365 days before the business date,
        from the loaded history (the eval makes no live order of the item before it asks), latest by date then order
        number. Orders not draft or cancelled; lines not cancelled."""
        start = on - timedelta(days=365)
        found = []
        for ln in self.history_lines:
            po = self.history_po.get(ln["po_no"])
            if ln["item_code"] != item or po is None or po.get("ship_to_location") != loc:
                continue
            if po.get("status") in ("draft", "cancelled") or ln.get("status") == "cancelled":
                continue
            when = date.fromisoformat(po["created_at"][:10])
            if start <= when <= on:
                found.append((when, po["no"], D(ln["unit_price"])))
        if not found:
            return None
        when, no, price = max(found)
        return {"price": price, "order": no, "date": when}

    def counting_benchmarks(self, item: str, loc: str, uom: str, on: date, category: str,
                            extra: list[dict[str, str]] = ()) -> list[dict[str, Any]]:  # fmt: skip
        """D41/D52: verified, at most `evidence_max_age_days` old on the business date, in the location's region (or with
        no region), in the line's unit. `extra` are benchmarks the eval entered and had verified through the API."""
        age = int(D(self.threshold(category, on)["evidence_max_age_days"]))
        region = self.region_of(loc)
        out = []
        for r in [*self.benchmarks, *extra]:
            if r["item_code"] != item or r.get("status") != "verified" or r["uom"] != uom:
                continue
            eff = date.fromisoformat(str(r["effective_date"])[:10])
            if eff > on or (on - eff).days > age:
                continue
            if r.get("region_code") not in ("", None, region):
                continue
            out.append({**r, "price_d": D(r["price"]), "date": eff})
        return sorted(out, key=lambda r: (r["price_d"], r["date"]))

    # ---- the negotiation brief (spec 6.4) ----
    def brief(self, l1: tuple[str, Decimal], l2: tuple[str, Decimal] | None, item: str, loc: str, qty: Decimal,
              on: date, extra: list[dict[str, str]] = (), target_rule: str = "lowest") -> dict[str, Any] | None:  # fmt: skip
        """None when no reference triggers. `target_rule` 'l2' is the planted mutation (the target is the next offer)."""
        category = self.items[item]["category_code"]
        uom = self.items[item]["purchase_uom"]
        price = l1[1]
        refs = []
        last = self.last_paid(item, loc, on)
        if last:
            t = self.gap_test(price, last["price"], qty, category, on)
            if t["go"]:
                refs.append({"kind": "history", "price": last["price"], "order": last["order"], "date": last["date"], **t})
        bench = self.counting_benchmarks(item, loc, uom, on, category, extra)
        if bench:
            low = bench[0]
            t = self.gap_test(price, low["price_d"], qty, category, on)
            if t["go"]:
                refs.append({"kind": "market", "price": low["price_d"], "source": low["source_name"], "date": low["date"],
                             **t})  # fmt: skip
        if not refs:
            return None
        target = money(min(r["price"] for r in refs))
        if target_rule == "l2" and l2:
            target = money(l2[1])
        return {"l1": l1, "l2": l2, "refs": refs, "target": target, "saving_at_target": money((price - target) * qty),
                "uom": uom, "qty": qty, "item": item}  # fmt: skip

    def brief_text(self, br: dict[str, Any], line: int = 1) -> str:
        """Spec 6.4 rule 4, the eval's own words."""
        sup, price = br["l1"]
        parts = []
        for r in br["refs"]:
            if r["kind"] == "history":
                parts.append(f"the last price paid, {rupees(r['price'])} on {r['order']} of {long_date(r['date'])} "
                             f"({pct2(r['pct'])}% gap)")  # fmt: skip
            else:
                parts.append(f"the market benchmark, {rupees(r['price'])} ({r['source']}, {long_date(r['date'])}; "
                             f"{pct2(r['pct'])}% gap)")  # fmt: skip
        out = (f"{self.supplier_name(sup)} is lowest on line {line} ({self.item_name(br['item'])}) at {rupees(price)} per "
               f"{br['uom']}, above " + " and ".join(parts) + f". Ask for {rupees(br['target'])} per {br['uom']}: "
               f"{rupees(br['saving_at_target'])} less on {b.fq(br['qty'])} {br['uom']}.")  # fmt: skip
        if br["l2"]:
            out += f" The next offer is {rupees(br['l2'][1])} from {self.supplier_name(br['l2'][0])}."
        return out

    # ---- the A/B case (spec 6.1) ----
    def path_case(self, item: str, loc: str, qty: Decimal, on: date, need_by: date,
                  extra: list[dict[str, str]] = ()) -> dict[str, Any] | None:  # fmt: skip
        """None when the line stays on the contract (no counting benchmark, the gap not above every threshold, or
        urgent: the need-by sooner than the price's lead days plus the RFQ days, in working days)."""
        cp = next((r for r in self.all_prices if r["item_code"] == item and r.get("contract_code")
                   and r.get("status", "active") == "active" and r["valid_from"] <= on.isoformat()
                   and (not r.get("valid_to") or r["valid_to"] >= on.isoformat())), None)  # fmt: skip
        if cp is None:
            return None
        category = self.items[item]["category_code"]
        bench = self.counting_benchmarks(item, loc, cp["uom"], on, category, extra)
        if not bench:
            return None
        price = D(cp["price"])
        low = bench[0]
        t = self.gap_test(price, low["price_d"], qty, category, on, on_price=price)
        lead = int(D(cp.get("lead_days") or self.items[item].get("default_lead_days") or 0))
        rfq_days = int(D(self.settings.get("default_rfq_response_days", "5")))
        urgent = need_by <= self.cal.add(on, lead + rfq_days)
        if not t["go"] or urgent:
            return None
        return {"contract": cp["contract_code"], "supplier": cp["supplier_code"], "price": price, "market": low["price_d"],
                "source": low["source_name"], "date": low["date"], "uom": cp["uom"], "qty": qty, "item": item, **t}  # fmt: skip

    def path_text(self, pc: dict[str, Any], line: int = 1) -> str:
        return (f"Line {line}, {self.item_name(pc['item'])}: contract {pc['contract']} with "
                f"{self.supplier_name(pc['supplier'])} at {rupees(pc['price'])} per {pc['uom']} fits, but the market is "
                f"lower: {rupees(pc['market'])} per {pc['uom']} ({pc['source']}, {long_date(pc['date'])}), a gap of "
                f"{rupees(pc['gap'])} ({pct2(pc['pct'])}%), {rupees(pc['saving'])} on {b.fq(pc['qty'])} {pc['uom']}. "
                "Recommended: path B, ask for quotes. Path A buys on the contract; the contract stays valid either way.")  # fmt: skip

    # ---- the extension mail (spec 5.1 rule 6) ----
    def extension_words(self, rfq: str, new: date, supplier: str) -> tuple[str, str]:
        """(subject, body up to the company name). The company may be named by its legal or short name."""
        contact = self.contacts[supplier]["name"]
        subject = f"Closing date extended: request for quotation {rfq}"
        body = (f"Dear {contact}, the closing date of our request for quotation {rfq} is now {long_date(new)}. If you "
                "have not quoted yet, please reply by then. A quote you have sent stands unless you revise it. Regards, "
                "Procurement,")  # fmt: skip
        return subject, body

    def company_names(self) -> set[str]:
        return {self.company.get("legal_name", ""), self.company.get("short_name", "")} - {""}


def self_check(check: b.Checker, scenario: str, mine: dict[str, Any]) -> None:
    """The oracle's figures against the spec's own (8.3) for the keys both name."""
    want = SPEC[scenario]
    wrong = []
    for key, v in want.items():
        if key not in mine:
            continue
        got = mine[key]
        same = (D(got) == D(v)) if b.dec(v) is not None and b.dec(got) is not None else str(got) == v
        if not same:
            wrong.append(f"{key} {got} (spec {v})")
    check(not wrong, f"oracle self-check {scenario}: {'; '.join(wrong)}", "medium")


def brief_figures(br: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {"l1": br["l1"][1], "target": br["target"], "saving_at_target": br["saving_at_target"]}
    if br["l2"]:
        out["l2"] = br["l2"][1]
    for r in br["refs"]:
        if r["kind"] == "history":
            out.update(last_paid=r["price"], last_paid_order=r["order"], last_paid_date=r["date"].isoformat(),
                       gap=r["gap"], gap_pct=r["pct"], saving=r["saving"])  # fmt: skip
        else:
            out.update(market=q4(r["price"]), market_gap=r["gap"], market_gap_pct=r["pct"], market_saving=r["saving"])
    return out


# ---- the truth file ----------------------------------------------------------------------------------------------------

ALIASES = {  # the spec names the file's columns, not its figure names: each of the eval's keys by the names likely used
    "last_paid": ("last_paid", "last_price_paid", "last_paid_price", "history_price", "last_price"),
    "last_paid_order": ("last_paid_order", "last_paid_po", "order", "po_no", "last_order"),
    "last_paid_date": ("last_paid_date", "last_paid_on", "order_date", "po_date"),
    "gap": ("gap", "gap_per_unit", "history_gap", "gap_inr", "last_paid_gap"),
    "gap_pct": ("gap_pct", "history_gap_pct", "last_paid_gap_pct", "gap_percent"),
    "saving": ("saving", "history_saving", "last_paid_saving"),
    "market": ("market", "market_price", "benchmark", "benchmark_price"),
    "market_gap": ("market_gap", "benchmark_gap", "market_gap_per_unit"),
    "market_gap_pct": ("market_gap_pct", "benchmark_gap_pct"),
    "market_saving": ("market_saving", "benchmark_saving"),
    "target": ("target", "target_price"),
    "saving_at_target": ("saving_at_target", "target_saving"),
    "l1": ("l1", "l1_price"),
    "l2": ("l2", "l2_price"),
    "contract_price": ("contract_price", "contract"),
    "taxable": ("taxable", "po_taxable"),
    "cgst": ("cgst", "po_cgst"),
    "sgst": ("sgst", "po_sgst"),
    "igst": ("igst", "po_igst"),
    "total": ("total", "po_total"),
    "rule": ("rule", "rule_code", "approval_rule"),
    "approver": ("approver",),
}


def compare_truth(check: b.Checker, co: "Co6", scenario: str, mine: dict[str, Any]) -> int:
    """`truth/truth_quotes_selection.csv` rows of a scenario against the oracle's figures of the same name. Returns how
    many were compared; the file missing is a medium finding (spec 3, 8.3: the generator writes it)."""
    path = co.file("truth/truth_quotes_selection.csv")
    if not check(path is not None, "the dataset has no truth/truth_quotes_selection.csv (spec 3, 8.3)", "medium"):
        return 0
    compared = 0
    names = {alias: key for key, aliases in ALIASES.items() for alias in aliases}
    for r in b.read_csv(path):
        if r.get("scenario", "").strip().upper() != scenario:
            continue
        fig = re.sub(r"[^a-z0-9]+", "_", r.get("figure", "").strip().lower()).strip("_")
        key = names.get(fig, fig)
        if key not in mine:
            continue
        compared += 1
        want, got = r.get("value", "").strip(), mine[key]
        same = (D(want) == D(got)) if b.dec(want) is not None and b.dec(got) is not None else want == str(got)
        if key == "approver" and not same:
            same = co.code_of(want) == str(got)
        check(same, f"truth file {scenario} {r.get('figure')}: {want}; oracle {got}", "medium")
    check(compared > 0, f"truth file: no figure of {scenario} could be compared with the oracle's", "low")
    return compared


# ======================================================================================================================
# The platform
# ======================================================================================================================


def pin(d: date) -> None:
    f.pin(d)


@contextlib.contextmanager
def pinned(d: date) -> Iterator[None]:
    with f.pinned(d):
        yield


def quote_answer(item: str, price: Any, valid_until: str = VALID_UNTIL) -> dict[str, Any]:
    """What a careful reader of the supplier's mail writes (the quote reading step's answer shape, as E34 uses it)."""
    return {"intent": "quote", "lines": [{"item": item, "unit_price": str(price), "discount_pct": None, "gst_rate": "18",
            "delivery_days": 5, "price_includes_gst": False}], "valid_until": valid_until, "confidence": 0.95}  # fmt: skip


class Co6(f.Co5):
    """A demo company with the block 6 calls."""

    # ---- reading ----
    def quotes(self, rfq: str, supplier: str | None = None) -> list[dict[str, Any]]:
        rows = self.qd("select * from quote where rfq_no = :r order by supplier_code, revision", r=rfq)
        return [q for q in rows if supplier is None or q["supplier_code"] == supplier]

    def quote_row(self, no: str) -> dict[str, Any]:
        found = self.qd("select * from quote where no = :n", n=no)
        return found[0] if found else {}

    def drafts(self, email_id: Any) -> list[dict[str, Any]]:
        return self.qd("select * from quote_draft where email_id = :e order by id", e=int(email_id or 0))

    def email_row(self, email_id: Any) -> dict[str, Any]:
        found = self.qd("select * from email_message where id = :e", e=int(email_id or 0))
        return found[0] if found else {}

    def tasks(self, kind: str, status: str | None = None) -> list[dict[str, Any]]:
        where = " and status = :s" if status else ""
        found = self.qd(f"select * from task where kind = :k{where} order by id", k=kind, s=status)
        for t in found:
            t["payload"] = k.as_json(t.get("payload")) or {}
            t["escalation_chain"] = k.as_json(t.get("escalation_chain")) or []
        return found

    def tasks_of(self, kind: str, key: str, status: str | None = None) -> list[dict[str, Any]]:
        """Tasks of a kind whose payload names this document (rfq_no, request, key) or whose title does."""
        return [t for t in self.tasks(kind, status) if key in json.dumps(t["payload"], default=str) or key in str(t["title"])]

    def recs(self, kind: str, key: str | None = None) -> list[dict[str, Any]]:
        found = self.qd("select * from recommendation where kind = :k order by id", k=kind)
        for r in found:
            r["engine_figures"] = k.as_json(r.get("engine_figures")) or {}
            r["rule_versions"] = k.as_json(r.get("rule_versions")) or {}
        return [r for r in found if key is None or r["business_key"] == key]

    def live_rec(self, kind: str, key: str) -> dict[str, Any]:
        """The record in force: proposed (or decided), not stale, withdrawn or superseded."""
        found = [r for r in self.recs(kind, key) if r.get("status") not in ("stale", "withdrawn", "superseded")
                 and not r.get("superseded_by")]  # fmt: skip
        return found[-1] if found else {}

    def model_mark(self) -> int:
        return int(self.one("select coalesce(max(id), 0) from model_call") or 0)

    def model_calls_since(self, mark: int) -> list[dict[str, Any]]:
        return self.qd("select * from model_call where id > :i order by id", i=mark)

    def org(self, key: str) -> str | None:
        found = self.qd("select value from org_setting where key = :k", k=key)
        return None if not found else str(found[0]["value"] or "")

    def lines_ordered(self, request: str) -> list[dict[str, Any]]:
        return self.qd("select l.*, p.supplier_code, p.status as po_status, p.touchless, p.touchless_note, p.rule_code, "
                       "p.contract_code as po_contract from po_line l join po p on p.no = l.po_no where l.request_no = :r "
                       "order by l.po_no, l.line_no", r=request)  # fmt: skip

    def decision_rows(self, request: str) -> list[dict[str, Any]]:
        return self.qd("select * from sourcing_decision where request_no = :r order by line_no, id", r=request)

    def supplier_mails(self, since: int) -> list[dict[str, Any]]:
        """Mail to any supplier contact address of this company (the eval's own list from the dataset)."""
        addresses = {r["email"].lower() for r in self.rows("master", "supplier_contact")}
        return [m for m in self.mails(since) if str(m.get("to", "")).lower() in addresses]

    # ---- the request and its RFQ ----
    def ask(self, check: b.Checker, item: str, qty: Any, need_by: date, label: str, *, by: str | None = None,
            loc: str = LOC) -> str:  # fmt: skip
        """A request made, submitted (a repeat of an earlier case is a separate need) and approved through its chain."""
        who = by or self.login("production")
        body = {"lines": [{"item_code": item, "qty": str(qty)}], "need_by": need_by.isoformat(), "location": loc,
                "reason": f"{label}: eval"}  # fmt: skip
        r = self.call(who, "POST", "/api/requisitions", body)
        no = str(js(r).get("id") or js(r).get("no") or "")
        if not check(r.status_code in (200, 201) and bool(no), f"{label}: the request was not made: {r.status_code} "
                     f"{r.text[:200]}"):  # fmt: skip
            return ""
        r = self.call(who, "POST", f"/api/requisitions/{no}/submit", {"override_reason": f"{label}: a separate case"})
        if r.status_code == 409 and "repeat" in r.text.lower():
            self.call(who, "POST", f"/api/requisitions/{no}/duplicate-override", {"reason": f"{label}: a separate case"})
            r = self.call(who, "POST", f"/api/requisitions/{no}/submit", {})
        check(r.status_code == 200, f"{label}: submitting {no} answered {r.status_code} {r.text[:200]}", "medium")
        status = self.approve(no)
        check(status == "approved", f"{label}: request {no} is {status} after its approvers acted, not approved", "medium")
        self.work()
        return no

    def rfq_of(self, request: str) -> str:
        return str(self.one("select no from rfq where request_no = :r order by no limit 1", r=request) or "")

    def r_rfq(self, check: b.Checker, label: str, qty: Any = R_QTY, confirm: bool = True) -> tuple[str, str]:
        """R (spec 8.3): production asks for RM-08 at the plant, need-by 21 Dec 2026, approved, sourced; the buyer
        confirms the send. Returns (request, rfq)."""
        req = self.ask(check, WIRE, qty, R_NEED, label)
        rfq = self.rfq_of(req) if req else ""
        if not check(bool(rfq), f"{label}: request {req} made no quotation request"):
            return req, ""
        invited = sorted(s["supplier_code"] for s in self.invited(rfq))
        check(invited == ["SUP-001", "SUP-003", "SUP-005"], f"{label}: {rfq} invites {invited}; the spec's R invites "
              "SUP-003, SUP-005, SUP-001", "medium")  # fmt: skip
        if confirm:
            check(self.confirm(self.login("buyer"), rfq), f"{label}: the buyer could not confirm sending {rfq}")
        return req, rfq

    def due(self, rfq: str) -> date | None:
        return b.day(self.rfq(rfq).get("due_date"))

    # ---- quotes ----
    def quote6(self, rfq: str, supplier: str, price: Any, *, valid_until: str | None = VALID_UNTIL,
               by: str | None = None) -> Any:  # fmt: skip
        """Spec 8.3: GST 18, freight 0, valid until 31 Dec 2026, delivery 5 days; entered by the buyer. No worker run."""
        body: dict[str, Any] = {"supplier_code": supplier, "freight": "0",
                                "lines": [{"rfq_line_no": 1, "unit_price": str(price), "discount_pct": "0",
                                           "gst_rate": "18", "delivery_days": 5}]}  # fmt: skip
        if valid_until:
            body["valid_until"] = valid_until
        return self.call(by or self.login("buyer"), "POST", f"/api/rfqs/{rfq}/quotes", body)

    def award6(self, rfq: str, supplier: str, reason: str | None = None, work: bool = True) -> Any:
        body: dict[str, Any] = {"choices": {"1": supplier}}
        if reason:
            body["reason"] = reason
        r = self.call(self.login("buyer"), "POST", f"/api/rfqs/{rfq}/award", body)
        if work:
            self.work()
        return r

    def extend(self, rfq: str, new: date | str | None, reason: str | None, by: str | None = None) -> Any:
        body: dict[str, Any] = {}
        if new is not None:
            body["due_date"] = new.isoformat() if isinstance(new, date) else new
        if reason is not None:
            body["reason"] = reason
        r = self.call(by or self.login("buyer"), "POST", f"/api/rfqs/{rfq}/extend", body)
        self.work()
        return r

    # ---- mail coming in ----
    def mail_in(self, rfq: str, sender: str, body: str, model: aif.Model, *, auth: tuple[str, ...] = (),
                notes: list[str] | None = None) -> tuple[int | None, list[dict[str, Any]]]:  # fmt: skip
        """A supplier's reply to the RFQ's plus address, stored by the mailbox's own `emails.ingest` on the business date
        in force, then read by the worker with the scripted model. `auth` are `Authentication-Results` headers, topmost
        first. When the worker leaves no draft for the mail (the job not queued by intake) the reading step is called
        on its own session as a sweep would, and `notes` says so. Returns (email id, the drafts of that mail)."""
        from p2p.platform.emails import plus_address

        msg = EmailMessage()
        for h in auth:
            msg["Authentication-Results"] = h
        msg["From"] = sender
        msg["To"] = plus_address("rfq", rfq)
        msg["Subject"] = f"Re: Request for quotation {rfq}"
        msg["Date"] = format_datetime(datetime.now(UTC))
        msg["Message-ID"] = f"<eval-{uuid.uuid4().hex}@{sender.partition('@')[2] or 'supplier.example'}>"
        msg.set_content(body)
        with aif.no_live_models(model):
            email_id = aif.ingest(self, msg.as_bytes())
            self.work()
            drafts = self.drafts(email_id)
            if email_id is not None and not drafts:
                from p2p.formulas.calendar import business_today
                from p2p.sourcing import replies

                db = aif.new_session(self)
                try:
                    replies.read_reply(db, self.slug, email_id, aif.gateway(model), as_of=business_today())
                    db.commit()
                finally:
                    db.close()
                self.work()
                drafts = self.drafts(email_id)
                if notes is not None:
                    notes.append("the worker read no reply after intake; the reading step was called as a sweep would")
        return email_id, drafts

    def confirm_draft(self, draft_id: Any, by: str | None = None) -> Any:
        r = self.call(by or self.login("buyer"), "POST", f"/api/quote-drafts/{draft_id}/confirm", {})
        self.work()
        return r

    # ---- the daily job ----
    def daily(self, day: date) -> list[str | None]:
        """The daily `rfq_reminders` job (which also runs the closing check, spec 5.3) at 09:30 India time on this
        business day, run the way the worker claims and finishes it. The business date stays pinned to `day`."""
        from p2p.platform import worker

        pin(day)
        at = datetime.combine(day, time(9, 30), tzinfo=IST)
        self.db.commit()
        worker.load_handlers()
        if REMINDER_JOB not in worker.HANDLERS:
            return ["no handler for rfq_reminders"]
        worker.enqueue(self.slug, REMINDER_JOB, {}, at)
        errors = aif.run_jobs(self.slug, [REMINDER_JOB], now=at + timedelta(minutes=1))
        pin(day)
        self.db.rollback()
        return errors

    # ---- settings that wait for a second administrator ----
    def set_policy(self, check: b.Checker, key: str, value: str, label: str) -> bool:
        """Two administrators (D-22). When the API refuses (the key unknown before block 6), the value is written as
        the owner so the rest of the scenario can still be tested, and the refusal is a finding."""
        asked, done = self.policy(key, value)
        ok = asked.status_code == 202 and done is not None and done.status_code == 200
        check(ok, f"{label}: setting {key} by two administrators answered {asked.status_code} "
              f"{getattr(done, 'status_code', None)} {asked.text[:150]}", "medium")  # fmt: skip
        if not ok:
            aif.set_org(self, key, value)
        return self.org(key) == value

    # ---- benchmarks ----
    def benchmark(self, check: b.Checker, item: str, price: str, uom: str, eff: date, region: str, source: str,
                  label: str) -> dict[str, str] | None:  # fmt: skip
        """Entered by the buyer, verified by the purchase manager (block 0b API). Returns the row as the oracle reads it."""
        body = {"item_code": item, "price": price, "uom": uom, "basis": "delivered", "effective_date": eff.isoformat(),
                "region_code": region or None, "source_kind": "market_survey", "source_name": source,
                "source_ref": label, "note": f"synthetic, {label}"}  # fmt: skip
        r = self.call(self.login("buyer"), "POST", "/api/benchmarks", body)
        bid = js(r).get("id")
        if not check(r.status_code in (200, 201) and bool(bid), f"{label}: entering the benchmark answered {r.status_code} "
                     f"{r.text[:200]}", "medium"):  # fmt: skip
            return None
        v = self.call(self.login("purchasemanager"), "POST", f"/api/benchmarks/{bid}/verify", {"note": label})
        check(v.status_code == 200, f"{label}: verifying benchmark {bid} answered {v.status_code} {v.text[:200]}", "medium")
        self.work()
        return {"item_code": item, "price": price, "uom": uom, "effective_date": eff.isoformat(), "region_code": region,
                "source_name": source, "status": "verified" if v.status_code == 200 else "unverified"}  # fmt: skip

    def verify_benchmark(self, check: b.Checker, item: str, eff: date, label: str) -> dict[str, str] | None:
        found = self.qd("select id from market_benchmark where item_code = :i and effective_date = :d and status = "
                        "'unverified' order by id", i=item, d=eff)  # fmt: skip
        if not check(bool(found), f"{label}: no unverified {item} benchmark of {eff} to verify", "medium"):
            return None
        bid = found[0]["id"]
        v = self.call(self.login("purchasemanager"), "POST", f"/api/benchmarks/{bid}/verify", {"note": label})
        check(v.status_code == 200, f"{label}: verifying benchmark {bid} answered {v.status_code} {v.text[:200]}", "medium")
        self.work()
        row = next(r for r in self.rows("master", "market_benchmark") if r["item_code"] == item
                   and r["effective_date"] == eff.isoformat())  # fmt: skip
        return {**row, "status": "verified"}

    # ---- a supplier's status through its proposal and a second person ----
    def block_supplier(self, check: b.Checker, code: str, label: str) -> bool:
        r = self.call(self.login("buyer"), "POST", f"/api/vendors/{code}/status", {"to": "blocked", "reason": label})
        for p in self.qd("select id, payload from proposal where action = 'supplier.set_status' and status = 'pending' "
                         "order by id"):  # fmt: skip
            if code in json.dumps(k.as_json(p["payload"]), default=str):
                self.call(self.login("director"), "POST", f"/api/admin/proposals/{p['id']}/approve", {"note": label})
        self.work()
        status = self.one("select status from supplier where code = :c", c=code)
        return check(status == "blocked", f"{label}: {code} is {status} after the status proposal ({r.status_code} "
                     f"{r.text[:150]})", "medium")  # fmt: skip


# ======================================================================================================================
# Checks shared by the evals
# ======================================================================================================================


def person_is(co: Co6, got: Any, code: str) -> bool:
    return f.same_person(co, got, code)


def chain_codes(co: Co6, chain: Any) -> list[str]:
    out = []
    for x in k.as_json(chain) or []:
        if isinstance(x, dict):
            x = x.get("user_id") or x.get("id") or x.get("code") or x.get("email")
        out.append(co.code_of(x) or str(x))
    return out


def codes(value: Any) -> list[str]:
    """Supplier codes in a payload list (strings or objects)."""
    out = []
    for x in k.as_json(value) or []:
        if isinstance(x, dict):
            x = x.get("supplier_code") or x.get("code") or x.get("supplier")
        out.append(str(x))
    return sorted(out)


def no_model_call(check: b.Checker, co: Co6, model: aif.Model, calls0: int, mark: int, label: str) -> bool:
    new_rows = [c for c in co.model_calls_since(mark) if "quote" in str(c.get("step") or "")]
    return check(model.calls == calls0 and not new_rows, f"{label}: the model was called {model.calls - calls0} "
                 f"time(s), {len(new_rows)} model_call row(s); the spec says the mail is not read", "high")  # fmt: skip


def tenant_tasks(check: b.Checker, mine: Co6, other: Co6, keys: list[str]) -> None:
    """The other company sees no task, recommendation or exception row naming this company's documents."""
    for key in [x for x in keys if x]:
        theirs = other.qd("select id from task where payload::text like :p or title like :p", p=f"%{key}%")
        own_doc = other.one("select count(*) from rfq where no = :n", n=key) or other.one(
            "select count(*) from request where no = :n", n=key)  # fmt: skip
        if own_doc:  # numbers start alike in every company: the other's own document of that number
            continue
        check(not theirs, f"tenant check: {other.name} holds {len(theirs)} task(s) naming {mine.name}'s {key}")
        r = other.call(other.login("buyer"), "GET", "/api/exceptions")
        check(key not in r.text, f"tenant check: {other.name}'s exceptions screen names {mine.name}'s {key}")
