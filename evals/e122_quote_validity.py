"""E122: an expired quote is never selected and never ordered (task 12.6, DF-33, Rev s12 #16), written without the
implementation.

Independence: written from the block 6 spec (sections 5.2, 8.3 V1-V2, 9, 10.2) and the dataset only. The order's
amounts come from `block5.Oracle5.award_value` (taxable = qty x price half up to paise; CGST + SGST at half of the
item's 18% each inside Maharashtra: SUP-003 and PLT-CKN are both state 27) and its approver from the `po` rows of
`approval_rule.csv` with the buyer never approving; the spec's own figures are a self-check and
`truth/truth_quotes_selection.csv` is compared.

Given a fresh Apex reset on Mon 23 Nov 2026:
- V1: R (RM-08 500 KGS at PLT-CKN, three invitees, confirmed); quotes SUP-005 960.00 valid until 25 Nov 2026, SUP-003
  975.00, SUP-001 990.00 (valid to 31 Dec 2026); the buyer selects SUP-005 on 23 Nov and the worker does not run; on
  pinned 26 Nov the worker runs: no order line from SUP-005; the award lapsed with the reason "<supplier>'s quote
  <quote no> expired on 25 Nov 2026 before the order was made"; the RFQ back to `sent`; audit `award.lapsed`; a task
  `quote_expired` for U03; selecting SUP-005 again is 409 even with a reason; SUP-003 with a reason makes an order of
  taxable 4,87,500.00, CGST 43,875.00, SGST 43,875.00, total 5,75,250.00, band PO-03 (the director).
- V2: a second R; SUP-005's quote valid until 20 Nov 2026 entered on 23 Nov; selecting it with a reason is 409
  "... expired on 20 Nov 2026 ...", nothing awarded.
Ends with a tenant check.
Mutations: an order made from an expired quote; a reason overriding the expiry.
"""

from datetime import date
from typing import Any

import block1 as b
import block4 as k
import block6 as s
import env
import lib
from lib import Outcome

ID = "E122"
TITLE = "An expired quote is never selected and never ordered; its award lapses back to the buyer"
REQUIREMENT = (
    "DF-33, Rev s12 #16 (block 6 spec 5.2): selecting a quote past valid_until refused 409 with or without a reason; an "
    "award whose quote expired before the order is not ordered, lapses with its reason, the RFQ reopens and the buyer "
    "gets a quote_expired task; another valid quote selected with a reason orders at the truth figures; tenant check"
)
PREFIX = "e122"
DAY = date(2026, 11, 26)


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
        req1, r1 = apex.r_rfq(check, "V1")
        _, r2 = apex.r_rfq(check, "V2")
        if r2:
            _v2(check, apex, m, r2)
        if r1:
            _v1(out, check, apex, oracle, m, req1, r1)
        m.report(check, out, ["po_from_expired", "reason_overrides_expiry"])
        s.f.tenant_check(check, apex, bw, [r1, r2], [], start)
        s.tenant_tasks(check, apex, bw, [r1, r2])
    finally:
        mail.send_email = saved  # type: ignore[assignment]
    out.metrics.update({"checks": check.done, "failed": len(check.failed)})
    out.notes += [
        (
            "V1's 'the worker does not run' is kept by selecting through the API without running the worker; the "
            "orders are made when the worker runs on pinned 26 Nov. If an order exists before that, the award made it "
            "itself and the eval says so."
        ),
        "Spec gap: the lapse words are checked as medium; the lapse itself (lapsed_at, no order line) as high.",
    ]
    return out


def _v2(check: b.Checker, co: s.Co6, m: k.Mutations, rfq: str) -> None:
    r = co.quote6(rfq, "SUP-005", "960.00", valid_until="2026-11-20")
    check(r.status_code == 200, f"V2: SUP-005's quote valid until 20 Nov 2026 on {rfq}: {r.status_code} {r.text[:200]}",
          "medium")  # fmt: skip
    r = co.award6(rfq, "SUP-005", reason="eval: the only quote, the price is fine")
    m.case("reason_overrides_expiry", "V2: expired quote selected with a reason", r.status_code, 409, 200)
    check(r.status_code == 409, f"V2: selecting SUP-005's quote that expired on 20 Nov 2026, with a reason, answered "
          f"{r.status_code} {r.text[:200]}; the spec says 409")  # fmt: skip
    check("expired on 20 nov 2026" in s.norm(r.text), f"V2: the refusal's words {r.text[:200]}; the spec says "
          "\"<supplier>'s quote expired on 20 Nov 2026: ask for a renewed price before selecting it\"", "medium")  # fmt: skip
    live = [a for a in co.awards(rfq) if not a.get("lapsed_at")]
    check(not live, f"V2: {rfq} holds {len(live)} award row(s) after the refused selection")


def _v1(out: Outcome, check: b.Checker, co: s.Co6, oracle: s.Oracle6, m: k.Mutations, req: str, rfq: str) -> None:
    for sup, price, valid in (("SUP-005", "960.00", "2026-11-25"), ("SUP-003", "975.00", s.VALID_UNTIL),
                              ("SUP-001", "990.00", s.VALID_UNTIL)):  # fmt: skip
        r = co.quote6(rfq, sup, price, valid_until=valid)
        check(r.status_code == 200, f"V1: {sup}'s quote {price} on {rfq}: {r.status_code} {r.text[:200]}")
    r = co.award6(rfq, "SUP-005", work=False)
    if not check(r.status_code == 200, f"V1: selecting SUP-005 (cheapest, valid until 25 Nov) on 23 Nov answered "
                 f"{r.status_code} {r.text[:200]}"):  # fmt: skip
        return
    early = [x for x in co.lines_ordered(req) if x["supplier_code"] == "SUP-005"]
    check(not early, f"V1: the selection made order {[x['po_no'] for x in early]} before the worker ran; the scenario "
          "needs the order made later (precondition)", "low")  # fmt: skip
    quote_no = (co.quotes(rfq, "SUP-005") or [{}])[-1].get("no")
    audit0 = co.last_audit_id()
    with s.pinned(DAY):
        co.work()
        made = [x for x in co.lines_ordered(req) if x["supplier_code"] == "SUP-005" and x["po_no"] not in
                {e["po_no"] for e in early}]  # fmt: skip
        m.case("po_from_expired", "V1: the worker on 26 Nov", bool(made), False, True)
        check(not made, f"V1: order(s) {[x['po_no'] for x in made]} were made on 26 Nov from SUP-005's quote that expired "
              "on 25 Nov 2026 (DF-33)")  # fmt: skip
        aw = [a for a in co.awards(rfq) if a["supplier_code"] == "SUP-005"]
        lapsed = aw[-1] if aw else {}
        check(lapsed.get("lapsed_at") is not None, f"V1: SUP-005's award lapsed_at {lapsed.get('lapsed_at')}; the spec "
              "says it lapses when the order is not made")  # fmt: skip
        name = oracle.supplier_name("SUP-005")
        want = f"{name}'s quote {quote_no} expired on 25 Nov 2026 before the order was made"
        check(s.norm(want) in s.norm(lapsed.get("lapse_reason")), f"V1: the lapse reason {lapsed.get('lapse_reason')!r}; "
              f"the spec says {want!r}", "medium")  # fmt: skip
        check(co.rfq(rfq).get("status") == "sent", f"V1: {rfq} is {co.rfq(rfq).get('status')} after the lapse; the spec "
              "says back to sent")  # fmt: skip
        check(bool(co.since("award.lapsed", audit0)), "V1: no award.lapsed audit row", "medium")
        tasks = co.tasks_of("quote_expired", rfq)
        title = f"{rfq} line 1: {name}'s quote expired before the order was made"
        check(len(tasks) == 1 and s.person_is(co, tasks[0].get("assignee"), "U03"), f"V1: quote_expired tasks "
              f"{[(x.get('title'), co.code_of(x.get('assignee'))) for x in tasks]}; the spec says one for U03")  # fmt: skip
        check(bool(tasks) and s.norm(title) in s.norm(tasks[0].get("title")), f"V1: the task's title "
              f"{tasks[0].get('title') if tasks else None!r}; the spec says {title!r}", "medium")  # fmt: skip
        check(bool(tasks) and float(tasks[0].get("sla_hours") or 0) == 18, "V1: the task's SLA is not 18 working hours",
              "low")  # fmt: skip
        r = co.award6(rfq, "SUP-005", reason="eval: the supplier said the price still holds")
        m.case("reason_overrides_expiry", "V1: reselecting the expired quote with a reason", r.status_code, 409, 200)
        check(r.status_code == 409 and "expired on 25 nov 2026" in s.norm(r.text), f"V1: selecting SUP-005 again with a "
              f"reason answered {r.status_code} {r.text[:200]}; the spec says 409 (expired on 25 Nov 2026)")  # fmt: skip
        r = co.award6(rfq, "SUP-003", reason="eval: SUP-005's price expired, next valid quote")
        check(r.status_code == 200, f"V1: selecting SUP-003 with a reason answered {r.status_code} {r.text[:200]}")
        waiting = r.json().get("proposal_id") if r.status_code == 200 else None
        if waiting:  # two counting quotes of three on 4,87,500: the purchase manager agrees first (spec 2, rfq.award_short)
            agreed = co.call(co.login("purchasemanager"), "POST", f"/api/admin/proposals/{waiting}/approve",
                             {"note": "eval: agreed"})  # fmt: skip
            check(agreed.status_code == 200, f"V1: the purchase manager agreeing answered {agreed.status_code}")
        co.work()
        orders = sorted({x["po_no"] for x in co.lines_ordered(req) if x["supplier_code"] == "SUP-003"})
        if not check(len(orders) == 1, f"V1: SUP-003's selection made orders {orders}; expected one"):
            return
        po = co.po_row(orders[0])
        want_v = oracle.award_value([(s.WIRE, s.R_QTY, "975.00")], "SUP-003", s.LOC)
        rule, approver = oracle.approver(want_v["total"], frozenset({"U03"}))
        s.self_check(check, "V1", {**{x: str(v) for x, v in want_v.items()}, "rule": rule, "approver": approver})
        s.compare_truth(check, co, "V1", {**want_v, "rule": rule, "approver": approver})
        got = {x: s.D(po.get(x)) for x in ("taxable", "cgst", "sgst", "igst", "total")}
        check(got == want_v, f"V1: order {orders[0]} amounts {got}; oracle {want_v}")
        check(str(po.get("rule_code") or "") == rule, f"V1: order {orders[0]} band {po.get('rule_code')}; oracle {rule} "
              f"({approver})", "medium")  # fmt: skip
        r = co.approve_po(co.login("buyer"), orders[0])
        check(r.status_code in s.REFUSED, f"V1: the buyer approving {orders[0]} answered {r.status_code}", "medium")
        out.metrics["V1"] = {"order": orders[0], "amounts": {x: str(v) for x, v in got.items()}, "band": po.get("rule_code"),
                             "oracle": {x: str(v) for x, v in want_v.items()}}  # fmt: skip
