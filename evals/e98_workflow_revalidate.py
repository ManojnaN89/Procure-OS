"""E98: one workflow per business key, and revalidation before a person's confirmation runs (task 11a.2), written
without the implementation.

Independence: written from the AI foundation spec (2.1 tenant 0101 and 0102, 2.3, 2.4 runtime hook) only. The model
behind planning is scripted (`aif.no_live_models`); the demo agent's decisions are scripted in `aif.waiting_run`.

Scenarios, on the main demo company (Apex):
1. Planning on (two administrators); Run store check; only the routing jobs run (`event_route`), so each request's
   workflow stays open with its explanation job queued. A stock count of the castings changes the figures; Run store
   check again and route: for a request with a second figures event, still one open `planning.explain` workflow,
   both events point to it (`event.workflow_id`), and one queued `planning_explain` job for it with
   `dedupe_key = workflow:<id>`.
2. The demo agent (`agents/demo.py`, capability `sourcing`, asked by the buyer) looks an invoice up and asks for the
   write tool: a recommendation is recorded with evidence, engine figures and a `basis_hash`, and the proposal carries
   `_recommendation_id`. The eval registers its own basis for that kind (`aif.MovesBasis`: the number of stock movements).
   A stock count changes it; approving the proposal answers 409, the proposal is `withdrawn`, the recommendation is
   `stale` with a reason, and a `<kind>.refresh` event is recorded. A second run with nothing changed: approving runs
   (200) and the recommendation is `accepted`.
Mutations caught: revalidation skipped (the stale approval runs); revalidation comparing the recommendation's status
only, not its figures (the stale approval runs); a second workflow or job per trigger.
"""

from typing import Any

import aif
import block1 as b
import block2 as s2
import env
from lib import Outcome

ID = "E98"
TITLE = "One workflow per business key; a stale recommendation is refused at confirmation and refreshed"
REQUIREMENT = (
    "D39 rules 4 and 5 (spec 2.3): `workflows.join_or_start` gives one open workflow per (kind, business key), later "
    "events join it and queue no second job (key workflow:<id>); before a person's confirmation runs, the "
    "recommendation's basis is recomputed: changed figures give 409, the proposal withdrawn, the record stale and a "
    "refresh event; unchanged figures let it run"
)
PREFIX = "e98"
KIND = "tool.release_payment"


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
    with aif.no_live_models(aif.Model()):
        _join(check, co)
        _revalidate(check, co)
    out.metrics.update({"checks": check.done, "failed": len(check.failed)})
    out.notes += [
        (
            "Spec gap: the runtime's recommendation (kind tool.<name>) has no basis registered by the platform, and the "
            "spec does not say what revalidation does for a kind with no basis; the eval registers its own basis for it."
        ),
        (
            "Spec gap: the business key of a runtime recommendation (a write tool's) is not named; the eval only requires "
            "it to be non-empty."
        ),
        (
            "Spec gap: a write tool asked for before any read tool gives empty evidence, which `record` refuses; the scripted "
            "agent reads first."
        ),
    ]
    return out


def _events_of(co: s2.Co, mr: str) -> list[dict[str, Any]]:
    return aif.rows(co, "select * from event where type = 'material_request.figures_changed' and key like :k "
                        "order by id", k=f"{mr}:%")  # fmt: skip


def _join(check: b.Checker, co: s2.Co) -> None:
    aif.planning_on(co, check)
    r = co.base_state()
    check(r.status_code == 200, f"Run store check answered {r.status_code}")
    aif.at_risk(co, check)  # eval set-up: a request with a line at risk (see aif.at_risk)
    aif.run_jobs(co.slug, ["event_route"], now=aif.later(0.2))
    before = {str(e["key"]).split(":")[0] for e in aif.rows(co, "select key from event where type = "
                                                                "'material_request.figures_changed'")}  # fmt: skip
    if not check(bool(before), "set-up: the store check recorded no figures event with planning on", "medium"):
        return
    target = None
    for n in (1, 2, 3):
        aif.count_stock(co, n)
        co.store_check()
        aif.run_jobs(co.slug, ["event_route"], now=aif.later(0.3 + n / 10))
        target = next((mr for mr in sorted(before) if len(_events_of(co, mr)) >= 2), None)
        if target:
            break
    if not check(target is not None, "set-up: no request got a second figures event after stock counts", "medium"):
        return
    flows = aif.rows(co, "select * from workflow where kind = 'planning.explain' and business_key = :k", k=target)
    open_ = [w for w in flows if w["status"] in ("open", "waiting")]
    check(len(open_) == 1, f"{target}: {len(open_)} open planning.explain workflows after two events, expected 1 "
          f"({[(w['id'], w['status']) for w in flows]})")  # fmt: skip
    if not open_:
        return
    wid = int(open_[0]["id"])
    evs = _events_of(co, target)
    check(all(e.get("workflow_id") == wid for e in evs), f"{target}: events point to workflows "
          f"{[e.get('workflow_id') for e in evs]}, expected all {wid}")  # fmt: skip
    check(open_[0].get("capability") == "planning", f"{target}: workflow capability {open_[0].get('capability')!r}")
    check(open_[0].get("last_event_id") == evs[-1]["id"], f"{target}: workflow last_event_id "
          f"{open_[0].get('last_event_id')}, the latest event is {evs[-1]['id']}", "medium")  # fmt: skip
    jobs = [j for j in aif.jobs(co.slug, ["planning_explain"]) if j["status"] in ("queued", "running")]
    keyed = [j for j in jobs if j.get("dedupe_key") == f"workflow:{wid}"]
    check(len(keyed) == 1, f"{target}: {len(keyed)} waiting planning_explain job(s) keyed workflow:{wid}, expected 1 "
          f"(waiting keys {[j.get('dedupe_key') for j in jobs]})")  # fmt: skip
    starts = [a for a in aif.audit_after(co, "workflow.start") if str(wid) == str(a.get("entity_id"))]
    joins = [a for a in aif.audit_after(co, "workflow.join") if str(wid) == str(a.get("entity_id"))]
    check(len(starts) == 1 and len(joins) >= 1, f"{target}: audit workflow.start {len(starts)}, workflow.join "
          f"{len(joins)}", "medium")  # fmt: skip


def _revalidate(check: b.Checker, co: s2.Co) -> None:
    basis = aif.MovesBasis(co)
    aif.register_basis(KIND, basis)
    maker = co.role("buyer")
    w, pid, rec = aif.recommended_run(co, "sourcing", maker)
    check(getattr(w.outcome, "status", None) == "waiting" and pid is not None, f"the demo run did not wait on a "
          f"proposal ({w.outcome})")  # fmt: skip
    if not check(bool(rec), f"no recommendation was recorded for the demo run's proposal {pid}"):
        return
    check(rec.get("kind") == KIND and bool(rec.get("business_key")), f"recommendation kind {rec.get('kind')!r}, "
          f"business key {rec.get('business_key')!r}")  # fmt: skip
    check(bool(rec.get("evidence")) and bool(rec.get("engine_figures")) and bool(rec.get("basis_hash")),
          "the recommendation lacks evidence, engine figures or a basis hash")  # fmt: skip
    check(rec.get("basis_hash") in aif.figures_hashes(rec.get("engine_figures")), "basis_hash is not the sha256 of "
          "the engine figures as sorted JSON (spec 2.4)", "low")  # fmt: skip
    check(rec.get("run_id") == getattr(w.outcome, "run_id", None), f"recommendation run_id {rec.get('run_id')}, the "
          f"run is {getattr(w.outcome, 'run_id', None)}")  # fmt: skip
    payload = aif.proposal(co, pid or 0).get("payload") or {}
    check(str(payload.get("_recommendation_id")) == str(rec["id"]), f"the proposal's payload carries "
          f"_recommendation_id {payload.get('_recommendation_id')!r}, expected {rec['id']}")  # fmt: skip
    basis.pin(str(rec["basis_hash"]))
    refresh0 = aif.count(co, "select count(*) from event where type like '%.refresh'")
    r = aif.count_stock(co, 4)
    check(r.status_code in (200, 201), f"the stock count answered {r.status_code}")
    who, r = aif.approve_as_anyone(co, pid or 0, skip=[maker])
    aif.check_status(check, r, 409, "approving a proposal whose figures changed since it was recommended")
    check(basis.asked > 0, "the eval's registered basis was never asked: the recommendation was not revalidated")
    after = aif.rows(co, "select * from recommendation where id = :i", i=rec["id"])[0]
    check(after.get("status") == "stale" and bool(after.get("stale_reason")), f"the stale recommendation is "
          f"{after.get('status')!r} with reason {after.get('stale_reason')!r}")  # fmt: skip
    check(aif.proposal(co, pid or 0).get("status") == "withdrawn", f"the stale proposal is "
          f"{aif.proposal(co, pid or 0).get('status')!r}, expected withdrawn")  # fmt: skip
    refreshed = aif.count(co, "select count(*) from event where type like '%.refresh'") - refresh0
    check(refreshed == 1, f"{refreshed} refresh event(s) after a stale confirmation, expected 1")
    # unchanged figures: approving runs
    _w2, pid2, rec2 = aif.recommended_run(co, "sourcing", maker)
    if not check(bool(rec2) and pid2 is not None, "the second demo run made no recommendation and proposal"):
        return
    basis.pin(str(rec2["basis_hash"]))
    order = [who] if who else []
    r2 = co.call(order[0], "POST", f"/api/admin/proposals/{pid2}/approve", {}) if order else None
    if r2 is None or r2.status_code == 403:
        _, r2 = aif.approve_as_anyone(co, pid2, skip=[maker])
    aif.check_status(check, r2, 200, "approving a proposal whose figures are unchanged")
    done = aif.rows(co, "select * from recommendation where id = :i", i=rec2["id"])[0]
    check(done.get("status") == "accepted", f"the confirmed recommendation is {done.get('status')!r}")
