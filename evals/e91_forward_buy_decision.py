"""E91: the buyer and the purchase manager decide a forward buy (task 13.5, FB6), written without the implementation.

Independence: written from the block 3 spec (sections 3.4 FB6, 5.4, 5.6, 9, 10), the plan and the dataset only. The
oracle: the recommendation of FB1 (`block3.evaluate`), the approver from `master/approval_rule.csv` (the `forward_buy`
document's first step, a designation found in `user.csv`), the new request's approval step from the same file's
`request` bands, its budget check from `block3.Budgets` with A0's request values counted as held.

Scenario (Apex Pumps, 23 November 2026), two resets:
- FB1, then refusals (no reason, a quantity above the recommendation, 0, not whole packs: 422; approve before accept:
  409); U03 accepts 746 kg ("Copper dip; Q4 plan firm"): a `forward_buy.approve` proposal of Rs 6,86,320.00 for the
  purchase manager (U08) and a task for him; accepting again 409; U03 approving 403, the director (not named) 403; U08
  approves: a new planning request, one line of 746 kg of RM-08, need-by 10 January 2027, stock part 746, estimate
  920.00, Rs 6,86,320.00, submitted, waiting for the director; its budget check against the oracle (1410: committed
  6,40,870, requested 6,86,320, 11.44%); the anchor request unchanged; the request page links both ways; audit rows.
- Reset, FB1, accepted, then a new benchmark of 800.0000 entered and verified: approving -> 409 with the new record's
  id; the new record rejected with a reason; a run of the engine offers nothing again until a newer benchmark.
Ends with a tenant check against Brightwave.

Mutations: the buyer approves their own acceptance; the extra quantity added to the submitted anchor request.
"""

from datetime import date
from decimal import Decimal
from typing import Any

import block1 as b
import block3 as b3
import env
import lib
from block1 import D, Z, js
from lib import Outcome

ID = "E91"
TITLE = "A forward buy is accepted by the buyer and approved by the person the rule names, as a new linked request"
REQUIREMENT = (
    "Task 13.5 (block 3 spec 3.4 FB6, 5.4, 5.6): accept (po.create) with a reason and whole packs up to the "
    "recommendation; a forward_buy.approve proposal and task for the forward_buy rule's person, never the buyer; "
    "revalidation 409 with new_id; approval writes a planning request (stock part all, anchor's need-by, department, "
    "estimate) through the normal chain and budget check; the anchor never grows; reject with a reason, not offered "
    "again until a newer benchmark; audit; tenant isolation"
)
PREFIX = "e91"
WIRE = "RM-08"
REASON = "Copper dip; Q4 plan firm"


def run(ctx: Any) -> Outcome:
    env.forget_tenants(PREFIX)
    try:
        return _run(ctx)
    finally:
        env.forget_tenants(PREFIX)


def approver_of(co: b3.Co3, document: str, value: Decimal, on: date) -> str | None:
    """The first person of the rule's chain for a document and value, from `approval_rule.csv` and `user.csv`."""
    words = {"purchase_manager": "Purchase Manager", "director": "Director", "plant_head": "Plant Head",
             "cfo": "Chief Financial"}  # fmt: skip
    for r in co.rows("master", "approval_rule"):
        if r["document"] != document or not (D(r["min_value"]) <= value <= D(r["max_value"])):
            continue
        if r.get("effective_from", "") > on.isoformat() or (r.get("effective_to") and r["effective_to"] < on.isoformat()):
            continue
        if r.get("spend_type", "ANY") not in ("ANY", ""):
            continue
        first = r["approver_chain"].split(">")[0].split(",")[0].strip().lstrip("+")
        return co.designation(words.get(first, first.replace("_", " ").title())) if first != "auto" else None
    return None


def _run(ctx: Any) -> Outcome:  # one story, told in order
    from fastapi.testclient import TestClient
    from p2p.main import app

    out = Outcome(True)
    check = b.Checker(out)
    lib.capture_mail()
    client = TestClient(app)
    apex = b3.Co3("apex", PREFIX, client)
    bw = b3.Co3("brightwave", PREFIX, client)
    st = b3.start3(apex, check)
    state = b3.FbState(apex, st)
    buyer, director, plant_head = apex.buyer(), apex.director(), apex.designation("Plant Head")
    named = approver_of(apex, "forward_buy", Decimal(686320), b3.AS_OF)
    check(named == apex.designation("Purchase Manager"), f"approval_rule.csv names {named} for forward_buy, the spec "
          "FWD-01 purchase_manager (3.2 step 4)", "medium")  # fmt: skip
    named = named or apex.pm()
    mrp = b3.request_no(apex, b3.MRP)
    anchor_before = (apex.request_lines(mrp), apex.request_head(mrp).get("total"))
    requests_before = apex.requests_now()

    state.add_bench(b3.scenario_bench(apex, check, "FB1"))
    want = state.want(WIRE, b3.CKN)
    rec = b3.check_record(check, "FB6", apex, state, WIRE, b3.CKN, request_label=b3.MRP)
    if not check(rec is not None, "FB6: no record to decide"):
        return out
    assert rec is not None
    fid = rec["_id"]
    with b3.day(b3.AS_OF):
        for body, why in (({"qty": str(want.qty)}, "no reason"),
                          ({"qty": str(want.qty + 1), "reason": REASON}, "a quantity above the recommendation"),
                          ({"qty": "0", "reason": REASON}, "quantity 0"),
                          ({"qty": str(want.qty - Decimal("0.5")), "reason": REASON}, "half a kilogram (not whole packs)")):  # fmt: skip
            r = apex.fb_act(fid, "accept", buyer, body)
            check(r.status_code == 422, f"FB6: accepting with {why} answered {r.status_code}, expected 422")
        r = apex.fb_act(fid, "approve", named, {})
        check(r.status_code == 409, f"FB6: approving before acceptance answered {r.status_code}, expected 409")
        r = apex.fb_act(fid, "accept", director, {"qty": str(want.qty), "reason": REASON})
        check(r.status_code == 403, f"FB6: the director (no po.create) accepting answered {r.status_code}", "medium")
        tasks0 = {t[0] for t in apex.q("select id from task")}
        props0 = {p[0] for p in apex.q("select id from proposal")}
        r = apex.fb_act(fid, "accept", buyer, {"qty": str(want.qty), "reason": REASON})
        body = js(r)
        check(r.status_code == 200 and body.get("status") == "accepted" and body.get("proposal_id"),
              f"FB6: accepting answered {r.status_code} {r.text[:300]}")  # fmt: skip
        check(D(body.get("accepted_qty")) == want.qty and D(body.get("accepted_net_saving")) == want.net_saving,
              f"FB6: accepted {body.get('accepted_qty')} / {body.get('accepted_net_saving')}, expected {want.qty} / "
              f"{want.net_saving}")  # fmt: skip
        check(apex.code_of(body.get("approver")) == named or body.get("approver") == named,
              f"FB6: approver {body.get('approver')}, expected {named}")  # fmt: skip
        pid = body.get("proposal_id")
        prop = apex.q("select action, value, required_permission, status from proposal where id = :i", i=pid) if pid else []
        check(bool(prop) and prop[0][0] == "forward_buy.approve" and D(prop[0][1]) == want.value
              and prop[0][2] == "sourcing.route_approve" and prop[0][3] == "pending",
              f"FB6: proposal {pid} is {prop}; expected forward_buy.approve, {want.value}, sourcing.route_approve, "
              "pending")  # fmt: skip
        check({p[0] for p in apex.q("select id from proposal")} - props0 == ({pid} if pid else set()),
              "FB6: accepting made more than one proposal", "medium")  # fmt: skip
        new_tasks = apex.q("select kind, assignee, due_at, created_at from task where kind = 'forward_buy'")
        mine = [t for t in new_tasks if t[1] == apex.user_id(named)]
        check(len(mine) == 1, f"FB6: forward_buy tasks {[(t[0], t[1]) for t in new_tasks]}, expected one for {named} "
              f"(user {apex.user_id(named)})")  # fmt: skip
        if mine:
            check(b.due_ok(apex.calendar(), mine[0][3], mine[0][2], 1, anchors=[b3.AS_OF]),
                  f"FB6: the task is due {mine[0][2]}, one working day expected", "medium")  # fmt: skip
        check({t[0] for t in apex.q("select id from task")} - tasks0, "FB6: no task was made", "medium")
        r = apex.fb_act(fid, "accept", buyer, {"qty": str(want.qty), "reason": REASON})
        check(r.status_code == 409, f"FB6: accepting again answered {r.status_code}, expected 409")
        for who, why in ((buyer, "the buyer who accepted"), (director, "the director (not named)"),
                         (plant_head, "the plant head (not named)")):  # fmt: skip
            r = apex.fb_act(fid, "approve", who, {})
            check(r.status_code == 403, f"FB6: {why} approving answered {r.status_code}, expected 403")
        if pid:
            r = apex.call(buyer, "POST", f"/api/admin/proposals/{pid}/approve", {})
            check(r.status_code in (403, 409), f"FB6: the buyer approving the proposal in the admin app answered "
                  f"{r.status_code}")  # fmt: skip
        check(apex.requests_now() == requests_before, "FB6: a request was made before the approval")
        r = apex.fb_act(fid, "approve", named, {"note": "eval: agreed"})
        body = js(r)
        check(r.status_code == 200 and body.get("status") == "approved" and body.get("request_no"),
              f"FB6: {named} approving answered {r.status_code} {r.text[:300]}")  # fmt: skip
        new_no = str(body.get("request_no") or "")
        _new_request(check, apex, state, want, fid, new_no, mrp, named)
        check((apex.request_lines(mrp), apex.request_head(mrp).get("total")) == anchor_before,
              f"FB6: the anchor request {mrp} changed: {apex.request_lines(mrp)} (the extra must be a new request)")  # fmt: skip
        for action in ("forward_buy.create", "forward_buy.accept", "forward_buy.approve"):
            check(apex.audit_count(action) >= 1, f"FB6: no {action} audit row", "medium")
        check(int(apex.one("select count(*) from audit_event where action like 'proposal.%'") or 0) >= 1,
              "FB6: no proposal.* audit row", "medium")  # fmt: skip
        page = js(apex.call(buyer, "GET", f"/api/requisitions/{mrp}"))
        check(isinstance(page.get("forward_buy"), dict) and str(page["forward_buy"].get("id")) == str(fid),
              f"FB6: the anchor's page forward_buy is {str(page.get('forward_buy'))[:150]}", "medium")  # fmt: skip
        if new_no:
            page = js(apex.call(buyer, "GET", f"/api/requisitions/{new_no}"))
            link = page.get("forward_buy_of") or {}
            check(str(link.get("id")) == str(fid) and link.get("anchor_request_no") == mrp,
                  f"FB6: the new request's forward_buy_of is {link}", "medium")  # fmt: skip

    # ---- the second company: revalidation, then reject ----
    apex.reset()
    st = b3.start3(apex, check)
    state = b3.FbState(apex, st)
    state.add_bench(b3.scenario_bench(apex, check, "FB1"))
    rec = b3.record_of(apex, check, "FB6 second", WIRE, b3.CKN)
    if check(rec is not None, "FB6 second: no record"):
        assert rec is not None
        fid = rec["_id"]
        with b3.day(b3.AS_OF):
            r = apex.fb_act(fid, "accept", buyer, {"qty": rec.get("qty"), "reason": REASON})
            check(r.status_code == 200, f"FB6 second: accepting answered {r.status_code} {r.text[:200]}")
        newer = {**b3.scenario_bench(apex, check, "FB1"), "price": "800.0000", "effective_date": "2026-11-20",
                 "source_ref": "Pune wire bulletin 2026-11 (third week)"}  # fmt: skip
        state.add_bench(newer)
        with b3.day(b3.AS_OF):
            r = apex.fb_act(fid, "approve", named, {})
            got = js(r) if isinstance(js(r), dict) else {}
            detail = got.get("detail")
            new_id = got.get("new_id") or (detail.get("new_id") if isinstance(detail, dict) else None)
            check(r.status_code == 409, f"FB6 second: approving after the price changed answered {r.status_code}, "
                  "expected 409")  # fmt: skip
            live = apex.live(WIRE, b3.CKN)
            check(bool(new_id) and bool(live) and str(new_id) == str(live[-1]["id"]),
                  f"FB6 second: the 409 named new_id {new_id}; live records {[x['id'] for x in live]}", "medium")  # fmt: skip
            rows = {x["id"]: x for x in apex.fb_rows(WIRE, b3.CKN)}
            check(rows.get(fid, {}).get("status") == "superseded", f"FB6 second: the accepted record is "
                  f"{rows.get(fid, {}).get('status')}, expected superseded")  # fmt: skip
            check(not apex.q("select 1 from request where source_ref = :s", s=f"FB-{fid}"),
                  "FB6 second: a request was made for the superseded record")  # fmt: skip
            b3.check_record(check, "FB6 second at 800", apex, state, WIRE, b3.CKN, request_label=b3.MRP)
            if live:
                nid = live[-1]["id"]
                r = apex.fb_act(nid, "reject", buyer, {})
                check(r.status_code == 422, f"rejecting without a reason answered {r.status_code}, expected 422")
                r = apex.fb_act(nid, "reject", buyer, {"reason": "Storage cage full until January"})
                check(r.status_code == 200 and js(r).get("status") == "rejected", f"rejecting answered {r.status_code} "
                      f"{r.text[:200]}")  # fmt: skip
                check(apex.audit_count("forward_buy.reject") >= 1, "no forward_buy.reject audit row", "medium")
                r = apex.fb_act(nid, "accept", buyer, {"qty": "1", "reason": "x"})
                check(r.status_code == 409, f"accepting a rejected record answered {r.status_code}, expected 409")
        apex.fb_cli("--item", WIRE, "--location", b3.CKN)
        check(not apex.live(WIRE, b3.CKN), f"after a rejection the engine offered again without a newer benchmark: "
              f"{apex.live(WIRE, b3.CKN)}")  # fmt: skip
    b3.tenant_check3(check, apex, bw)
    out.metrics.update({"checks": check.done, "failed": len(check.failed)})
    out.notes.append(
        "Spec gap: the 409 body for a changed recommendation is `{detail, new_id}`; whether new_id sits at the top or "
        "inside detail is not fixed, both are read."
    )
    return out


def _new_request(check: b.Checker, co: b3.Co3, state: b3.FbState, want: b3.Result, fid: Any, no: str, mrp: str,
                 named: str) -> None:  # fmt: skip
    if not no:
        return
    est = D(state.items[WIRE]["standard_price"])
    lines = co.q("select item_code, qty, uom, need_by, stock_qty, est_price, cost_centre_code, delivery_location "
                 "from request_line where request_no = :n", n=no)  # fmt: skip
    head = co.q("select status, total_value, department_code, channel, source_ref, justification, ship_to_location "
                "from request where no = :n", n=no)  # fmt: skip
    anchor_dept = co.one("select department_code from request where no = :n", n=mrp)
    anchor_line = co.q("select need_by, coalesce(delivery_location, '') from request_line where request_no = :n and "
                       "item_code = :i", n=mrp, i=WIRE)  # fmt: skip
    ok = (len(lines) == 1 and lines[0][0] == WIRE and D(lines[0][1]) == want.qty and lines[0][2] in ("KGS", None)
          and lines[0][3] == (anchor_line[0][0] if anchor_line else None) and D(lines[0][4]) == want.qty
          and D(lines[0][5]) == est and lines[0][6] is None)  # fmt: skip
    check(ok, f"FB6: the new request's lines are {lines}; expected one: RM-08 {want.qty} KGS, need-by of the anchor "
          f"line, stock part {want.qty}, estimate {est}, no cost centre")  # fmt: skip
    if head:
        status, total, dept, channel, ref, why, _ = head[0]
        check(status == "submitted" and D(total) == want.value, f"FB6: the new request is {status} for {total}, "
              f"expected submitted for {want.value}")  # fmt: skip
        check(dept == anchor_dept, f"FB6: the new request's department {dept}, the anchor's {anchor_dept}", "medium")
        check(channel == "planning" and ref == f"FB-{fid}" and str(why or "").startswith(f"Forward buy FB-{fid}"),
              f"FB6: channel {channel}, source_ref {ref}, justification {str(why)[:60]!r}", "medium")  # fmt: skip
    first = approver_of(co, "request", want.value, b3.AS_OF)
    check(co.approver_of(no) == first, f"FB6: the new request waits for {co.approver_of(no)}, the request bands say "
          f"{first}")  # fmt: skip
    status, body = co.check_of(no)
    g = b3.groups_of(body).get(("1410", "department"))
    values = {m: b3.a0_values(state.stock, m) for m in ("MO-1", "MO-2", b3.MRP)} if state.stock else {}
    held = sum((v.get("1410", Z) for v in values.values()), Z)
    wantg = state.budgets.group("1410", "department", "2026-11", state.budgets.scope("PRD", b3.CKN),
                                held_by_requests=held, requested=b3.r2(want.qty * est))  # fmt: skip
    wrong = b3.group_diff(g, wantg.as_dict())
    check(status == 200 and not wrong, f"FB6: the new request's 1410 check: {'; '.join(wrong) or status}")
    check(wantg.used_pct == Decimal("11.44"), f"oracle FB6 1410 used {wantg.used_pct}, the spec 11.44 (self-check)",
          "medium")  # fmt: skip
