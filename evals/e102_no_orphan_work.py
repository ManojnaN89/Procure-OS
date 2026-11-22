"""E102: switching a capability off leaves no orphan work (task 11a.7, D54 check 5), written without the implementation.

Independence: written from the AI foundation spec (2.6 "Switching off", 2.3 workflows, 2.4 recommendations, OP-16) only.
Every model is scripted (`aif.no_live_models`), and the eval counts its calls.

Set-up on the main demo company (Apex), for each capability with jobs (intake, sourcing, execution, invoice_ap): a mail
of its kind stored and routed, so its job is queued (`intake_email`, `read_supplier_reply`, `supplier_message`,
`read_invoice_email`); an open workflow (`workflows.join_or_start`); a waiting agent run on a proposal (the demo
registry); a `proposed` recommendation without a proposal. Planning on, a store check with changed figures routed, so
explanation jobs wait.

Then each capability is switched off through the API (immediate). Checks:
- no job of its kinds is left queued; each queued one is `failed` with the reason;
- one open `ai_handover` task per job that carried work, per open workflow (`workflow.task_id`, status `handed_over`)
  and per proposal-less recommendation (status `handed_over`); each assigned to an active login holding the owner role,
  each on `GET /api/exceptions` for that person; the response's `handed_over` counts agree;
- the waiting run is `failed` and its proposal still pending, its recommendation still `proposed`; once the proposal is
  approved, `resume` does not give the agent another turn;
- the worker run afterwards makes no model call; switching back on (two administrators) re-runs nothing handed over
  (no new task, job, draft or model call);
- planning: its queued explanations dropped, the engine's cases (other tasks) kept, no hand-over task for a job.
Mutations caught: the flag flipped without a hand-over; a task without an assignee; resume continuing after the
switch-off.
"""

from typing import Any

import aif
import block1 as b
import block2 as s2
import env
from block1 import js
from lib import Outcome

ID = "E102"
TITLE = "Switching a capability off hands every piece of its open work to a person; nothing runs on"
REQUIREMENT = (
    "D54 check 5 (spec 2.6): at switch-off, queued jobs fail with the reason and become ai_handover tasks, open "
    "workflows and proposal-less recommendations are handed over with one task each to an active owner, waiting runs "
    "stop and are never resumed, nothing calls a model afterwards, switching on again re-runs nothing; planning's "
    "queued explanations are dropped with no task"
)
PREFIX = "e102"
WITH_JOBS = ("intake", "sourcing", "execution", "invoice_ap")
INVOICE_BOX = "invoices@apexpumps-eval.example"
REQUEST_BOX = "requests@apexpumps-eval.example"


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
    aif.install_smtp()
    co = s2.Co("apex", PREFIX, TestClient(app))
    model = aif.Model()
    with aif.no_live_models(model):
        # eval set-up first: a request with a line at risk (its steps run the worker, which must not read the mails
        # _prepare queues before the switch-off)
        aif.planning_on(co, check)
        co.base_state()
        aif.at_risk(co, check)
        runs = _prepare(check, co)
        _planning(check, co)
        for code in WITH_JOBS:
            _switch_off(check, co, code, runs.get(code))
        _afterwards(check, co, model, runs)
    out.metrics.update({"checks": check.done, "failed": len(check.failed), "model calls": model.calls})
    out.notes += [
        (
            "Spec gap: 2.6 step 3 hands over every open workflow of a capability with a task, while E102's row says planning "
            "makes no task; the eval requires no task for planning's queued jobs and accepts a handed-over planning "
            "workflow with or without one."
        ),
        (
            "Spec gap: a waiting run's hand-over makes no task (its proposal is already a person's); the eval checks the "
            "run is failed and the proposal pending."
        ),
        (
            "Spec gap: the task payload's `ref` for each kind of item (email id, workflow key, recommendation id) is not "
            "fixed; tasks are matched by capability and count, workflows by `workflow.task_id`."
        ),
    ]
    return out


def _prepare(check: b.Checker, co: s2.Co) -> dict[str, aif.Waiting]:
    from p2p.platform import emails, workflows

    aif.set_org(co, "invoice_receipt_mailbox", INVOICE_BOX)
    aif.set_org(co, "request_mailbox", REQUEST_BOX)
    mails = {
        "sourcing": aif.raw_mail(emails.plus_address("rfq", "RFQ-E102-1"), "Re: RFQ-E102-1", "Our rate is Rs 10."),
        "execution": aif.raw_mail(emails.plus_address("po", "PO-E102-1"), "Re: PO-E102-1", "We ship on Monday."),
        "invoice_ap": aif.raw_mail(INVOICE_BOX, "Invoice INV-E102-1", "Attached.",
                                   attachments=[("INV-E102-1.pdf", "application/pdf", aif.PDF)]),
        "intake": aif.raw_mail(REQUEST_BOX, "Need 10 cartons", "Please buy 10 cartons for packing by Friday.",
                               sender=co.people[co.role("buyer")]["email"]),
    }  # fmt: skip
    for code, raw in mails.items():
        check(aif.ingest(co, raw) is not None, f"set-up: the {code} mail was not stored")
    aif.run_jobs(co.slug, ["event_route"], now=aif.later(0.1))
    for code in WITH_JOBS:
        queued = [j for j in aif.jobs(co.slug, aif.JOB_KINDS[code]) if j["status"] == "queued"]
        check(bool(queued), f"set-up: no {aif.JOB_KINDS[code][0]} job queued after routing its mail", "medium")
    runs: dict[str, aif.Waiting] = {}
    for code in WITH_JOBS:
        db = aif.new_session(co)
        try:
            workflows.join_or_start(db, f"eval.{code}", f"E102-{code}", capability=code)
            db.commit()
        finally:
            db.close()
        aif.recommend(co, code, f"E102-REC-{code}")
        w = aif.waiting_run(co, code, co.user_id(co.role("buyer")), agent=f"eval_{code}")
        check(getattr(w.outcome, "status", None) == "waiting", f"set-up: the {code} demo run is "
              f"{getattr(w.outcome, 'status', None)}, expected waiting")  # fmt: skip
        runs[code] = w
    return runs


def _planning(check: b.Checker, co: s2.Co) -> None:
    aif.count_stock(co, 1)
    co.store_check()
    aif.run_jobs(co.slug, ["event_route"], now=aif.later(0.2))
    queued = [j for j in aif.jobs(co.slug, ["planning_explain"]) if j["status"] == "queued"]
    check(bool(queued), "set-up: no planning_explain job queued", "medium")
    other_tasks = aif.count(co, "select count(*) from task where kind <> 'ai_handover'")
    before = {t["id"] for t in aif.tasks(co)}
    r = aif.switch_off(co, "planning", "eval: planning off")
    aif.check_status(check, r, 200, "switching planning off")
    left = [j["id"] for j in aif.jobs(co.slug, ["planning_explain"]) if j["status"] in ("queued", "running")]
    check(not left, f"{len(left)} planning_explain job(s) still waiting after planning was switched off")
    check(aif.count(co, "select count(*) from task where kind <> 'ai_handover'") == other_tasks, "switching planning "
          "off changed the engine's own tasks and cases")  # fmt: skip
    new = [t for t in aif.tasks(co) if t["id"] not in before]
    flows = aif.count(co, "select count(*) from workflow where capability = 'planning' and status = 'handed_over'")
    check(len(new) <= flows, f"switching planning off made {len(new)} hand-over task(s) for {flows} handed-over "
          "workflow(s); its queued explanations make none (block 2 rule)")  # fmt: skip


def _expected(co: s2.Co, code: str) -> dict[str, Any]:
    jobs = [j for j in aif.jobs(co.slug, aif.JOB_KINDS[code]) if j["status"] in ("queued", "running")]
    flows = aif.rows(co, "select * from workflow where capability = :c and status in ('open', 'waiting')", c=code)
    recs = aif.rows(co, "select * from recommendation where capability = :c and status = 'proposed' and proposal_id "
                        "is null", c=code)  # fmt: skip
    return {"jobs": jobs, "flows": flows, "recs": recs}


def _switch_off(check: b.Checker, co: s2.Co, code: str, w: aif.Waiting | None) -> None:
    want = _expected(co, code)
    before = {t["id"] for t in aif.tasks(co)}
    r = aif.switch_off(co, code, f"eval: {code} off")
    if not aif.check_status(check, r, 200, f"switching {code} off"):
        return
    check(aif.enabled(co).get(code) is False, f"{code} is not off right after the switch-off")
    for j in want["jobs"]:
        now = next((x for x in aif.jobs(co.slug) if x["id"] == j["id"]), {})
        check(now.get("status") in ("done", "failed") and (now.get("status") == "done" or "switched off" in
              str(now.get("last_error") or "")), f"{code}: job {j['id']} ({j['kind']}) is {now.get('status')!r} with "
              f"{now.get('last_error')!r} after the switch-off")  # fmt: skip
    new = [t for t in aif.tasks(co) if t["id"] not in before]
    mine = [t for t in new if (t["payload"] or {}).get("capability") == code]
    n_items = len(want["jobs"]) + len(want["flows"]) + len(want["recs"])
    check(len(mine) == n_items and len(new) == n_items, f"{code}: {len(mine)} hand-over task(s) naming it ({len(new)} "
          f"new in all) for {len(want['jobs'])} job(s), {len(want['flows'])} workflow(s) and {len(want['recs'])} "
          "recommendation(s), expected exactly one each")  # fmt: skip
    for t in new:
        check(t.get("assignee") is not None and aif.login_active(co, t["assignee"]), f"{code}: task {t['id']} has "
              f"no active assignee ({t.get('assignee')})")  # fmt: skip
        check(aif.holds(co, t["assignee"], aif.OWNERS[code]), f"{code}: task {t['id']} went to "
              f"{co.code_of(t['assignee'])}, not an owner ({aif.OWNERS[code]})", "medium")  # fmt: skip
    for f in want["flows"]:
        now = aif.rows(co, "select * from workflow where id = :i", i=f["id"])[0]
        check(now.get("status") == "handed_over" and now.get("task_id") in {t["id"] for t in new}, f"{code}: workflow "
              f"{f['id']} is {now.get('status')!r} with task {now.get('task_id')}")  # fmt: skip
    for rec in want["recs"]:
        now = aif.rows(co, "select * from recommendation where id = :i", i=rec["id"])[0]
        check(now.get("status") == "handed_over", f"{code}: recommendation {rec['id']} is {now.get('status')!r}")
    got = js(r).get("handed_over") or {}
    check(int(got.get("tasks") or 0) == n_items, f"{code}: the response says handed_over {got}, expected {n_items} "
          "task(s)", "medium")  # fmt: skip
    for code_of_person in {co.code_of(t["assignee"]) for t in new if co.code_of(t["assignee"])}:
        uid = co.user_id(str(code_of_person))
        theirs = [t for t in aif.tasks(co) if t["assignee"] == uid]
        listed = js(co.call(str(code_of_person), "GET", "/api/exceptions?kind=ai_handover")).get("rows") or []
        check(len(listed) >= len(theirs), f"{code}: {code_of_person} has {len(theirs)} open hand-over task(s), the "
              f"Exceptions screen lists {len(listed)}")  # fmt: skip
    if w is not None:
        run = aif.rows(co, "select * from agent_run where id = :i", i=w.outcome.run_id)[0]
        check(run.get("status") == "failed" and "switched off" in str(run.get("result") or ""), f"{code}: the waiting "
              f"run is {run.get('status')!r} ({run.get('result')!r})")  # fmt: skip
        p = aif.proposal(co, w.outcome.proposal_id)
        check(p.get("status") == "pending", f"{code}: the run's proposal is {p.get('status')!r}, expected pending")
        rec = aif.rec_of_proposal(co, w.outcome.proposal_id)
        check(not rec or rec.get("status") == "proposed", f"{code}: a recommendation with a proposal was "
              f"{rec.get('status')!r} after the switch-off, expected left as it was", "medium")  # fmt: skip


def _afterwards(check: b.Checker, co: s2.Co, model: aif.Model, runs: dict[str, aif.Waiting]) -> None:
    calls, logged = model.calls, aif.count(co, "select count(*) from model_call")
    aif.drain(co.slug, now=aif.later(30))
    check(model.calls == calls and aif.count(co, "select count(*) from model_call") == logged, f"after the switch-off "
          f"the worker made {model.calls - calls} model call(s)")  # fmt: skip
    # a person approves a stopped run's proposal: the run is not continued
    w = runs.get("sourcing")
    if w is not None:
        basis = aif.MovesBasis(co)
        aif.register_basis("tool.release_payment", basis)
        rec = aif.rec_of_proposal(co, w.outcome.proposal_id)
        if rec:
            basis.pin(str(rec["basis_hash"]))
        aif.approve_as_anyone(co, w.outcome.proposal_id, skip=[co.role("buyer")])
        asked = w.decisions[0]
        try:
            w.runtime.resume(w.thread, w.outcome.proposal_id)
        except Exception:  # noqa: BLE001 - refusing to resume is the expected answer
            w.db.rollback()
        check(w.decisions[0] == asked, "resume gave the agent another turn after its capability was switched off")
        run = aif.rows(co, "select * from agent_run where id = :i", i=w.outcome.run_id)[0]
        check(run.get("status") == "failed", f"the stopped run is {run.get('status')!r} after a resume attempt")
    tasks0 = len(aif.tasks(co, status=None))
    drafts0 = aif.count(co, "select count(*) from quote_draft")
    queued0 = {j["id"] for j in aif.jobs(co.slug) if j["status"] == "queued"}
    for code in WITH_JOBS:
        asked_, approved = aif.switch_on(co, code)
        check(approved is not None and approved.status_code == 200, f"switching {code} back on answered "
              f"{asked_.status_code} / {getattr(approved, 'status_code', None)}")  # fmt: skip
    calls = model.calls
    aif.drain(co.slug, now=aif.later(60))
    kinds = {k for code in WITH_JOBS for k in aif.JOB_KINDS[code]}
    requeued = [j["id"] for j in aif.jobs(co.slug, kinds) if j["id"] not in queued0 and j["status"] != "failed"]
    check(not requeued, f"switching back on re-ran handed-over work: jobs {requeued[:5]}")
    check(model.calls == calls, f"switching back on made {model.calls - calls} model call(s) for handed-over work")
    check(aif.count(co, "select count(*) from quote_draft") == drafts0, "switching back on read a handed-over reply")
    check(len(aif.tasks(co, status=None)) == tasks0, "switching back on made more hand-over tasks")
    for w in runs.values():
        w.db.close()
