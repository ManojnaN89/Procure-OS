"""Shared pieces of the block 5 evals (E112-E119): an RFQ is never sent unconfirmed (D42), period RFQs without a request,
award approval by band, award letters and regret notes (D45), the draft rate contract the supplier accepts and a second
person activates (D48, D53), and the award note that never invents. Written from the block 5 spec, the plan and the
dataset only, never from the implementation.

Two halves, as in `block4`:

- **The oracle** (`Oracle5`, built on `block4.Oracle`): the award value (taxable per part half up to paise, CGST + SGST
  inside the location's state, IGST outside), the approver (the `po` rows of `approval_rule.csv` on the value with GST,
  an `auto` band moved up to the next band that names a person, the buyer never), who is invited (approved for the
  category from `supplier_category.csv`, inside `supplier_category_scope.csv`, active, prices on file first then by
  code, `max(3, min_quotes)` of them), `min_quotes` (the category's own figure and the plan's D37 bands: 2 from
  Rs 25,000, 3 above Rs 2,00,000), the months of a period and the suggested quantity (spec 5.2: the 12 full months of
  orders before the creation month from `history/po.csv` and `po_line.csv`, pro rata, against the open requirements of
  the period, rounded up). `SPEC` holds the spec's own figures (section 8.3) as a self-check of the oracle, and
  `truth/truth_period_sourcing.csv` (written by the generator's own arithmetic) is compared row by row.
- **The platform** (`Co5`, built on `block4.Co`): a demo company made by `demo reset`, its people signed in with their
  own logins, the API section 9 names, the worker run in this process, mail caught in this process, read-only views of
  the tables section 9 lets an eval read, and the owner's login for test-only writes (planting a stored setting, ageing
  an invitation, trying a forbidden row).

Where the spec leaves a shape open (a person as code, user id, email or name; money as text or number; a JSON key the
spec names only in words), the readers accept every reasonable shape and say so in a comment.
"""

import base64
import contextlib
import json
import math
import re
from collections.abc import Iterator
from datetime import date
from decimal import Decimal
from typing import Any

import block1 as b
import block3 as b3
import block4 as k
from block1 import D, js

TODAY = date(
    2026, 11, 23
)  # the business date of every scenario unless it says (spec 8.3)
LOC = "PLT-CKN"
WIRE, SEAL = "RM-08", "RM-07"
P_FROM, P_TO = date(2027, 1, 1), date(2027, 3, 31)
VALID_UNTIL = "2026-12-31"
BW_LOC = "OFF-BLR"
BW_FROM, BW_TO = date(2027, 4, 1), date(2028, 3, 31)
CENT = Decimal("0.01")
REFUSED = (403, 404, 409, 422)

# the spec's figures (8.3); the oracle must give the same, else the eval or the spec is wrong
SPEC: dict[str, dict[str, str]] = {
    "P1": {"taxable": "792000.00", "cgst": "71280.00", "sgst": "71280.00", "igst": "0.00", "total": "934560.00",
           "rule": "PO-03", "approver": "U09"},
    "P2": {"taxable": "805500.00", "cgst": "72495.00", "sgst": "72495.00", "igst": "0.00", "total": "950490.00",
           "rule": "PO-03", "approver": "U09"},
    "P4": {"taxable": "84000.00", "cgst": "0.00", "sgst": "0.00", "igst": "15120.00", "total": "99120.00",
           "rule": "PO-02", "approver": "U08"},
    "B1": {"taxable": "246000.00", "cgst": "0.00", "sgst": "0.00", "igst": "44280.00", "total": "290280.00",
           "rule": "PO-02", "approver": "U09"},
    "P1-use": {"taxable": "105600.00", "cgst": "9504.00", "sgst": "9504.00", "igst": "0.00", "total": "124608.00",
               "rule": "PO-02", "approver": "U08"},
}  # fmt: skip
SPEC_INVITED = {"P1": {"SUP-003", "SUP-005", "SUP-001"}, "P4": {"SUP-002", "SUP-001", "SUP-003"},
                "B1": {"SUP-002", "SUP-001"}}  # fmt: skip
SPEC_MIN_QUOTES = {"P1": 3, "P4": 2, "B1": 3}

PERIOD_SENTENCE = (
    "Prices are asked for the period {f} to {t}, for orders placed during it. The quantities are our estimate for the "
    "period, not a commitment to buy."
)
NOTICE = "This is a notice of selection, not an order and not a commitment to buy."
REGRET = (
    "Dear {contact}, thank you for your quotation {quote} for {rfq}. After comparing the offers we have not selected "
    "yours this time. We value your interest and hope to invite you again."
)


def long_date(d: date) -> str:
    return k.long_date(d)  # '01 Jan 2027', the spec's <dd Mon yyyy>


def norm(s: Any) -> str:
    """Words compared without case or spacing, and with the rupee written one way (format_inr may give '₹ ')."""
    words = re.sub(r"\s+", " ", str(s or "")).strip()
    words = re.sub(r"(₹\s?|Rs\.\s?|INR\s)", "Rs ", words)
    return words.lower()


def money(value: Any) -> Decimal:
    return k.money(value)


def same_person(co: "Co5", got: Any, code: str) -> bool:
    """A person named as code, user id, email or name (the spec does not fix the shape)."""
    if got in (None, ""):
        return False
    if isinstance(got, dict):
        return any(
            same_person(co, got.get(x), code)
            for x in ("code", "user_id", "id", "email", "name")
        )
    return str(got) == code or co.code_of(got) == code


def months_in(f: date, t: date) -> int:
    """Whole calendar months a period covers; a part month counts as one (spec 5.2)."""
    return (t.year * 12 + t.month) - (f.year * 12 + f.month) + 1


def month_key(d: date, shift: int = 0) -> str:
    n = d.year * 12 + (d.month - 1) + shift
    return f"{n // 12:04d}-{n % 12 + 1:02d}"


# ======================================================================================================================
# The oracle
# ======================================================================================================================


class Oracle5(k.Oracle):
    """Values, bands, people, invitees and suggested quantities from the company's own dataset files and profile."""

    def __init__(self, co: "Co5") -> None:
        super().__init__(co)
        self.locations = {r["code"]: r for r in co.rows("master", "location")}
        self.categories = {r["code"]: r for r in co.rows("master", "category")}
        self.approved = co.rows("master", "supplier_category")
        self.scopes = co.rows("master", "supplier_category_scope")
        self.contacts = {
            r["supplier_code"]: r
            for r in co.rows("master", "supplier_contact")
            if r.get("is_primary") == "true"
        }
        self.all_prices = co.rows("master", "price_record")
        self.history_po = {r["no"]: r for r in co.rows("history", "po")}
        self.history_lines = co.rows("history", "po_line")

    # ---- money ----
    def award_value(
        self, parts: list[tuple[str, Any, Any]], supplier: str, loc: str, rate: Any = 18
    ) -> dict[str, Decimal]:
        """Spec 6.1 rule 2: per part taxable = qty x net price half up; GST at the quoted rate, CGST + SGST (each half of
        the rate, half up) when the supplier's state is the location's, else IGST; summed over the parts. (Whether the
        GST is rounded per part or on the sum is not said; the spec's scenarios give the same either way.)"""
        intra = (
            self.suppliers[supplier]["state_code"] == self.locations[loc]["state_code"]
        )
        out = {x: Decimal("0.00") for x in ("taxable", "cgst", "sgst", "igst")}
        for _item, qty, price in parts:
            taxable = money(D(qty) * D(price))
            out["taxable"] += taxable
            if intra:
                half = money(taxable * D(rate) / 200)
                out["cgst"] += half
                out["sgst"] += half
            else:
                out["igst"] += money(taxable * D(rate) / 100)
        out["total"] = out["taxable"] + out["cgst"] + out["sgst"] + out["igst"]
        return out

    def award_approver(self, total: Decimal, buyer: str) -> tuple[str, str]:
        """OP-1 interim: the `po` rows on the value with GST; an auto band moves up; never the buyer (C1)."""
        return self.approver(total, frozenset({buyer}))

    # ---- who is asked ----
    def region_of(self, loc: str) -> str:
        return self.locations[loc].get("region_code", "")

    def estimate(self, item: str, loc: str, on: date) -> Decimal:
        """Contract price in force at the location, else the last price paid (history), else the standard price."""
        for r in self.all_prices:
            if (r["item_code"] == item and r.get("contract_code") and r.get("status", "active") == "active"
                    and r["valid_from"] <= on.isoformat() and (not r.get("valid_to") or r["valid_to"] >= on.isoformat())
                    and (r.get("location_code") in ("", None, loc) and r.get("region_code") in ("", None, self.region_of(loc)))):  # fmt: skip
                return D(r["price"])
        paid = sorted((self.history_po[ln["po_no"]]["created_at"], ln["unit_price"]) for ln in self.history_lines
                      if ln["item_code"] == item and ln["po_no"] in self.history_po)  # fmt: skip
        return D(paid[-1][1]) if paid else D(self.items[item]["standard_price"])

    def min_quotes(self, category: str, value: Decimal) -> int:
        """The category's own figure and the plan's D37 bands (2 from Rs 25,000, 3 above Rs 2,00,000; spec 5.1 rule 4)."""
        band = 3 if value > 200000 else 2 if value >= 25000 else 1
        own = int(
            D(
                self.categories.get(category, {}).get("min_quotes")
                or self.settings.get("min_quotes_default", "2")
            )
        )
        return max(own, band)

    def eligible(self, category: str, loc: str, on: date) -> list[str]:
        found = []
        for r in self.approved:
            code = r["supplier_code"]
            if (
                r["category_code"] != category
                or (r.get("approved_from") or "") > on.isoformat()
            ):
                continue
            if self.suppliers.get(code, {}).get("status", "active") != "active":
                continue
            scope = [
                s
                for s in self.scopes
                if s["supplier_code"] == code and s["category_code"] == category
            ]
            if scope and not any(s.get("location_code") == loc or (s.get("region_code") and s["region_code"] == self.region_of(loc))
                                 for s in scope):  # fmt: skip
                continue
            found.append(code)
        return sorted(set(found))

    def invitees(
        self, category: str, items: list[str], loc: str, on: date, min_quotes: int
    ) -> list[str]:
        """Prices on file first, then by code; max(rfq_supplier_count 3, min_quotes) of them (spec 5.1 rule 4)."""
        pool = self.eligible(category, loc, on)
        priced = {
            r["supplier_code"] for r in self.all_prices if r["item_code"] in items
        }
        ranked = [c for c in pool if c in priced] + [c for c in pool if c not in priced]
        return ranked[: max(3, min_quotes)]

    # ---- the suggested quantity ----
    def ordered_12m(self, item: str, loc: str, created: date) -> Decimal:
        """Order quantity of the item at the location over the 12 full calendar months before the creation month
        (orders not draft, submitted or cancelled; lines not cancelled), from the dataset's history (the eval orders
        nothing of these items before it asks)."""
        first, last = month_key(created, -12), month_key(created, -1)
        total = Decimal(0)
        for ln in self.history_lines:
            po = self.history_po.get(ln["po_no"])
            if (
                ln["item_code"] != item
                or po is None
                or po.get("ship_to_location") != loc
            ):
                continue
            if (
                po.get("status") in ("draft", "submitted", "cancelled")
                or ln.get("status") == "cancelled"
            ):
                continue
            if first <= po["created_at"][:7] <= last:
                total += D(ln["qty"])
        return total

    @staticmethod
    def suggested(open_need: Decimal, ordered: Decimal, months: int) -> Decimal:
        """max(open need, ordered x months / 12), rounded up to a whole purchase unit (spec 5.2)."""
        return Decimal(math.ceil(max(open_need, ordered * months / 12)))


def open_need(co: "Co5", item: str, loc: str, f: date, t: date) -> Decimal:
    """Open material requirements of the item at the location with need-by inside the period (the platform's own
    demand rows, loaded by the reset before block 5 runs: a fact, not a block 5 figure). Base and purchase unit are the
    same for every item the scenarios use."""
    rows = co.qd(
        "select coalesce(sum(base_qty), 0) as q from material_requirement where status = 'open' and item_code = :i "
        "and location_code = :l and need_by between :f and :t", i=item, l=loc, f=f, t=t,
    )  # fmt: skip
    return D(rows[0]["q"]) if rows else Decimal(0)


def self_check(
    check: b.Checker,
    oracle: Oracle5,
    scenario: str,
    got: dict[str, Decimal],
    rule: str,
    approver: str,
) -> None:
    want = SPEC[scenario]
    mine = {x: str(got[x]) for x in ("taxable", "cgst", "sgst", "igst", "total")}
    check(all(Decimal(mine[x]) == Decimal(want[x]) for x in mine) and rule == want["rule"] and approver == want["approver"],
          f"oracle self-check {scenario}: {mine}, {rule}, {approver}; the spec says {want}", "medium")  # fmt: skip


# ---- the truth file -------------------------------------------------------------------------------------------------


def compare_truth(check: b.Checker, co: "Co5", mine: list[dict[str, Any]]) -> int:
    """`truth/truth_period_sourcing.csv` (the generator's own arithmetic) against this eval's oracle, for the rows of the
    same (scenario, line). Returns how many rows were compared."""
    path = co.file("truth/truth_period_sourcing.csv")
    if not check(
        path is not None,
        "the dataset has no truth/truth_period_sourcing.csv (spec 3, 8.3)",
        "medium",
    ):
        return 0
    compared = 0
    for r in b.read_csv(path):
        key = (r.get("scenario", "").strip().upper(), str(r.get("line", "")).strip())
        for m in mine:
            if (str(m["scenario"]).upper(), str(m["line"])) != key:
                continue
            compared += 1
            wrong = []
            for col in (
                "qty",
                "suggested_qty",
                "net_price",
                "taxable",
                "cgst",
                "sgst",
                "igst",
                "ceiling",
            ):
                if (
                    b.dec(r.get(col)) is not None
                    and m.get(col) is not None
                    and D(r[col]) != D(m[col])
                ):
                    wrong.append(f"{col} {r[col]} (oracle {m[col]})")
            # a line's total, or the decision's (both are read as 'total'; the spec names one column)
            if (
                b.dec(r.get("total")) is not None
                and m.get("total") is not None
                and D(r["total"]) not in m["total"]
            ):
                wrong.append(f"total {r['total']} (oracle {sorted(m['total'])})")
            for col in (
                "item",
                "location",
                "period_from",
                "period_to",
                "supplier",
                "rule",
                "contract_code",
            ):
                if r.get(col) and m.get(col) and str(r[col]).strip() != str(m[col]):
                    wrong.append(f"{col} {r[col]} (oracle {m[col]})")
            if r.get("approver") and m.get("approver") and co.code_of(r["approver"].strip()) != m["approver"] \
                    and r["approver"].strip() != m["approver"]:  # fmt: skip
                wrong.append(f"approver {r['approver']} (oracle {m['approver']})")
            check(not wrong, f"truth file {key}: {'; '.join(wrong)}", "medium")
    return compared


# ======================================================================================================================
# The platform
# ======================================================================================================================


def pin(d: date) -> None:
    from p2p.formulas.calendar import pin_today

    pin_today(d)


@contextlib.contextmanager
def pinned(d: date) -> Iterator[None]:
    """The business date for a step, back to the scenarios' 23 Nov 2026 afterwards."""
    pin(d)
    try:
        yield
    finally:
        pin(TODAY)


class Co5(k.Co):
    """A demo company with the block 5 calls."""

    # ---- people ----
    def login(self, name: str) -> str:
        """The person whose login (the part of the email before @) is this: buyer, director, purchasemanager ..."""
        return next(
            c
            for c, u in sorted(self.people.items())
            if u["email"].split("@")[0] == name
        )

    def name_of(self, code: str) -> str:
        return str(self.people[code]["name"])

    # ---- reading ----
    def rfq(self, no: str) -> dict[str, Any]:
        found = self.qd("select * from rfq where no = :n", n=no)
        return found[0] if found else {}

    def rfq_lines(self, no: str) -> list[dict[str, Any]]:
        return self.qd(
            "select * from rfq_line where rfq_no = :n order by line_no", n=no
        )

    def invited(self, no: str) -> list[dict[str, Any]]:
        return self.qd(
            "select * from rfq_supplier where rfq_no = :n order by supplier_code", n=no
        )

    def demand_lines(self, no: str) -> list[dict[str, Any]]:
        return self.qd(
            "select d.* from period_demand_line d join rfq_line l on l.period_line_id = d.id where l.rfq_no = :n "
            "order by l.line_no", n=no,
        )  # fmt: skip

    def decisions(self, no: str) -> list[dict[str, Any]]:
        return self.qd(
            "select * from award_decision where rfq_no = :n order by round", n=no
        )

    def decision(self, no: str) -> dict[str, Any]:
        found = self.decisions(no)
        return found[-1] if found else {}

    def awards(self, no: str) -> list[dict[str, Any]]:
        return self.qd(
            "select * from award where rfq_no = :n order by rfq_line_no, supplier_code",
            n=no,
        )

    def letters(self, decision_id: Any) -> list[dict[str, Any]]:
        return self.qd(
            "select * from award_letter where decision_id = :d order by kind, supplier_code",
            d=decision_id,
        )

    def contracts_of(self, rfq: str) -> list[dict[str, Any]]:
        return self.qd(
            "select * from contract where source_rfq = :r order by code", r=rfq
        )

    def contract(self, code: str) -> dict[str, Any]:
        found = self.qd("select * from contract where code = :c", c=code)
        return found[0] if found else {}

    def prices_of(self, code: str) -> list[dict[str, Any]]:
        return self.qd(
            "select * from price_record where contract_code = :c order by item_code, id",
            c=code,
        )

    def proposal(self, pid: Any) -> dict[str, Any]:
        found = self.qd("select * from proposal where id = :i", i=pid) if pid else []
        return found[0] if found else {}

    def recommendation(self, rid: Any) -> dict[str, Any]:
        found = (
            self.qd("select * from recommendation where id = :i", i=rid) if rid else []
        )
        return found[0] if found else {}

    def since(self, action: str, mark: int) -> list[dict[str, Any]]:
        rows = self.qd("select id, actor_type, actor_id, entity, entity_id, after, reason from audit_event "
                       "where action = :a and id > :i order by id", a=action, i=mark)  # fmt: skip
        for r in rows:
            r["after"] = k.as_json(r["after"]) or {}
        return rows

    def count(self, table: str) -> int:
        """Rows in a table; -1 when the table is missing (the block not built), so a check fails rather than crashes."""
        found = self.qd(f"select count(*) as n from {table}")
        return int(found[0]["n"]) if found else -1

    def budget_state(self) -> dict[str, str]:
        """Every budget table's content, hashed (spec 5.1 rule 8: no budget figure comes from a period RFQ or award)."""
        names = [r[0] for r in self.q("select table_name from information_schema.tables where table_schema = 'public' "
                                      "and table_name like 'budget%' order by 1")]  # fmt: skip
        return {n: str(self.one(f"select md5(coalesce(string_agg(t::text, '|' order by t::text), '')) from {n} t"))
                for n in names}  # fmt: skip

    def rfq_detail(self, no: str, by: str) -> dict[str, Any]:
        r = self.call(by, "GET", f"/api/rfqs/{no}")
        return js(r) if r.status_code == 200 else {}

    # ---- acting ----
    def period_rfq(
        self,
        by: str,
        loc: str,
        f: date,
        t: date,
        lines: list[tuple[str, Any]],
        **extra: Any,
    ) -> Any:
        body = {"location_code": loc, "period_from": f.isoformat(), "period_to": t.isoformat(),
                "lines": [{"item_code": i, "qty": str(q)} for i, q in lines], **extra}  # fmt: skip
        return self.call(by, "POST", "/api/rfqs/period", body)

    def confirm_send(self, by: str, nos: list[str]) -> Any:
        r = self.call(by, "POST", "/api/rfqs/confirm-send", {"rfqs": nos})
        self.work()
        return r

    def confirm(self, by: str, no: str) -> bool:
        """The buyer confirms sending through the RFQ's pending rfq.send proposal (spec 4.1 rule 3); confirm-send if
        the proposal route is refused."""
        pid = self.rfq(no).get("proposal_id")
        if pid:
            r = self.call(
                by,
                "POST",
                f"/api/admin/proposals/{pid}/approve",
                {"note": "eval: confirmed"},
            )
            self.work()
            if r.status_code == 200:
                return True
        r = self.confirm_send(by, [no])
        return r.status_code == 200

    def quote(
        self,
        by: str,
        rfq: str,
        supplier: str,
        prices: list[Any],
        valid_until: str = VALID_UNTIL,
    ) -> Any:
        """Spec 8.3: freight 0, GST 18, valid until 31 Dec 2026, delivery 5 days, the supplier's own payment terms."""
        body = {"supplier_code": supplier, "valid_until": valid_until, "freight": "0",
                "lines": [{"rfq_line_no": n, "unit_price": str(p), "discount_pct": "0", "gst_rate": "18",
                           "delivery_days": 5} for n, p in enumerate(prices, 1)]}  # fmt: skip
        return self.call(by, "POST", f"/api/rfqs/{rfq}/quotes", body)

    def award(
        self,
        by: str,
        rfq: str,
        choices: dict[int, str],
        reason: str | None = None,
        regret: bool | None = None,
    ) -> Any:
        body: dict[str, Any] = {"choices": {str(n): s for n, s in choices.items()}}
        if reason:
            body["reason"] = reason
        if regret is not None:
            body["regret_notes"] = regret
        r = self.call(by, "POST", f"/api/rfqs/{rfq}/award", body)
        self.work()
        return r

    def decide(self, by: str, rfq: str, action: str, **body: Any) -> Any:
        r = self.call(by, "POST", f"/api/rfqs/{rfq}/award/{action}", body)
        self.work()
        return r

    # ---- the supplier's link ----
    def contract_token(self, since: int = 0, to: str | None = None) -> str | None:
        for m in reversed(self.mails(since)):
            if to and str(m.get("to", "")).lower() != to.lower():
                continue
            found = re.search(
                r"/contract/([A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+)",
                f"{m.get('body') or ''} {m.get('html') or ''}",
            )
            if found:
                return found.group(1)
        return None

    def link_get(self, path: str) -> Any:
        self.db.rollback()
        r = self.client.get(path)
        self.db.rollback()
        return r

    def link_post(self, path: str, **data: str) -> Any:
        self.db.rollback()
        r = self.client.post(path, data=data)
        self.db.rollback()
        return r


def foreign_token(token: str, mine: str, other: str) -> str:
    """The token with the company's slug in its values swapped for another's and the old signature kept: a forged link
    (the signing key is the platform's; the eval does not guess the new link's purpose string)."""
    body, _, sig = token.partition(".")
    try:
        values = json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))
    except ValueError:
        return token[:-2] + ("AA" if not token.endswith("AA") else "BB")
    swapped = json.dumps(
        [other if v == mine else v for v in values], separators=(",", ":")
    ).encode()
    return base64.urlsafe_b64encode(swapped).decode().rstrip("=") + "." + sig


# ======================================================================================================================
# Scenario steps shared by the evals
# ======================================================================================================================


def start(co: Co5) -> None:
    """Every scenario's business date (spec 8.3)."""
    pin(TODAY)
    co.work()


def created(check: b.Checker, co: Co5, r: Any, label: str) -> str:
    body = js(r)
    no = str(body.get("rfq_no") or body.get("no") or body.get("id") or "")
    check(r.status_code in (200, 201) and bool(no), f"{label}: creating the period RFQ answered {r.status_code} "
          f"{r.text[:300]}")  # fmt: skip
    check(
        r.status_code == 201,
        f"{label}: creating answered {r.status_code}, the spec says 201",
        "low",
    )
    return no


def p1_create(check: b.Checker, co: Co5, qty: Any = 900) -> str:
    return created(
        check,
        co,
        co.period_rfq(co.login("buyer"), LOC, P_FROM, P_TO, [(WIRE, qty)]),
        "P1",
    )


def p1_quotes(check: b.Checker, co: Co5, rfq: str) -> None:
    buyer = co.login("buyer")
    for supplier, price in (
        ("SUP-005", "880.00"),
        ("SUP-003", "895.00"),
        ("SUP-001", "910.00"),
    ):
        r = co.quote(buyer, rfq, supplier, [price])
        check(
            r.status_code == 200,
            f"P1: the quote of {supplier} at {price} on {rfq}: {r.status_code} {r.text[:200]}",
        )
    co.work()


def p1_proposal(
    check: b.Checker, co: Co5, regret: bool | None = None
) -> tuple[str, Any]:
    """P1 to the award proposal: create, the buyer confirms, three quotes, award SUP-005. Returns (rfq, answer)."""
    rfq = p1_create(check, co)
    if not rfq:
        return "", None
    check(
        co.confirm(co.login("buyer"), rfq),
        f"P1: the buyer could not confirm sending {rfq}",
    )
    p1_quotes(check, co, rfq)
    r = co.award(co.login("buyer"), rfq, {1: "SUP-005"}, regret=regret)
    check(r.status_code == 202 and js(r).get("status") == "award_proposed",
          f"P1: the buyer's award of {rfq} answered {r.status_code} {r.text[:300]}; the spec says 202 award_proposed")  # fmt: skip
    return rfq, r


def p4_proposal(
    check: b.Checker, co: Co5, regret: bool | None = None
) -> tuple[str, Any]:
    """P4: RM-07 1,000 NOS for Jan-Mar 2027 at the plant, quotes SUP-002 84.00 and SUP-003 86.00, award SUP-002."""
    buyer = co.login("buyer")
    rfq = created(
        check, co, co.period_rfq(buyer, LOC, P_FROM, P_TO, [(SEAL, 1000)]), "P4"
    )
    if not rfq:
        return "", None
    check(co.confirm(buyer, rfq), f"P4: the buyer could not confirm sending {rfq}")
    for supplier, price in (("SUP-002", "84.00"), ("SUP-003", "86.00")):
        r = co.quote(buyer, rfq, supplier, [price])
        check(
            r.status_code == 200,
            f"P4: the quote of {supplier} on {rfq}: {r.status_code} {r.text[:200]}",
        )
    co.work()
    r = co.award(buyer, rfq, {1: "SUP-002"}, regret=regret)
    check(r.status_code == 202 and js(r).get("status") == "award_proposed",
          f"P4: the buyer's award of {rfq} answered {r.status_code} {r.text[:300]}")  # fmt: skip
    return rfq, r


def check_decision(check: b.Checker, co: Co5, rfq: str, want: dict[str, Decimal], rule: str, approver: str, label: str,
                   round_no: int | None = None) -> dict[str, Any]:  # fmt: skip
    d = co.decision(rfq)
    if not check(bool(d), f"{label}: {rfq} has no award_decision row"):
        return {}
    got = {
        x: D(d.get(f"value_{x}" if x in ("taxable", "total") else x))
        for x in ("taxable", "cgst", "sgst", "igst", "total")
    }
    check(got == want, f"{label}: decision figures {got}; oracle {want}")
    check(
        str(d.get("rule_code") or "") == rule,
        f"{label}: decision rule {d.get('rule_code')}; oracle {rule}",
    )
    check(
        same_person(co, d.get("approver"), approver),
        f"{label}: decision approver {d.get('approver')}; oracle {approver}",
    )
    if round_no is not None:
        check(
            int(d.get("round") or 0) == round_no,
            f"{label}: decision round {d.get('round')}, expected {round_no}",
        )
    return d


def api_value(body: dict[str, Any], *names: str) -> Decimal | None:
    for n in names:
        if body.get(n) not in (None, ""):
            return b.dec(body[n])
    return None


def mails_to(
    co: Co5, address: str, since: int, words: str = ""
) -> list[dict[str, Any]]:
    return [m for m in co.mails(since) if str(m.get("to", "")).lower() == address.lower()
            and words.lower() in (str(m.get("subject") or "") + " " + str(m.get("body") or "")).lower()]  # fmt: skip


def tenant_check(
    check: b.Checker,
    mine: Co5,
    other: Co5,
    rfqs: list[str],
    contracts: list[str],
    since: int,
) -> None:
    """Spec 10.2: the other company gets 404 (or, when it has a document of the same number, its own) and sees no mail."""
    by = other.login("buyer")
    for no in [x for x in rfqs if x]:
        r = other.call(by, "GET", f"/api/rfqs/{no}")
        ours = {str(x["item_code"]) for x in mine.rfq_lines(no)}
        theirs = (
            {str(x.get("item_code")) for x in (js(r).get("lines") or [])}
            if r.status_code == 200
            else set()
        )
        check(r.status_code == 404 or (r.status_code == 200 and not (ours & theirs)),
              f"tenant check: {other.name} reads {mine.name}'s {no} ({r.status_code})")  # fmt: skip
        sent = {str(s["supplier_code"]): s.get("invited_at") for s in mine.invited(no)}
        r = other.call(by, "POST", "/api/rfqs/confirm-send", {"rfqs": [no]})
        other.work()
        after = {str(s["supplier_code"]): s.get("invited_at") for s in mine.invited(no)}
        check(
            after == sent,
            f"tenant check: {other.name}'s buyer confirming {mine.name}'s {no} changed its invitations",
        )
    for code in [x for x in contracts if x]:
        r = other.call(by, "GET", f"/api/contracts/{code}")
        ours = mine.contract(code)
        if (
            r.status_code == 200
        ):  # numbers start alike in every company: then it must be the other's own contract
            body = js(r)
            vendor = (
                (body.get("vendor") or {}).get("name")
                if isinstance(body.get("vendor"), dict)
                else None
            )
            mine_name = mine.one("select coalesce(trade_name, legal_name) from supplier where code = :c",
                                 c=ours.get("supplier_code"))  # fmt: skip
            check(other.contract(code).get("code") == code and vendor != mine_name,
                  f"tenant check: {other.name} reads {mine.name}'s contract {code}")  # fmt: skip
        else:
            check(
                r.status_code == 404,
                f"tenant check: {other.name} asking for {mine.name}'s {code} answered {r.status_code}",
            )
    to_other = [
        m
        for m in mine.mails(since)
        if str(m.get("to", "")).lower().endswith("@" + other.domain)
    ]
    check(not to_other, f"tenant check: {len(to_other)} mail(s) went to {other.name} while only {mine.name} acted: "
          f"{[m.get('subject') for m in to_other[:3]]}")  # fmt: skip


def numbers_of(value: Any) -> set[Decimal]:
    return b3.record_numbers(value)


def tokens(text_: str) -> list[Decimal]:
    return b3.number_tokens(text_)
