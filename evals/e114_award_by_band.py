"""E114: a period award is approved by the band's approver, never the buyer (task 12.2, D45, OP-1 interim, C1),
written without the implementation.

Independence: written from the block 5 spec (sections 6.1, 8.3 P1, P4, P5, 9) and the dataset only. The award value is
the oracle's (`block5.Oracle5.award_value`: taxable half up, CGST + SGST inside Maharashtra, IGST from Gujarat), the
approver the `po` rows of `approval_rule.csv` on the value with GST with an `auto` band moved up and the buyer left out,
the person from the profile's approver roles.

On a fresh Apex reset, 23 Nov 2026. P1: the buyer's award of SUP-005 at 880.00 is 202 `award_proposed`: no order, the
RFQ `award_proposed`, award rows `proposed` with the decision, a pending `rfq.award_approve` proposal (permission
po.approve, value the total), a one-working-day task for the director, audit `award.propose`; the decision holds
7,92,000.00 / 71,280.00 / 71,280.00 / 0.00 / 9,34,560.00, PO-03 version 1, U09. While it waits, a new quote and a second
award are refused. P5: the buyer, the purchase manager and the plant head approving are 403; the buyer approving the
proposal through /api/admin/proposals is 403; the director approves (200 approved): still no order and no budget change.
P4: RM-07 1,000 from SUP-002 (Gujarat) at 84.00: 84,000.00 + IGST 15,120.00 = 99,120.00 is in PO-01's automatic band,
so the purchase manager U08 (PO-02) approves; the buyer cannot. Budget tables are the same before and after. Truth file;
tenant check.
Mutations: the automatic band approving an award; the buyer as approver.
"""

from typing import Any

import block1 as b
import block4 as k
import block5 as f
import env
import lib
from block1 import js
from lib import Outcome

ID = "E114"
TITLE = "A period award is approved by the band's approver, never by the buyer or an automatic band"
REQUIREMENT = (
    "Task 12.2, D45, OP-1 interim, C1 (block 5 spec 6.1, P1, P4, P5): the buyer proposes (202 award_proposed, no order, "
    "no budget); value with GST from the oracle; the po band names the approver, an auto band moves up; only the named "
    "approver decides (buyer, others, the proposals API 403); audit; truth file; tenant check"
)
PREFIX = "e114"


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
        start = apex.mark()
        oracle = f.Oracle5(apex)
        m = k.Mutations()
        truth: list[dict[str, Any]] = []
        budget = apex.budget_state()
        p1 = _p1(out, check, apex, oracle, m, truth)
        p4 = _p4(out, check, apex, oracle, m, truth)
        check(apex.budget_state() == budget, "a budget table changed between the period RFQs and their approved awards "
              "(spec 5.1 rule 8: no commitment)")  # fmt: skip
        out.metrics["truth rows compared"] = f.compare_truth(check, apex, truth)
        m.report(check, out, ["auto_band_approves", "buyer_approves"])
        f.tenant_check(check, apex, bw, [p1, p4], [], start)
    finally:
        mail.send_email = saved  # type: ignore[assignment]
    out.metrics.update({"checks": check.done, "failed": len(check.failed)})
    out.notes += [
        (
            "OP-1 is the interim rule (the po rows); the eval names the director for P1 and the purchase manager for P4 from "
            "approval_rule.csv and the profile, not from the spec's table."
        ),
        (
            "The approver in the answer and on award_decision may be a code, a user id, an email or a name; any of them "
            "naming the same person is accepted."
        ),
        "The task's due time (one working day) is checked as present, not to the hour.",
    ]
    return out


def _decide_refused(
    check: b.Checker, co: f.Co5, m: k.Mutations, rfq: str, who: list[str], label: str
) -> None:
    buyer = co.login("buyer")
    for code in who:
        before = (co.decision(rfq).get("status"), co.rfq(rfq).get("status"))
        r = co.decide(code, rfq, "approve", note="eval")
        if code == buyer:
            m.case(
                "buyer_approves",
                f"{label}: the buyer approves",
                r.status_code,
                403,
                200,
            )
        after = (co.decision(rfq).get("status"), co.rfq(rfq).get("status"))
        check(r.status_code == 403 and after == before, f"{label}: {co.people[code]['email']} approving the award "
              f"answered {r.status_code}; decision {before} -> {after}; expected 403 and nothing changed")  # fmt: skip


def _p1(
    out: Outcome,
    check: b.Checker,
    co: f.Co5,
    oracle: f.Oracle5,
    m: k.Mutations,
    truth: list[dict[str, Any]],
) -> str:
    buyer, director = co.login("buyer"), co.login("director")
    want = oracle.award_value([(f.WIRE, 900, "880.00")], "SUP-005", f.LOC)
    rule, approver = oracle.award_approver(want["total"], buyer)
    f.self_check(check, oracle, "P1", want, rule, approver)
    orders = co.count("po")
    mark = co.last_audit_id()
    rfq, r = f.p1_proposal(check, co)
    if not rfq or r is None:
        return rfq
    body = js(r)
    check(f.api_value(body, "value_total") == want["total"] and f.same_person(co, body.get("approver"), approver)
          and body.get("decision_id") is not None and body.get("proposal_id") is not None,
          f"P1 answer {body}; oracle value_total {want['total']}, approver {approver}")  # fmt: skip
    d = f.check_decision(check, co, rfq, want, rule, approver, "P1", round_no=1)
    check(int(d.get("rule_version") or 0) == 1 and d.get("status") == "waiting" and same_buyer(co, d.get("proposed_by")),
          f"P1 decision: rule version {d.get('rule_version')}, status {d.get('status')}, proposed by "
          f"{d.get('proposed_by')}; expected 1, waiting, the buyer")  # fmt: skip
    awards = co.awards(rfq)
    check(len(awards) == 1 and awards[0].get("status") == "proposed" and awards[0].get("decision_id") == d.get("id")
          and awards[0].get("supplier_code") == "SUP-005",
          f"P1 award rows {[(a.get('supplier_code'), a.get('status'), a.get('decision_id')) for a in awards]}")  # fmt: skip
    check(co.rfq(rfq).get("status") == "award_proposed" and co.count("po") == orders,
          f"P1: RFQ {co.rfq(rfq).get('status')}, orders {orders} -> {co.count('po')}; expected award_proposed and no order")  # fmt: skip
    prop = co.proposal(d.get("proposal_id") or body.get("proposal_id"))
    check(prop.get("action") == "rfq.award_approve" and prop.get("status") == "pending"
          and prop.get("required_permission") == "po.approve" and f.D(prop.get("value")) == want["total"],
          f"P1 proposal {prop.get('id')}: {prop.get('action')} {prop.get('status')} {prop.get('required_permission')} "
          f"value {prop.get('value')}; expected a pending rfq.award_approve, po.approve, {want['total']}")  # fmt: skip
    tasks = co.qd("select * from task where kind = 'award_approval' and status = 'open' and assignee = :u",
                  u=co.user_id(director))  # fmt: skip
    check(bool(tasks), "P1: no open award_approval task for the director", "medium")
    check(
        any(
            a["entity_id"] in (rfq, str(d.get("id"))) or rfq in str(a["after"])
            for a in co.since("award.propose", mark)
        ),
        "P1: no award.propose audit row",
        "medium",
    )
    # nothing new while the decision waits
    r = co.quote(buyer, rfq, "SUP-006", ["870.00"])
    check(
        r.status_code in f.REFUSED,
        f"P1: a new quote while the award waits answered {r.status_code}; expected refused",
    )
    r = co.award(buyer, rfq, {1: "SUP-003"}, reason="eval: changed my mind")
    check(
        r.status_code == 409,
        f"P1: a second award while the first waits answered {r.status_code}; expected 409",
    )
    # P5: who may not approve
    pid = prop.get("id") or body.get("proposal_id")
    _decide_refused(
        check,
        co,
        m,
        rfq,
        [buyer, co.login("purchasemanager"), co.login("planthead")],
        "P5",
    )
    r = co.call(buyer, "POST", f"/api/admin/proposals/{pid}/approve", {"note": "eval"})
    co.work()
    m.case(
        "buyer_approves",
        "P5: the buyer through the proposals API",
        r.status_code,
        403,
        200,
    )
    check(r.status_code == 403 and co.decision(rfq).get("status") == "waiting",
          f"P5: the buyer approving proposal {pid} through /api/admin/proposals answered {r.status_code}; expected 403")  # fmt: skip
    r = co.decide(director, rfq, "approve", note="eval: agreed")
    check(r.status_code == 200 and js(r).get("status") == "approved", f"P1: the director's approval answered "
          f"{r.status_code} {r.text[:200]}")  # fmt: skip
    d = co.decision(rfq)
    check(d.get("status") == "approved" and f.same_person(co, d.get("decided_by"), director)
          and co.rfq(rfq).get("status") == "awarded" and all(a.get("status") == "approved" for a in co.awards(rfq))
          and co.count("po") == orders,
          f"P1 after approval: decision {d.get('status')} by {d.get('decided_by')}, RFQ {co.rfq(rfq).get('status')}, "
          f"awards {[a.get('status') for a in co.awards(rfq)]}, orders {orders} -> {co.count('po')}")  # fmt: skip
    truth.append({"scenario": "P1", "line": "1", "item": f.WIRE, "qty": 900, "supplier": "SUP-005", "net_price": "880.00",
                  **{x: want[x] for x in ("taxable", "cgst", "sgst", "igst")}, "total": {want["total"]}, "rule": rule,
                  "approver": approver})  # fmt: skip
    out.metrics["P1 (value, rule, approver)"] = (
        f"{rfq}: {want['total']}, {rule}, {approver}"
    )
    return rfq


def same_buyer(co: f.Co5, who: Any) -> bool:
    return f.same_person(co, who, co.login("buyer"))


def _p4(
    out: Outcome,
    check: b.Checker,
    co: f.Co5,
    oracle: f.Oracle5,
    m: k.Mutations,
    truth: list[dict[str, Any]],
) -> str:
    buyer, pm = co.login("buyer"), co.login("purchasemanager")
    want = oracle.award_value([(f.SEAL, 1000, "84.00")], "SUP-002", f.LOC)
    rule, approver = oracle.award_approver(want["total"], buyer)
    f.self_check(check, oracle, "P4", want, rule, approver)
    auto_rule = next(
        r["rule_code"]
        for r in oracle.bands()
        if f.D(r["min_value"]) <= want["total"] <= f.D(r["max_value"])
    )
    value = 1000 * oracle.estimate(f.SEAL, f.LOC, f.TODAY)
    mq = oracle.min_quotes("RAW", value)
    invitees = oracle.invitees("RAW", [f.SEAL], f.LOC, f.TODAY, mq)
    check(set(invitees) == f.SPEC_INVITED["P4"] and mq == f.SPEC_MIN_QUOTES["P4"], f"oracle self-check P4: invitees "
          f"{invitees}, min_quotes {mq}", "medium")  # fmt: skip
    rfq, r = f.p4_proposal(check, co)
    if not rfq or r is None:
        return rfq
    got = sorted(str(s["supplier_code"]) for s in co.invited(rfq))
    check(got == sorted(invitees) and int(co.rfq(rfq).get("min_quotes") or 0) == mq,
          f"P4 invited {got}, min_quotes {co.rfq(rfq).get('min_quotes')}; oracle {sorted(invitees)}, {mq}")  # fmt: skip
    d = f.check_decision(check, co, rfq, want, rule, approver, "P4")
    approved_now = d.get("status") == "approved"
    m.case("auto_band_approves", f"P4: {want['total']} in {auto_rule}", (d.get("status"), d.get("rule_code")),
           ("waiting", rule), ("approved", auto_rule))  # fmt: skip
    check(not approved_now and d.get("status") == "waiting", f"P4: the decision is {d.get('status')} at once; the "
          f"automatic band {auto_rule} never approves an award")  # fmt: skip
    _decide_refused(check, co, m, rfq, [buyer, co.login("director")], "P4")
    r = co.decide(pm, rfq, "approve", note="eval")
    check(r.status_code == 200 and co.decision(rfq).get("status") == "approved",
          f"P4: the purchase manager's approval answered {r.status_code} {r.text[:200]}")  # fmt: skip
    truth.append({"scenario": "P4", "line": "1", "item": f.SEAL, "qty": 1000, "supplier": "SUP-002", "net_price": "84.00",
                  **{x: want[x] for x in ("taxable", "cgst", "sgst", "igst")}, "total": {want["total"]}, "rule": rule,
                  "approver": approver})  # fmt: skip
    out.metrics["P4 (value, band, rule, approver)"] = (
        f"{rfq}: {want['total']}, {auto_rule} auto -> {rule}, {approver}"
    )
    return rfq
