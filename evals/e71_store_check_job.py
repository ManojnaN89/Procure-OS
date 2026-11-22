"""E71: the store check as a job and after events (task 6.5), written without the implementation.

Independence: written from the block 2 spec (sections 6, 9.3 E1-E2, 11) and its decisions of 22 Nov (OP-14: the 05:30
job creates the shortfall requests and submits them only when `store_check_submit` is on, default off; event checks
follow the same setting; the button keeps submitting). The oracle (`block2.Stock`) is block 1's store check with
block 2's rules; an event re-runs it for the item-locations of the lines pegged to the document; automatic checks never
buy again for a line whose latest purchase request was rejected (OP-16), the button does.

Scenarios:
- Job from a fresh reset (no button): the `store_check` handler is registered; with `auto_store_check` false (set by
  two administrators) the job sets nothing aside and buys nothing but queues the next run; set true, the job at 05:30 on
  6 Nov gives block 1's A0 (Rs 72,930 / 2,09,700 / 3,69,490) as requests left as drafts, audits `store_check.run`, queues
  the next run at 05:30 India time the next day; a second run changes nothing (one more `store_check.run` row only).
- E1 (on T1: MO-1's castings late): 30 arrive on 8 Dec, the buyer closes the balance: MO-1 30 set aside and a new
  request for MO-1 RM-01 20 (Rs 13,000) made by the event, no button.
- PO cancel (on T1): the buyer cancels the castings order: the event buys MO-1's 50 again (Rs 32,500).
- T1's late promise: the acknowledgement alone (no button, no job) flags MO-1's castings at risk and opens the case.
- E2 (A0): the director rejects MO-2's request: no new request from the event or the job; its lines show
  `last_rejected`; Run store check raises it again (Rs 2,09,700).
Each ends with a tenant check. Mutations: no store check after a short-close; the job re-raising a rejected request.
"""

from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any

import block1 as b
import block2 as s2
import env
import lib
from lib import Outcome

ID = "E71"
TITLE = "The store check runs every morning and after short-close, cancel, promise and rejection, never re-raising a rejection"
REQUIREMENT = (
    "Task 6.5 (block 2 spec 6, 9.3 E1-E2, decision OP-14 of 22 Nov): job store_check daily 05:30 IST when "
    "auto_store_check, drafts unless store_check_submit, always queues the next run, audited, idempotent; events "
    "(close-balance, cancel, acknowledgement, rejection) re-run evaluation and a scoped store check; a rejected "
    "shortfall is not re-raised automatically but is by the button; tenant isolation"
)
PREFIX = "e71"
CKN = "PLT-CKN"
ACK_DAY = date(2026, 11, 23)


def run(ctx: Any) -> Outcome:
    env.forget_tenants(PREFIX)
    try:
        return _run(ctx)
    finally:
        env.forget_tenants(PREFIX)


def _run(ctx: Any) -> Outcome:
    from fastapi.testclient import TestClient

    from p2p.main import app

    out = Outcome(True)
    check = b.Checker(out)
    lib.capture_mail()
    client = TestClient(app)

    apex = s2.Co("apex", PREFIX, client)
    bw = s2.Co("brightwave", PREFIX, client)
    bw_before = bw.snapshot2()
    _job(check, apex)
    s2.tenant_check(check, apex, bw, bw_before)
    apex.reset()
    _e1(out, check, apex)
    apex.reset()
    _cancel(check, apex)
    apex.reset()
    _e2(out, check, apex)
    s2.tenant_check(check, apex, bw, bw_before)

    out.metrics.update({"checks": check.done, "failed": len(check.failed)})
    out.notes += [
        "Spec conflict settled by the 22 Nov decision: section 9.3 E1 says the event's request goes 'to the plant head'; "
        "with `store_check_submit` off by default it stays a draft. The eval expects a draft and checks the approver "
        "only if the request was submitted.",
        "Spec ambiguity: 'late promise re-reserves' is read as the acknowledgement event running the evaluation and the "
        "scoped store check by itself (at risk and the case without a button or job); T1 has no free castings to set "
        "aside, so the re-reservation itself is exercised in E69 (T2, BT1).",
        "Spec gap: whether demo reset queues the first store_check job is not said; the eval queues it itself.",
        "Not exercised: `revise`, request withdrawal and the supplier agent's date change as triggers.",
    ]
    return out


def _job(check: b.Checker, co: s2.Co) -> None:
    from p2p.platform import worker

    worker.load_handlers()
    check(s2.JOB in worker.HANDLERS, "the worker has no 'store_check' job (spec 6.1)")
    check(co.setting("auto_store_check") in ("true", None), f"auto_store_check is {co.setting('auto_store_check')}, "
          "the spec's default is true", "medium")  # fmt: skip
    check(co.setting("store_check_submit") in ("false", None), f"store_check_submit is "
          f"{co.setting('store_check_submit')}, the 22 Nov default is off", "medium")  # fmt: skip
    # off: nothing happens, the next run is still queued
    asked, approved = co.policy("auto_store_check", "false")
    check(asked.status_code == 202 and approved is not None and approved.status_code == 200, f"auto_store_check off "
          f"answered {asked.status_code} / {getattr(approved, 'status_code', None)}")  # fmt: skip
    before = co.snapshot2()
    runs = co.audit_count("store_check.run")
    co.run_job(lib.TODAY)
    after = co.snapshot2()
    moved = [k for k in ("lines", "requests", "request_lines", "reserve_audit") if after[k] != before[k]]
    check(not moved, f"job with auto_store_check off changed {moved}")
    queued = [j for j in co.queued_jobs() if j[2] == "queued"]
    check(
        len(queued) == 1,
        f"job with auto_store_check off: {len(queued)} store_check job(s) queued, expected the next run",
    )
    asked, approved = co.policy("auto_store_check", "true")
    check(asked.status_code == 202 and approved is not None and approved.status_code == 200, "auto_store_check on "
          "was not applied")  # fmt: skip
    # on: the job at 05:30 gives A0, as drafts
    before = co.requests_now()
    co.run_job(lib.TODAY)
    co.first_requests = {n for (n,) in co.q("select distinct request_no from material_request_buy")}
    rank = {label: i for i, (label, _, _) in enumerate(co.mr_labels())}
    st = s2.Stock(co.dataset, s2.RIGHT2, rank)
    st.store_check(tag="first", automatic=True)
    s2.base_ok(check, co, st)
    new = co.requests_now() - before
    statuses = {n: co.request_head(n).get("status") for n in new}
    check(bool(new) and all(v == "draft" for v in statuses.values()), f"job: the requests it made are {statuses}, "
          "expected drafts (store_check_submit off)")  # fmt: skip
    check(co.audit_count("store_check.run") >= runs + 1, "job: no store_check.run audit row")
    rows = co.q(
        "select actor_type, after::text from audit_event where action = 'store_check.run' order by id desc limit 1"
    )
    check(bool(rows) and rows[0][0] != "user", f"job: store_check.run by {rows[0][0] if rows else None}, expected the "
          "system", "medium")  # fmt: skip
    nxt = [j for j in co.queued_jobs() if j[2] == "queued"]
    ok = (
        len(nxt) == 1
        and nxt[0][1].astimezone(b.IST).time() == s2.JOB_AT
        and nxt[0][1] > datetime.combine(lib.TODAY, s2.JOB_AT, tzinfo=b.IST)
    )
    check(ok, f"job: next run(s) queued {[(j[1].astimezone(b.IST), j[2]) for j in nxt]}, expected one at 05:30 India "
          "time on a later day")  # fmt: skip
    if nxt:
        check(nxt[0][1].astimezone(b.IST).date() == lib.TODAY + timedelta(days=1), f"job: next run on "
              f"{nxt[0][1].astimezone(b.IST).date()}, expected the next day", "medium")  # fmt: skip
    # a second run changes nothing but its own audit row
    snap = co.snapshot2()
    runs = co.audit_count("store_check.run")
    co.run_job(lib.TODAY + timedelta(days=1))
    again = co.snapshot2()
    moved = [k for k in again if k != "audit" and again[k] != snap[k]]
    check(not moved, f"job: a second run changed {moved}")
    check(co.audit_count("store_check.run") == runs + 1, f"job: a second run wrote "
          f"{co.audit_count('store_check.run') - runs} store_check.run rows, expected one")  # fmt: skip


def _t1(check: b.Checker, co: s2.Co) -> tuple[s2.Stock, str | None, int]:
    """T1 (spec 9.3): A0, MO-1's request approved, bought, the castings order sent, promised for 8 Dec."""
    st, _ = s2.start2(co, check)
    for no in co.request_of("MO-1"):
        co.approve(no)
    co.source()
    st.mark_buyer(co.with_buyer())
    found = co.po_of("MO-1", "RM-01")
    if not check(found is not None, "T1: no castings order after sourcing"):
        return st, None, 0
    po, line = found
    co.advance_po(po)
    tasks = len(co.risks())
    r = co.acknowledge(po, date(2026, 12, 8), ACK_DAY)
    check(r.status_code == 200, f"T1: the acknowledgement answered {r.status_code}: {r.text[:200]}")
    st.today = ACK_DAY
    st.promise("MO-1", "RM-01", date(2026, 12, 8))
    st.store_check({("RM-01", CKN)}, automatic=True)
    # the late promise alone (no button, no job) evaluates: at risk and one case
    got = co.api_lines().get(("MO-1", "RM-01"), {})
    check(b.dec(got.get("at_risk_qty")) == 50, f"T1 promise: MO-1 RM-01 at risk {got.get('at_risk_qty')} right after "
          "the acknowledgement, expected 50 (the event evaluates)")  # fmt: skip
    check(len(co.risks()) == tasks + 1, "T1 promise: the acknowledgement did not open the late-supply case by itself")
    return st, po, line


def _e1(out: Outcome, check: b.Checker, co: s2.Co) -> None:
    s2.scenario_known(check, co, "E1-short-close")
    st, po, line = _t1(check, co)
    if po is None:
        return
    arrive = date(2026, 12, 8)
    r = co.receive(po, line, "30", arrive)
    check(r.status_code == 200, f"E1: receiving 30 answered {r.status_code}: {r.text[:200]}")
    st.today = arrive
    st.arrive("MO-1", "RM-01", Decimal(30))
    quiet = s2.copy_state(st, s2.Wrong(no_event=True))
    before = co.requests_now()
    with b.on_day(arrive):
        r = co.call(co.role("buyer"), "POST", f"/api/purchase-orders/{po}/close-balance",
                    {"reason": "Deccan's furnace is down"})  # fmt: skip
    check(r.status_code == 200, f"E1: close-balance answered {r.status_code}: {r.text[:200]}")
    made = st.drop_order("MO-1", "RM-01")
    quiet.drop_order("MO-1", "RM-01")
    check(made == {("MO-1", "RM-01"): Decimal(20)}, f"E1: the oracle buys {made}, spec MO-1 RM-01 20 (self-check)",
          "medium")  # fmt: skip
    s2.mutation(check, out, "no store check after a short-close", s2.told(st.observe2(), quiet.observe2()))
    new = co.requests_now() - before
    lines = {n: co.request_lines(n) for n in new}
    hit = [n for n, x in lines.items() if x == {"RM-01": Decimal(20)}]
    check(len(hit) == 1 and co.request_head(hit[0]).get("total") == Decimal("13000"), f"E1: the event made {lines}, "
          "expected one request for RM-01 20 worth Rs 13,000")  # fmt: skip
    if hit:
        head = co.request_head(hit[0])
        check(head.get("status") == "draft", f"E1: the event's request is {head.get('status')}, expected a draft "
              "(store_check_submit off)", "medium")  # fmt: skip
        if head.get("status") == "submitted":
            check(co.approver_of(hit[0]) == co.designation("Plant Head"), f"E1: the request waits for "
                  f"{co.approver_of(hit[0])}, expected the plant head")  # fmt: skip
        check(hit[0] in co.mr_requests("MO-1"), f"E1: request {hit[0]} is not linked to MO-1's material request")
    with b.on_day(arrive):
        s2.agree2(check, "E1", st, co, fields=("need", "reserved", "issued", "short"))
        s2.truth_check(check, co, "E1-short-close", [co.observe2()], None, items={"RM-01"})
    s2.truth_requests(check, co, "E1-short-close", new)


def _cancel(check: b.Checker, co: s2.Co) -> None:
    st, po, _ = _t1(check, co)
    if po is None:
        return
    before = co.requests_now()
    with b.on_day(ACK_DAY):
        r = co.call(co.role("buyer"), "POST", f"/api/purchase-orders/{po}/cancel",
                    {"reason": "Supplier cannot cast this month", "reorder": False})  # fmt: skip
    check(r.status_code == 200, f"PO cancel: answered {r.status_code}: {r.text[:200]}")
    made = st.drop_order("MO-1", "RM-01")
    check(made == {("MO-1", "RM-01"): Decimal(50)}, f"PO cancel: the oracle buys {made} (self-check)", "medium")
    new = co.requests_now() - before
    lines = {n: co.request_lines(n) for n in new}
    rm01 = sum((x.get("RM-01", b.Z) for x in lines.values()), b.Z)
    check(rm01 == 50, f"PO cancel: the event bought {lines}, expected MO-1's 50 castings again (Rs 32,500)")
    with b.on_day(ACK_DAY):
        s2.agree2(check, "PO cancel", st, co, fields=("need", "reserved", "issued", "short", "at_risk"),
                  only=[("MO-1", "RM-01")])  # fmt: skip
    open_ = [c for c in co.risks() if c["po_no"] == po and c["closed_at"] is None]
    check(not open_, f"PO cancel: the late-supply case on the cancelled order is still open: {open_}", "medium")


def _e2(out: Outcome, check: b.Checker, co: s2.Co) -> None:
    s2.scenario_known(check, co, "E2-rejected")
    st, _ = s2.start2(co, check)
    mo2 = co.request_of("MO-2")
    director = co.designation("Director")
    before = co.requests_now()
    for no in mo2:
        r = co.call(director, "POST", f"/api/requisitions/{no}/reject", {"reason": "Demand plan to be reviewed"})
        check(r.status_code == 200, f"E2: the director rejecting {no} answered {r.status_code}: {r.text[:200]}")
    st.reject("MO-2")
    rebuy = s2.copy_state(st, s2.Wrong(rebuy_rejected=True))
    co.run_job(lib.TODAY)
    st.store_check(None, automatic=True)
    rebuy.store_check(None, automatic=True)
    s2.mutation(check, out, "the job re-raising a rejected request", s2.told(st.observe2(), rebuy.observe2()))
    new = co.requests_now() - before
    mo2_new = [n for n in new if n in co.mr_requests("MO-2")]
    check(not mo2_new, f"E2: the rejection event or the job bought MO-2 again: {mo2_new}")
    lines = {k: v for k, v in co.api_lines().items() if k[0] == "MO-2"}
    # spec 4.5: last_rejected is the request number or null; only a line some request bought for can have one (MO-2's
    # terminal boxes and paint were covered by stock and never requested, so theirs is null)
    flagged = {k[1]: v.get("last_rejected") for k, v in lines.items() if v.get("requests")}
    unbought = {k[1]: v.get("last_rejected") for k, v in lines.items() if not v.get("requests")}
    check(bool(flagged) and all(v in mo2 for v in flagged.values()), f"E2: MO-2's lines last_rejected {flagged}, "
          f"expected {mo2}")  # fmt: skip
    check(all(v is None for v in unbought.values()), f"E2: MO-2's lines never requested show last_rejected {unbought}")
    s2.agree2(check, "E2 after the job", st, co, fields=("need", "reserved", "issued", "short"))
    # the button: a person chose to buy again
    before = co.requests_now()
    r = co.store_check()
    check(r.status_code == 200, f"E2: Run store check answered {r.status_code}")
    made = st.store_check(None)
    new = [n for n in co.requests_now() - before if n in co.mr_requests("MO-2")]
    got = sum((co.request_head(n).get("total", b.Z) for n in new), b.Z)
    want = b.money(sum((q * st.price(i) for (m, i), q in made.items() if m == "MO-2"), b.Z))
    check(want == Decimal("209700"), f"E2: the oracle re-raises Rs {want}, spec 2,09,700 (self-check)", "medium")
    check(len(new) == 1 and b.money(got) == want, f"E2: the button made {new} worth {got}, expected one request worth "
          f"{want}")  # fmt: skip
    s2.truth_check(check, co, "E2-rejected", [co.observe2()], None)
