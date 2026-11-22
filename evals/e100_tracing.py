"""E100: runs and model calls are traced (task 11a.4), written without the implementation.

Independence: written from the AI foundation spec (2.1 tenant 0103, 2.5, OP-10) only. Every model is scripted
(`aif.no_live_models`).

Scenarios, on the main demo company (Apex):
1. The buyer asks the assistant, in process (`assistant.ask`) and through the API (`POST /api/chat`): the new
   `agent_run` has `user_id` = the buyer, `started_by 'user:<id>'`, `capability 'assistant'`; every `model_call` made
   during it carries its `run_id` (and capability and user); `GET /api/admin/agent-runs/{id}` lists those model calls
   and `GET /api/admin/agent-runs?capability=assistant&user_id=` finds the run.
2. A planning explanation run by its job (planning on, figures changed, the `planning_explain` job run here):
   `started_by 'job:<job id>'`, `user_id` null, capability `planning`, its model calls carry the run id.
3. A supplier's quote read (`replies.read_reply`): its model calls carry `entity`, `entity_id` and `capability
   'sourcing'`; `GET /api/admin/model-calls?capability=sourcing` returns them.
4. No column of `model_call` or `agent_run` holds a sentence of the assistant's system prompt (OP-10: prompts are not
   stored).
5. `trace.span` nests and restores; when the dev switcher's `audit.VIA` exists, a run started through it records `via`.
Mutations caught: the run id lost in nested calls (scenario 1, 5); the user not passed by the assistant (scenario 1).
"""

from decimal import Decimal
from types import SimpleNamespace
from typing import Any

import ai_harness
import aif
import block1 as b
import block2 as s2
import env
import lib
from ai_cases import Item
from block1 import js
from lib import Outcome

ID = "E100"
TITLE = "Agent runs and model calls are traced to the person, job, capability, run and document"
REQUIREMENT = (
    "Task 11a.4 (spec 2.5): agent_run records capability, started_by, user_id and via; every model_call made inside a "
    "run carries its run_id, capability and user; an AI step's calls carry the document (entity, entity_id) and "
    "capability; the run viewer lists a run's model calls; prompts are not stored (OP-10)"
)
PREFIX = "e100"
SYSTEM_SENTENCE = "Never add up, subtract, average or estimate"


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
        _assistant(check, co, model)
        _planning_job(check, co, model)
        _quote_reading(check, co, model)
        _no_prompts(check, co)
        _spans(check, out, co, model)
    out.metrics.update({"checks": check.done, "failed": len(check.failed), "model calls": model.calls})
    out.notes += [
        (
            "Spec gap: `audit.VIA` (the dev switcher) does not exist in the base this eval was written on; the via check "
            "runs only when it exists and is a context variable."
        ),
        (
            "Spec gap: the entity name an AI step records (the email, the RFQ or the draft) is not named; the eval requires "
            "both entity and entity_id to be filled."
        ),
    ]
    return out


def _calls_since(co: s2.Co, last: int) -> list[dict[str, Any]]:
    return aif.rows(co, "select * from model_call where id > :i order by id", i=last)


def _last_call(co: s2.Co) -> int:
    return aif.count(co, "select coalesce(max(id), 0) from model_call")


def _last_run(co: s2.Co) -> int:
    return aif.count(co, "select coalesce(max(id), 0) from agent_run")


def _assistant_script(model: aif.Model) -> None:
    model.queue = [{"tool": "find_requests", "args": {}, "confidence": 0.9},
                   {"final": "I looked at your requests; nothing is waiting.", "confidence": 0.9}]  # fmt: skip


def _check_run(check: b.Checker, co: s2.Co, run: dict[str, Any], calls: list[dict[str, Any]], label: str,
               uid: int | None, started_by: str, capability: str) -> None:  # fmt: skip
    check(run.get("capability") == capability, f"{label}: agent_run.capability {run.get('capability')!r}")
    check(run.get("user_id") == uid, f"{label}: agent_run.user_id {run.get('user_id')!r}, expected {uid}")
    check(run.get("started_by") == started_by, f"{label}: started_by {run.get('started_by')!r}, expected "
          f"{started_by!r}")  # fmt: skip
    if not check(bool(calls), f"{label}: no model_call row was written during the run", "medium"):
        return
    wrong = [(c["id"], c.get("run_id")) for c in calls if c.get("run_id") != run["id"]]
    check(not wrong, f"{label}: model calls not linked to run {run['id']}: {wrong[:4]}")
    check(all(c.get("capability") == capability for c in calls), f"{label}: model call capabilities "
          f"{sorted({str(c.get('capability')) for c in calls})}")  # fmt: skip
    check(all(c.get("user_id") == uid for c in calls), f"{label}: model call user ids "
          f"{sorted({str(c.get('user_id')) for c in calls})}, expected {uid}", "medium")  # fmt: skip


def _assistant(check: b.Checker, co: s2.Co, model: aif.Model) -> None:
    from p2p.agents import assistant

    buyer = co.role("buyer")
    uid = co.user_id(buyer)
    runs0, calls0 = _last_run(co), _last_call(co)
    _assistant_script(model)
    db = aif.new_session(co)
    try:
        assistant.ask(db, co.slug, uid, "Which of my requests are waiting?", aif.gateway(model))
        db.commit()
    finally:
        db.close()
    found = aif.rows(co, "select * from agent_run where id > :i and agent = 'assistant' order by id", i=runs0)
    if check(len(found) == 1, f"asking the assistant made {len(found)} assistant run(s), expected 1"):
        run = found[0]
        _check_run(check, co, run, _calls_since(co, calls0), "assistant (in process)", uid, f"user:{uid}", "assistant")
        admin = aif.admins(co)[0]
        r = co.call(admin, "GET", f"/api/admin/agent-runs/{run['id']}")
        if aif.check_status(check, r, 200, "the run viewer's run"):
            listed = js(r).get("model_calls")
            n = aif.count(co, "select count(*) from model_call where run_id = :r", r=run["id"])
            check(isinstance(listed, list) and len(listed) == n, f"the run viewer lists "
                  f"{len(listed) if isinstance(listed, list) else listed!r} model call(s); the run has {n}")  # fmt: skip
        r = co.call(admin, "GET", f"/api/admin/agent-runs?capability=assistant&user_id={uid}")
        body = js(r)
        items = body if isinstance(body, list) else body.get("runs") or body.get("rows") or []
        check(any(x.get("id") == run["id"] for x in items), "the run viewer filtered by capability and user does not "
              "list the run", "medium")  # fmt: skip
    # through the API: the same trace
    runs0, calls0 = _last_run(co), _last_call(co)
    _assistant_script(model)
    co.call(buyer, "POST", "/api/chat", {"message": "Which of my requests are waiting?"})
    found = aif.rows(co, "select * from agent_run where id > :i and agent = 'assistant' order by id", i=runs0)
    if check(len(found) == 1, f"the chat API made {len(found)} assistant run(s), expected 1", "medium"):
        _check_run(check, co, found[0], _calls_since(co, calls0), "assistant (API)", uid, f"user:{uid}", "assistant")


def _planning_job(check: b.Checker, co: s2.Co, model: aif.Model) -> None:
    aif.planning_on(co, check)
    co.base_state()
    aif.at_risk(co, check)  # eval set-up: a request with a line at risk (see aif.at_risk)
    aif.count_stock(co, 1)
    co.store_check()
    aif.run_jobs(co.slug, ["event_route"], now=aif.later(0.2))
    queued = [j for j in aif.jobs(co.slug, ["planning_explain"]) if j["status"] == "queued"]
    if not check(bool(queued), "set-up: no planning_explain job queued after a store check with planning on", "medium"):
        return
    job = queued[0]
    runs0, calls0 = _last_run(co), _last_call(co)
    model.queue = [{"final": '{"summary": "Lines wait on supply.", "lines": []}', "confidence": 0.9}]
    aif.run_jobs(co.slug, ["planning_explain"], now=aif.later(0.5))
    found = aif.rows(co, "select * from agent_run where id > :i and agent = 'planning' order by id", i=runs0)
    if check(bool(found), "the planning job started no agent run", "medium"):
        first = found[0]
        _check_run(check, co, first, [c for c in _calls_since(co, calls0) if c.get("run_id") == first["id"]] or
                   _calls_since(co, calls0), "planning job", None, f"job:{job['id']}", "planning")  # fmt: skip


def _quote_reading(check: b.Checker, co: s2.Co, model: aif.Model) -> None:
    from p2p.sourcing import replies

    category = lib.busiest_category()
    item = next(r for r in co.rows("master", "item") if r["category_code"] == category)
    world = SimpleNamespace(db=co.db, slug=co.slug)
    rfq, _supplier, email = ai_harness.setup_rfq(world, "E100", [Item(1, item["code"], item["name"], Decimal(5),
                                                                     Decimal(100), Decimal(18))])  # fmt: skip
    email_id = ai_harness.deliver(world, rfq, email, f"Rate for {item['name']}: Rs 99 per unit, GST 18%, 6 days.")
    model.queue = []
    model.default = {"intent": "quote", "lines": [{"item": item["code"], "unit_price": "99", "discount_pct": None,
                     "gst_rate": "18", "delivery_days": 6, "price_includes_gst": False}],
                     "valid_until": "2026-12-31", "confidence": 0.95}  # fmt: skip
    calls0 = _last_call(co)
    db = aif.new_session(co)
    try:
        replies.read_reply(db, co.slug, email_id, aif.gateway(model), as_of=lib.TODAY)
        db.commit()
    finally:
        db.close()
    model.default = {"final": "Done."}
    calls = _calls_since(co, calls0)
    if not check(bool(calls), "reading a quote wrote no model_call row", "medium"):
        return
    check(all(c.get("capability") == "sourcing" for c in calls), f"quote reading calls have capabilities "
          f"{sorted({str(c.get('capability')) for c in calls})}, expected sourcing")  # fmt: skip
    check(all(c.get("entity") and c.get("entity_id") for c in calls), f"quote reading calls without the document: "
          f"{[(c.get('entity'), c.get('entity_id')) for c in calls][:3]}")  # fmt: skip
    r = co.call(aif.admins(co)[0], "GET", "/api/admin/model-calls?capability=sourcing")
    body = js(r)
    items = body if isinstance(body, list) else body.get("calls") or body.get("rows") or body.get("model_calls") or []
    ids = {x.get("id") for x in items if isinstance(x, dict)}
    check(r.status_code == 200 and {c["id"] for c in calls} <= ids, "GET /api/admin/model-calls?capability=sourcing "
          "does not return the quote reading's calls", "medium")  # fmt: skip


def _no_prompts(check: b.Checker, co: s2.Co) -> None:
    for table in ("model_call", "agent_run"):
        n = aif.count(co, f"select count(*) from {table} t where row_to_json(t)::text like :s",
                      s=f"%{SYSTEM_SENTENCE}%")  # fmt: skip
        check(n == 0, f"{n} {table} row(s) hold a sentence of the assistant's system prompt (OP-10: no prompts stored)")


def _spans(check: b.Checker, out: Outcome, co: s2.Co, model: aif.Model) -> None:
    from p2p.agents import trace

    with trace.span(run_id=101, capability="assistant", user_id=7):
        with trace.span(entity="email", entity_id="55"):
            inner = trace.CURRENT.get()
        outer = trace.CURRENT.get()
    after = trace.CURRENT.get()
    check(inner is not None and inner.run_id == 101 and inner.entity == "email" and inner.user_id == 7,
          f"a nested span lost the outer fields: {inner}")  # fmt: skip
    check(outer is not None and outer.run_id == 101 and outer.entity is None, f"leaving a nested span did not restore "
          f"the outer trace: {outer}")  # fmt: skip
    check(after is None or getattr(after, "run_id", None) is None, f"leaving every span left a trace set: {after}")
    from p2p.platform import audit

    via = getattr(audit, "VIA", None)
    if via is None or not hasattr(via, "set"):
        out.notes.append("audit.VIA is absent or not a context variable: the dev switcher's via was not checked.")
        return
    token = via.set("eval dev switcher")
    try:
        runs0 = _last_run(co)
        _assistant_script(model)
        db = aif.new_session(co)
        try:
            from p2p.agents import assistant

            assistant.ask(db, co.slug, co.user_id(co.role("buyer")), "Anything waiting?", aif.gateway(model))
            db.commit()
        finally:
            db.close()
    finally:
        via.reset(token)
    found = aif.rows(co, "select via from agent_run where id > :i", i=runs0)
    check(bool(found) and found[0].get("via") == "eval dev switcher", f"a run started through the dev switcher has "
          f"via {[f.get('via') for f in found]}")  # fmt: skip
