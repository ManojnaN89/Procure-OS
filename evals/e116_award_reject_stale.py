"""E116: a rejected award, a stale award, and selecting again (task 12.2, Rev s12 #16, 17), written without the
implementation.

Independence: written from the block 5 spec (sections 6.1 rules 5 and 7, 7.1, 8.3 P2 and P3, 9) and the dataset only.
Round 2's figures are the oracle's (`block5.Oracle5.award_value`, SUP-003 at 895.00 inside Maharashtra); the quote's
expiry is the date the eval typed (valid until 31 Dec 2026).

P2 on a fresh Apex reset, 23 Nov 2026: P1 to the proposal; the director's reject without a reason is 422; with "Check
SUP-003's delivery record" it is 200: decision rejected, awards rejected, the RFQ back to `sent`, no letter (mail or
row), no contract, audit `award.reject`. The buyer selects SUP-003 (the recommendation was SUP-005): without a reason
refused, with one 202: round 2, 8,05,500.00 / 72,495.00 / 72,495.00 / 9,50,490.00, PO-03, U09. P3 on a second fresh
reset: P1 to the proposal; on 1 Jan 2027 the director's approval is 409 "the award is stale: ... expired on 31 Dec 2026":
decision stale, awards withdrawn, RFQ `sent`, a buyer task "Select again on <rfq>", the recommendation stale, audit
`award.stale`, no letter and no contract. Truth file; tenant check.
Mutations: the approval of an expired quote; a rejected award left proposed.
"""

from datetime import date
from typing import Any

import block1 as b
import block4 as k
import block5 as f
import env
import lib
from block1 import js
from lib import Outcome

ID = "E116"
TITLE = "A rejected or stale award sends nothing and the buyer selects again"
REQUIREMENT = (
    "Task 12.2, Rev s12 #16-17 (block 5 spec 6.1 rules 5 and 7, P2, P3): reject needs a reason and leaves no letter or "
    "contract, awards rejected, RFQ sent, round 2 with the oracle's figures; an expired quote makes the approval 409 "
    "stale with nothing approved, awards withdrawn, a buyer task and the recommendation stale; audit; tenant check"
)
PREFIX = "e116"
STALE_DAY = date(2027, 1, 1)
REASON = "Check SUP-003's delivery record"


def run(ctx: Any) -> Outcome:
    env.forget_tenants(PREFIX)
    try:
        return _run(ctx)
    finally:
        f.pin(lib.TODAY)
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
        bw = f.Co5("brightwave", PREFIX, client, box)
        apex = f.Co5("apex", PREFIX, client, box)
        f.start(apex)
        oracle = f.Oracle5(apex)
        m = k.Mutations()
        truth: list[dict[str, Any]] = []
        start = apex.mark()
        p2 = _p2(out, check, apex, oracle, m, truth)
        out.metrics["truth rows compared"] = f.compare_truth(check, apex, truth)
        f.tenant_check(check, apex, bw, [p2], [], start)
        apex.reset()  # P3 starts again from P1: a fresh company
        f.start(apex)
        start = apex.mark()
        p3 = _p3(out, check, apex, m)
        m.report(check, out, ["stale_approved", "reject_left_proposed"])
        f.tenant_check(check, apex, bw, [p3], [], start)
    finally:
        mail.send_email = saved  # type: ignore[assignment]
    out.metrics.update({"checks": check.done, "failed": len(check.failed)})
    out.notes += [
        (
            "Selecting SUP-003 without a reason may be refused 409 (today's award errors) or 422; both are accepted, the "
            "spec says only that a choice other than the recommendation needs a written reason."
        ),
        (
            "The stale reason is checked for 'stale', 'expired' and '31 Dec 2026' (the supplier may be named by code or "
            "name)."
        ),
    ]
    return out


def no_letters(
    check: b.Checker,
    co: f.Co5,
    oracle: f.Oracle5,
    rfq: str,
    decision: Any,
    since: int,
    label: str,
) -> None:
    addresses = {
        oracle.contacts[s]["email"].lower() for s in ("SUP-005", "SUP-003", "SUP-001")
    }
    mailed = [
        x
        for x in co.mails(since)
        if str(x.get("to", "")).lower() in addresses and rfq in str(x.get("body", ""))
    ]
    check(not mailed and not co.letters(decision) and not co.contracts_of(rfq),
          f"{label}: {len(mailed)} letter mail(s), {len(co.letters(decision))} award_letter row(s), "
          f"{len(co.contracts_of(rfq))} contract(s); expected none")  # fmt: skip


def _p2(
    out: Outcome,
    check: b.Checker,
    co: f.Co5,
    oracle: f.Oracle5,
    m: k.Mutations,
    truth: list[dict[str, Any]],
) -> str:
    buyer, director = co.login("buyer"), co.login("director")
    rfq, r = f.p1_proposal(check, co)
    if not rfq or r is None:
        return rfq
    first = co.decision(rfq)
    r = co.decide(director, rfq, "reject")
    check(r.status_code == 422 and co.decision(rfq).get("status") == "waiting",
          f"P2: rejecting without a reason answered {r.status_code}; expected 422 and the decision still waiting")  # fmt: skip
    since, mark = co.mark(), co.last_audit_id()
    r = co.decide(director, rfq, "reject", reason=REASON)
    check(r.status_code == 200 and js(r).get("status") == "rejected", f"P2: the director's reject answered "
          f"{r.status_code} {r.text[:200]}")  # fmt: skip
    d = next((x for x in co.decisions(rfq) if x.get("id") == first.get("id")), {})
    statuses = sorted(
        {
            str(a.get("status"))
            for a in co.awards(rfq)
            if a.get("decision_id") == first.get("id")
        }
    )
    m.case(
        "reject_left_proposed",
        "P2 round 1's awards after the reject",
        statuses,
        ["rejected"],
        ["proposed"],
    )
    check(d.get("status") == "rejected" and statuses == ["rejected"] and co.rfq(rfq).get("status") == "sent"
          and REASON in str(d.get("decision_note") or d.get("reason") or ""),
          f"P2 after the reject: decision {d.get('status')} ({d.get('decision_note')!r}), awards {statuses}, RFQ "
          f"{co.rfq(rfq).get('status')}; expected rejected, rejected, sent, with the reason")  # fmt: skip
    no_letters(check, co, oracle, rfq, first.get("id"), since, "P2 rejected")
    check(
        bool(co.since("award.reject", mark)), "P2: no award.reject audit row", "medium"
    )
    # select again: SUP-003 is not the recommendation, so a reason is needed
    r = co.award(buyer, rfq, {1: "SUP-003"})
    check(r.status_code in (409, 422) and len(co.decisions(rfq)) == 1, f"P2: selecting SUP-003 without a reason answered "
          f"{r.status_code} {r.text[:200]}; expected refused, no new decision")  # fmt: skip
    r = co.award(
        buyer,
        rfq,
        {1: "SUP-003"},
        reason="Shorter delivery record checked; SUP-003 delivers to Chakan on time",
    )
    check(r.status_code == 202 and js(r).get("status") == "award_proposed", f"P2: the second selection answered "
          f"{r.status_code} {r.text[:200]}")  # fmt: skip
    want = oracle.award_value([(f.WIRE, 900, "895.00")], "SUP-003", f.LOC)
    rule, approver = oracle.award_approver(want["total"], buyer)
    f.self_check(check, oracle, "P2", want, rule, approver)
    f.check_decision(check, co, rfq, want, rule, approver, "P2 round 2", round_no=2)
    truth.append({"scenario": "P2", "line": "1", "item": f.WIRE, "qty": 900, "supplier": "SUP-003", "net_price": "895.00",
                  **{x: want[x] for x in ("taxable", "cgst", "sgst", "igst")}, "total": {want["total"]}, "rule": rule,
                  "approver": approver})  # fmt: skip
    out.metrics["P2 round 2 (value, approver)"] = f"{rfq}: {want['total']}, {approver}"
    return rfq


def _p3(out: Outcome, check: b.Checker, co: f.Co5, m: k.Mutations) -> str:
    oracle = f.Oracle5(co)
    buyer, director = co.login("buyer"), co.login("director")
    rfq, r = f.p1_proposal(check, co)
    if not rfq or r is None:
        return rfq
    d0 = co.decision(rfq)
    rec_id = d0.get("recommendation_id")
    since, mark = co.mark(), co.last_audit_id()
    with f.pinned(STALE_DAY):
        r = co.decide(director, rfq, "approve", note="eval: after the quote ended")
        m.case(
            "stale_approved",
            "P3: the director approves on 1 Jan 2027",
            r.status_code,
            409,
            200,
        )
        check(r.status_code == 409, f"P3: approving on 1 Jan 2027 answered {r.status_code} {r.text[:200]}; the spec "
              "says 409 stale (SUP-005's quote expired on 31 Dec 2026)")  # fmt: skip
        words = r.text.lower()
        check("stale" in words and "expired" in words and "31 dec 2026" in words, f"P3: the 409's words: {r.text[:200]}; "
              "expected 'the award is stale: SUP-005's quote expired on 31 Dec 2026'", "medium")  # fmt: skip
        co.work()
    d = next((x for x in co.decisions(rfq) if x.get("id") == d0.get("id")), {})
    statuses = sorted(
        {
            str(a.get("status"))
            for a in co.awards(rfq)
            if a.get("decision_id") == d0.get("id")
        }
    )
    check(d.get("status") == "stale" and statuses == ["withdrawn"] and co.rfq(rfq).get("status") == "sent",
          f"P3 after the stale approval: decision {d.get('status')}, awards {statuses}, RFQ {co.rfq(rfq).get('status')}; "
          "expected stale, withdrawn, sent")  # fmt: skip
    tasks = co.qd("select * from task where assignee = :u and status = 'open' and title like :t",
                  u=co.user_id(buyer), t=f"%Select again on {rfq}%")  # fmt: skip
    check(bool(tasks), f"P3: no open buyer task 'Select again on {rfq}: <reason>'")
    rec = co.recommendation(rec_id)
    check(
        rec.get("status") == "stale",
        f"P3: recommendation {rec_id} is {rec.get('status')}, expected stale",
        "medium",
    )
    check(bool(co.since("award.stale", mark)), "P3: no award.stale audit row", "medium")
    no_letters(check, co, oracle, rfq, d0.get("id"), since, "P3 stale")
    out.metrics["P3"] = (
        f"{rfq}: {d.get('status')}, task {bool(tasks)}, recommendation {rec.get('status')}"
    )
    return rfq
