"""E57: a regional approval or price is never used outside its scope (task 12.4, D44, D40 step 2, D48), written without
the implementation.

Independence: written from the block 0b spec (section 5) and the dataset only. The oracle reads `location.csv`,
`region.csv`, `supplier_category.csv`, `supplier_category_scope.csv`, `price_record.csv`, `contract.csv`, `supplier.csv`,
`supplier_contact.csv`, `supplier_document.csv`, `category.csv` and `sanction_entry.csv` of the demo company's own
dataset. A location is inside a scope when the scope is empty (everywhere), lists the location, or lists its region. On
any day a contract price is offered at a location only when it is an active version in force that day (a newer version
of the same supplier and item, in an intersecting scope, ends it the day before it starts), of an active contract valid
that day, and inside its scope; a supplier is invited for a category at a location only when it is active, approved for
the category on the day and inside the approval's scope, has an email, is not on the sanctions list and holds the
category's documents verified and unexpired.

Probed through the API: the item's price view (`GET /api/items/{code}/prices?location=`, contract section) for every
item at both locations of each company on four days around the year end and the contract's end; requests at each
location (each line's estimate source and buying route); the invitees and delivery location of the RFQs a Sanand request
makes; a quote from a supplier approved in Maharashtra only on a Sanand RFQ; a Gujarat price added to CNT-0001 (accepted
beside the Maharashtra one, an everywhere price refused as an overlap, a price with both a location and a region refused)
and activated by someone other than who priced it; a category-scope change that waits for a second person. Two broken
rules (scope ignored in the contract lookup; scope ignored in the quote must-have) must be told apart by the cases.
"""

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from typing import Any

import env
import lib
from block0b import Checker, Company, dec, dicts, js, on_day, pick, section
from lib import D, Outcome

ID = "E57"
TITLE = "A regional supplier approval or contract price is never used outside its scope; the delivery location decides"
REQUIREMENT = (
    "Task 12.4, D44, D40, D48: approvals and contract prices may carry a location or region; contract lookup, estimate, "
    "buying route, RFQ invitees and the quote must-have use the line's delivery location; RFQs are per category and "
    "location; prices of one supplier and item may not overlap where their scopes intersect; a scoped price is activated "
    "by someone other than its maker; a category scope change is a second person's decision; audited"
)

BUYER, PROD, PLANT_HEAD, PM, DIRECTOR = "U03", "U01", "U07", "U08", "U09"
DAYS = (lib.TODAY, date(2026, 12, 31), date(2027, 1, 1), date(2027, 4, 1))


@dataclass
class Price:
    supplier: str
    item: str
    contract: str
    price: Decimal
    version: int
    status: str
    start: date
    end: date | None
    location: str
    region: str


class Scope:
    """The oracle: scopes, prices and approvals as the dataset writes them."""

    def __init__(self, c: Company) -> None:
        self.region = c.region_of()
        self.regions = {r["code"]: r["name"] for r in c.rows("master", "region")}
        self.contracts = {r["code"]: r for r in c.rows("master", "contract")}
        self.prices = [
            Price(r["supplier_code"], r["item_code"], r["contract_code"], D(r["price"]), int(r.get("version") or 1),
                  r.get("status") or "active", date.fromisoformat(r["valid_from"]),
                  date.fromisoformat(r["valid_to"]) if r["valid_to"] else None, r.get("location_code") or "", r.get("region_code") or "")
            for r in c.rows("master", "price_record")
            if r["contract_code"]
        ]  # fmt: skip
        self.sup = {r["code"]: r for r in c.rows("master", "supplier")}
        self.cat = c.rows("master", "supplier_category")
        self.scope = c.rows("master", "supplier_category_scope")
        self.emails = {r["supplier_code"] for r in c.rows("master", "supplier_contact") if r["email"]}
        self.docs = c.rows("master", "supplier_document")
        self.categories = {r["code"]: r for r in c.rows("master", "category")}
        self.sanctioned = {r["name"].lower() for r in c.rows("master", "sanction_entry")}
        self.items = {r["code"]: r for r in c.rows("master", "item")}

    def inside(self, location: str, loc_code: str, region: str) -> bool:
        return (
            (not loc_code and not region)
            or loc_code == location
            or (bool(region) and region == self.region.get(location))
        )

    def intersects(self, a: Price, b: Price) -> bool:
        if not (a.location or a.region) or not (b.location or b.region):
            return True
        regions = lambda p: {p.region} if p.region else {self.region.get(p.location, "")}  # noqa: E731
        return (a.location and a.location == b.location) or bool(regions(a) & regions(b))

    def in_force(self, item: str, location: str, day: date, *, ignore_scope: bool = False) -> set[tuple[str, Decimal]]:
        usable = [p for p in self.prices if p.item == item and p.status == "active"]
        found: set[tuple[str, Decimal]] = set()
        for p in usable:
            end = p.end
            newer = [
                q
                for q in usable
                if q.supplier == p.supplier and q.version > p.version and q.start > p.start and self.intersects(p, q)
            ]
            if newer:
                end = min(q.start for q in newer) - timedelta(days=1)
            k = self.contracts.get(p.contract, {})
            k_ok = k.get("status") == "active" and date.fromisoformat(k["valid_from"]) <= day <= date.fromisoformat(
                k["valid_to"]
            )
            if (
                p.start <= day
                and (end is None or day <= end)
                and k_ok
                and (ignore_scope or self.inside(location, p.location, p.region))
            ):
                found.add((p.supplier, p.price))
        return found

    def invitees(self, category: str, location: str, day: date) -> set[str]:
        need = {d for d in self.categories.get(category, {}).get("required_supplier_docs", "").split(";") if d}
        out: set[str] = set()
        for r in self.cat:
            code = r["supplier_code"]
            if r["category_code"] != category or date.fromisoformat(r["approved_from"]) > day:
                continue
            scope = [s for s in self.scope if s["supplier_code"] == code and s["category_code"] == category]
            if scope and not any(
                self.inside(location, s.get("location_code", ""), s.get("region_code", "")) for s in scope
            ):
                continue
            s = self.sup.get(code, {})
            held = {
                d["doc_type"] for d in self.docs
                if d["supplier_code"] == code and d["verified"] == "true" and (not d["expires_on"] or date.fromisoformat(d["expires_on"]) >= day)
            }  # fmt: skip
            if (
                s.get("status") == "active"
                and code in self.emails
                and s.get("legal_name", "").lower() not in self.sanctioned
                and need <= held
            ):
                out.add(code)
        return out


def shown_contract_prices(payload: Any) -> set[tuple[str, Decimal]] | None:
    rows = section(payload, "contract")
    if rows is None:
        return None
    return {(str(pick(r, "supplier_code", "supplier")), dec(pick(r, "price", "unit_price")) or D(-1)) for r in rows}


def run(ctx: lib.Ctx) -> Outcome:
    env.forget_tenants("e57")
    try:
        return _run(ctx)
    finally:
        env.forget_tenants("e57")


def _run(ctx: lib.Ctx) -> Outcome:  # noqa: C901 - one scenario, step by step
    from fastapi.testclient import TestClient

    from p2p.main import app

    out = Outcome(True)
    check = Checker(out)
    lib.capture_mail()
    client = TestClient(app)
    c = Company("apex", "e57", client)
    o = Scope(c)
    discriminating = {"contract lookup": 0, "must-have": 0}

    if not check(set(o.region) == {"PLT-CKN", "PLT-SND"} and o.region.get("PLT-SND") == "GJ" and o.region.get("PLT-CKN") == "MH",
                 f"Apex's dataset should have PLT-CKN in MH and PLT-SND in GJ: {o.region}"):  # fmt: skip
        return out

    # ---- 1. regions as master data -----------------------------------------------------------------------------------
    regions = js(c.call(BUYER, "GET", "/api/regions"))
    shown = {
        str(pick(r, "code")): sorted(str(pick(x, "code", default=x)) for x in pick(r, "locations", default=[]))
        for r in (regions or [])
    }
    want = {code: sorted(loc for loc, reg in o.region.items() if reg == code) for code in o.regions}
    check(shown == want, f"GET /api/regions {shown}, the dataset {want}", "medium")

    def grid(
        company: Company,
        oracle: Scope,
        days: tuple[date, ...],
        items: list[str],
        label: str,
        extra: list[Price] | None = None,
    ) -> list[str]:
        wrong: list[str] = []
        saved = list(oracle.prices)
        oracle.prices += extra or []
        try:
            for day in days:
                with on_day(day):
                    for item in items:
                        for loc in sorted(oracle.region):
                            payload = js(
                                company.call(BUYER, "GET", f"/api/items/{item}/prices", params={"location": loc})
                            )
                            got = shown_contract_prices(payload)
                            want_ = oracle.in_force(item, loc, day)
                            naive = oracle.in_force(item, loc, day, ignore_scope=True)
                            if got is None:
                                wrong.append(
                                    f"{item} at {loc} on {day}: the price view has no contract section ({str(payload)[:120]})"
                                )
                                continue
                            if got != want_:
                                wrong.append(
                                    f"{item} at {loc} on {day}: contract prices {sorted(got)}, oracle {sorted(want_)}"
                                )
                            elif naive != want_:
                                discriminating["contract lookup"] += 1
        finally:
            oracle.prices = saved
        out.metrics[f"{label}: price view cells off the oracle"] = len(wrong)
        return wrong

    # ---- 2. the grid: every item, both plants, four days ------------------------------------------------------------
    apex_items = sorted(o.items)
    for w in grid(c, o, DAYS, apex_items, "Apex grid")[:8]:
        out.find("high", f"Apex: {w}")

    # ---- 3. requests at each plant: estimate source and buying route per line ----------------------------------------
    def requests_at(
        company: Company, oracle: Scope, location: str, items: list[str]
    ) -> dict[str, tuple[str, str, str]]:
        """item -> (est_source, route, rfq_no) for a one-line request of each item at the location."""
        found: dict[str, tuple[str, str, str]] = {}
        line_col = next((x for x in sorted(company.columns("sourcing_decision")) if "line" in x), None)
        for item in items:
            no = company.request(PROD, item, "2", location)
            company.work()
            ln = company.line(BUYER, no)
            route = ""
            if line_col:
                sql = f"select route from sourcing_decision where request_no = :n order by {line_col}"  # noqa: S608
                route = str(company.one(sql, n=no) or "")
            found[item] = (str(ln.get("est_source") or ""), route, str((ln.get("source") or {}).get("rfq_no") or ""))
        return found

    goods = ["RM-01", "RM-02", "RM-03", "RM-04", "RM-05", "PK-01"]  # the contract items, and the cartons
    by_loc = {loc: requests_at(c, o, loc, goods) for loc in ("PLT-CKN", "PLT-SND")}
    est_wrong, route_wrong = [], []
    for loc, found in by_loc.items():
        for item, (est_text, route, _) in found.items():
            # est_source is in words, "contract CNT-0001" (data dictionary, request_line); the kind is its first word
            est = est_text.split(" ")[0]
            on_contract = bool(o.in_force(item, loc, lib.TODAY))
            naive = bool(o.in_force(item, loc, lib.TODAY, ignore_scope=True))
            if (est == "contract") != on_contract:
                est_wrong.append(
                    f"{item} at {loc}: estimate from {est_text!r}, oracle {'contract' if on_contract else 'not the contract'}"
                )
            if (route == "contract") != on_contract:
                route_wrong.append(
                    f"{item} at {loc}: route {route!r}, oracle {'contract' if on_contract else 'not the contract'}"
                )
            if naive != on_contract and (est == "contract") == on_contract:
                discriminating["contract lookup"] += 1
    out.metrics["estimates / routes off the oracle"] = f"{len(est_wrong)} / {len(route_wrong)}"
    out.metrics["RM-01 at Chakan / Sanand (estimate source, route)"] = (
        f"{by_loc['PLT-CKN'].get('RM-01', ('', ''))[:2]} / {by_loc['PLT-SND'].get('RM-01', ('', ''))[:2]}"
    )
    for w in (est_wrong + route_wrong)[:8]:
        out.find("high", w)

    # ---- 4. Sanand RFQs: invitees, delivery location, a Maharashtra-only supplier's quote -----------------------------
    for item in ("RM-01", "PK-01"):
        rfq = by_loc["PLT-SND"][item][2]
        category = o.items[item]["category_code"]
        want_inv = o.invitees(category, "PLT-SND", lib.TODAY)
        if not rfq:
            check(not want_inv, f"Sanand {item}: no RFQ, though {sorted(want_inv)} are approved there", "medium")
            out.notes.append(
                f"Sanand {item}: no RFQ made (the oracle has {sorted(want_inv) or 'no supplier'} approved there)"
            )
            continue
        page = js(c.call(BUYER, "GET", f"/api/rfqs/{rfq}"))
        asked = {str(pick(s, "code", "supplier_code")) for s in pick(page, "suppliers", default=[])}
        out.metrics[f"Sanand {item} RFQ {rfq}: invitees (platform / oracle)"] = f"{sorted(asked)} / {sorted(want_inv)}"
        # the platform asks at most `rfq_supplier_count` suppliers (default 3, as built before this block; the spec does
        # not change it), raised to the RFQ's min_quotes: every one asked must be eligible at Sanand, and as many as that
        cap = max(3, int(pick(page, "min_quotes") or 0))
        check(asked <= want_inv and len(asked) == min(len(want_inv), cap),
              f"Sanand {item} RFQ {rfq} asks {sorted(asked)}, the oracle {sorted(want_inv)} (at most {cap})")  # fmt: skip
        check(
            c.one("select delivery_location from rfq where no = :n", n=rfq) == "PLT-SND",
            f"RFQ {rfq} does not store delivery location PLT-SND",
        )
        check(
            str(pick(page, "delivery_location", default="")) == "PLT-SND" or "PLT-SND" in str(page)[:2000],
            f"RFQ {rfq} does not show its delivery location",
            "low",
        )
    rfq_raw = by_loc["PLT-SND"]["RM-01"][2]
    mh_only = next((s for s in sorted(o.sup) if o.sup[s]["status"] == "active" and s in o.invitees("RAW", "PLT-CKN", lib.TODAY)
                    and s not in o.invitees("RAW", "PLT-SND", lib.TODAY)), None)  # fmt: skip
    if rfq_raw and mh_only:
        c.work()
        for p in c.proposals("rfq.send"):
            if p["payload"].get("rfq_no") == rfq_raw:
                c.approve(BUYER, p["id"])
        r = c.quote(BUYER, rfq_raw, mh_only, "640.00", "18")
        page = js(c.call(BUYER, "GET", f"/api/rfqs/{rfq_raw}"))
        says = [
            d
            for d in dicts(page)
            if pick(d, "supplier_code", "supplier") == mh_only and "not approved" in str(d).lower()
        ]
        award = c.award(BUYER, rfq_raw, mh_only, "E57")
        out.metrics[f"quote from {mh_only} (Maharashtra only) on Sanand {rfq_raw}"] = (
            f"entry {r.status_code}; must-have shown {bool(says)}; award {award.status_code}"
        )
        check(
            r.status_code in (409, 422) or bool(says),
            f"{mh_only}'s quote at Sanand is not failed 'not approved for this category at PLT-SND': {str(page)[:200]}",
        )
        check(
            award.status_code in (403, 409, 422),
            f"{mh_only}, approved for RAW in Maharashtra only, was awarded a Sanand line ({award.status_code})",
        )
        discriminating["must-have"] += int(award.status_code in (403, 409, 422))
    else:
        out.find(
            "medium",
            f"no Sanand RAW RFQ ({rfq_raw}) or no Maharashtra-only RAW supplier ({mh_only}) to try the must-have on",
        )

    # ---- 5. a Gujarat price on CNT-0001: no overlap across regions, refused across an everywhere price -----------------
    base = "/api/contracts/CNT-0001/prices"
    start = date(2027, 1, 1)
    common = {"item_code": "RM-01", "uom": "NOS", "valid_from": start.isoformat()}
    tries = {
        "GJ 685.00": c.call(PM, "POST", base, {**common, "price": "685.00", "region_code": "GJ"}),
        "everywhere 690.00": c.call(PM, "POST", base, {**common, "price": "690.00"}),
        "location and region": c.call(
            PM, "POST", base, {**common, "price": "695.00", "region_code": "GJ", "location_code": "PLT-SND"}
        ),
    }
    out.metrics["prices added to CNT-0001 for RM-01"] = {
        k: f"{r.status_code} {str(pick(js(r), 'detail', default=''))[:60]}" for k, r in tries.items()
    }
    check(
        tries["GJ 685.00"].status_code in (200, 201),
        f"a GJ price beside the MH one was refused: {tries['GJ 685.00'].text[:200]}",
    )
    check(
        tries["everywhere 690.00"].status_code == 409,
        f"an everywhere price over the MH one answered {tries['everywhere 690.00'].status_code}, expected 409 (overlap)",
    )
    check("MH" in tries["everywhere 690.00"].text or "Maharashtra" in tries["everywhere 690.00"].text or "region" in tries["everywhere 690.00"].text.lower(),
          "the overlap refusal does not name the scope", "low")  # fmt: skip
    check(
        tries["location and region"].status_code == 422,
        f"a price with both a location and a region answered {tries['location and region'].status_code}",
    )
    trail = c.audit("contract.price_add")
    check(
        any("GJ" in str(a["after"]) for a in trail),
        f"contract.price_add audit has no region GJ in 'after': {[a['after'] for a in trail][-2:]}",
        "medium",
    )
    c.call(PLANT_HEAD, "POST", "/api/contracts/CNT-0001/versions/submit", {})
    by_maker = c.call(PM, "POST", "/api/contracts/CNT-0001/versions/activate", {})
    check(by_maker.status_code == 403, f"the maker of the GJ price activated its version ({by_maker.status_code})")
    done = c.call(DIRECTOR, "POST", "/api/contracts/CNT-0001/versions/activate", {"note": "E57"})
    check(done.status_code == 200, f"the director's activation answered {done.status_code} {done.text[:150]}")
    if done.status_code == 200:
        for p in o.prices:
            if p.contract == "CNT-0001" and p.status == "draft":
                p.status = "active"
        gj = Price("SUP-001", "RM-01", "CNT-0001", D("685.00"), max(p.version for p in o.prices if p.contract == "CNT-0001"),
                   "active", start, date.fromisoformat(o.contracts["CNT-0001"]["valid_to"]), "", "GJ")  # fmt: skip
        for w in grid(c, o, (date(2026, 12, 31), start), ["RM-01", "RM-02"], "after activation", [gj])[:6]:
            out.find("high", f"after activation: {w}")

    # ---- 6. Brightwave: housekeeping at Hyderabad is never on the Bengaluru service contract --------------------------
    bw = Company("brightwave", "e57", client)
    bo = Scope(bw)
    for w in grid(bw, bo, (lib.TODAY, date(2027, 1, 1)), sorted(bo.items), "Brightwave grid")[:6]:
        out.find("high", f"Brightwave: {w}")
    hyd = next((loc for loc, reg in bo.region.items() if reg == "TS"), "OFF-HYD")
    sv = requests_at(bw, bo, hyd, ["SV-01"]).get("SV-01", ("", "", ""))
    out.metrics[f"Brightwave housekeeping at {hyd} (estimate source, route)"] = sv[:2]
    check(
        sv[0].split(" ")[0] != "contract" and sv[1] != "contract",
        f"housekeeping at {hyd} used CNT-0002 (Karnataka only): {sv[:2]}",
    )
    seen = js(bw.call("U03", "GET", "/api/regions"))
    check(not ({"MH", "GJ"} & {str(pick(r, "code")) for r in (seen or [])}), "Brightwave sees Apex's regions")

    # ---- 7. a category scope change waits for a second person -----------------------------------------------------------
    url = "/api/vendors/SUP-004/categories"
    ask = {
        "category": "PKG",
        "approved_from": lib.TODAY.isoformat(),
        "scope": {"locations": [], "regions": ["MH", "GJ"]},
        "reason": "E57: Sanand cartons",
    }
    bad = c.call(BUYER, "POST", url, {**ask, "scope": {"locations": [], "regions": ["XX"]}})
    check(bad.status_code == 422, f"an unknown region in a scope answered {bad.status_code}", "medium")
    r = c.call(BUYER, "POST", url, ask)
    pid = pick(js(r), "proposal_id")
    check(r.status_code == 202 and pid, f"asking for SUP-004's PKG scope answered {r.status_code} {r.text[:150]}")
    again = c.call(BUYER, "POST", url, ask)
    check(again.status_code == 409, f"a second scope change while one waits answered {again.status_code}", "medium")
    if pid:
        own = c.approve(BUYER, int(pid))
        check(own.status_code == 403, f"the asker approved her own category scope change ({own.status_code})")
        profile = js(c.call(BUYER, "GET", "/api/vendors/SUP-004/profile"))
        check(
            "GJ" not in str(pick(profile, "categories", default="")),
            "the scope changed before the second person approved",
        )
        ok = c.approve(DIRECTOR, int(pid))
        check(
            ok.status_code == 200,
            f"the director's approval of the scope change answered {ok.status_code} {ok.text[:150]}",
        )
        profile = js(c.call(BUYER, "GET", "/api/vendors/SUP-004/profile"))
        pkg = next((x for x in pick(profile, "categories", default=[]) if pick(x, "code", "category") == "PKG"), {})
        check("GJ" in str(pkg.get("scope")), f"after approval SUP-004's PKG scope is {pkg.get('scope')}", "medium")
        check(bool(c.audit("supplier.category_scope")), "supplier.category_scope not audited", "medium")

    # ---- the mutations ------------------------------------------------------------------------------------------------
    out.metrics["cases that tell a broken rule apart"] = discriminating
    check(
        discriminating["contract lookup"] > 0,
        "no case tells 'scope ignored in the contract lookup' from the right rule",
        "medium",
    )
    check(
        discriminating["must-have"] > 0, "no case tells 'scope ignored in the must-have' from the right rule", "medium"
    )
    out.notes.append(
        "The price view's contract section stands in for the contract option (the spec gives its sections, not its JSON keys)"
    )
    out.metrics["checks"] = f"{check.done - len(check.failed)} of {check.done} passed"
    return out
