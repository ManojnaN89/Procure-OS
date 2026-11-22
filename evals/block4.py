"""Shared pieces of the block 4 evals (E105-E111): approval bound to the purchase order version, a material change
approved again (Q19), the cumulative price-rise rule (DF-08), a one-time code on every email decision (D-07) and the PO
approval code. Written from the block 4 spec, the plan and the dataset only, never from the implementation.

Two halves, as in `block1` and `block2`:

- **The oracle** (`Oracle`, `Snap`, the rule functions): GST, the approval band and its person, the material reasons
  measured from the last approved version, the price judged from the first approved price, the variance value and when
  a code is needed, in plain code over the raw dataset files (`approval_rule.csv`, `item.csv`, `supplier.csv`,
  `gst_registration.csv`, `setting.csv`, `tolerance.csv`, `price_record.csv`, the company profile's approver roles).
  `Wrong` switches one rule to a deliberately wrong version: each eval shows that its cases tell the wrong rule from
  the right one and that the platform did what the right rule says (the mutation checks). The spec's own figures
  (`SPEC_AMOUNTS`, `SPEC_VARIANCE`) are a self-check of the oracle, and `truth/truth_approval_binding.csv` (written by
  the generator's own arithmetic) is compared with the oracle row by row.
- **The platform** (`Co`, built on `block2.Co`): a demo company made by `demo reset`, its people signed in with their
  own logins, the API the spec names (`/api/purchase-orders/{no}/approve|reject|revise|send|approval-code|controls|
  supplier-answer`, `/api/supplier-changes/{id}/decide`, the supplier's `/supplier/{token}` page, the approval link
  page `/approve/{token}`), the job worker run in this process, mail caught in this process (`lib.capture_mail`: the
  queued emails reach it when the worker runs, the one-time codes go to it straight), and read-only views of the
  tables section 7 lets an eval read. Test-only writes (ageing a code, forcing a revision row) use the owner's login
  (`lib.owner_session`).

Where the spec leaves a shape open (a person as code, user id, email or name; a money value as text or number; the
reasons as a list of strings), the readers accept every reasonable shape and say so in a comment.
"""

import json
import re
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal
from fractions import Fraction
from typing import Any

import block1 as b
import block2 as s2
import lib
import yaml
from block1 import D, dec, js
from sqlalchemy import text

CENT = Decimal("0.01")
NEED_BY = date(2026, 12, 14)  # every Apex scenario (spec 3.2 and 6.3)
LOC = "PLT-CKN"
WIRE = "RM-08"  # copper winding wire, Rs 920.00 standard, GST 18
SHAFT = "RM-05"  # pump shaft on the castings rate contract (V5)

# the spec's figures (6.3): the oracle must give the same, else the eval or the spec is wrong
SPEC_AMOUNTS = {  # (qty, unit price) -> (taxable, CGST, SGST, total), inside Maharashtra
    (120, "905.00"): ("108600.00", "9774.00", "9774.00", "128148.00"),
    (100, "905.00"): ("90500.00", "8145.00", "8145.00", "106790.00"),
    (120, "920.00"): ("110400.00", "9936.00", "9936.00", "130272.00"),
    (120, "925.00"): ("111000.00", "9990.00", "9990.00", "130980.00"),
    (120, "923.10"): ("110772.00", "9969.48", "9969.48", "130710.96"),
    (500, "905.00"): ("452500.00", "40725.00", "40725.00", "533950.00"),
    (480, "905.00"): ("434400.00", "39096.00", "39096.00", "512592.00"),
}
SPEC_VARIANCE = {("925.00", 120): "2400.00", ("923.11", 120): "2173.20"}  # (new price, qty) from base 905.00
SPEC_EDGE = (("923.10", True), ("923.11", False), ("920.00", True), ("925.00", False))  # from 905.00 at 2%


def money(value: Any) -> Decimal:
    return Decimal(str(value)).quantize(CENT, rounding=ROUND_HALF_UP)


def long_date(d: date) -> str:
    """'14 Dec 2026', as the spec writes `<dd Mon yyyy>`."""
    return f"{d.day:02d} {d:%b %Y}"


def norm(s: Any) -> str:
    """Words compared without case, spacing, a closing full stop or the sign of a date move ('moved -4 days')."""
    words = re.sub(r"\s+", " ", str(s or "")).strip().rstrip(".").lower()
    return re.sub(r"moved -(\d)", r"moved \1", words)


def as_list(value: Any) -> list[str]:
    """Reasons as stored: a jsonb list, its text, or None (the spec names a list of str)."""
    if value is None:
        return []
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            return [value] if value.strip() else []
    if isinstance(value, dict):  # a reason object: keep its words
        value = [value.get("text") or value.get("reason") or json.dumps(value)]
    return [str(x.get("text") or x.get("reason") or x) if isinstance(x, dict) else str(x) for x in value or []]


def as_json(value: Any) -> Any:
    if isinstance(value, str):
        try:
            return json.loads(value)
        except ValueError:
            return value
    return value


def truthy(value: Any) -> bool | None:
    if value in (None, ""):
        return None
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in ("true", "t", "yes", "1", "y")


# ======================================================================================================================
# The oracle
# ======================================================================================================================


@dataclass(frozen=True)
class Wrong:
    """Rules of block 4 switched to a wrong version, one at a time (the mutations the spec's eval table names)."""

    version_ignored: bool = False  # approve ignores the version the approver saw (E105)
    no_snapshot: bool = False  # no po_approved_version row on approval (E105)
    snapshot_first: bool = False  # the snapshot written for version 1 whatever was approved (E105)
    nonmaterial_restarts: bool = False  # a non-material change restarts approval (E106)
    material_keeps: bool = False  # a material change keeps the old approval (E106)
    previous_baseline: bool = False  # changes compared with the version before, not the approved one (E107)
    no_abs: bool = False  # only a later date counts; an earlier one is never material (E107)
    base_current: bool = False  # the price judged from the current price, not the first approved one (E108)
    variance_current: bool = False  # the variance value from the current price (E108)
    rule_reapproves: bool = False  # a revised order approved again by the touchless rule (E109)
    no_revision_check: bool = False  # `touchless.revision` missing from the checks (E109)
    code_above_only: bool = False  # a link needs a code only above the limit (E110, the old rule)
    reject_free: bool = False  # reject and return by link need no code (E110)
    code_after_writes: bool = False  # the PO code checked after the step is written (E111)
    ref_no_version: bool = False  # the PO code's ref without the version (E111)


RIGHT = Wrong()


@dataclass(frozen=True)
class Snap:
    """One version of a one-line order, as the material rule compares it (spec 6.2 `Snapshot`)."""

    version: int
    qty: Decimal
    price: Decimal
    need_by: date
    total: Decimal
    band_max: Decimal | None = None
    rule: str = ""
    supplier: str = ""
    ship_to: str = LOC
    terms: str = ""


def material(base: Snap | None, new: Snap, prev: Snap | None, days: int, w: Wrong = RIGHT) -> list[str]:
    """Spec 4.1 rule 4, in its order, against the last approved version (or the version before, for the mutation).
    The header reasons (supplier, location, terms) and item changes cannot happen through block 4's revise."""
    ref = prev if w.previous_baseline else base
    if ref is None:
        return []
    out = []
    if new.price > ref.price:
        out.append("line 1: price up")
    if new.qty > ref.qty:
        out.append("line 1: quantity up")
    if new.supplier and ref.supplier and new.supplier != ref.supplier:
        out.append("supplier changed")
    if new.ship_to != ref.ship_to:
        out.append("delivery location changed")
    if new.terms and ref.terms and new.terms != ref.terms:
        out.append("payment terms changed")
    moved = (new.need_by - ref.need_by).days
    if (moved > days) if w.no_abs else (abs(moved) > days):
        out.append(
            f"line 1: date moved {abs(moved)} days from the approved {long_date(ref.need_by)}, more than the {days} "
            "accepted"
        )
    if ref.band_max is not None and new.total > ref.band_max:
        out.append(f"the total is above the {ref.rule} band")  # "(up to Rs x)": the amount's format is open
    if new.total > ref.total:
        out.append("the total went up")
    return out


def price_rule(first: Decimal, current: Decimal, new: Decimal, pct: Decimal, qty: Decimal, w: Wrong = RIGHT) -> tuple[bool, Decimal]:
    """(within the buyer's tolerance, variance proposal value), spec 4.2: judged exactly from the first approved price;
    the value is (new - base) x the current quantity, half up to paise."""
    base = current if w.base_current else first
    within = Fraction(new) - Fraction(base) <= Fraction(base) * Fraction(pct) / 100
    vbase = current if (w.base_current or w.variance_current) else first
    return within, money((new - vbase) * qty)


def rise_pct(base: Decimal, new: Decimal) -> str:
    return str(money((new - base) * 100 / base))


def after_revision(reasons: list[str], was: str, w: Wrong = RIGHT) -> str:
    """The order's status after a revision of an order that was approved (or further), spec 4.1 rules 5-7."""
    if was in ("draft", "submitted"):
        return "submitted" if was == "submitted" else "draft"
    reapprove = bool(reasons)
    if w.nonmaterial_restarts:
        reapprove = True
    if w.material_keeps:
        reapprove = False
    if reapprove:
        return "submitted"
    return "sent" if was in ("sent", "acknowledged") else was


def link_needs_code(action: str, total: Decimal, limit: Decimal, w: Wrong = RIGHT) -> bool:
    """D-07 (spec 5.1, OP-9): every decision from an email link needs a code, whatever the amount."""
    if w.code_above_only:
        return action == "approve" and total > limit
    if w.reject_free:
        return action == "approve"
    return True


def code_ref(po: str, version: int, round_no: int, step: int, w: Wrong = RIGHT) -> str:
    return f"{po}:{round_no}:{step}" if w.ref_no_version else f"{po}:{version}:{round_no}:{step}"


class Oracle:
    """Amounts, bands, people and limits from the company's own dataset files and profile."""

    def __init__(self, co: "Co") -> None:
        self.co = co
        self.rules = co.rows("master", "approval_rule")
        self.items = {r["code"]: r for r in co.rows("master", "item")}
        self.suppliers = {r["code"]: r for r in co.rows("master", "supplier")}
        reg = co.rows("master", "gst_registration")[0]
        self.state = reg.get("state_code") or reg["gstin"][:2]
        self.settings = {r["key"]: r["value"] for r in co.rows("master", "setting")}
        self.tolerance = {
            r["key"]: r["value"] for r in co.rows("master", "tolerance") if r.get("category_code") in ("ANY", "", None)
        }
        self.prices = [r for r in co.rows("master", "price_record") if r.get("status", "active") != "draft"]
        profile = yaml.safe_load(
            (lib.ROOT / "datagen" / "catalog" / co.name / "company.yaml").read_text(encoding="utf-8")
        )
        self.profile = profile
        self.role_map = {r[0]: (r[1], r[2]) for r in profile.get("approver_roles") or []}
        self.location_head = (profile.get("location") or {}).get("head")

    # ---- settings ----
    def code_limit(self) -> Decimal:
        return D(self.settings.get("approval_otp_above", "500000.00"))

    def date_days(self) -> int:
        return int(D(self.tolerance.get("supplier_date_days", "3")))

    def price_pct(self) -> Decimal:
        return D(self.tolerance.get("supplier_price_pct", "2.00"))

    # ---- money ----
    def gst_rate(self, item: str, on: date = lib.TODAY) -> Decimal:
        it = self.items[item]
        changed = it.get("gst_rate_change_date") and date.fromisoformat(it["gst_rate_change_date"]) <= on
        return D(it["gst_rate_after"] if changed else it["gst_rate_before"])

    def amounts(self, item: str, qty: Any, price: Any, supplier: str) -> dict[str, Decimal]:
        """Spec 3.2: CGST = SGST = taxable x half the rate, each half up to paise, inside the state; IGST outside."""
        taxable = money(D(qty) * D(price))
        rate = self.gst_rate(item)
        if self.suppliers[supplier]["state_code"] == self.state:
            half = money(taxable * rate / 200)
            return {"taxable": taxable, "cgst": half, "sgst": half, "igst": Decimal("0.00"), "total": taxable + 2 * half}
        igst = money(taxable * rate / 100)
        return {"taxable": taxable, "cgst": Decimal("0.00"), "sgst": Decimal("0.00"), "igst": igst, "total": taxable + igst}

    def contract_price(self, item: str, on: date = lib.TODAY) -> Decimal | None:
        for r in self.prices:
            if r["item_code"] == item and r.get("contract_code") and r["valid_from"] <= on.isoformat() and (
                not r.get("valid_to") or r["valid_to"] >= on.isoformat()
            ):
                return D(r["price"])
        return None

    # ---- bands and people ----
    def bands(self, document: str = "po", on: date = lib.TODAY) -> list[dict[str, str]]:
        found = [
            r
            for r in self.rules
            if r["document"] == document
            and not r["approver_chain"].startswith("+")
            and r["effective_from"] <= on.isoformat()
            and (not r.get("effective_to") or r["effective_to"] >= on.isoformat())
        ]
        return sorted(found, key=lambda r: D(r["min_value"]))

    def band(self, total: Decimal) -> dict[str, str]:
        return next(r for r in self.bands() if D(r["min_value"]) <= total <= D(r["max_value"]))

    def band_max(self, total: Decimal) -> Decimal | None:
        """The approving band's max_value; None for the top band (spec 6.1)."""
        bands = self.bands()
        row = self.band(total)
        return None if row is bands[-1] else D(row["max_value"])

    def person(self, role: str) -> str:
        kind, value = self.role_map[role]
        if kind == "location_head":
            return str(self.location_head)
        return next(c for c, u in sorted(self.co.people.items()) if u.get("designation") == value and u.get("active", "true") == "true")

    def approver(self, total: Decimal, makers: frozenset[str] = frozenset()) -> tuple[str, str]:
        """(rule code, person) for an order that is not touchless: an 'auto' band moves up to the next (C1/_chain)."""
        started = False
        for row in self.bands():
            if not started and not D(row["min_value"]) <= total <= D(row["max_value"]):
                continue
            started = True
            if row["approver_chain"] == "auto":
                continue
            who = self.person(row["approver_chain"].split(">")[0])
            if who in makers:
                continue
            return row["rule_code"], who
        return "", ""

    def needs_code(self, total: Decimal) -> bool:
        return total > self.code_limit()

    def snap(self, version: int, qty: Any, price: Any, need_by: date, supplier: str, item: str = WIRE) -> Snap:
        a = self.amounts(item, qty, price, supplier)
        row = self.band(a["total"])
        return Snap(version, D(qty), D(price), need_by, a["total"], self.band_max(a["total"]), row["rule_code"], supplier)


def self_check(check: b.Checker, oracle: Oracle, supplier: str) -> None:
    """The oracle against the spec's own figures: a difference is an eval bug or a spec gap, never the platform's."""
    for (qty, price), want in SPEC_AMOUNTS.items():
        a = oracle.amounts(WIRE, qty, price, supplier)
        got = (a["taxable"], a["cgst"], a["sgst"], a["total"])
        check(got == tuple(Decimal(x) for x in want), f"oracle self-check: {qty} kg at {price} gives {got}, the spec "
              f"says {want}", "medium")  # fmt: skip
    for (new, qty), want in SPEC_VARIANCE.items():
        _, value = price_rule(Decimal("905.00"), Decimal("920.00"), Decimal(new), oracle.price_pct(), Decimal(qty))
        check(value == Decimal(want), f"oracle self-check: variance of {new} is {value}, the spec says {want}", "medium")
    for new, within in SPEC_EDGE:
        got, _ = price_rule(Decimal("905.00"), Decimal("905.00"), Decimal(new), oracle.price_pct(), Decimal(120))
        check(got == within, f"oracle self-check: {new} from 905.00 within={got}, the spec says {within}", "medium")
    check(oracle.date_days() == 3 and oracle.price_pct() == Decimal("2.00") and oracle.code_limit() == Decimal(500000),
          f"oracle self-check: tolerances {oracle.date_days()} days, {oracle.price_pct()}%, code limit "
          f"{oracle.code_limit()}; the spec's figures assume 3, 2.00 and 5,00,000.00", "medium")  # fmt: skip


# ---- the truth file -------------------------------------------------------------------------------------------------


def truth_row(scenario: str, s: Snap, oracle: Oracle, *, approver: str = "", reasons: list[str] | None = None,
              base: Decimal | None = None, within: bool | None = None, variance: Decimal | None = None) -> dict:  # fmt: skip
    a = oracle.amounts(WIRE, s.qty, s.price, s.supplier) if s.supplier else {}
    return {
        "scenario": scenario, "version": s.version, "qty": s.qty, "unit_price": s.price, "need_by": s.need_by.isoformat(),
        **a, "rule": s.rule, "approver": approver, "material": None if reasons is None else bool(reasons),
        "reasons": reasons, "base_price": base, "within_tolerance": within, "variance_value": variance,
        "needs_code": oracle.needs_code(s.total),
    }  # fmt: skip


def compare_truth(check: b.Checker, co: "Co", mine: list[dict[str, Any]]) -> int:
    """The generator's `truth/truth_approval_binding.csv` against this eval's oracle, for the rows of the same
    (scenario, version, unit price). Returns how many rows were compared."""
    path = co.file("truth/truth_approval_binding.csv")
    if not check(path is not None, "the dataset has no truth/truth_approval_binding.csv (spec 6.3)", "medium"):
        return 0
    rows = b.read_csv(path)
    compared = 0
    for r in rows:
        try:
            key = (r.get("scenario", "").strip().upper(), int(r.get("version") or 0))
        except ValueError:
            continue
        for m in mine:
            if (str(m["scenario"]).upper(), int(m["version"])) != key:
                continue
            if any(dec(r.get(col)) is not None and D(r[col]) != m[col] for col in ("unit_price", "qty")):
                continue  # another run or order of the same scenario and version (V4b's 923.11, O2's small order)
            compared += 1
            wrong = []
            for col in ("qty", "unit_price", "taxable", "cgst", "sgst", "igst", "total", "base_price", "variance_value"):
                if dec(r.get(col)) is not None and m.get(col) is not None and D(r[col]) != D(m[col]):
                    wrong.append(f"{col} {r[col]} (oracle {m[col]})")
            for col in ("need_by", "rule"):
                if r.get(col) and m.get(col) and str(r[col]).strip() != str(m[col]):
                    wrong.append(f"{col} {r[col]} (oracle {m[col]})")
            if r.get("approver") and m.get("approver") and co.code_of(r["approver"].strip()) != m["approver"]:
                wrong.append(f"approver {r['approver']} (oracle {m['approver']})")
            for col in ("material", "within_tolerance", "needs_code"):
                if truthy(r.get(col)) is not None and m.get(col) is not None and truthy(r[col]) != m[col]:
                    wrong.append(f"{col} {r[col]} (oracle {m[col]})")
            check(not wrong, f"truth file {key}: {'; '.join(wrong)}", "medium")
    return compared


def reasons_match(got: list[str], want: list[str]) -> bool:
    """Every reason the oracle gives is in the platform's list (its words contain the oracle's), and no more."""
    if len(got) != len(want):
        return False
    return all(any(norm(w) in norm(g) for g in got) for w in want)


# ======================================================================================================================
# The platform
# ======================================================================================================================


class Co(s2.Co):
    """A demo company with the block 4 calls."""

    box: Any = None  # the mail caught in this process (one for the run)

    def __init__(self, profile: str, prefix: str, client: Any, box: Any) -> None:
        self.box = box
        super().__init__(profile, prefix, client)

    # ---- people ----
    @property
    def domain(self) -> str:
        return next(iter(self.people.values()))["email"].partition("@")[2]

    def login_email(self, code: str) -> str:
        return self.people[code]["email"]

    # ---- reading ----
    def qd(self, sql: str, **p: Any) -> list[dict[str, Any]]:
        """Rows as dicts; a missing column or table (the block not built) gives no rows, not a crash."""
        try:
            self.db.rollback()
            found = [dict(r) for r in self.db.execute(text(sql), p).mappings().all()]
        except Exception:  # noqa: BLE001 - the shape the spec names may be missing: the checks say so
            self.db.rollback()
            return []
        self.db.rollback()
        return found

    def po_row(self, no: str) -> dict[str, Any]:
        found = self.qd("select * from po where no = :n", n=no)
        return found[0] if found else {}

    def po_line1(self, no: str) -> dict[str, Any]:
        found = self.qd("select * from po_line where po_no = :n and line_no = 1", n=no)
        return found[0] if found else {}

    def steps(self, no: str) -> list[dict[str, Any]]:
        return self.qd("select * from po_approval_step where po_no = :n order by round, step", n=no)

    def open_steps(self, no: str) -> list[dict[str, Any]]:
        return [s for s in self.steps(no) if s["status"] in ("pending", "waiting")]

    def revisions(self, no: str) -> dict[int, dict[str, Any]]:
        return {int(r["version"]): r for r in self.qd("select * from po_revision where po_no = :n order by version", n=no)}

    def approved_versions(self, no: str) -> list[dict[str, Any]]:
        return self.qd("select * from po_approved_version where po_no = :n order by version", n=no)

    def price_changes(self, no: str) -> list[dict[str, Any]]:
        return self.qd("select * from po_change where po_no = :n and kind = 'price' order by id", n=no)

    def audits(self, action: str, entity_id: str, after_id: int = 0) -> list[dict[str, Any]]:
        rows = self.qd(
            "select id, actor_type, actor_id, entity_id, after from audit_event where action = :a and entity_id = :e "
            "and id > :i order by id",
            a=action, e=entity_id, i=after_id,
        )  # fmt: skip
        for r in rows:
            r["after"] = as_json(r["after"]) or {}
        return rows

    def entity_audits(self, entity_id: str) -> int:
        return int(self.one("select count(*) from audit_event where entity_id = :e", e=entity_id) or 0)

    def snap_of(self, no: str) -> Snap:
        """The platform's current version of a one-line order (for messages, never for the oracle's figures)."""
        p, ln = self.po_row(no), self.po_line1(no)
        return Snap(int(p.get("version") or 0), D(ln.get("qty")), D(ln.get("unit_price")), ln.get("need_by") or NEED_BY,
                    D(p.get("total")), None, str(p.get("rule_code") or ""), str(p.get("supplier_code") or ""))  # fmt: skip

    def detail(self, no: str, by: str) -> dict[str, Any]:
        r = self.call(by, "GET", f"/api/purchase-orders/{no}")
        return js(r) if r.status_code == 200 else {}

    def history(self, no: str, by: str) -> dict[int, dict[str, Any]]:
        r = self.call(by, "GET", f"/api/purchase-orders/{no}/controls")
        return {int(h["version"]): h for h in (js(r).get("history") or []) if isinstance(h, dict) and "version" in h}

    # ---- the order's people ----
    def buyer(self) -> str:
        return self.role("buyer")

    # ---- acting ----
    def approve_po(self, by: str, no: str, version: int | None = None, code: str | None = None) -> Any:
        body: dict[str, Any] = {"comment": "eval"}
        if version is not None:
            body["version"] = version
        if code is not None:
            body["code"] = code
        return self.call(by, "POST", f"/api/purchase-orders/{no}/approve", body)

    def reject_po(self, by: str, no: str, version: int | None = None) -> Any:
        body: dict[str, Any] = {"reason": "eval: not this one"}
        if version is not None:
            body["version"] = version
        return self.call(by, "POST", f"/api/purchase-orders/{no}/reject", body)

    def revise(self, no: str, *, qty: Any = None, need_by: date | None = None, by: str | None = None) -> Any:
        line: dict[str, Any] = {"line_no": 1}
        if qty is not None:
            line["qty"] = str(qty)
        if need_by is not None:
            line["need_by"] = need_by.isoformat()
        return self.call(by or self.buyer(), "POST", f"/api/purchase-orders/{no}/revise",
                         {"lines": [line], "reason": "eval revision"})  # fmt: skip

    def send(self, no: str) -> Any:
        r = self.call(self.buyer(), "POST", f"/api/purchase-orders/{no}/send")
        self.work()
        return r

    def approval_code(self, by: str, no: str) -> Any:
        return self.call(by, "POST", f"/api/purchase-orders/{no}/approval-code", {})

    # ---- mail ----
    def mails(self, since: int = 0) -> list[dict[str, Any]]:
        return list(self.box.sent[since:])

    def mark(self) -> int:
        self.work()
        return len(self.box.sent)

    def supplier_token(self, no: str, since: int = 0) -> str | None:
        """The newest supplier link in a mail about this order (the respond link of the version just sent)."""
        for m in reversed(self.mails(since)):
            body = str(m.get("body") or "")
            if no in (str(m.get("subject") or "") + body) and "/supplier/" in body:
                found = re.search(r"/supplier/([^\s<>\"']+)", body)
                if found:
                    return found.group(1).rstrip(".,)")
        return None

    def supplier(self, token: str, **data: str) -> Any:
        self.db.rollback()
        r = self.client.post(f"/supplier/{token}", data=data)
        self.db.rollback()
        return r

    def supplier_page(self, token: str) -> Any:
        self.db.rollback()
        return self.client.get(f"/supplier/{token}")

    def changes_seen(self, no: str, by: str) -> list[dict[str, Any]]:
        r = self.call(by, "GET", f"/api/purchase-orders/{no}/supplier-answer")
        return [c for c in (js(r).get("changes") or []) if isinstance(c, dict)]

    def decide_change(self, by: str, change_id: int, accept: bool = True) -> Any:
        return self.call(by, "POST", f"/api/supplier-changes/{change_id}/decide", {"accept": accept})

    def code_mail(self, to: str, since: int) -> tuple[str | None, list[dict[str, Any]]]:
        """(the six-digit code, the mails since `since`) from the code mail to this address."""
        found = self.mails(since)
        for m in reversed(found):
            if str(m.get("to", "")).lower() == to.lower():
                code = re.search(r"\b(\d{6})\b", str(m.get("body") or ""))
                if code:
                    return code.group(1), found
        return None, found

    # ---- the quotation path (spec 3.2) ----
    def quotation_order(self, check: b.Checker, kg: int, price: str, label: str) -> str:
        """Q(kg, price): production asks, the director approves in the app, the RFQ goes out, the buyer types the quotes
        (the one to award at `price`, every other at price + 10) and awards; the worker makes and submits the order."""
        prod, buyer = self.designation("Production Manager"), self.buyer()
        body = {"lines": [{"item_code": WIRE, "qty": str(kg)}], "need_by": NEED_BY.isoformat(), "location": LOC,
                "reason": "Winding wire"}  # fmt: skip
        r = self.call(prod, "POST", "/api/requisitions", body)
        no = str(js(r).get("id") or "")
        if not check(r.status_code == 200 and bool(no), f"{label}: the request was not made: {r.status_code} {r.text[:200]}"):
            return ""
        r = self.call(prod, "POST", f"/api/requisitions/{no}/submit", {"override_reason": f"{label}: a separate case"})
        if r.status_code == 409 and "repeat" in r.text.lower():  # the same item as an earlier case: a separate need
            self.call(prod, "POST", f"/api/requisitions/{no}/duplicate-override", {"reason": f"{label}: a separate case"})
            r = self.call(prod, "POST", f"/api/requisitions/{no}/submit", {})
        check(r.status_code == 200, f"{label}: submitting {no} answered {r.status_code} {r.text[:200]}")
        status = self.approve(no)
        check(status == "approved", f"{label}: request {no} is {status} after its approvers acted, not approved")
        self.work()
        rfq = self.one("select no from rfq where request_no = :r order by no limit 1", r=no)
        if not check(rfq is not None, f"{label}: request {no} has no quotation request"):
            return ""
        proposal = self.one("select proposal_id from rfq where no = :n", n=rfq)
        if proposal:
            self.call(buyer, "POST", f"/api/admin/proposals/{proposal}/approve", {})
            self.work()
        invited = [x[0] for x in self.q("select supplier_code from rfq_supplier where rfq_no = :n order by 1", n=rfq)]
        if not check(bool(invited), f"{label}: {rfq} asked no supplier"):
            return ""
        pick = next((s for s in ("SUP-005", "SUP-003") if s in invited), invited[0])
        for supplier in invited:
            unit = price if supplier == pick else str(D(price) + 10)
            q = {"supplier_code": supplier, "valid_until": "2026-12-31", "freight": "0",
                 "lines": [{"rfq_line_no": 1, "unit_price": unit, "discount_pct": "0", "gst_rate": "18",
                            "delivery_days": 7}]}  # fmt: skip
            r = self.call(buyer, "POST", f"/api/rfqs/{rfq}/quotes", q)
            check(r.status_code == 200, f"{label}: quote of {supplier} on {rfq}: {r.status_code} {r.text[:150]}", "medium")
        r = self.call(buyer, "POST", f"/api/rfqs/{rfq}/award", {"choices": {"1": pick}})
        if r.status_code == 409 and "reason" in r.text.lower():  # fewer quotes than the route wants: the buyer says why
            r = self.call(buyer, "POST", f"/api/rfqs/{rfq}/award",
                          {"choices": {"1": pick}, "reason": f"{label}: the quotes we have are enough"})  # fmt: skip
        answer = js(r) if r.status_code == 200 else {}
        if answer.get("status") == "waiting" and answer.get("proposal_id"):  # block 0b: someone outside the order agrees
            self.call(self.designation("Plant Head"), "POST", f"/api/admin/proposals/{answer['proposal_id']}/approve", {})
        check(r.status_code == 200, f"{label}: award of {rfq}: {r.status_code} {r.text[:200]}")
        self.work()
        orders = [x[0] for x in self.q("select distinct po_no from po_line where request_no = :r order by 1", r=no)]
        if not check(len(orders) == 1, f"{label}: request {no} gave orders {orders}, expected one"):
            return ""
        po = orders[0]
        p, ln = self.po_row(po), self.po_line1(po)
        state = self.one("select state_code from supplier where code = :c", c=p.get("supplier_code"))
        check(
            str(state) == "27" and D(ln.get("qty")) == kg and D(ln.get("unit_price")) == D(price)
            and ln.get("need_by") == NEED_BY,
            f"{label}: precondition of {po}: supplier {p.get('supplier_code')} state {state}, qty {ln.get('qty')}, "
            f"price {ln.get('unit_price')}, need-by {ln.get('need_by')}; expected 27, {kg}, {price}, {NEED_BY}",
        )  # fmt: skip
        return str(po)

    def contract_order(self, check: b.Checker, item: str, qty: int, label: str) -> str:
        """A request for a contract item, approved by its rule or approver, and the order the worker makes from it."""
        prod = self.designation("Production Manager")
        body = {"lines": [{"item_code": item, "qty": str(qty)}], "need_by": NEED_BY.isoformat(), "location": LOC,
                "reason": "Shafts for the 0.5 HP pumps"}  # fmt: skip
        r = self.call(prod, "POST", "/api/requisitions", body)
        no = str(js(r).get("id") or "")
        if not check(r.status_code == 200 and bool(no), f"{label}: the request was not made: {r.status_code} {r.text[:200]}"):
            return ""
        self.call(prod, "POST", f"/api/requisitions/{no}/submit", {"override_reason": f"{label}: a separate case"})
        self.approve(no)
        self.work()
        orders = [x[0] for x in self.q("select distinct po_no from po_line where request_no = :r order by 1", r=no)]
        check(len(orders) == 1, f"{label}: request {no} gave orders {orders}, expected one from the contract")
        return str(orders[0]) if orders else ""


# ======================================================================================================================
# Checks shared by the evals
# ======================================================================================================================


@dataclass
class Mutations:
    """Each case: the observed value, what the right rule says and what each wrong rule says. A mutation is caught
    when some case's wrong answer differs from the right one and the platform gave the right one."""

    cases: list[tuple[str, str, bool]] = field(default_factory=list)

    def case(self, name: str, label: str, observed: Any, right: Any, wrong: Any) -> None:
        self.cases.append((name, label, right != wrong and observed == right))

    def report(self, check: b.Checker, out: Any, names: list[str]) -> None:
        for name in names:
            hits = [label for n, label, ok in self.cases if n == name and ok]
            check(bool(hits), f"mutation '{name}': no case of this eval tells it from the right rule with the platform "
                  "on the right side", "medium")  # fmt: skip
            out.metrics.setdefault("mutations caught", []).append(f"{name}: {hits[0]}" if hits else f"{name} (NOT caught)")


def check_amounts(check: b.Checker, co: Co, oracle: Oracle, no: str, qty: Any, price: Any, label: str) -> dict[str, Decimal]:
    """The order's header amounts against the oracle (and the line's quantity and price)."""
    p, ln = co.po_row(no), co.po_line1(no)
    want = oracle.amounts(WIRE, qty, price, str(p.get("supplier_code")))
    got = {k: D(p.get(k)) for k in want}
    check(got == want and D(ln.get("qty")) == D(qty) and D(ln.get("unit_price")) == D(price),
          f"{label}: {no} amounts {got}, qty {ln.get('qty')}, price {ln.get('unit_price')}; oracle {want} for {qty} at "
          f"{price}")  # fmt: skip
    return want


def check_reasons(check: b.Checker, label: str, got: Any, want: list[str], exact: tuple[str, ...] = ()) -> None:
    """The stored reasons against the oracle; the spec's own sentences in `exact` must appear word for word."""
    found = as_list(got)
    check(reasons_match(found, want), f"{label}: material reasons {found}, oracle {want}")
    for sentence in exact:
        check(any(norm(sentence) == norm(g) for g in found), f"{label}: the reason '{sentence}' is not written as the spec "
              f"says; platform {found}", "medium")  # fmt: skip
    if want and found and reasons_match(found, want):
        order = [next(i for i, g in enumerate(found) if norm(w) in norm(g)) for w in want]
        check(order == sorted(order), f"{label}: reasons {found} are not in the spec's order", "low")


def check_snapshot(check: b.Checker, co: Co, no: str, row: dict[str, Any], want: Snap, approved_by: str, label: str) -> None:
    """One po_approved_version row against the oracle's version (spec 6.1)."""
    snap = as_json(row.get("snapshot")) or {}
    lines = snap.get("lines") or [] if isinstance(snap, dict) else []
    ln = lines[0] if lines and isinstance(lines[0], dict) else {}
    got_by = str(row.get("approved_by") or "")
    who_ok = approved_by in got_by.split(",") or co.code_of(got_by) == approved_by or got_by == approved_by
    problems = []
    if int(row.get("version") or 0) != want.version:
        problems.append(f"version {row.get('version')} (oracle {want.version})")
    if D(row.get("total")) != want.total:
        problems.append(f"total {row.get('total')} (oracle {want.total})")
    if (dec(row.get("band_max")) if row.get("band_max") is not None else None) != want.band_max:
        problems.append(f"band_max {row.get('band_max')} (oracle {want.band_max})")
    if want.rule and str(row.get("rule_code") or "") != want.rule:
        problems.append(f"rule {row.get('rule_code')} (oracle {want.rule})")
    if not who_ok:
        problems.append(f"approved_by {got_by!r} (oracle {approved_by})")
    if D(ln.get("qty")) != want.qty or D(ln.get("unit_price")) != want.price or str(ln.get("need_by"))[:10] != want.need_by.isoformat():
        problems.append(f"snapshot line {ln} (oracle qty {want.qty}, price {want.price}, need-by {want.need_by})")
    if isinstance(snap, dict) and want.supplier and snap.get("supplier_code") != want.supplier:
        problems.append(f"snapshot supplier {snap.get('supplier_code')} (oracle {want.supplier})")
    if isinstance(snap, dict) and snap.get("ship_to_location") not in (want.ship_to, None):
        problems.append(f"snapshot ship-to {snap.get('ship_to_location')} (oracle {want.ship_to})")
    check(not problems, f"{label}: approved version row of {no}: {'; '.join(problems)}")


def stale_refused(check: b.Checker, co: Co, no: str, r: Any, label: str, before: tuple[Any, ...], now_version: int) -> None:
    """A 409 that names the current version and writes nothing (spec 4.1 rule 2)."""
    after = (co.entity_audits(no), tuple((s["round"], s["step"], s["status"]) for s in co.steps(no)), co.po_row(no).get("status"))
    check(r.status_code == 409, f"{label}: answered {r.status_code} {r.text[:200]}, expected 409 (the order changed)")
    check(f"version {now_version}" in r.text.lower() or "changed since you opened" in r.text.lower(),
          f"{label}: the refusal does not say the order changed (version {now_version}): {r.text[:200]}", "medium")  # fmt: skip
    check(after == before, f"{label}: a refused decision wrote something: {before} -> {after}")


def state_of(co: Co, no: str) -> tuple[Any, ...]:
    return co.entity_audits(no), tuple((s["round"], s["step"], s["status"]) for s in co.steps(no)), co.po_row(no).get("status")


def tenant_check(check: b.Checker, mine: Co, other: Co, docs: list[str], mail_from: int) -> None:
    """Spec 8.2: the other company gets 404 for the order (or, when it has an order of the same number, its own and not
    this one) and sees no mail about it."""
    by = other.buyer()
    for no in docs:
        if not no:
            continue
        r = other.call(by, "GET", f"/api/purchase-orders/{no}")
        if r.status_code == 200:  # the numbers start alike in every company: then it must be the other's own order
            theirs = js(r)
            ours = mine.po_row(no)
            check(D(theirs.get("total")) != D(ours.get("total")) or theirs.get("supplier_code") != ours.get("supplier_code"),
                  f"tenant check: {other.name} reads {mine.name}'s order {no}")  # fmt: skip
        else:
            check(r.status_code == 404, f"tenant check: {other.name} asking for {mine.name}'s {no} answered {r.status_code}")
        leaked = [m for m in mine.mails(mail_from) if str(m.get("to", "")).lower().endswith("@" + other.domain)
                  and no in str(m.get("subject", "")) + str(m.get("body", "")) and mine.po_row(no).get("supplier_code")
                  and str(mine.po_row(no).get("supplier_code")) in str(m.get("body", ""))]  # fmt: skip
        check(not leaked, f"tenant check: {len(leaked)} mail(s) about {mine.name}'s {no} went to {other.name}")
    to_other = [m for m in mine.mails(mail_from) if str(m.get("to", "")).lower().endswith("@" + other.domain)]
    check(not to_other, f"tenant check: {len(to_other)} mail(s) went to {other.name} while only {mine.name} acted: "
          f"{[m.get('subject') for m in to_other[:3]]}")  # fmt: skip


def version_bound_link(check: b.Checker, co: Co, token: str | None, label: str) -> None:
    """An older version's respond link is refused (block 0 behaviour the spec keeps: a new link per version)."""
    if not check(token is not None, f"{label}: no respond link to try"):
        return
    r = co.supplier(token or "", action="accept")
    check(r.status_code in (400, 409, 410), f"{label}: an older version's link answered {r.status_code}, expected a "
          "refusal")  # fmt: skip


def approve_ok(check: b.Checker, co: Co, by: str, no: str, version: int | None, label: str, code: str | None = None,
               want: str = "approved") -> bool:  # fmt: skip
    r = co.approve_po(by, no, version, code)
    co.work()
    return check(r.status_code == 200 and js(r).get("status") == want,
                 f"{label}: {by} approving {no} (version {version}) answered {r.status_code} {r.text[:200]}")  # fmt: skip


def send_ok(check: b.Checker, co: Co, no: str, label: str) -> str | None:
    """The buyer sends the order; returns the respond link mailed with it."""
    since = co.mark()
    r = co.send(no)
    check(r.status_code == 200, f"{label}: sending {no} answered {r.status_code} {r.text[:200]}")
    token = co.supplier_token(no, since)
    check(token is not None, f"{label}: the mail sending {no} carries no respond link")
    return token


def ask_price(check: b.Checker, co: Co, no: str, token: str | None, price: str, label: str) -> dict[str, Any]:
    """The supplier asks for a new price from its link; returns the change as the buyer sees it (with its row)."""
    if not check(token is not None, f"{label}: no link to ask {price} from"):
        return {}
    r = co.supplier(token or "", action="change", price_1=price, reason="copper went up")
    check(r.status_code == 200, f"{label}: asking {price} from the link answered {r.status_code} {r.text[:200]}")
    seen = [c for c in co.changes_seen(no, co.buyer()) if c.get("kind") == "price" and D(c.get("new_value")) == D(price)]
    rows = [c for c in co.price_changes(no) if D(c.get("new_value")) == D(price)]
    if not check(bool(seen) and bool(rows), f"{label}: the price change to {price} is not listed for the buyer"):
        return {}
    return {**seen[0], "row": rows[-1]}


def days(d: date, n: int) -> date:
    return d + timedelta(days=n)
