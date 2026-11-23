"""E101: the capability switch (task 11a.5, D54, D-22), written without the implementation.

Independence: written from the AI foundation spec (2.1 tenant 0104, 2.6, OP-6, OP-11, OP-16) only. Every model is
scripted (`aif.no_live_models`), and the eval counts its calls.

Scenarios, on the main demo company (Apex):
1. `GET /api/setup/capabilities` lists the seven capabilities, `monitoring` off and the other six on (OP-6), each with
   its decision rows; the kill switch is listed apart.
2. Administrator 1 switches `invoice_ap` off: 200 at once (no waiting change), the table says off. An invoice mailed
   to the invoice mailbox is then routed, its reading job hands it over: one open `ai_handover` task for a holder of
   AP, and no model call.
3. Administrator 1 asks to switch it on with the D54 checks: 202 with a proposal; administrator 1 approving it is
   refused and it stays off; administrator 2 approves: on, with its checks saved, a `setup_change` row (area
   `capability`) and a `capability.on` audit row.
4. Refusals: empty check text, `tracing` false and an `eval_result` below its threshold give 422; no reason 422; an
   unknown code 404; the buyer 403; a second switch-on while one waits 409; switching off what is off 409.
5. The gate names the capability: with `sourcing` off, `autonomy.gate('rfq.award')` refuses with a reason naming it; on
   again (two administrators) the reason no longer does.
6. The CLI: `capability list` names all seven; `capability off apex assistant --reason` switches it off at once
   (actor `service:cli`); the assistant then answers without a model call.
7. The kill switch on: every capability stays on; an automatic decision (`po.issue`, automatic for Apex) is refused.
Mutations caught: switch-on without a second administrator (scenario 3); switch-off waiting for approval (scenario 2);
the gate ignoring the capability (scenario 5).
"""

from decimal import Decimal
from typing import Any

import aif
import block1 as b
import block2 as s2
import env
from block1 import js
from lib import Outcome

ID = "E101"
TITLE = "Each AI capability is switched off at once by one administrator and on only by two, with the D54 checks"
REQUIREMENT = (
    "Task 11a.5 (spec 2.6): seven capabilities, monitoring off at first; off is immediate and hands its work to a "
    "person; on needs the checks and a second administrator; the gate and every job of a capability refuse while it "
    "is off and say why; the kill switch stays separate"
)
PREFIX = "e101"
LABEL = {"sourcing": "reads quotes, prepares rfqs"}


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
        _listing(check, co)
        _invoice_off(check, co, model)
        _invoice_on(check, co)
        _refusals(check, co)
        _gate(check, co)
        _cli(check, co, model)
        _kill_switch(check, co)
    out.metrics.update({"checks": check.done, "failed": len(check.failed)})
    out.notes += [
        (
            "Spec gap: the status for the proposer approving their own switch-on is not named (existing proposal "
            "segregation); any refusal (not 200) is accepted if the capability stays off."
        ),
        (
            "Spec gap: owner roles are written as names ('AP', 'Purchase Manager'); the eval accepts a holder of the role "
            "or a person with those words in their designation."
        ),
    ]
    return out


def _listing(check: b.Checker, co: s2.Co) -> None:
    status, caps, body = aif.listing(co)
    check(status == 200, f"GET /api/setup/capabilities answered {status}")
    check(set(caps) == set(aif.CAPABILITIES), f"listed capabilities {sorted(caps)}, expected the seven")
    for code, row in caps.items():
        want = code != "monitoring"
        check(row.get("enabled") is want, f"{code}: enabled {row.get('enabled')!r} at first, expected {want} (OP-6)")
    decisions = {str(d.get("decision_type")) for d in caps.get("sourcing", {}).get("decisions") or []}
    check(
        {"rfq.send", "rfq.award", "quote.read"} <= decisions, f"sourcing lists decisions {sorted(decisions)}", "medium"
    )
    check(isinstance(body.get("kill_switch"), bool), "the listing has no kill_switch flag", "medium")
    seeded = aif.rows(co, "select * from ai_capability")
    check(all(r.get("changed_by") for r in seeded), "an ai_capability row has no changed_by", "medium")


def _invoice_off(check: b.Checker, co: s2.Co, model: aif.Model) -> None:
    r = aif.switch_off(co, "invoice_ap", "eval: stop invoice reading")
    aif.check_status(check, r, 200, "administrator 1 switching invoice_ap off")
    check(aif.enabled(co).get("invoice_ap") is False, "invoice_ap is not off right after the switch-off (it must not "
          "wait for a second administrator)")  # fmt: skip
    check(not js(r).get("proposal_id"), "switching off made a proposal to wait on", "medium")
    box = "invoices@apexpumps-eval.example"
    aif.set_org(co, "invoice_receipt_mailbox", box)
    calls0, model_calls0 = model.calls, aif.count(co, "select count(*) from model_call")
    tasks0 = {t["id"] for t in aif.tasks(co)}
    email_id = aif.ingest(co, aif.raw_mail(box, "Invoice INV-E101-1", "Please find our invoice attached.",
                                           attachments=[("INV-E101-1.pdf", "application/pdf", aif.PDF)]))  # fmt: skip
    check(email_id is not None, "the invoice mail was not stored")
    aif.run_jobs(co.slug, ["event_route"], now=aif.later(0.2))
    aif.run_jobs(co.slug, ["read_invoice_email"], now=aif.later(0.3))
    check(model.calls == calls0 and aif.count(co, "select count(*) from model_call") == model_calls0,
          "an invoice was read by a model while invoice_ap was off")  # fmt: skip
    new = [t for t in aif.tasks(co) if t["id"] not in tasks0]
    check(len(new) == 1, f"{len(new)} new ai_handover task(s) for the invoice mail, expected 1")
    for t in new:
        check(aif.login_active(co, t["assignee"]), f"hand-over task {t['id']} is assigned to {t['assignee']}, not an "
              "active login of the company")  # fmt: skip
        check(aif.holds(co, t["assignee"], aif.OWNERS["invoice_ap"]), f"hand-over task {t['id']} went to "
              f"{co.code_of(t['assignee'])}, not to AP", "medium")  # fmt: skip
        check((t["payload"] or {}).get("capability") == "invoice_ap", f"task payload {t['payload']}", "medium")


def _invoice_on(check: b.Checker, co: s2.Co) -> None:
    a1, a2 = aif.admins(co)
    audit0 = co.last_audit_id()
    asked = aif.ask_on(co, "invoice_ap", by=a1)
    if not aif.check_status(check, asked, 202, "administrator 1 asking to switch invoice_ap on"):
        return
    pid = js(asked).get("proposal_id")
    check(aif.enabled(co).get("invoice_ap") is False, "invoice_ap went on before a second administrator approved")
    _status, caps, _ = aif.listing(co)
    check(caps.get("invoice_ap", {}).get("waiting") == pid, f"the listing shows waiting "
          f"{caps.get('invoice_ap', {}).get('waiting')!r}, expected proposal {pid}", "medium")  # fmt: skip
    r = co.call(a1, "POST", f"/api/admin/proposals/{pid}/approve", {})
    check(r.status_code != 200, "administrator 1 approved their own switch-on")
    check(aif.enabled(co).get("invoice_ap") is False, "invoice_ap is on after its proposer approved it")
    r = co.call(a2, "POST", f"/api/admin/proposals/{pid}/approve", {})
    aif.check_status(check, r, 200, "administrator 2 approving invoice_ap on")
    row = aif.rows(co, "select * from ai_capability where code = 'invoice_ap'")[0]
    check(row.get("enabled") is True, "invoice_ap is still off after the second administrator approved")
    saved = row.get("checks") or {}
    check(all(str(saved.get(k)) == str(v) for k, v in aif.CHECKS.items()), f"the saved checks {saved} are not the ones "
          "given", "medium")  # fmt: skip
    changes = aif.count(co, "select count(*) from setup_change where area = 'capability' and key = 'invoice_ap'")
    check(changes >= 2, f"{changes} setup_change row(s) for invoice_ap (off, then on), expected 2 or more")
    check(len(aif.audit_after(co, "capability.on", audit0)) == 1, "no single capability.on audit row")


def _refusals(check: b.Checker, co: s2.Co) -> None:
    r = aif.switch_off(co, "monitoring", "eval")
    aif.check_status(check, r, 409, "switching off monitoring, which is off")
    bad = [("an empty check text", {**aif.CHECKS, "permission_tests": ""}),
           ("tracing false", {**aif.CHECKS, "tracing": False}),
           ("switch_off_tested false", {**aif.CHECKS, "switch_off_tested": False}),
           ("an eval result below its threshold", {**aif.CHECKS, "eval_threshold": "0.9", "eval_result": "0.85"})]  # fmt: skip
    for what, checks in bad:
        r = aif.ask_on(co, "monitoring", checks)
        aif.check_status(check, r, 422, f"switching monitoring on with {what}")
    r = aif.ask_on(co, "monitoring", reason="")
    aif.check_status(check, r, 422, "switching monitoring on with no reason")
    r = aif.switch_off(co, "assistant", reason="")
    aif.check_status(check, r, 422, "switching assistant off with no reason")
    r = aif.ask_on(co, "telepathy")
    aif.check_status(check, r, 404, "an unknown capability")
    r = aif.ask_on(co, "monitoring", by=co.role("buyer"))
    aif.check_status(check, r, 403, "the buyer switching a capability on")
    r = aif.switch_off(co, "sourcing", by=co.role("buyer"))
    aif.check_status(check, r, 403, "the buyer switching a capability off")
    first = aif.ask_on(co, "monitoring")
    aif.check_status(check, first, 202, "a valid switch-on of monitoring")
    again = aif.ask_on(co, "monitoring", by=aif.admins(co)[1])
    aif.check_status(check, again, 409, "a second switch-on while one waits")
    r = aif.ask_on(co, "intake")
    aif.check_status(check, r, 409, "switching on intake, which is on")
    pid = js(first).get("proposal_id")
    if pid:
        co.call(aif.admins(co)[1], "POST", f"/api/admin/proposals/{pid}/reject", {"reason": "eval: not now"})
    check(aif.enabled(co).get("monitoring") is False, "monitoring went on without an approval")


def _gate(check: b.Checker, co: s2.Co) -> None:
    from p2p.agents import autonomy

    r = aif.switch_off(co, "sourcing", "eval: the gate")
    aif.check_status(check, r, 200, "switching sourcing off")
    db = aif.new_session(co)
    try:
        g = autonomy.gate(db, "rfq.award", value=Decimal(1), confidence=1.0)  # rfq.send is always a person's (D42)
    finally:
        db.close()
    said = str(g.reason).lower()
    check(not g.allowed and ("sourcing" in said or LABEL["sourcing"] in said), f"with sourcing off the gate said "
          f"{g.allowed} / {g.reason!r}, expected a refusal naming the capability")  # fmt: skip
    asked, approved = aif.switch_on(co, "sourcing")
    check(approved is not None and approved.status_code == 200, "switching sourcing back on (two administrators) "
          f"answered {asked.status_code} / {getattr(approved, 'status_code', None)}")  # fmt: skip
    db = aif.new_session(co)
    try:
        g = autonomy.gate(db, "rfq.award", value=Decimal(1), confidence=1.0)  # rfq.send is always a person's (D42)
    finally:
        db.close()
    check("switched off" not in str(g.reason).lower(), f"sourcing on again, the gate still says {g.reason!r}")


def _cli(check: b.Checker, co: s2.Co, model: aif.Model) -> None:
    from p2p.agents import assistant

    code, text_ = b.cli("capability", "list", co.slug)
    check(code == 0 and all(c in text_ for c in aif.CAPABILITIES), f"`capability list` exited {code} and printed "
          f"{text_[:200]!r}", "medium")  # fmt: skip
    audit0 = co.last_audit_id()
    code, text_ = b.cli("capability", "off", co.slug, "assistant", "--reason", "eval: the CLI")
    check(code == 0 and aif.enabled(co).get("assistant") is False, f"`capability off` exited {code}: {text_[:200]!r}")
    rows = aif.audit_after(co, "capability.off", audit0)
    check(any(f"{r.get('actor_type')}:{r.get('actor_id')}" == "service:cli" for r in rows), f"the CLI's switch-off "
          f"audit rows name {[(r.get('actor_type'), r.get('actor_id')) for r in rows]}, expected service:cli",
          "medium")  # fmt: skip
    code, _ = b.cli("capability", "off", co.slug, "assistant")
    check(code != 0, "`capability off` without --reason succeeded", "medium")
    calls = model.calls
    model.queue = [{"tool": "find_requests", "args": {}, "confidence": 0.9}, {"final": "Nothing waits."}]
    db = aif.new_session(co)
    try:
        assistant.ask(db, co.slug, co.user_id(co.role("buyer")), "What is waiting?", aif.gateway(model))
    finally:
        db.close()
    model.queue = []
    check(model.calls == calls, f"the assistant called the model {model.calls - calls} time(s) while switched off")


def _kill_switch(check: b.Checker, co: s2.Co) -> None:
    from p2p.agents import autonomy

    before = aif.enabled(co)
    r = co.call(aif.admins(co)[0], "PUT", "/api/setup/automation-kill-switch", {"on": True, "reason": "eval"})
    aif.check_status(check, r, 200, "the kill switch on")
    check(aif.enabled(co) == before, "the kill switch changed the capability switches")
    _status, _, body = aif.listing(co)
    check(body.get("kill_switch") is True, "the capability listing does not show the kill switch on", "medium")
    db = aif.new_session(co)
    try:
        g = autonomy.gate(db, "po.issue", value=Decimal(1), confidence=1.0)
    finally:
        db.close()
    check(not g.allowed, f"with the kill switch on an automatic decision was allowed ({g.reason!r})")
