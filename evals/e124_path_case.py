"""E124: a contract line priced above the market waits for the buyer to choose path A or B (task 11b.2, D40, D43,
owner decision OP-17 (a)), written without the implementation.

Independence: written from the block 6 spec (sections 6.1, 8.3 A1-A4, 9, 10.2) and the dataset only. The oracle
(`block6.Oracle6.path_case`) takes the contract price from `price_record.csv` (CNT-0001, SUP-001, RM-01 650.00, region
MH), the counting benchmarks from `market_benchmark.csv` plus the one the eval enters (verified, at most 45 days old,
region MH or none, NOS), the gap test on `price_threshold_rule.csv` v1 (5%, Rs 10 per unit, Rs 25,000 saving; every
one must be passed), the percent on the contract price, the saving on the line's quantity, and urgency (need-by within
the price's lead days plus the RFQ days, in working days). The template is written here from the spec's words. The
order's amounts and band come from `block5.Oracle5`, with the buyer as a maker of the order (D43).

Given a fresh Apex reset on Mon 23 Nov 2026, the buyer enters a benchmark RM-01 600.00 NOS delivered MH of 20 Nov 2026
and the purchase manager verifies it:
- A1: production asks for RM-01 600 NOS need-by 15 Jan 2027 at PLT-CKN, approved, sourced: the line is not ordered,
  `path_case` waiting, a task `path_case` for U03, a `path_case` recommendation (key <request>/1) with figures contract
  650.00, market 600.0000, gap 50.0000, 7.6923%, saving 30,000.00, and the template exactly.
- A2: the buyer chooses A without a reason (422), production tries (403), Brightwave's buyer tries (404); with a reason:
  an order on CNT-0001 at 650.00, taxable 3,90,000.00, CGST 35,100.00, SGST 35,100.00, total 4,60,200.00, not
  touchless ("a person chose path A (D43)"), the buyer cannot approve it, the purchase manager (PO-02, U08) does; the
  recommendation rejected; audit `sourcing.path`.
- A3: a second A1 case, the buyer chooses B: an RFQ draft for RM-01 with a pending `rfq.send` proposal and an
  `rfq_draft` record; no order; the recommendation accepted.
- A4: as A1 with need-by 30 Nov 2026 (urgent): no case; ordered on the contract.
Ends with a tenant check.
Mutations: the gap test skipped (A1 ordered); a path A order touchless.
"""

from datetime import date
from decimal import Decimal
from typing import Any

import block1 as b
import block4 as k
import block6 as s
import env
import lib
from block1 import js
from lib import Outcome

ID = "E124"
TITLE = "A contract line above the market waits for the buyer to choose path A or B"
REQUIREMENT = (
    "Task 11b.2, D40, D43, OP-17 (a) (block 6 spec 6.1): the gap test at sourcing holds a contract line whose price is "
    "above every threshold and not urgent; case figures and template = the oracle; A needs a reason, orders on the "
    "contract, never touchless, its decider a maker; B makes an RFQ draft waiting for confirmation; urgent lines stay on "
    "the contract; tenant check"
)
PREFIX = "e124"
BENCH_DAY = date(2026, 11, 20)
SOURCE = "E124 Pune foundry survey"


def run(ctx: Any) -> Outcome:
    env.forget_tenants(PREFIX)
    try:
        return _run(ctx)
    finally:
        s.pin(lib.TODAY)
        env.forget_tenants(PREFIX)


def _run(ctx: Any) -> Outcome:
    from fastapi.testclient import TestClient
    from p2p.main import app
    from p2p.platform import mail

    out = Outcome(True)
    check = b.Checker(out)
    saved = mail.send_email
    box = lib.capture_mail()
    try:
        client = TestClient(app)
        bw = s.Co6("brightwave", PREFIX, client, box)
        apex = s.Co6("apex", PREFIX, client, box)
        s.pin(s.TODAY)
        apex.work()
        start = apex.mark()
        oracle = s.Oracle6(apex)
        m = k.Mutations()
        bench = apex.benchmark(check, s.CASTING, "600.00", "NOS", BENCH_DAY, "MH", SOURCE, "A1")
        extra = [bench] if bench else []
        pc = oracle.path_case(s.CASTING, s.LOC, Decimal(600), s.TODAY, s.A_NEED, extra)
        mine = {"contract_price": str(pc["price"]) if pc else None, "market": str(s.q4(pc["market"])) if pc else None,
                "gap": str(pc["gap"]) if pc else None, "gap_pct": str(pc["pct"]) if pc else None,
                "saving": str(pc["saving"]) if pc else None}  # fmt: skip
        s.self_check(check, "A1", mine)
        s.compare_truth(check, apex, "A1", mine)
        req_a = _a1(out, check, apex, oracle, m, pc, "A1")
        req_b = _a1(out, check, apex, oracle, m, pc, "A3 (a second A1)")
        if req_a:
            _a2(out, check, apex, oracle, m, req_a, bw)
        if req_b:
            _a3(check, apex, req_b)
        req_u = _a4(check, apex, oracle, extra)
        m.report(check, out, ["gap_test_skipped", "path_a_touchless"])
        s.tenant_tasks(check, apex, bw, [req_a, req_b, req_u])
        rfqs = [x for x in (apex.rfq_of(req_b) if req_b else "",) if x]
        s.f.tenant_check(check, apex, bw, rfqs, [], start)
        out.metrics["requests"] = {"A1/A2": req_a, "A3": req_b, "A4": req_u}
    finally:
        mail.send_email = saved  # type: ignore[assignment]
    out.metrics.update({"checks": check.done, "failed": len(check.failed)})
    out.notes += [
        (
            "Spec gap: the figures' JSON keys are not named; the eval checks that the record holds each number "
            "(rounded to 0, 2 and 4 places) and compares the summary word for word with every number read by its value."
        ),
        "The approver PO-02 / U08 is checked by the purchase manager's approval going through and the buyer's refused.",
    ]
    return out


def _a1(out: Outcome, check: b.Checker, co: s.Co6, oracle: s.Oracle6, m: k.Mutations, pc: dict[str, Any] | None,
        label: str) -> str:  # fmt: skip
    check(pc is not None, f"{label}: the oracle finds no A/B case for RM-01 600 NOS (precondition)", "medium")
    req = co.ask(check, s.CASTING, 600, s.A_NEED, label)
    if not req:
        return ""
    ordered = co.lines_ordered(req)
    m.case("gap_test_skipped", f"{label}: the line ordered at sourcing", bool(ordered), False, True)
    check(not ordered, f"{label}: request {req}'s line was ordered ({[x['po_no'] for x in ordered]}); the spec holds it "
          "for the buyer (A/B case)")  # fmt: skip
    dec = co.decision_rows(req)
    check(bool(dec) and dec[-1].get("path_case") == "waiting", f"{label}: sourcing_decision path_case "
          f"{dec[-1].get('path_case') if dec else None}; the spec says waiting")  # fmt: skip
    tasks = co.tasks_of("path_case", req)
    title = f"Choose path A or B for {req} line 1 ({oracle.item_name(s.CASTING)})"
    check(len(tasks) == 1 and s.person_is(co, tasks[0].get("assignee"), "U03"), f"{label}: path_case tasks "
          f"{[(t.get('title'), co.code_of(t.get('assignee'))) for t in tasks]}; the spec says one for U03")  # fmt: skip
    check(bool(tasks) and s.norm(title) in s.norm(tasks[0].get("title")), f"{label}: the task title "
          f"{tasks[0].get('title') if tasks else None!r}; the spec says {title!r}", "medium")  # fmt: skip
    rec = co.live_rec("path_case", f"{req}/1")
    if not check(bool(rec), f"{label}: no path_case recommendation keyed {req}/1"):
        return req
    check(rec.get("capability") == "sourcing", f"{label}: the record's capability {rec.get('capability')}", "medium")
    if pc:
        missing = s.held(rec["engine_figures"], pc["price"], pc["market"], pc["gap"], pc["pct"], pc["saving"], 600)
        check(not missing, f"{label}: the record's figures lack {missing}: {str(rec['engine_figures'])[:300]}")
        want = oracle.path_text(pc)
        check(s.canon(rec.get("summary")) == s.canon(want), f"{label}: the template differs from the spec's words:\n"
              f"  got:  {rec.get('summary')}\n  want: {want}")  # fmt: skip
        check(bool(rec.get("rule_versions")), f"{label}: the record keeps no rule versions", "medium")
        out.metrics[f"{label} summary"] = rec.get("summary")
    detail = js(co.call(co.login("buyer"), "GET", f"/api/requests/{req}"))
    line = next((x for x in detail.get("lines") or [] if str(x.get("line_no", 1)) == "1"), {})
    check(line.get("path_case") == "waiting" and isinstance(line.get("path_recommendation"), dict),
          f"{label}: GET /api/requests/{req} line 1 shows path_case {line.get('path_case')!r} and "
          f"path_recommendation {str(line.get('path_recommendation'))[:120]}", "medium")  # fmt: skip
    return req


def _path(co: s.Co6, by: str, req: str, path: str, reason: str | None = None) -> Any:
    body: dict[str, Any] = {"path": path}
    if reason is not None:
        body["reason"] = reason
    r = co.call(by, "POST", f"/api/requests/{req}/lines/1/path", body)
    co.work()
    return r


def _a2(out: Outcome, check: b.Checker, co: s.Co6, oracle: s.Oracle6, m: k.Mutations, req: str, bw: s.Co6) -> None:
    buyer = co.login("buyer")
    r = _path(co, buyer, req, "A")
    check(r.status_code == 422, f"A2: path A without a reason answered {r.status_code} {r.text[:150]}; the spec says 422")
    r = _path(co, co.login("production"), req, "A", "eval: not mine to choose")
    check(r.status_code == 403, f"A2: production (no rfq.create) choosing answered {r.status_code}", "medium")
    r = bw.call(bw.login("buyer"), "POST", f"/api/requests/{req}/lines/1/path", {"path": "A", "reason": "x"})
    own = bw.one("select count(*) from request where no = :n", n=req)
    check(r.status_code in (403, 404) or own, f"tenant check: Brightwave's buyer deciding Apex's case answered "
          f"{r.status_code}")  # fmt: skip
    check(not co.lines_ordered(req), "A2: a refused choice ordered the line")
    audit0 = co.last_audit_id()
    r = _path(co, buyer, req, "A", "eval: the casting is tied to the 1 HP pattern on the contract")
    check(r.status_code == 200 and js(r).get("path") == "A", f"A2: path A with a reason answered {r.status_code} "
          f"{r.text[:200]}")  # fmt: skip
    lines = co.lines_ordered(req)
    orders = sorted({x["po_no"] for x in lines})
    if not check(len(orders) == 1, f"A2: path A made orders {orders}; expected one on the contract"):
        return
    po = co.po_row(orders[0])
    ln = lines[0]
    want = oracle.award_value([(s.CASTING, 600, "650.00")], "SUP-001", s.LOC)
    rule, approver = oracle.approver(want["total"], frozenset({"U03"}))
    s.self_check(check, "A2", {**{x: str(v) for x, v in want.items()}, "rule": rule, "approver": approver})
    s.compare_truth(check, co, "A2", {**want, "rule": rule, "approver": approver})
    got = {x: s.D(po.get(x)) for x in ("taxable", "cgst", "sgst", "igst", "total")}
    check(got == want and po.get("supplier_code") == "SUP-001" and s.D(ln.get("unit_price")) == Decimal("650.00"),
          f"A2: order {orders[0]}: {po.get('supplier_code')} at {ln.get('unit_price')}, amounts {got}; oracle SUP-001 "
          f"at 650.00, {want}")  # fmt: skip
    check(po.get("contract_code") == "CNT-0001" or ln.get("source_ref") == "CNT-0001" or "CNT-0001" in str(ln),
          f"A2: order {orders[0]} is not on CNT-0001 (contract {po.get('contract_code')})", "medium")  # fmt: skip
    touchless = bool(po.get("touchless"))
    m.case("path_a_touchless", "A2: the path A order", touchless, False, True)
    check(not touchless, f"A2: order {orders[0]} is touchless ({po.get('status')}); a person chose path A (D43)")
    check("path a" in s.norm(po.get("touchless_note")), f"A2: touchless_note {po.get('touchless_note')!r}; the spec "
          "says 'a person chose path A (D43)'", "medium")  # fmt: skip
    check(str(po.get("rule_code") or "") == rule, f"A2: the order's band {po.get('rule_code')}; oracle {rule} "
          f"({approver})", "medium")  # fmt: skip
    r = co.approve_po(buyer, orders[0])
    check(r.status_code in s.REFUSED, f"A2: the buyer (who chose path A) approving {orders[0]} answered "
          f"{r.status_code}; the decider is a maker of the order")  # fmt: skip
    r = co.approve_po(co.login("purchasemanager"), orders[0])
    co.work()
    check(r.status_code == 200, f"A2: the purchase manager ({approver} by the oracle) approving {orders[0]} answered "
          f"{r.status_code} {r.text[:200]}", "medium")  # fmt: skip
    dec = co.decision_rows(req)
    check(bool(dec) and dec[-1].get("path_case") == "chosen_a" and s.person_is(co, dec[-1].get("path_confirmed_by"),
          "U03"), f"A2: sourcing_decision path_case {dec[-1].get('path_case') if dec else None}, confirmed by "
          f"{dec[-1].get('path_confirmed_by') if dec else None}", "medium")  # fmt: skip
    rec = [x for x in co.recs("path_case", f"{req}/1")]
    check(bool(rec) and rec[-1].get("status") == "rejected", f"A2: the recommendation is "
          f"{rec[-1].get('status') if rec else None}; the spec says rejected with the reason", "medium")  # fmt: skip
    check(any(a.get("entity_id") in (req, f"{req}/1") or req in str(a) for a in co.since("sourcing.path", audit0)),
          "A2: no sourcing.path audit row", "medium")  # fmt: skip
    check(not co.tasks_of("path_case", req, "open"), "A2: the path_case task is still open after the choice", "medium")
    out.metrics["A2 order"] = {"no": orders[0], **{x: str(v) for x, v in got.items()}, "band": po.get("rule_code"),
                               "touchless": touchless}  # fmt: skip


def _a3(check: b.Checker, co: s.Co6, req: str) -> None:
    r = _path(co, co.login("buyer"), req, "B")
    body = js(r)
    check(r.status_code == 200 and body.get("path") == "B", f"A3: path B answered {r.status_code} {r.text[:200]}")
    rfq = str(body.get("rfq_no") or co.rfq_of(req))
    row = co.rfq(rfq) if rfq else {}
    prop = co.proposal(row.get("proposal_id"))
    check(bool(row) and row.get("status") == "draft" and prop.get("action") == "rfq.send"
          and prop.get("status") == "pending", f"A3: RFQ {rfq} is {row.get('status')} with proposal "
          f"{prop.get('action')} {prop.get('status')}; the spec says a draft waiting for a pending rfq.send")  # fmt: skip
    lines = co.rfq_lines(rfq) if rfq else []
    check([x.get("item_code") for x in lines] == [s.CASTING], f"A3: {rfq}'s lines {[x.get('item_code') for x in lines]}")
    check(bool(co.recs("rfq_draft", rfq)), f"A3: no rfq_draft record keyed {rfq}", "medium")
    check(not co.lines_ordered(req), f"A3: path B ordered the line ({[x['po_no'] for x in co.lines_ordered(req)]})")
    rec = co.recs("path_case", f"{req}/1")
    check(bool(rec) and rec[-1].get("status") == "accepted", f"A3: the recommendation is "
          f"{rec[-1].get('status') if rec else None}; the spec says accepted", "medium")  # fmt: skip


def _a4(check: b.Checker, co: s.Co6, oracle: s.Oracle6, extra: list[dict[str, str]]) -> str:
    pc = oracle.path_case(s.CASTING, s.LOC, Decimal(600), s.TODAY, s.URGENT_NEED, extra)
    check(pc is None, "A4: the oracle holds the urgent line (precondition)", "medium")
    req = co.ask(check, s.CASTING, 600, s.URGENT_NEED, "A4")
    if not req:
        return ""
    lines = co.lines_ordered(req)
    check(bool(lines) and all(x["supplier_code"] == "SUP-001" and s.D(x["unit_price"]) == Decimal("650.00")
                              for x in lines), f"A4: the urgent line was not ordered on the contract: "
          f"{[(x['po_no'], x['supplier_code'], x['unit_price']) for x in lines]}")  # fmt: skip
    check(not co.tasks_of("path_case", req) and not co.recs("path_case", f"{req}/1"), "A4: the urgent line got an A/B "
          "case; the spec keeps it on the contract (OP-19)")  # fmt: skip
    return req
