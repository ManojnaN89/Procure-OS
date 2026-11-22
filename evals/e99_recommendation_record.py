"""E99: the recommendation record (task 11a.3), written without the implementation.

Independence: written from the AI foundation spec (2.1 tenant 0102, 2.4) only; the demo agent's turns are scripted.

Scenarios, on the main demo company (Apex), with Brightwave for the tenant check:
1. The demo agent (capability `sourcing`, asked by the buyer) recommends the write tool; a person other than the buyer
   approves its proposal (figures unchanged; the eval's own basis as in E98). `GET /api/admin/recommendations/{id}`
   shows capability, kind, business key, summary, non-empty evidence and engine figures, rule versions, the run, the
   proposal, `decided_by` = the approver (not the agent), `decided_at`; the list filters by business key.
2. The evidence is frozen: `update recommendation set evidence = ...` as the app's own login fails; so do changes of
   the summary and the engine figures.
3. Segregation: a recommendation directed by a person cannot be accepted by that person (`recommendations.decide`
   raises the permission error, and the API answers 403 when that person approves its proposal); another person may.
   Deciding twice is refused.
4. Brightwave's administrator gets 404 for Apex's record; a requester without `audit.view` gets 403.
Mutations caught: the confirmer stored as the agent; evidence editable; the maker allowed to confirm.
"""

from typing import Any

import aif
import block1 as b
import block2 as s2
import env
from block1 import js
from lib import Outcome

ID = "E99"
TITLE = "Each recommendation keeps its evidence, engine figures, rule versions, run and confirmer, frozen"
REQUIREMENT = (
    "D39 rule 7 (spec 2.4): one record of what was recommended, on what evidence, the engine's numbers, the rule "
    "versions, the run and the proposal; who confirmed it and when; the evidence cannot change; the person who "
    "directed the agent cannot confirm; read by audit.view holders of the same company only"
)
PREFIX = "e99"
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
    client = TestClient(app)
    co = s2.Co("apex", PREFIX, client)
    bw = s2.Co("brightwave", PREFIX, client)
    with aif.no_live_models(aif.Model()):
        rec_id = _accepted(check, co)
        if rec_id:
            _frozen(check, co, rec_id)
            _tenant_and_permission(check, co, bw, rec_id)
        _maker(check, co)
    out.metrics.update({"checks": check.done, "failed": len(check.failed)})
    out.notes += [
        (
            "Spec gap: the response shapes of the recommendation API are listed as fields; the eval reads them as a JSON "
            "object (the detail) and a list or an object holding one (the list)."
        ),
        (
            "Spec gap: the exception `decide` raises for a second decision is named only by its status (409); any error "
            "is accepted."
        ),
    ]
    return out


def _accepted(check: b.Checker, co: s2.Co) -> int | None:
    basis = aif.MovesBasis(co)
    aif.register_basis(KIND, basis)
    maker = co.role("buyer")
    w, pid, rec = aif.recommended_run(co, "sourcing", maker)
    if not check(bool(rec) and pid is not None, f"the demo run made no recommendation and proposal ({w.outcome})"):
        return None
    basis.pin(str(rec["basis_hash"]))
    who, r = aif.approve_as_anyone(co, pid, skip=[maker])
    if not aif.check_status(check, r, 200, "approving the demo proposal (figures unchanged)"):
        return None
    admin = aif.admins(co)[0]
    got = co.call(admin, "GET", f"/api/admin/recommendations/{rec['id']}")
    if not aif.check_status(check, got, 200, "GET /api/admin/recommendations/{id} as an administrator"):
        return int(rec["id"])
    body = js(got)
    for field in ("capability", "kind", "business_key", "summary"):
        check(bool(body.get(field)), f"the record's {field} is empty")
    check(body.get("capability") == "sourcing" and body.get("kind") == KIND, f"capability {body.get('capability')!r}, "
          f"kind {body.get('kind')!r}")  # fmt: skip
    check(bool(body.get("evidence")) and bool(body.get("engine_figures")), "evidence or engine figures are empty")
    check(isinstance(body.get("rule_versions"), dict), f"rule_versions {body.get('rule_versions')!r} is not an object",
          "medium")  # fmt: skip
    check(body.get("run_id") == getattr(w.outcome, "run_id", None) and body.get("proposal_id") == pid,
          f"run {body.get('run_id')} / proposal {body.get('proposal_id')}, expected {w.outcome.run_id} / {pid}")  # fmt: skip
    check(body.get("status") == "accepted", f"the confirmed record is {body.get('status')!r}")
    approver = co.user_id(who) if who else None
    check(body.get("decided_by") == approver, f"decided_by {body.get('decided_by')!r}, the approver is {approver} "
          "(the confirmer is the person, never the agent)")  # fmt: skip
    check(bool(body.get("decided_at")), "decided_at is empty")
    check(body.get("directed_by") == co.user_id(maker), f"directed_by {body.get('directed_by')!r}, the agent was asked "
          f"by {co.user_id(maker)}", "medium")  # fmt: skip
    check(str(body.get("created_by", "")).startswith("agent:"), f"created_by {body.get('created_by')!r}", "medium")
    check(isinstance(body.get("proposal"), dict) and isinstance(body.get("run"), dict), "the detail lacks the "
          "proposal or the run", "medium")  # fmt: skip
    listed = js(co.call(admin, "GET", f"/api/admin/recommendations?business_key={body.get('business_key')}"))
    items = listed if isinstance(listed, list) else listed.get("recommendations") or listed.get("rows") or []
    check(any(x.get("id") == rec["id"] for x in items), "the list filtered by business key does not show the record")
    others = [x for x in items if x.get("business_key") != body.get("business_key")]
    check(not others, f"the list filtered by business key shows {len(others)} other record(s)", "medium")
    return int(rec["id"])


def _frozen(check: b.Checker, co: s2.Co, rec_id: int) -> None:
    from sqlalchemy import text

    for column, value in (("evidence", "'[]'::jsonb"), ("summary", "'changed by the eval'"),
                          ("engine_figures", "'{\"value\": 1}'::jsonb")):  # fmt: skip
        co.db.rollback()
        try:
            co.db.execute(text(f"update recommendation set {column} = {value} where id = :i"), {"i": rec_id})
            co.db.commit()
            changed = True
        except Exception:  # noqa: BLE001 - refused, as it must be
            co.db.rollback()
            changed = False
        check(not changed, f"the app's login changed a recommendation's {column}")
    status_ok = True
    try:
        co.db.execute(text("update recommendation set decision_note = decision_note where id = :i"), {"i": rec_id})
        co.db.rollback()
    except Exception:  # noqa: BLE001
        co.db.rollback()
        status_ok = False
    check(status_ok, "the frozen-evidence rule also refuses a change of the decision fields", "medium")


def _tenant_and_permission(check: b.Checker, co: s2.Co, bw: s2.Co, rec_id: int) -> None:
    theirs = {int(r[0]) for r in bw.q("select id from recommendation")} if "recommendation" in bw.tables() else set()
    if rec_id not in theirs:
        r = bw.call(aif.admins(bw)[0], "GET", f"/api/admin/recommendations/{rec_id}")
        aif.check_status(check, r, 404, "Brightwave's administrator reading Apex's recommendation")
    listed = js(bw.call(aif.admins(bw)[0], "GET", "/api/admin/recommendations?capability=sourcing"))
    items = listed if isinstance(listed, list) else listed.get("recommendations") or listed.get("rows") or []
    leaked = [x for x in items if x.get("kind") == KIND and x.get("id") not in theirs]
    check(not leaked, f"Brightwave's list shows {len(leaked)} of Apex's recommendations")
    requester = next(c for c, held in sorted(co.roles.items()) if held == {"requester"})
    r = co.call(requester, "GET", "/api/admin/recommendations")
    aif.check_status(check, r, 403, "a requester without audit.view listing recommendations")
    r = co.call(requester, "GET", f"/api/admin/recommendations/{rec_id}")
    aif.check_status(check, r, 403, "a requester without audit.view reading a recommendation")


def _maker(check: b.Checker, co: s2.Co) -> None:
    from p2p.agents import recommendations
    from p2p.platform import permissions

    maker = co.role("buyer")
    other = aif.admins(co)[0]
    rid = aif.recommend(co, "sourcing", "E99-MAKER-1", directed_by=co.user_id(maker))
    db = aif.new_session(co)
    try:
        try:
            recommendations.decide(db, rid, co.user_id(maker), accepted=True)
            db.commit()
            refused = None
        except permissions.PermissionDenied:
            db.rollback()
            refused = "PermissionDenied"
        except Exception as exc:  # noqa: BLE001 - refused, but not as a permission error
            db.rollback()
            refused = type(exc).__name__
        check(refused == "PermissionDenied", f"the person who directed the agent accepting its recommendation: "
              f"{refused or 'allowed'}, expected a permission error (403)")  # fmt: skip
        recommendations.decide(db, rid, co.user_id(other), accepted=True, note="eval: another person")
        db.commit()
    finally:
        db.close()
    row = aif.rows(co, "select * from recommendation where id = :i", i=rid)[0]
    check(row.get("status") == "accepted" and row.get("decided_by") == co.user_id(other), f"another person's "
          f"acceptance left {row.get('status')!r} decided by {row.get('decided_by')!r}")  # fmt: skip
    db = aif.new_session(co)
    try:
        try:
            recommendations.decide(db, rid, co.user_id(other), accepted=False)
            db.commit()
            again = True
        except Exception:  # noqa: BLE001 - only `proposed` can be decided
            db.rollback()
            again = False
    finally:
        db.close()
    check(not again, "a decided recommendation was decided a second time")
    accepted = aif.audit_after(co, "recommendation.accept")
    check(any(str(rid) == str(a.get("entity_id")) for a in accepted), "no recommendation.accept audit row", "medium")
    # the maker's own approval of a proposal it directed, through the API
    basis = aif.MovesBasis(co)
    aif.register_basis(KIND, basis)
    fin = co.holders("finance")
    if fin:
        _w, pid, rec = aif.recommended_run(co, "sourcing", fin[0])
        if rec and pid is not None:
            basis.pin(str(rec["basis_hash"]))
            r = co.call(fin[0], "POST", f"/api/admin/proposals/{pid}/approve", {})
            aif.check_status(check, r, 403, "the person who asked the agent approving its proposal")
            check(aif.proposal(co, pid).get("status") == "pending", "the maker's refused approval changed the "
                  "proposal")  # fmt: skip
