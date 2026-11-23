"""E121: an RFQ that closes short of quotes opens one case for its buyer, and nothing else happens by itself (task
12.6, AT-10), written without the implementation.

Independence: written from the block 6 spec (sections 5.3, 8.3 C1-C3, 9, 10.2) and the dataset only. Who owns the case
(the RFQ's buyer, U03) and who it escalates to (the first active Purchase Manager by designation, U08) come from
`user.csv`; the closing date and the next working days from the eval's own calendar; `min_quotes` 3 from the plan's D37
bands (RM-08 500 KGS at Rs 920.00 standard = Rs 4,60,000, above Rs 2,00,000).

Given a fresh Apex reset on Mon 23 Nov 2026, two RFQs R (C1) and R' (C2) as spec 8.3 (RM-08 500 KGS at PLT-CKN, three
invitees, confirmed): by the closing day D only SUP-005 quotes on R; all three quote on R'.
- C1: the daily job on the next working day after D opens one task `rfq_no_response` for U03, chain [U08], title
  "<rfq> closed on <D> with 1 of 3 quotes wanted", payload answered [SUP-005], silent [SUP-001, SUP-003], counting 1,
  min_quotes 3 and the next step; audit `rfq.no_response`; no mail to any supplier, the closing date unchanged, no
  award. The job run again that day and on the next working day opens nothing more. An extension completes the task.
- C2: R' gets no task.
- C3: on a fresh Brightwave reset, the employee asks for HS-01 60 NOS at OFF-BLR (estimate Rs 1,44,000.00), approved,
  sourced; SUP-002 and SUP-001 invited; nobody quotes; the daily job on the next working day after its D opens a task
  "... with no quotes"; Apex's people see no such task and Brightwave's none of Apex's.
Mutations: a task every day; an automatic extension or supplier mail.
"""

from datetime import date
from typing import Any

import block1 as b
import block4 as k
import block6 as s
import env
import lib
from lib import Outcome

ID = "E121"
TITLE = "An RFQ that closes short of quotes opens one case for its buyer, and nothing else happens by itself"
REQUIREMENT = (
    "Task 12.6, AT-10 (block 6 spec 5.3): at closing, counting quotes below min_quotes open one rfq_no_response task "
    "(owner the RFQ's buyer, chain the purchase manager, payload answered / silent / counting / min / next), once per "
    "closing; no supplier mail, no extension, no award; completed on extension; enough quotes, no task; Brightwave's "
    "'no quotes' case; tenant check"
)
PREFIX = "e121"


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
        m = k.Mutations()
        oracle = s.Oracle6(apex)
        _, r1 = apex.r_rfq(check, "C1")
        _, r2 = apex.r_rfq(check, "C2")
        if r1 and r2:
            _apex(out, check, apex, oracle, m, r1, r2)
        s.pin(s.TODAY)
        bw_rfq = _brightwave(out, check, bw)
        m.report(check, out, ["task_every_day", "automatic_extension_or_mail"])
        s.f.tenant_check(check, apex, bw, [r1, r2], [], start)
        s.tenant_tasks(check, apex, bw, [r1, r2])
        s.tenant_tasks(check, bw, apex, [bw_rfq])
        apex_view = apex.call(apex.login("buyer"), "GET", "/api/exceptions").text
        check(not bw_rfq or bw_rfq in (r1, r2) or bw_rfq not in apex_view, f"tenant check: Apex's exceptions screen "
              f"names Brightwave's {bw_rfq}")  # fmt: skip
    finally:
        mail.send_email = saved  # type: ignore[assignment]
    out.metrics.update({"checks": check.done, "failed": len(check.failed)})
    out.notes += [
        (
            "The daily job is run the way the worker claims it, at 09:30 India time on the pinned business day (spec 5.3: "
            "the rfq_reminders job also runs the closing check)."
        ),
        "Spec gap: the task's payload keys are read by the spec's names; supplier codes as strings or objects.",
    ]
    return out


def _apex(out: Outcome, check: b.Checker, co: s.Co6, oracle: s.Oracle6, m: k.Mutations, r1: str, r2: str) -> None:
    d = co.due(r1)
    if not check(d is not None and d == co.due(r2), f"C1/C2: closing dates {d} and {co.due(r2)}; both made and sent on "
                 "23 Nov"):  # fmt: skip
        return
    with s.pinned(d):
        for sup, price in (("SUP-005", "960.00"),):
            r = co.quote6(r1, sup, price)
            check(r.status_code == 200, f"C1: {sup}'s quote on {r1} on {d}: {r.status_code} {r.text[:200]}")
        for sup, price in (("SUP-005", "960.00"), ("SUP-003", "975.00"), ("SUP-001", "990.00")):
            r = co.quote6(r2, sup, price)
            check(r.status_code == 200, f"C2: {sup}'s quote on {r2} on {d}: {r.status_code} {r.text[:200]}")
        co.work()
    day1 = oracle.next_working(d)
    since = co.mark()
    audit0 = co.last_audit_id()
    errors = co.daily(day1)
    check(not any(errors), f"C1: the daily job on {day1} failed: {errors}", "medium")
    tasks = co.tasks_of("rfq_no_response", r1)
    if not check(len(tasks) == 1, f"C1: {len(tasks)} rfq_no_response task(s) for {r1} after the daily job on {day1}; "
                 "the spec says one"):  # fmt: skip
        out.metrics["C1 task"] = None
    t = tasks[0] if tasks else {}
    p = t.get("payload") or {}
    want_title = f"{r1} closed on {s.long_date(d)} with 1 of 3 quotes wanted"
    if t:
        check(s.person_is(co, t.get("assignee"), "U03"), f"C1: the task is for {co.code_of(t.get('assignee'))}; the spec "
              "says the RFQ's buyer U03")  # fmt: skip
        check(s.chain_codes(co, t.get("escalation_chain")) == ["U08"], f"C1: the escalation chain "
              f"{s.chain_codes(co, t.get('escalation_chain'))}; the spec says [U08] (the purchase manager)")  # fmt: skip
        check(s.norm(want_title) in s.norm(t.get("title")), f"C1: the title {t.get('title')!r}; the spec says "
              f"{want_title!r}", "medium")  # fmt: skip
        check(s.codes(p.get("answered")) == ["SUP-005"] and s.codes(p.get("silent")) == ["SUP-001", "SUP-003"]
              and s.codes(p.get("declined")) == [], f"C1: payload answered {p.get('answered')}, silent "
              f"{p.get('silent')}, declined {p.get('declined')}; expected [SUP-005], [SUP-001, SUP-003], []")  # fmt: skip
        check(str(p.get("counting")) == "1" and str(p.get("min_quotes")) == "3" and str(p.get("rfq_no")) == r1
              and str(p.get("due_date", ""))[:10] == d.isoformat(), f"C1: payload {str(p)[:300]}; expected rfq_no "
              f"{r1}, due_date {d}, counting 1, min_quotes 3", "medium")  # fmt: skip
        check("extend the closing date" in s.norm(p.get("next")), f"C1: payload next {p.get('next')!r}", "low")
        check(float(t.get("sla_hours") or 0) == 18, f"C1: the task's SLA {t.get('sla_hours')} hours; the spec says 18",
              "low")  # fmt: skip
    check(any(a.get("entity_id") == r1 for a in co.since("rfq.no_response", audit0)), f"C1: no rfq.no_response audit "
          f"row for {r1}", "medium")  # fmt: skip
    mailed = co.supplier_mails(since)
    unchanged = co.due(r1) == d and not co.awards(r1) and co.rfq(r1).get("status") == "sent"
    m.case("automatic_extension_or_mail", "C1: after the closing job", (len(mailed), unchanged), (0, True), (3, False))
    check(not mailed, f"C1: the closing job mailed suppliers: {[x.get('subject') for x in mailed[:3]]} (AT-10: nothing "
          "automatic beyond reminders)")  # fmt: skip
    check(unchanged, f"C1: after the closing job {r1} closes on {co.due(r1)}, status {co.rfq(r1).get('status')}, "
          f"{len(co.awards(r1))} award(s); expected {d}, sent, none")  # fmt: skip
    check(not co.tasks_of("rfq_no_response", r2), f"C2: {r2} has all three quotes but got a no-response task")
    # once per closing
    co.daily(day1)
    day2 = oracle.next_working(day1)
    co.daily(day2)
    n = len(co.tasks_of("rfq_no_response", r1))
    m.case("task_every_day", "C1: the job run again and on the next working day", n, 1, 3)
    check(n == 1, f"C1: {n} rfq_no_response tasks for {r1} after the job ran again on {day1} and on {day2}; the spec "
          "says once per closing")  # fmt: skip
    check(not co.supplier_mails(since), "C1: a later daily run mailed a supplier after the closing date")
    # an extension completes the task
    new = oracle.next_working(day2, 2)
    r = co.extend(r1, new, "eval: one more round")
    check(r.status_code == 200, f"C1: extending {r1} to {new} on {day2} answered {r.status_code} {r.text[:200]}")
    after = [x for x in co.tasks_of("rfq_no_response", r1) if x["id"] == t.get("id")]
    check(bool(after) and after[0].get("status") != "open" and after[0].get("completed_at") is not None,
          f"C1: after the extension the task is {after[0].get('status') if after else None}; the spec says the system "
          "completes it")  # fmt: skip
    s.pin(s.TODAY)
    out.metrics["C1"] = {"rfq": r1, "D": str(d), "job day": str(day1), "title": t.get("title")}


def _brightwave(out: Outcome, check: b.Checker, co: s.Co6) -> str:
    oracle = s.Oracle6(co)
    loc = "OFF-BLR"
    req = co.ask(check, "HS-01", 60, date(2026, 12, 21), "C3", by=co.login("employee"), loc=loc)
    rfq = co.rfq_of(req) if req else ""
    if not check(bool(rfq), f"C3: Brightwave's request {req} made no quotation request"):
        return ""
    invited = sorted(x["supplier_code"] for x in co.invited(rfq))
    check(invited == ["SUP-001", "SUP-002"], f"C3: {rfq} invites {invited}; the spec says SUP-002 and SUP-001", "medium")
    est = s.D(co.one("select total_value from request where no = :n", n=req))
    check(est == s.D("144000.00"), f"C3: the request's estimate {est}; the spec says 1,44,000.00", "low")
    check(co.confirm(co.login("buyer"), rfq), f"C3: Brightwave's buyer could not confirm {rfq}")
    d = co.due(rfq)
    if not check(d is not None, f"C3: {rfq} has no closing date"):
        return rfq
    day1 = oracle.next_working(d)
    co.daily(day1)
    tasks = co.tasks_of("rfq_no_response", rfq)
    want = f"{rfq} closed on {s.long_date(d)} with no quotes"
    check(len(tasks) == 1 and s.norm(want) in s.norm(tasks[0].get("title")), f"C3: Brightwave's tasks for {rfq}: "
          f"{[x.get('title') for x in tasks]}; the spec says one, '{want}'")  # fmt: skip
    if tasks:
        p = tasks[0]["payload"]
        check(s.codes(p.get("silent")) == ["SUP-001", "SUP-002"] and str(p.get("counting")) == "0", f"C3: payload "
              f"{str(p)[:300]}", "medium")  # fmt: skip
    s.pin(s.TODAY)
    out.metrics["C3"] = {"rfq": rfq, "D": str(d), "job day": str(day1), "tasks": [x.get("title") for x in tasks]}
    return rfq
