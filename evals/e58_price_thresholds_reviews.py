"""E58: strategic reviews open on their triggers; price triggers only above the thresholds (task 12.5, D40, D41, D52, O2),
written without the implementation.

Independence: written from the block 0b spec (section 6, with the owner's 20 Nov decisions: gap 5%, Rs 10 a unit, saving
Rs 25,000, evidence at most 45 days old, one source, all strictly above; the lowest counting benchmark is the market price;
the contract price is the denominator) and the dataset only. The oracle reads `price_threshold_rule.csv`,
`market_benchmark.csv`, `price_record.csv`, `contract.csv`, `location.csv`, `uom_conversion.csv` and the demand inputs,
and computes the gap and the saving in exact fractions: gap Rs = contract - market (4 places), gap % = gap / contract x 100
(4 places, half up), saving = gap Rs x relevant quantity (paise, half up). A price trigger goes ahead only when every test
holds; `plan`, `expiry` and `buyer` always open a review. The relevant quantity is what the platform ordered of the item
in the price's scope over the last 365 days (read from the order rows here), else the open requirements and plan: the
eval plants ten laptops ordered so the Brightwave case is decided by the gap alone, and keeps the Apex rupee and saving
cases far from any quantity the demand inputs could give.

Cases: Brightwave LP-01 at exactly 5.0000% (no review, one evaluation row); a verified benchmark at 58,899 (5.0016%,
review opens for the category's buyer); the same % with the rupee test failing (shaft, Rs 10.00 a unit exactly) and with
the saving failing (Rs 10.01 a unit on a few hundred shafts); stale (46 days), unverified and other-region benchmarks never
counted, the 45-day one counted; a second buyer trigger joins the open review; the expiry review on day 60, not day 61; a
buyer trigger always opens; the contract is still used while a review is open; a threshold change waits for the second
administrator and the evaluation records the version in force on its day; closing needs a reason; each company sees only
its own reviews. Two broken rules (">=" for ">"; any test instead of all) must be told apart by the cases.
"""

import json
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal
from fractions import Fraction
from typing import Any

import env
import lib
from block0b import Checker, Company, cli, dec, dicts, js, listed, on_day, pick, section
from lib import D, Outcome

ID = "E58"
TITLE = (
    "Strategic reviews open on their triggers; price triggers only above every threshold, on verified fresh evidence"
)
REQUIREMENT = (
    "Task 12.5, D40, D41, D52, O2: the price-gap test (gap %, Rs a unit, saving at the relevant quantity, sources) holds "
    "only when every part is strictly above the versioned threshold; only verified, fresh, in-region, convertible "
    "benchmarks count; plan, expiry and buyer triggers always open; one open review per business key; every evaluation "
    "is written with its rule version; the contract stays valid; a threshold change waits for a second administrator"
)

TOMORROW = lib.TODAY + timedelta(days=1)
P4 = Decimal("0.0001")


# ---- the oracle -------------------------------------------------------------------------------------------------------


@dataclass
class Rule:
    version: int
    gap_pct: Decimal
    gap_inr: Decimal
    min_saving: Decimal
    max_age: int
    min_sources: int
    expiry_days: int


def rule_rows(c: Company) -> dict[int, Rule]:
    found = {}
    for r in c.rows("master", "price_threshold_rule"):
        if r.get("category_code"):
            continue
        found[int(r["version"])] = Rule(int(r["version"]), D(r["gap_pct"]), D(r["gap_inr_per_unit"]), D(r["min_saving_inr"]),
                                        int(r["evidence_max_age_days"]), int(r["min_sources"]), int(r["expiry_review_days"]))  # fmt: skip
    return found


def gap(contract: Decimal, market: Decimal) -> tuple[Decimal, Decimal]:
    rs = Fraction(contract) - Fraction(market)
    pct = rs / Fraction(contract) * 100
    return Decimal(rs.numerator) / Decimal(rs.denominator), (
        Decimal(pct.numerator) / Decimal(pct.denominator)
    ).quantize(P4, rounding=ROUND_HALF_UP)


def decide(
    rule: Rule, contract: Decimal, market: Decimal | None, qty: Decimal, sources: int, how: str = "right"
) -> bool:
    """Does a price trigger go ahead? 'right' = all strictly above; 'ge' = >= (mutation); 'any' = OR (mutation)."""
    if market is None or sources < rule.min_sources:
        return False
    rs, pct = gap(contract, market)
    saving = (rs * qty).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    tests = [(pct, rule.gap_pct), (rs, rule.gap_inr), (saving, rule.min_saving)]
    if how == "ge":
        return all(a >= b for a, b in tests)
    if how == "any":
        return any(a > b for a, b in tests)
    return all(a > b for a, b in tests)


def counting(bm: dict[str, str], day: date, rule: Rule, region: str, convertible: bool) -> tuple[bool, str]:
    """Rule 2: verified, at most max_age days old, national or the price's region, a convertible unit."""
    eff = date.fromisoformat(bm["effective_date"])
    if bm.get("status") != "verified":
        return False, "unverified"
    if eff > day or (day - eff).days > rule.max_age:
        return False, f"stale: {(day - eff).days} days old"
    if bm.get("region_code") and bm["region_code"] != region:
        return False, "other region"
    if not convertible:
        return False, "unit not convertible"
    return True, "counts"


# ---- the eval -------------------------------------------------------------------------------------------------------


def run(ctx: lib.Ctx) -> Outcome:
    env.forget_tenants("e58")
    try:
        return _run(ctx)
    finally:
        env.forget_tenants("e58")


def _run(ctx: lib.Ctx) -> Outcome:  # noqa: C901 - one scenario, step by step
    from fastapi.testclient import TestClient

    from p2p.main import app

    out = Outcome(True)
    check = Checker(out)
    lib.capture_mail()
    client = TestClient(app)
    mutation_cases = {"ge": 0, "any": 0}

    def tasks(co: Company, word: str = "") -> list[tuple[Any, ...]]:
        found = co.q("select id, assignee, status, payload::text from task where kind = 'strategic_review' order by id")
        return [t for t in found if str(t[2]) not in ("done", "closed", "completed", "cancelled") and word in str(t[3])]

    def evaluations(co: Company, item: str) -> list[tuple[Any, ...]]:
        found = co.q(
            "select id, version, input::text, output::text, coalesce(trace::text, '') from rule_evaluation where key = 'price_threshold' order by id"
        )
        return [r for r in found if f'"{item}"' in str(r[2])]

    def bench(
        co: Company, by: str, item: str, price: str, eff: date, uom: str, region: str = "", source: str = "E58 check"
    ) -> int | None:
        body = {"item_code": item, "price": price, "uom": uom, "basis": "delivered", "effective_date": eff.isoformat(),
                "region_code": region or None, "source_kind": "market_survey", "source_name": source,
                "source_ref": "eval", "note": "synthetic, E58"}  # fmt: skip
        r = co.call(by, "POST", "/api/benchmarks", body)
        bid = pick(js(r), "id")
        check(
            r.status_code in (200, 201) and bid,
            f"{co.name}: entering a benchmark for {item}: {r.status_code} {r.text[:150]}",
            "medium",
        )
        return int(bid) if bid else None

    def verify(co: Company, by: str, bid: int | None) -> None:
        if bid:
            r = co.call(by, "POST", f"/api/benchmarks/{bid}/verify", {"note": "E58"})
            check(
                r.status_code == 200, f"{co.name}: verifying benchmark {bid}: {r.status_code} {r.text[:150]}", "medium"
            )

    def review_run(co: Company, day: date | None = None) -> str:
        with on_day(day or lib.TODAY):
            co.work()
            code, said = cli("review", co.slug)
        if code:
            out.notes.append(f"{co.name}: `p2p.cli review` exited {code}: {said[-200:]}")
        return said

    def thresholds(co: Company) -> list[dict[str, Any]]:
        return list(pick(js(co.call("U06", "GET", "/api/setup/price-thresholds")), "versions", default=[]) or [])

    def same_rule(co: Company, rules: dict[int, Rule]) -> None:
        for v in thresholds(co):
            if v.get("category") not in (None, ""):
                continue
            n = int(v["version"])
            vals = v.get("values") or v
            want = rules.get(n)
            got = (dec(vals.get("gap_pct")), dec(vals.get("gap_inr_per_unit")), dec(vals.get("min_saving_inr")),
                   int(vals.get("evidence_max_age_days") or 0), int(vals.get("min_sources") or 0), int(vals.get("expiry_review_days") or 0))  # fmt: skip
            check(want is not None and got == (want.gap_pct, want.gap_inr, want.min_saving, want.max_age, want.min_sources, want.expiry_days),
                  f"{co.name}: threshold version {n} on screen {got}, the dataset {want}")  # fmt: skip

    # ================= Brightwave: the 5% boundary on laptops ================================================
    bw = Company("brightwave", "e58", client)
    bw_rules = rule_rows(bw)
    rule = bw_rules.get(1)
    if not check(rule is not None and rule.gap_pct == D(5) and rule.gap_inr == D(10) and rule.min_saving == D(25000)
                 and rule.max_age == 45 and rule.min_sources == 1, f"Brightwave's threshold rule is not the owner's defaults: {rule}"):  # fmt: skip
        return out
    same_rule(bw, bw_rules)
    lp_contract = next(D(r["price"]) for r in bw.rows("master", "price_record")
                       if r["item_code"] == "LP-01" and r["contract_code"] and (r.get("status") or "active") == "active")  # fmt: skip
    lp_bench = [b for b in bw.rows("master", "market_benchmark") if b["item_code"] == "LP-01"]
    count_now = [b for b in lp_bench if counting(b, lib.TODAY, rule, "", True)[0]]
    market = min((D(b["price"]) for b in count_now), default=None)
    out.metrics["LP-01: contract, market, gap"] = (
        f"{lp_contract}, {market}, {gap(lp_contract, market) if market else '-'}"
    )
    check(market == D("58900.00") and gap(lp_contract, market)[1] == D("5.0000"),
          f"the dataset's LP-01 boundary is not 5.0000%: market {market} vs contract {lp_contract}", "medium")  # fmt: skip

    # ten laptops ordered on the platform, so the relevant quantity is known (rule 3)
    no = bw.request("U01", "LP-01", "10", "OFF-BLR")
    bw.work()
    po = str((bw.line("U03", no).get("source") or {}).get("po_no") or "")
    if po:
        bw.approve_po(po)
    qty = D(bw.one("select coalesce(sum(l.qty), 0) from po_line l join po p on p.no = l.po_no where l.item_code = 'LP-01' "
                   "and p.status not in ('draft', 'cancelled') and p.created_at >= now() - interval '365 days'") or 0)  # fmt: skip
    out.metrics["LP-01 relevant quantity (orders on the platform)"] = str(qty)
    check(qty >= 9, f"the planted laptop order did not count ({po}, {qty}): the saving test cannot pass", "medium")

    before = len(evaluations(bw, "LP-01"))
    review_run(bw)
    evals_lp = evaluations(bw, "LP-01")
    want = decide(rule, lp_contract, market, qty, len(count_now))
    out.metrics["LP-01 at 5.0000%: evaluations written, reviews"] = (
        f"{len(evals_lp) - before}, {len(tasks(bw, 'LP-01'))}"
    )
    check(
        not want and not tasks(bw, "LP-01"), "a review opened for LP-01 at exactly 5.0000% (the test is strictly above)"
    )
    check(
        len(evals_lp) - before == 1,
        f"one detector run wrote {len(evals_lp) - before} evaluation rows for LP-01, expected 1 (written whether or not it goes ahead)",
    )
    mutation_cases["ge"] += int(decide(rule, lp_contract, market, qty, len(count_now), "ge") and not tasks(bw, "LP-01"))

    # a verified benchmark one rupee lower: 5.0016%
    bid = bench(bw, "U03", "LP-01", "58899.00", lib.TODAY, "NOS", source="E58 channel partner quote")
    own = bw.call("U03", "POST", f"/api/benchmarks/{bid}/verify", {"note": "mine"}) if bid else None
    check(
        own is None or own.status_code == 403,
        f"the enterer verified their own benchmark ({own.status_code if own else '-'})",
    )
    verify(bw, "U08", bid)
    review_run(bw)
    rs, pct = gap(lp_contract, D("58899.00"))
    want = decide(rule, lp_contract, D("58899.00"), qty, len(count_now) + 1)
    opened = tasks(bw, "LP-01")
    out.metrics["LP-01 at 58,899: gap, saving, reviews"] = (
        f"Rs {rs} {pct}%, Rs {(rs * qty).quantize(Decimal('0.01'))}, {len(opened)}"
    )
    check(pct == D("5.0016") and want, f"oracle: 58,899 should be 5.0016% and go ahead ({pct}, {want})", "medium")
    check(len(opened) == 1, f"LP-01 at 5.0016% with saving Rs {rs * qty}: {len(opened)} open reviews, expected 1")
    buyer = next((r["buyer_user"] for r in bw.rows("master", "category") if r["code"] == "ITH"), "U03")
    if opened:
        check(
            int(opened[0][1]) == bw.user_id(buyer),
            f"the LP-01 review went to user {opened[0][1]}, the ITH buyer is {buyer}",
        )
    shown = js(bw.call("U03", "GET", "/api/reviews", params={"status": "open"}))
    gaps = {dec(v) for d in dicts(shown) if "LP-01" in str(d) for v in d.values() if dec(v) is not None}
    check(pct in gaps, f"the open review does not show the engine's gap {pct}%: {str(shown)[:200]}", "medium")
    check(bool(bw.audit("review.open")), "review.open not audited", "medium")

    # buyer triggers: always open; the second for the same business key joins
    first = bw.call(
        "U03",
        "POST",
        "/api/reviews",
        {"trigger": "buyer", "contract_code": "CNT-0001", "item_code": "LP-01", "reason": "E58: renewal talks"},
    )
    second = bw.call(
        "U03",
        "POST",
        "/api/reviews",
        {"trigger": "buyer", "contract_code": "CNT-0001", "item_code": "LP-01", "reason": "E58: again"},
    )
    out.metrics["buyer triggers on LP-01 (joined)"] = f"{pick(js(first), 'joined')}, {pick(js(second), 'joined')}"
    check(
        first.status_code in (200, 201) and pick(js(first), "joined") is False,
        f"a buyer trigger did not open a review: {first.status_code} {first.text[:150]}",
    )
    check(pick(js(second), "joined") is True and pick(js(second), "task_id") == pick(js(first), "task_id"),
          f"a second buyer trigger did not join the open one: {second.text[:150]}")  # fmt: skip
    check(bool(bw.audit("review.joined")), "review.joined not audited", "medium")
    unknown = bw.call("U03", "POST", "/api/reviews", {"trigger": "buyer", "contract_code": "CNT-9999", "reason": "E58"})
    check(
        unknown.status_code == 422, f"a buyer trigger on an unknown contract answered {unknown.status_code}", "medium"
    )

    # the contract is still used for ordering while reviews are open
    no2 = bw.request("U01", "LP-01", "1", "OFF-BLR")
    bw.work()
    route = bw.one("select route from sourcing_decision where request_no = :n", n=no2)
    check(
        route == "contract", f"with a review open the laptop request went {route!r}, not on the contract (Rev line 186)"
    )

    # closing needs a reason
    tid = pick(js(first), "task_id")
    if tid:
        r = bw.call("U03", "POST", f"/api/reviews/{tid}/close", {"outcome": "no_action", "reason": ""})
        check(r.status_code == 422, f"closing a review with no reason answered {r.status_code}", "medium")
        r = bw.call(
            "U03", "POST", f"/api/reviews/{tid}/close", {"outcome": "no_action", "reason": "E58: price agreed for 2027"}
        )
        check(
            r.status_code == 200 and bool(bw.audit("review.closed")),
            f"closing with a reason: {r.status_code}; review.closed audited {bool(bw.audit('review.closed'))}",
            "medium",
        )

    # ================= Apex: rupee and saving tests, evidence age, region, expiry, a new version =================
    c = Company("apex", "e58", client)
    rules = rule_rows(c)
    rule = rules[1]
    same_rule(c, rules)
    prices = {r["item_code"]: D(r["price"]) for r in c.rows("master", "price_record")
              if r["contract_code"] == "CNT-0001" and (r.get("status") or "active") == "active" and r["valid_from"] <= lib.TODAY.isoformat()}  # fmt: skip
    bom = c.rows("inputs", "bom")
    per = {(b["product_code"], b["item_code"]): D(b["qty"]) for b in bom}
    demand = sum((D(r["qty"]) for r in c.rows("inputs", "production_order_line") if r["item_code"] == "RM-05"), D(0))
    demand += sum(
        (D(r["qty"]) * per.get((r["product_code"], "RM-05"), D(0)) for r in c.rows("inputs", "demand_plan")), D(0)
    )
    out.metrics["RM-05 shafts: an upper bound on the relevant quantity from the demand inputs"] = str(demand)
    check(
        Decimal("10.01") * demand <= rule.min_saving,
        f"the shaft cases need fewer than 2,498 shafts in demand; the inputs give {demand}",
        "medium",
    )

    # rupee test fails: Rs 10.00 a unit exactly on a Rs 180 shaft (5.5556%)
    shaft = prices.get("RM-05", D("180.00"))
    b1 = bench(c, "U03", "RM-05", str(shaft - 10), lib.TODAY, "NOS", "MH", "E58 shaft rate A")
    verify(c, "U08", b1)
    review_run(c)
    r_ok = decide(rule, shaft, shaft - 10, demand, 1)
    check(
        not r_ok and not tasks(c, "RM-05"),
        f"RM-05 at Rs 10.00 a unit below the contract opened a review ({tasks(c, 'RM-05')})",
    )
    mutation_cases["any"] += int(decide(rule, shaft, shaft - 10, demand, 1, "any") and not tasks(c, "RM-05"))
    # saving fails: Rs 10.01 a unit on a few hundred shafts
    b2 = bench(c, "U03", "RM-05", str(shaft - Decimal("10.01")), lib.TODAY, "NOS", "MH", "E58 shaft rate B")
    verify(c, "U08", b2)
    review_run(c)
    check(
        not tasks(c, "RM-05"),
        f"RM-05 at Rs 10.01 a unit, saving under Rs 25,000, opened a review ({tasks(c, 'RM-05')})",
    )
    mutation_cases["any"] += int(
        decide(rule, shaft, shaft - Decimal("10.01"), demand, 2, "any") and not tasks(c, "RM-05")
    )
    ev = evaluations(c, "RM-05")
    out.metrics["RM-05 evaluations written"] = len(ev)
    check(len(ev) >= 2, f"the shaft evaluations were not written whether or not they go ahead ({len(ev)})")

    # evidence: 45 days counts, 46 stale, unverified and other region never
    fresh = bench(c, "U03", "RM-03", "150.00", lib.TODAY - timedelta(days=45), "NOS", "MH", "E58 impeller 45")
    stale = bench(c, "U03", "RM-03", "149.00", lib.TODAY - timedelta(days=46), "NOS", "MH", "E58 impeller 46")
    raw = bench(c, "U03", "RM-03", "148.00", lib.TODAY, "NOS", "MH", "E58 impeller unverified")
    verify(c, "U08", fresh)
    verify(c, "U08", stale)
    review_run(c)
    view = js(c.call("U03", "GET", "/api/items/RM-03/prices", params={"location": "PLT-CKN"}))
    rows_ = {int(pick(r, "id", default=0) or 0): str(r) for r in (section(view, "benchmark", "market") or [])}
    reasons = {
        "fresh": rows_.get(fresh or -1, ""),
        "stale": rows_.get(stale or -1, ""),
        "raw": rows_.get(raw or -1, ""),
    }
    out.metrics["RM-03 benchmark reasons (45 days, 46 days, unverified)"] = {k: v[:80] for k, v in reasons.items()}
    check(
        "stale" in reasons["stale"].lower() and "46" in reasons["stale"],
        f"the 46-day benchmark is not shown stale: {reasons['stale'][:150]}",
    )
    check(
        "stale" not in reasons["fresh"].lower() and bool(reasons["fresh"]),
        f"the 45-day benchmark is not counted: {reasons['fresh'][:150]}",
    )
    check(
        "unverified" in reasons["raw"].lower(),
        f"the unverified benchmark is not shown unverified: {reasons['raw'][:150]}",
    )
    last = evaluations(c, "RM-03")[-1:]
    used = used_ids(last[0][2]) if last else set()
    if used:
        check(
            fresh in used and stale not in used and raw not in used,
            f"RM-03's evaluation counted benchmarks {sorted(used)}: 45-day {fresh} in, 46-day {stale} and unverified {raw} out",
        )
    else:
        out.notes.append(
            "RM-03's evaluation input lists no benchmark ids the eval could read; the reasons were checked on the price view"
        )
    gj = [b for b in c.rows("master", "market_benchmark") if b["item_code"] == "RM-01" and b.get("region_code") == "GJ"]
    gj_ids = {int(pick(r, "id")) for r in listed(js(c.call("U03", "GET", "/api/benchmarks", params={"item": "RM-01", "region": "GJ"})), "benchmarks", "records", "items", "rows")
              if pick(r, "id")} if gj else set()  # fmt: skip
    rm01 = evaluations(c, "RM-01")
    check(
        bool(rm01) and not tasks(c, "RM-01"),
        f"RM-01 at 3.3846% below the MH contract: evaluations {len(rm01)}, reviews {tasks(c, 'RM-01')} (expected one or more, none)",
    )
    if gj_ids and rm01:
        counted = used_ids(rm01[-1][2])
        if counted:
            check(
                not counted & gj_ids,
                f"the GJ benchmark {sorted(gj_ids)} counted for the MH contract on RM-01: {sorted(counted)}",
            )
    else:
        out.notes.append(
            "the GJ benchmark of RM-01 could not be found by id; 'other region' is checked in E59 on the price view"
        )

    # expiry: CNT-0001 ends 31 Mar 2027; the review opens 60 days before, not 61
    valid_to = date.fromisoformat(next(r["valid_to"] for r in c.rows("master", "contract") if r["code"] == "CNT-0001"))
    day60, day61 = valid_to - timedelta(days=rule.expiry_days), valid_to - timedelta(days=rule.expiry_days + 1)
    review_run(c, day61)
    on61 = tasks(c, "expiry")
    review_run(c, day60)
    on60 = tasks(c, "expiry")
    review_run(c, day60)
    again = tasks(c, "expiry")
    out.metrics["expiry reviews on day 61 / day 60 / day 60 again"] = (
        f"{len(on61)} / {len(on60)} / {len(again)} ({day61}, {day60})"
    )
    check(not on61, f"an expiry review opened on {day61}, {rule.expiry_days + 1} days before the contract ends")
    check(bool(on60), f"no expiry review on {day60}, {rule.expiry_days} days before the contract ends")
    check(
        len(again) == len(on60), "running the expiry detector twice on one day opened a second review for the same key"
    )

    # a buyer trigger always opens, below every threshold
    r = c.call(
        "U03",
        "POST",
        "/api/reviews",
        {
            "trigger": "buyer",
            "contract_code": "CNT-0001",
            "item_code": "RM-01",
            "reason": "E58: supplier asked to talk",
        },
    )
    check(
        r.status_code in (200, 201) and pick(js(r), "task_id"),
        f"an Apex buyer trigger below the thresholds did not open: {r.status_code} {r.text[:150]}",
    )

    # a threshold change waits for the second administrator; the evaluation records the version in force on its day
    vals = {"gap_pct": "4.0000", "gap_inr_per_unit": str(rule.gap_inr), "min_saving_inr": str(rule.min_saving),
            "evidence_max_age_days": rule.max_age, "min_sources": rule.min_sources, "expiry_review_days": rule.expiry_days}  # fmt: skip

    def put(body: dict[str, Any], who: str = "U06") -> Any:
        return c.call(who, "PUT", "/api/setup/price-thresholds", {"category": None, "reason": "E58", **body})

    bad = {
        "negative": put(
            {"values": {**vals, "gap_inr_per_unit": "-1.00"}, "effective_from": TOMORROW.isoformat()}
        ).status_code,
        "over 100%": put(
            {"values": {**vals, "gap_pct": "100.0001"}, "effective_from": TOMORROW.isoformat()}
        ).status_code,
        "in the past": put({"values": vals, "effective_from": (lib.TODAY - timedelta(days=1)).isoformat()}).status_code,
    }
    out.metrics["threshold changes refused"] = bad
    check(all(v == 422 for v in bad.values()), f"bad threshold changes: {bad}, expected 422 each")
    r = put({"values": vals, "effective_from": TOMORROW.isoformat()})
    pid = pick(js(r), "proposal_id")
    check(r.status_code == 202 and pid, f"a threshold change answered {r.status_code} {r.text[:150]}")
    if pid:
        check(c.approve("U06", int(pid)).status_code == 403, "the administrator approved their own threshold change")
        check(
            c.approve("U10", int(pid)).status_code == 200,
            "the second administrator could not approve the threshold change",
        )
    review_run(c)
    today_v = {r[1] for r in evaluations(c, "RM-01")[-1:]}
    review_run(c, TOMORROW)
    tomorrow_v = {r[1] for r in evaluations(c, "RM-01")[-1:]}
    out.metrics["evaluation version today / tomorrow"] = f"{today_v} / {tomorrow_v}"
    check(
        today_v == {1} and tomorrow_v == {2},
        f"evaluations used versions {today_v} today and {tomorrow_v} tomorrow; expected 1 then 2",
    )
    labels = {a["rule_version"] for a in c.audit("review.open")}
    check(
        any(str(x).startswith("price_threshold v") for x in labels),
        f"review.open audit rows carry no 'price_threshold vN': {labels}",
        "medium",
    )

    # ================= each company sees only its own reviews ===================================================
    apex_view = str(js(c.call("U03", "GET", "/api/reviews", params={"status": "open"})))
    bw_view = str(js(bw.call("U03", "GET", "/api/reviews", params={"status": "open"})))
    check("LP-01" not in apex_view and "RM-0" not in bw_view, "a company's review list shows another company's reviews")

    # ================= the mutations ================================================================================
    out.metrics["cases that tell a broken rule apart"] = mutation_cases
    check(mutation_cases["ge"] > 0, "no case tells '>=' from '>' (the 5.0000% laptop)", "medium")
    check(mutation_cases["any"] > 0, "no case tells 'any test' from 'all tests' (the shafts)", "medium")
    out.notes.append(
        "Relevant quantity when nothing was ordered (requirements plus plan) is bounded here, not computed exactly (OP-15 leaves the BOM horizon open)"
    )
    out.metrics["checks"] = f"{check.done - len(check.failed)} of {check.done} passed"
    return out


def used_ids(raw: str) -> set[int]:
    """The benchmark ids an evaluation's input names (under any key that says benchmark)."""
    try:
        data = json.loads(raw)
    except ValueError:
        return set()
    found: set[int] = set()
    for d in dicts(data):
        for k, v in d.items():
            if "benchmark" not in k.lower():
                continue
            for x in v if isinstance(v, list) else [v]:
                if isinstance(x, int):
                    found.add(x)
                elif isinstance(x, dict) and isinstance(x.get("id"), int):
                    found.add(x["id"])
    return found
