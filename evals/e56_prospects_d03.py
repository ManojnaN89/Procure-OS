"""E56: prospects quote, count only when checked, and never reach an order early (task 4.4, D-03 rules R1-R12, the owner's
tests T-Q1-1 to T-Q1-9), written without the implementation.

Independence: written from the block 0b spec (section 4, the twelve rules as the product owner decided them) and the
dataset only. The oracles read `master/supplier*.csv`, `sanction_entry.csv`, `user.csv`, `category.csv`, `setting.csv`
and `supplier_flag.csv` of the demo company's own dataset:

- a registration is refused when its GSTIN, PAN or bank account equals any supplier's (blocked ones included) or its name
  is on the sanctions list; otherwise it is created as a prospect and flagged for each contact (email or phone) or
  address-and-pincode it shares with another supplier (`same_contact`, `same_address`), each non-public email domain it
  shares (`same_domain`), each staff email or phone (`staff_contact`); any match with a blocked supplier is
  `blocked_match`;
- a quote counts toward the minimum only from an active supplier approved for the category at the RFQ's location, or a
  prospect whose identity is checked and which has no open flag;
- the identity check is refused to whoever registered or invited the prospect; a checked prospect's award is conditional
  and no order is made from it until the supplier is active; a conditional award lapses on the `prospect_qualification_days`
  -th working day (company calendar, `reference.calendar_for`: Monday to Friday and the central holidays), opens one task for
  the buyer and awards nothing to anyone else.

Three broken rules (identity check by the inviter allowed; an unchecked quote counted; an order made from a conditional
award) must each be told apart from the right one by the cases.
"""

from datetime import date, datetime, time
from typing import Any
from zoneinfo import ZoneInfo

import env
import lib
from block0b import REFUSED, Checker, Company, add_working, dicts, holidays, js, on_day, open_days, pick
from lib import D, Outcome

ID = "E56"
TITLE = "Prospects quote, count only when their identity is checked by someone else, and never reach an order early"
REQUIREMENT = (
    "Task 4.4, D-03 R1-R12, T-Q1-1..9: duplicates (GSTIN, PAN, bank, sanctions) refused, shared contacts or addresses "
    "flagged; the identity check by neither registerer nor inviter; only valid, independent, eligible quotes count; an "
    "award to a checked prospect is conditional and makes no order until activation; a lapsed award opens a buyer task and "
    "awards nobody else; an expired quote cannot be re-selected; an inactive supplier never gets an order; an open RFQ keeps "
    "its quotation-count rule; another company sees none of it"
)

BUYER, PROD, PLANT_HEAD, PM, DIRECTOR, ADMIN, ADMIN2 = "U03", "U01", "U07", "U08", "U09", "U06", "U10"
AWARD_DAY = date(2026, 11, 25)  # a Wednesday; no holiday in the ten working days after it in any reference list


# ---- the oracle: the raw supplier files -------------------------------------------------------------------------------


def domain(email: str) -> str:
    return email.rpartition("@")[2].lower()


class Suppliers:
    def __init__(self, c: Company) -> None:
        self.sup = {r["code"]: r for r in c.rows("master", "supplier")}
        self.contacts = c.rows("master", "supplier_contact")
        self.addresses = c.rows("master", "supplier_address")
        self.banks = c.rows("master", "supplier_bank_account")
        self.sanctions = c.rows("master", "sanction_entry")
        self.staff = c.rows("master", "user")
        self.cat = c.rows("master", "supplier_category")
        self.scope = c.rows("master", "supplier_category_scope")
        self.region = c.region_of()
        settings = {r["key"]: r["value"] for r in c.rows("master", "setting")}
        self.public = {
            d.strip().lower() for d in settings.get("public_mail_domains", "").replace(";", ",").split(",") if d.strip()
        }
        self.methods = [
            m.strip() for m in settings.get("identity_check_methods", "").replace(";", ",").split(",") if m.strip()
        ]
        self.days = int(settings.get("prospect_qualification_days") or 10)
        self.min_quotes = {r["code"]: int(r["min_quotes"] or 0) for r in c.rows("master", "category")}

    def refusal(self, cand: dict[str, str]) -> str | None:
        if cand.get("gstin") and cand["gstin"] in {s["gstin"] for s in self.sup.values()}:
            return "GSTIN"
        if cand["pan"] in {s["pan"] for s in self.sup.values()}:
            return "PAN"
        if cand.get("account_no") and cand["account_no"] in {b["account_no"] for b in self.banks}:
            return "bank account"
        names = {s["name"].lower() for s in self.sanctions} | {
            a.strip().lower() for s in self.sanctions for a in s.get("aliases", "").split(";") if a.strip()
        }
        return "sanctions list" if cand["legal_name"].lower() in names else None

    def flags(self, cand: dict[str, str], own: str = "") -> set[tuple[str, str]]:
        found: set[tuple[str, str]] = set()

        def kind(code: str, plain: str) -> str:
            return "blocked_match" if self.sup.get(code, {}).get("status") == "blocked" else plain

        for ct in self.contacts:
            if ct["supplier_code"] == own:
                continue
            if cand["email"].lower() == ct["email"].lower() or cand["phone"] == ct["phone"]:
                found.add((kind(ct["supplier_code"], "same_contact"), ct["supplier_code"]))
            d = domain(ct["email"])
            if d == domain(cand["email"]) and d not in self.public:
                found.add((kind(ct["supplier_code"], "same_domain"), ct["supplier_code"]))
        for ad in self.addresses:
            if ad["supplier_code"] != own and ad["address"] == cand["address"] and ad["pincode"] == cand["pincode"]:
                found.add((kind(ad["supplier_code"], "same_address"), ad["supplier_code"]))
        for u in self.staff:
            if cand["email"].lower() == u["email"].lower() or cand["phone"] == u.get("phone"):
                found.add(("staff_contact", u["code"]))
        return found

    def of(self, code: str) -> dict[str, str]:
        ct = next((x for x in self.contacts if x["supplier_code"] == code), {})
        ad = next((x for x in self.addresses if x["supplier_code"] == code), {})
        return {
            "email": ct.get("email", ""),
            "phone": ct.get("phone", ""),
            "address": ad.get("address", ""),
            "pincode": ad.get("pincode", ""),
        }

    def approved_at(self, code: str, category: str, location: str, day: date) -> bool:
        if not any(
            r["supplier_code"] == code
            and r["category_code"] == category
            and date.fromisoformat(r["approved_from"]) <= day
            for r in self.cat
        ):
            return False
        scope = [r for r in self.scope if r["supplier_code"] == code and r["category_code"] == category]
        return not scope or any(
            r.get("location_code") == location
            or (r.get("region_code") and r.get("region_code") == self.region.get(location))
            for r in scope
        )


def counts(
    oracle: Suppliers, state: dict[str, dict[str, Any]], supplier: str, category: str, location: str, day: date
) -> bool:
    """Rule 8 (R2, R9): active and approved here, or a prospect checked with no open flag."""
    s = state.get(supplier, {"status": oracle.sup.get(supplier, {}).get("status", "")})
    if s["status"] == "active":
        return oracle.approved_at(supplier, category, location, day)
    return s["status"] == "prospect" and bool(s.get("checked")) and not s.get("open_flags")


# ---- the eval -------------------------------------------------------------------------------------------------------


def run(ctx: lib.Ctx) -> Outcome:
    env.forget_tenants("e56")
    try:
        return _run(ctx)
    finally:
        env.forget_tenants("e56")


def _run(ctx: lib.Ctx) -> Outcome:  # noqa: C901 - one scenario, step by step
    from fastapi.testclient import TestClient

    from p2p.main import app

    out = Outcome(True)
    check = Checker(out)
    lib.capture_mail()
    client = TestClient(app)
    c = Company("apex", "e56", client)
    o = Suppliers(c)
    state: dict[str, dict[str, Any]] = {}  # what the oracle knows of each prospect as the eval goes
    mutations: dict[str, bool] = {}

    # the dataset itself (the generator's own promises, spec 4.6)
    want_status = {"SUP-007": "blocked", "SUP-008": "prospect", "SUP-009": "prospect"}
    have = {k: o.sup.get(k, {}).get("status") for k in want_status}
    if not check(have == want_status, f"the dataset's supplier statuses {have}, the spec plants {want_status}"):
        return out
    file_flags = {
        (r["kind"], r["other_ref"]) for r in c.rows("master", "supplier_flag") if r["supplier_code"] == "SUP-009"
    }
    own_flags = o.flags(
        {**o.of("SUP-009"), "legal_name": o.sup["SUP-009"]["legal_name"], "pan": o.sup["SUP-009"]["pan"]}, "SUP-009"
    )
    check(file_flags == own_flags == {("same_contact", "SUP-005"), ("same_address", "SUP-005")},
          f"SUP-009's flags: supplier_flag.csv {file_flags}, this oracle {own_flags}; the spec plants same_contact and same_address with SUP-005", "medium")  # fmt: skip
    for code in ("SUP-008", "SUP-009"):
        state[code] = {
            "status": "prospect",
            "checked": False,
            "open_flags": len(o.flags({**o.of(code), "legal_name": "", "pan": ""}, code)),
        }

    def vendor(code: str, who: str = BUYER) -> dict[str, Any]:
        return dict(js(c.call(who, "GET", f"/api/vendors/{code}/profile")) or {})

    def documents(code: str) -> list[dict[str, Any]]:
        """The supplier's document records. Spec silent on the shape: the vendor page lists them under
        documents.on_file (id, type, verified); any dict with a doc_type and an id on the profile counts too."""
        found = [d for d in dicts(vendor(code)) if d.get("doc_type") and d.get("id")]
        page = js(c.call(BUYER, "GET", f"/api/vendors/{code}"))
        on_file = (page.get("documents") or {}).get("on_file") if isinstance(page, dict) else None
        found += [{"id": d["id"], "doc_type": d.get("type"), "verified": d.get("verified")} for d in on_file or []]
        return found

    def proof(code: str, who: str = BUYER) -> list[int]:
        """A proof file for the identity check. Spec silent on how one is referenced (`document_ids`): here a scan
        attached to one of the supplier's document records through the existing upload; the stored file's id is cited."""
        docs = documents(code)
        if not docs:
            c.call(
                who,
                "POST",
                f"/api/vendors/{code}/documents",
                {"type": "pan", "number": o.sup.get(code, {}).get("pan") or "EVPPI0000O"},
            )
            docs = documents(code)
        if not docs:
            return []
        r = c.call(who, "POST", f"/api/vendors/{code}/documents/{docs[0]['id']}/file",
                   files={"file": ("pan.pdf", b"%PDF-1.4 eval proof", "application/pdf")})  # fmt: skip
        fid = pick(js(r), "id")
        return [int(fid)] if fid else []

    def flags_shown(code: str) -> list[dict[str, Any]]:
        return [d for d in dicts(vendor(code)) if "kind" in d and ("other_ref" in d or "detail" in d)]

    # ---- 1. SUP-009 is flagged on the platform exactly as the file says ----------------------------------------------
    shown = {(f["kind"], f.get("other_ref")) for f in flags_shown("SUP-009")}
    out.metrics["SUP-009 flags shown"] = sorted(shown)
    check(shown == file_flags, f"SUP-009's flags on its profile {shown}, supplier_flag.csv {file_flags}")
    listing = js(c.call(BUYER, "GET", "/api/vendors", params={"status": "prospect"}))
    # spec silent on the list key: the existing vendor list names the code "id"
    codes = {pick(d, "code", "supplier_code", "id") for d in dicts(listing) if pick(d, "status") == "prospect"}
    check({"SUP-008", "SUP-009"} <= codes and not codes & {"SUP-007", "SUP-001"},
          f"the prospect list shows {sorted(c_ for c_ in codes if c_)}", "medium")  # fmt: skip

    # ---- 2. registering: duplicates refused, shared contacts flagged (R7, R8, T-Q1-2, T-Q1-3) ------------------------
    s7, s3 = o.sup["SUP-007"], o.sup["SUP-003"]
    bank7 = next((b for b in o.banks if b["supplier_code"] == "SUP-007"), None)
    staff = c.people[BUYER]

    def cand(n: int, **kw: str) -> dict[str, str]:
        base = {"legal_name": f"Eval Prospect {n} Pvt Ltd", "pan": f"EVPP{chr(64 + n)}0000{chr(70 + n)}", "gst_reg_type": "unregistered",
                "gstin": "", "address": f"{n}, Eval Industrial Estate", "city": "Pune", "state_code": "27", "pincode": f"4110{40 + n}",
                "email": f"sales@evalprospect{n}.example", "phone": f"+91 00000 955{n:02d}", "category": "RAW"}  # fmt: skip
        return {**base, **kw}

    def register(cd: dict[str, str]) -> Any:
        body = {
            "legal_name": cd["legal_name"], "pan": cd["pan"], "gst_reg_type": cd["gst_reg_type"], "gstin": cd["gstin"] or None,
            "address": cd["address"], "address_line": cd["address"], "city": cd["city"], "state_code": cd["state_code"],
            "pincode": cd["pincode"], "category": cd["category"], "category_code": cd["category"],
            "contact": {"name": "Sales Desk", "email": cd["email"], "phone": cd["phone"]},
            "contact_name": "Sales Desk", "contact_email": cd["email"], "contact_phone": cd["phone"], "email": cd["email"], "phone": cd["phone"],
        }  # fmt: skip
        if cd.get("account_no"):
            body["bank"] = {"account_no": cd["account_no"], "ifsc": cd.get("ifsc", "")}
            body["account_no"], body["ifsc"] = cd["account_no"], cd.get("ifsc", "")
        return c.call(BUYER, "POST", "/api/vendors/prospects", body)

    cases = {
        "SUP-007's PAN under a new name": cand(1, pan=s7["pan"]),
        "SUP-007's bank account": cand(
            2, account_no=(bank7 or {}).get("account_no", ""), ifsc=(bank7 or {}).get("ifsc", "")
        ),
        "SUP-003's GSTIN": cand(3, gst_reg_type="regular", gstin=s3["gstin"], pan=s3["gstin"][2:12]),
    }
    if o.sanctions:
        cases["a name on the sanctions list"] = cand(4, legal_name=o.sanctions[0]["name"])
    else:
        out.notes.append(
            "sanction_entry.csv is empty: the sanctions refusal is not tried (the spec plants no listed name)"
        )
    suppliers_before = c.one("select count(*) from supplier")
    refused_before = len(c.audit("supplier.prospect_refused"))
    results: dict[str, str] = {}
    for name, cd in cases.items():
        why = o.refusal(cd)
        r = register(cd)
        detail = str(pick(js(r), "detail", default=r.text))
        results[name] = f"{r.status_code} {detail[:90]}"
        check(why is not None, f"{name}: the oracle itself does not refuse it (dataset?)", "medium")
        check(
            r.status_code == 422,
            f"registering with {name} answered {r.status_code} {detail[:150]}; expected 422 ({why})",
        )
        words = {
            "GSTIN": ("gstin", "pan"),
            "PAN": ("pan",),
            "bank account": ("bank", "account"),
            "sanctions list": ("sanction",),
        }.get(why or "", ())
        check(
            any(w in detail.lower() for w in words),
            f"the refusal of {name} does not say what matched ({why}): {detail[:150]}",
            "medium",
        )
    check(c.one("select count(*) from supplier") == suppliers_before, "a refused registration created a supplier")
    check(len(c.audit("supplier.prospect_refused")) - refused_before == len(cases),
          f"refused registrations audited {len(c.audit('supplier.prospect_refused')) - refused_before}, expected {len(cases)}")  # fmt: skip
    out.metrics["registrations refused"] = results

    made: dict[str, str] = {}

    def register_ok(label: str, cd: dict[str, str], want_flags: set[tuple[str, str]]) -> str:
        r = register(cd)
        body = js(r)
        code = str(pick(body, "code", "supplier_code", default=""))
        got = {(f.get("kind"), f.get("other_ref")) for f in pick(body, "flags", default=[]) if isinstance(f, dict)}
        if not got and code:
            got = {(f["kind"], f.get("other_ref")) for f in flags_shown(code)}
        made[label] = f"{r.status_code} {code} flags {sorted(got)}"
        check(
            r.status_code in (200, 201) and pick(body, "status") == "prospect" and code,
            f"registering {label}: {r.status_code} {r.text[:150]}",
        )
        check({k for k, _ in got} >= {k for k, _ in want_flags} and (not want_flags or got),
              f"{label}: flags {sorted(got)}, the oracle {sorted(want_flags)}")  # fmt: skip
        if not want_flags:
            check(not got, f"{label}: a clean prospect was flagged {sorted(got)}")
        state[code] = {"status": "prospect", "checked": False, "open_flags": len(want_flags)}
        return code

    blocked_cd = cand(5, email=o.of("SUP-007")["email"])
    p_blocked = register_ok("SUP-007's contact email", blocked_cd, o.flags(blocked_cd))
    check(
        any(k == "blocked_match" for k, _ in o.flags(blocked_cd)),
        "oracle: SUP-007's contact must give blocked_match",
        "medium",
    )
    staff_cd = cand(6, phone=staff.get("phone", ""))
    p_staff = register_ok("the buyer's own phone", staff_cd, o.flags(staff_cd))
    clean = [register_ok(f"clean prospect {n}", cand(n), set()) for n in (7, 8, 9)]
    out.metrics["registrations made"] = made
    check(len(c.audit("supplier.prospect_add")) >= 5, "supplier.prospect_add not audited for each prospect", "medium")
    check(len(c.audit("supplier.flag_raised")) >= 2, "supplier.flag_raised not audited", "medium")
    del p_blocked, p_staff

    # ---- 3. the purchase manager also buys (two administrators) ------------------------------------------------------
    check(c.grant(ADMIN, ADMIN2, PM, "Buyer"), "the purchase manager could not be given the Buyer role", "medium")

    def quotes_counted(rfq: str) -> dict[str, tuple[bool, str]]:
        found: dict[str, tuple[bool, str]] = {}
        for d in dicts(js(c.call(BUYER, "GET", f"/api/rfqs/{rfq}"))):
            if "counts" in d:
                found[str(pick(d, "supplier_code", "supplier"))] = (bool(d["counts"]), str(d.get("why_not") or ""))
        return found

    def awards(rfq: str) -> list[tuple[str, Any, Any]]:
        cols = c.columns("award")
        sql = (
            "select supplier_code, "
            + ("conditional" if "conditional" in cols else "null")
            + ", "
            + ("lapsed_at" if "lapsed_at" in cols else "null")
        )
        return [tuple(r) for r in c.q(sql + " from award where rfq_no = :r order by at", r=rfq)]

    def invite(who: str, rfq: str, code: str) -> Any:
        return c.call(who, "POST", f"/api/rfqs/{rfq}/prospects", {"supplier_code": code})

    # ---- 4. copper wire RFQ: SUP-005, flagged SUP-009 and two unchecked prospects (T-Q1-1, R2, R4, R9) ---------------
    req = c.request(PROD, "RM-08", "150", "PLT-CKN")
    rfq = c.rfq_of(BUYER, req)
    copper_po = ""
    if check(bool(rfq), f"copper wire request {req} went to no RFQ: {c.line(BUYER, req).get('source')}", "medium"):
        refusals = {"blocked SUP-007": invite(BUYER, rfq, "SUP-007").status_code, "PKG prospect SUP-008": invite(BUYER, rfq, "SUP-008").status_code,
                    "active SUP-005": invite(BUYER, rfq, "SUP-005").status_code}  # fmt: skip
        out.metrics["copper RFQ: invitations refused"] = refusals
        check(
            all(s == 422 for s in refusals.values()),
            f"inviting a blocked, wrong-category or non-prospect supplier: {refusals}, expected 422 each",
        )
        for code in ("SUP-009", clean[0], clean[1]):
            r = invite(BUYER, rfq, code)
            check(r.status_code in (200, 201), f"inviting prospect {code} to {rfq}: {r.status_code} {r.text[:150]}")
        check(len(c.audit("rfq.prospect_invite")) >= 3, "rfq.prospect_invite not audited", "medium")
        for code, price in (("SUP-005", "905"), ("SUP-009", "880"), (clean[0], "870"), (clean[1], "875")):
            r = c.quote(BUYER, rfq, code, price, "18")
            check(
                r.status_code in (200, 201), f"a quote from {code} on {rfq}: {r.status_code} {r.text[:150]}", "medium"
            )
        got = quotes_counted(rfq)
        want = {
            code: counts(o, state, code, "RAW", "PLT-CKN", lib.TODAY)
            for code in ("SUP-005", "SUP-009", clean[0], clean[1])
        }
        out.metrics["copper RFQ: counts (platform / oracle)"] = {
            k: f"{got.get(k, ('-',))[0]} / {v}" for k, v in want.items()
        }
        check({k: v[0] for k, v in got.items() if k in want} == want, f"copper RFQ counts {got}, oracle {want}")
        check(
            all(v[1] for k, v in got.items() if k in want and not want[k]),
            f"an uncounted quote gives no reason: {got}",
            "medium",
        )
        mutations["an unchecked quote counted"] = sum(want.values()) != len(want) and sum(
            v[0] for k, v in got.items() if k in want
        ) == sum(want.values())
        page = js(c.call(BUYER, "GET", f"/api/rfqs/{rfq}"))
        out.metrics["copper RFQ: min_quotes, rule"] = f"{pick(page, 'min_quotes')}, {pick(page, 'min_quotes_rule')}"
        check(
            pick(page, "min_quotes") is not None and bool(pick(page, "min_quotes_rule")),
            f"the RFQ does not show its stored min_quotes and rule: {str(page)[:200]}",
            "medium",
        )
        for code in ("SUP-009", clean[0]):
            r = c.award(BUYER, rfq, code, "E56: cheapest")
            check(
                r.status_code == 409,
                f"awarding unchecked or flagged prospect {code}: {r.status_code} {r.text[:150]}, expected 409",
            )
        check(not awards(rfq), f"refused awards left award rows: {awards(rfq)}")
        r = c.award(BUYER, rfq, "SUP-005")
        need = int(pick(page, "min_quotes") or o.min_quotes.get("RAW", 2))
        if sum(want.values()) < need:
            check(
                r.status_code in (409, 422),
                f"one counting quote of {need} wanted, awarded with no reason: {r.status_code}",
            )
            r = c.award(BUYER, rfq, "SUP-005", "E56: only one valid quote; the others are unchecked prospects")
        check(r.status_code in (200, 202), f"awarding SUP-005 with a reason: {r.status_code} {r.text[:150]}")
        route = {}
        for v in pick(js(c.call(ADMIN, "GET", "/api/setup/buying-route")), "versions", default=[]):
            if v.get("in_force") and v.get("category") in (None, "", "RAW"):
                route = v.get("values") or {}
        agree_above = D(route.get("agree_above")) if route.get("agree_above") not in (None, "") else None
        value = D(150) * D(905)
        c.work()
        # spec 4.3.10 names `sourcing.route_approve`, which is the permission of the existing route proposals (the
        # purchase manager's); the proposal is found by that permission or by an action of that name
        pending = c.q(
            "select id, payload::text from proposal where status = 'pending' "
            "and (action = 'sourcing.route_approve' or required_permission = 'sourcing.route_approve') order by id"
        )
        agreement = [
            {"id": pid_, "payload": payload_}
            for pid_, payload_ in pending
            if rfq in str(payload_) or req in str(payload_)
        ]
        out.metrics["copper RFQ: line value, agree_above, agreement proposals"] = (
            f"{value}, {agree_above}, {len(agreement)}"
        )
        if agree_above is None:
            out.notes.append(
                "the buying route shows no agree_above value: the purchase manager's agreement is not checked"
            )
        elif value > agree_above:
            check(
                bool(agreement),
                f"too few counting quotes on a line of Rs {value} above agree_above {agree_above}: no purchase manager agreement asked (OP-6)",
            )
            check(
                not (c.line(BUYER, req).get("source") or {}).get("po_no"),
                "an order was made before the purchase manager agreed",
            )
            for p in agreement:
                check(
                    c.approve(BUYER, p["id"]).status_code in REFUSED,
                    "the buyer agreed to her own short-competition selection",
                )
                c.approve(PM, p["id"])
            c.work()
        copper_po = str((c.line(BUYER, req).get("source") or {}).get("po_no") or "")
        check(
            bool(copper_po),
            f"no order after SUP-005 was selected with a reason (and agreed): {c.line(BUYER, req).get('source')}",
            "medium",
        )

    # ---- 5. Sanand cartons: SUP-008 invited by the purchase manager, checked by the plant head (R2-R5, R11) ----------
    proof_ids = proof("SUP-008")
    method = (o.methods or ["pan_and_gstin_online"])[0]
    req2 = c.request(PROD, "PK-01", "500", "PLT-SND")
    rfq2 = c.rfq_of(BUYER, req2)
    conditional_po_seen = None
    if check(
        bool(rfq2),
        f"the Sanand carton request {req2} went to no RFQ (no supplier approved there: spec silent): {c.line(BUYER, req2).get('source')}",
        "medium",
    ):
        r = invite(PM, rfq2, "SUP-008")
        check(
            r.status_code in (200, 201),
            f"the purchase manager (also a buyer) inviting SUP-008: {r.status_code} {r.text[:150]}",
        )
        c.work()
        for p in c.proposals("rfq.send"):
            if p["payload"].get("rfq_no") == rfq2:
                c.approve(BUYER, p["id"])
        c.work()
        c.quote(BUYER, rfq2, "SUP-008", "41.00", "5")
        got = quotes_counted(rfq2).get("SUP-008")
        check(
            got is not None and got[0] is False,
            f"SUP-008's quote before its identity check: {got}, expected not counted",
        )
        check(
            c.award(BUYER, rfq2, "SUP-008", "E56").status_code == 409,
            "a conditional award to an unchecked prospect was not refused (T-Q1-7)",
        )
        url = "/api/vendors/SUP-008/identity-check"
        good = {"method": method, "document_ids": proof_ids, "note": "PAN and GSTIN seen online"}
        tries = {
            "the buyer who registered it": c.call(BUYER, "POST", url, good).status_code,
            "the purchase manager who invited it": c.call(PM, "POST", url, good).status_code,
            "a method not in the list": c.call(
                PLANT_HEAD, "POST", url, {**good, "method": "asked_a_friend"}
            ).status_code,
            "no proof file": c.call(PLANT_HEAD, "POST", url, {**good, "document_ids": []}).status_code,
        }
        out.metrics["SUP-008 identity check refused"] = tries
        check(
            tries["the buyer who registered it"] == 403,
            f"the registering buyer's identity check answered {tries['the buyer who registered it']}",
        )
        check(
            tries["the purchase manager who invited it"] == 403,
            f"the inviter's identity check answered {tries['the purchase manager who invited it']} (R3)",
        )
        mutations["identity check by the inviter allowed"] = tries["the purchase manager who invited it"] == 403
        check(
            tries["a method not in the list"] == 422 and tries["no proof file"] == 422,
            f"bad method / no proof: {tries}",
        )
        if not proof_ids:
            out.notes.append(
                "SUP-008's profile lists no document ids to cite as proof; the spec does not say how a proof file is referenced"
            )
        r = c.call(PLANT_HEAD, "POST", url, good)
        body = js(r)
        check(
            r.status_code == 200 and pick(body, "method") == method and pick(body, "checked_by"),
            f"the plant head's identity check: {r.status_code} {r.text[:200]}",
        )
        check(
            c.call(PLANT_HEAD, "POST", url, good).status_code == 409,
            "a second identity check was not refused (409)",
            "medium",
        )
        check(len(c.audit("supplier.identity_check")) >= 1, "supplier.identity_check not audited")
        check(
            c.one("select status from supplier where code = 'SUP-008'") == "prospect",
            "the identity check changed the supplier's status (R11: it is not activation)",
        )
        state["SUP-008"]["checked"] = True
        got = quotes_counted(rfq2).get("SUP-008")
        check(got is not None and got[0] is True, f"SUP-008's quote after its identity check: {got}, expected counted")
        r = c.award(BUYER, rfq2, "SUP-008")
        if r.status_code in (409, 422):  # one counting quote of two wanted: the buyer says why
            r = c.award(BUYER, rfq2, "SUP-008", "E56: the only supplier for Sanand cartons")
        check(
            # spec silent on the shape of `conditional` in the answer: true, or the list of lines awarded on condition
            r.status_code in (200, 202) and (pick(js(r), "conditional") is None or bool(pick(js(r), "conditional"))),
            f"the award to checked SUP-008: {r.status_code} {r.text[:200]}",
        )
        c.work()
        rows_ = awards(rfq2)
        check(
            any(s == "SUP-008" and cond is True for s, cond, _ in rows_),
            f"SUP-008's award is not marked conditional: {rows_}",
        )
        check(len(c.audit("award.conditional")) >= 1, "award.conditional not audited", "medium")
        conditional_po_seen = c.one("select count(*) from po where supplier_code = 'SUP-008'")
        check(
            conditional_po_seen == 0,
            f"an order was made for prospect SUP-008 from a conditional award ({conditional_po_seen})",
        )
        mutations["an order made from a conditional award"] = conditional_po_seen == 0
        # activation: documents verified by a person other than who uploaded them, then the director's approval
        # the bank proof is not part of the activation set (owner decision 9, 22 Nov 2026): SUP-008 activates with
        # its PAN card and GST certificate, and R6's "the order needs no bank account" is checked below
        for d in documents("SUP-008"):
            if not d.get("verified"):
                c.call(PLANT_HEAD, "POST", f"/api/vendors/SUP-008/documents/{d['id']}/verify", {})
        r = c.call(
            BUYER,
            "POST",
            "/api/vendors/SUP-008/status",
            {"to": "active", "reason": "E56: identity checked, documents verified"},
        )
        pid = pick(js(r), "proposal_id", "id")
        waiting = [
            p
            for p in c.proposals("supplier.set_status")
            # spec silent on the proposal's payload keys: supplier_code, code or supplier
            if pick(p["payload"], "supplier_code", "code", "supplier") == "SUP-008"
        ]
        check(bool(waiting) or pid, f"activating SUP-008 made no proposal: {r.status_code} {r.text[:150]}")
        for p in waiting:
            c.approve(DIRECTOR, p["id"])
        c.work()
        check(
            c.one("select status from supplier where code = 'SUP-008'") == "active",
            "SUP-008 is not active after the director's approval",
        )
        made_po = c.one("select count(*) from po where supplier_code = 'SUP-008'")
        bank = c.one("select count(*) from supplier_bank_account where supplier_code = 'SUP-008'")
        out.metrics["SUP-008: orders after activation, bank accounts"] = f"{made_po}, {bank}"
        check(made_po == 1, f"after activation the conditional award should make one order for SUP-008, made {made_po}")
        check(bank == 0, "SUP-008 has a bank account; the R6 case (order without bank verification) needs none")
        out.notes.append(
            "R6's payment side (a run refuses an unverified account) is E52's; here only: the order needs no bank account"
        )

    # ---- 6. SUP-009's flags cleared only by an independent person ----------------------------------------------------
    flags9 = flags_shown("SUP-009")
    for f in flags9:
        fid = f.get("id")
        if not fid:
            continue
        url = f"/api/vendors/SUP-009/flags/{fid}/clear"
        check(c.call(BUYER, "POST", url, {"reason": "E56"}).status_code == 403, "the registering buyer cleared a flag")
        check(
            c.call(PLANT_HEAD, "POST", url, {"reason": ""}).status_code == 422,
            "a flag cleared with no reason",
            "medium",
        )
        check(
            c.call(
                PLANT_HEAD, "POST", url, {"reason": "E56: same industrial estate, different owners (checked)"}
            ).status_code
            == 200,
            f"the plant head could not clear flag {fid}",
        )
    check(
        len(c.audit("supplier.flag_cleared")) == len([f for f in flags9 if f.get("id")]),
        "supplier.flag_cleared not audited per flag",
        "medium",
    )

    # ---- 7. the conditional award lapses on the tenth working day, not the ninth (R10, T-Q1-5, T-Q1-8) ----------------
    # spec section 1: "working days" use the company calendar (`reference.calendar_for`): Monday to Friday and the
    # central closed holidays (not a location's own week or its state's list)
    days, closed = open_days("Mon-Fri"), holidays("")
    day9, day10 = add_working(AWARD_DAY, o.days - 1, days, closed), add_working(AWARD_DAY, o.days, days, closed)
    out.metrics["award day, day before the deadline, deadline"] = f"{AWARD_DAY}, {day9}, {day10}"
    lapse_rfq = ""
    with on_day(AWARD_DAY):
        req3 = c.request(PROD, "RM-08", "100", "PLT-CKN", "2027-01-15")
        lapse_rfq = c.rfq_of(BUYER, req3)
        if lapse_rfq:
            invite(BUYER, lapse_rfq, clean[2])
            ids = proof(clean[2])
            r = c.call(
                PLANT_HEAD,
                "POST",
                f"/api/vendors/{clean[2]}/identity-check",
                {"method": method, "document_ids": ids, "note": "E56"},
            )
            check(
                r.status_code == 200,
                f"identity check of {clean[2]} for the deadline case: {r.status_code} {r.text[:150]} (proof upload: spec silent)",
                "medium",
            )
            for code, price, until in (
                (clean[2], "880", "2026-12-31"),
                ("SUP-005", "905", "2026-11-30"),
                ("SUP-003", "915", "2026-12-31"),
            ):
                c.quote(BUYER, lapse_rfq, code, price, "18", until)
            r = c.award(BUYER, lapse_rfq, clean[2])
            if r.status_code in (409, 422):
                r = c.award(BUYER, lapse_rfq, clean[2], "E56: cheapest")
            check(
                r.status_code in (200, 202),
                f"conditional award to {clean[2]}: {r.status_code} {r.text[:150]}",
                "medium",
            )
            c.work()
    if lapse_rfq:
        buyer_id = c.user_id(BUYER)

        def lapse_tasks() -> list[Any]:
            return c.q("select assignee, payload::text from task where kind = 'prospect_lapsed'")

        # the spec names no CLI for the deadline: the worker's daily jobs are run on each day, as at noon that day
        def noon(day: date) -> datetime:
            return datetime.combine(day, time(12, 0), tzinfo=ZoneInfo("Asia/Kolkata"))

        with on_day(day9):
            c.work(now=noon(day9))
        lapsed9 = [a for a in awards(lapse_rfq) if a[2] is not None]
        check(not lapsed9 and not lapse_tasks(), f"the award lapsed on {day9}, working day {o.days - 1}: {lapsed9}")
        with on_day(day10):
            c.work(now=noon(day10))
        rows_ = awards(lapse_rfq)
        tasks = lapse_tasks()
        out.metrics["deadline: awards, buyer tasks"] = f"{rows_}, {[(t[0]) for t in tasks]}"
        check(
            any(s == clean[2] and lapsed is not None for s, _, lapsed in rows_),
            f"the conditional award did not lapse on {day10}, working day {o.days}: {rows_}",
        )
        check(
            len(tasks) == 1 and int(tasks[0][0]) == buyer_id,
            f"lapse tasks {tasks}; expected one for the buyer (user {buyer_id})",
        )
        check(
            {s for s, _, _ in rows_} == {clean[2]},
            f"after the lapse something else was awarded automatically: {rows_} (R10)",
        )
        check(len(c.audit("award.lapsed")) >= 1, "award.lapsed not audited", "medium")
        with on_day(day10):
            expired = c.award(BUYER, lapse_rfq, "SUP-005", "E56: the next cheapest")
            check(
                expired.status_code in (409, 422),
                f"re-selecting SUP-005, whose quote expired 30 Nov, answered {expired.status_code} (T-Q1-5)",
            )
            again = c.award(BUYER, lapse_rfq, "SUP-003", "E56: the prospect did not qualify in time; next valid quote")
            check(
                again.status_code in (200, 202),
                f"re-selecting SUP-003 with a reason: {again.status_code} {again.text[:150]}",
                "medium",
            )

    # ---- 8. an open RFQ keeps its quotation-count rule (R12, T-Q1-9) -------------------------------------------------
    req4 = c.request(PROD, "RM-11", "100", "PLT-CKN")
    rfq4 = c.rfq_of(BUYER, req4)
    if rfq4:
        before = js(c.call(BUYER, "GET", f"/api/rfqs/{rfq4}"))
        route_now = next((v for v in pick(js(c.call(ADMIN, "GET", "/api/setup/buying-route")), "versions", default=[])
                          if v.get("in_force") and v.get("category") in (None, "")), None)  # fmt: skip
        keys = [k for k in (route_now or {}).get("values", {}) if "quote" in k]
        if route_now and keys:
            values = {k: v for k, v in route_now["values"].items() if v is not None}
            for k in keys:
                values[k] = "1.00" if "two" in k else "2.00" if "three" in k else values[k]
            r = c.call(
                ADMIN,
                "POST",
                "/api/setup/buying-route",
                {"values": values, "category": None, "effective_from": lib.TODAY.isoformat(), "reason": "E56"},
            )
            pid = pick(js(r), "proposal_id")
            if pid:
                c.approve(ADMIN2, int(pid))
            after = js(c.call(BUYER, "GET", f"/api/rfqs/{rfq4}"))
            pair = lambda p: (pick(p, "min_quotes"), pick(p, "min_quotes_rule"))  # noqa: E731
            out.metrics["open RFQ min_quotes before / after a route change"] = f"{pair(before)} / {pair(after)}"
            check(
                pair(before) == pair(after) and pair(before)[0] is not None,
                f"an open RFQ's quotation count changed with the rule: {pair(before)} -> {pair(after)}",
            )
            req5 = c.request(PROD, "RM-09", "200", "PLT-CKN")
            rfq5 = c.rfq_of(BUYER, req5)
            newer = js(c.call(BUYER, "GET", f"/api/rfqs/{rfq5}")) if rfq5 else {}
            out.metrics["an RFQ made after the change"] = str(pair(newer))
            if pair(newer) == pair(before):
                out.find(
                    "low",
                    "an RFQ made after the route change has the same min_quotes and rule as before: the control did not move",
                )
        else:
            out.notes.append(
                "the buying route shows no quote band keys; T-Q1-9 is not tried (the spec names no endpoint for a category's min_quotes)"
            )
    else:
        out.find("medium", f"the paint request {req4} went to no RFQ; T-Q1-9 not tried")

    # ---- 9. no order to an inactive supplier, touchless included (T-Q1-6) ---------------------------------------------
    refused_before = len(c.audit("po.issue_refused"))
    # an order already on its way when the supplier is blocked: sourcing never offers a blocked supplier's contract, so
    # the refusal of "every route" is reached only by an order made while the supplier was still active (500 castings
    # of Rs 220 are above the buyer's own tier, so the order waits for an approver)
    flight_before = {x[0] for x in c.q("select no from po where supplier_code = 'SUP-001'")}
    c.request(PROD, "RM-03", "500", "PLT-CKN")
    c.work()
    in_flight = [n for n, s_ in c.q("select no, status from po where supplier_code = 'SUP-001'")
                 if n not in flight_before and s_ in ("draft", "submitted")]  # fmt: skip
    out.metrics["SUP-001 orders waiting when it is blocked"] = in_flight
    # orders issued while SUP-001 was still active are fine; those waiting must not be issued after the block
    at_block = {x[0] for x in c.q("select no from po where supplier_code = 'SUP-001'")} - set(in_flight)
    r = c.call(BUYER, "POST", "/api/vendors/SUP-001/status", {"to": "blocked", "reason": "E56: quality hold"})
    for p in c.proposals("supplier.set_status"):
        if "SUP-001" in str(p["payload"]):
            c.approve(DIRECTOR, p["id"])
    c.work()
    if check(
        c.one("select status from supplier where code = 'SUP-001'") == "blocked",
        f"SUP-001 could not be blocked: {r.status_code} {r.text[:150]}",
        "medium",
    ):
        for po_no in in_flight:  # its approver tries to issue it after the block
            c.approve_po(po_no)
        before_pos = at_block
        req6 = c.request(PROD, "RM-03", "20", "PLT-CKN")
        c.work()
        new = c.q("select no, status from po where supplier_code = 'SUP-001'")
        issued = [(n, s) for n, s in new if n not in before_pos and s in ("approved", "sent", "acknowledged")]
        out.metrics["contract line for blocked SUP-001: orders issued, refusals audited"] = (
            f"{issued}, {len(c.audit('po.issue_refused')) - refused_before}"
        )
        check(not issued, f"an order was issued to blocked SUP-001 ({req6}): {issued}")
        check(
            len(c.audit("po.issue_refused")) > refused_before,
            "an order refused for an inactive supplier left no po.issue_refused audit row",
            "medium",
        )

    # ---- 10. another company sees none of it --------------------------------------------------------------------------
    bw = Company("brightwave", "e56", client)
    seen = js(bw.call("U03", "GET", "/api/vendors", params={"status": "prospect"}))
    text_ = str(seen)
    apex_names = [o.sup[k]["legal_name"] for k in ("SUP-008", "SUP-009")] + ["Eval Prospect"]
    check(not any(n in text_ for n in apex_names), "Brightwave's buyer sees Apex prospects")
    check(c.call(BUYER, "GET", "/api/vendors/SUP-008/profile").status_code == 200 and "Sabarmati" not in str(js(bw.call("U03", "GET", "/api/vendors/SUP-008/profile"))),
          "Brightwave reads Apex's SUP-008")  # fmt: skip
    bw_prospects = {r["code"] for r in bw.rows("master", "supplier") if r["status"] == "prospect"}
    check(
        bw_prospects == {"SUP-006"}, f"Brightwave's dataset prospects {bw_prospects}, the spec plants SUP-006", "medium"
    )

    # ---- the mutations ------------------------------------------------------------------------------------------------
    out.metrics["mutations caught"] = mutations
    for name in (
        "identity check by the inviter allowed",
        "an unchecked quote counted",
        "an order made from a conditional award",
    ):
        check(mutations.get(name) is True, f"mutation '{name}' is not told apart by these cases", "medium")
    out.notes.append(
        "Not tried (spec gaps): the agent tool refusals of R3/R12 (no tool name given), 'two flagged suppliers count as one', a category min_quotes edit endpoint"
    )
    out.metrics["checks"] = f"{check.done - len(check.failed)} of {check.done} passed"
    return out
