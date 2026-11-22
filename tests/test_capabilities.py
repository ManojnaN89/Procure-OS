"""One switch per AI capability (task 11a.5, D49, D54): on needs two administrators and the D54 checks, off is at once
and hands every piece of open work to a person; a switched-off capability makes no model call."""

import uuid
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session

from p2p.agents import autonomy, capabilities, recommendations
from p2p.agents.runtime import AgentRuntime, Decision, Message
from p2p.agents.tools import Tool, ToolRegistry
from p2p.ai_steps.runner import AiStep, run_step
from p2p.main import app
from p2p.platform import proposals, routing, tenants, users, worker, workflows
from p2p.platform.sod import Actor
from p2p.platform.tokens import create_access_token

client = TestClient(app)
PASSWORD = "correct horse battery"
CHECKS = {
    "permission_tests": "tests/test_capabilities.py", "eval": "E101", "eval_threshold": "0.9", "eval_result": "0.95",
    "tracing": True, "switch_off_tested": True,
}  # fmt: skip


class Env:
    def __init__(self) -> None:
        self.slug = "t" + uuid.uuid4().hex[:8]
        tenants.create_tenant(self.slug, load_reference=False)
        self.db = Session(routing.engine_for(self.slug))
        sfx = uuid.uuid4().hex[:6]
        self.admin = users.create_user(self.slug, f"a{sfx}@x.example", PASSWORD, "Ada Admin", "Admin")
        self.admin2 = users.create_user(self.slug, f"b{sfx}@x.example", PASSWORD, "Bea Admin", "Admin")
        self.buyer = users.create_user(self.slug, f"c{sfx}@x.example", PASSWORD, "Cy Buyer", "Buyer")
        self.ap = users.create_user(self.slug, f"d{sfx}@x.example", PASSWORD, "Di AP", "AP")

    def h(self, user_id: int, role: str = "Admin", slug: str | None = None) -> dict[str, str]:
        return {"Authorization": f"Bearer {create_access_token(user_id, slug or self.slug, role)}"}

    def put(self, code: str, body: dict[str, Any], who: int | None = None) -> Any:
        return client.put(f"/api/setup/capabilities/{code}", json=body, headers=self.h(who or self.admin))

    def enabled(self, code: str) -> bool:
        self.db.rollback()
        return capabilities.is_on(self.db, code)


@pytest.fixture
def env(control_db: str) -> Iterator[Env]:
    e = Env()
    yield e
    e.db.close()
    routing.clear_engine_cache()


def test_seven_capabilities_are_listed_with_monitoring_off(env: Env) -> None:
    got = client.get("/api/setup/capabilities", headers=env.h(env.admin)).json()["capabilities"]

    assert {c["code"]: c["enabled"] for c in got} == {
        "intake": True, "planning": True, "sourcing": True, "execution": True, "invoice_ap": True,
        "monitoring": False, "assistant": True,
    }  # fmt: skip
    assert client.get("/api/setup/capabilities", headers=env.h(env.buyer, "Buyer")).status_code == 403


def test_switching_off_is_immediate_and_the_gate_names_the_capability(env: Env) -> None:
    autonomy.configure(env.db, Actor("user", str(env.admin)), "rfq.send", level="auto")
    r = env.put("sourcing", {"on": False, "reason": "incident"})

    assert r.status_code == 200 and r.json()["enabled"] is False and not env.enabled("sourcing")
    verdict = autonomy.gate(env.db, "rfq.send", value=None, confidence=1.0)
    assert not verdict.allowed and "Reads quotes, prepares RFQs capability is switched off" in verdict.reason
    assert env.put("sourcing", {"on": False, "reason": "again"}).status_code == 409
    assert env.put("sourcing", {"on": False}).status_code == 422
    assert env.db.execute(text("select count(*) from audit_event where action = 'capability.off'")).scalar() == 1


def test_switching_on_waits_for_a_second_administrator_with_the_checks(env: Env) -> None:
    env.put("invoice_ap", {"on": False, "reason": "incident"})

    for bad in ({**CHECKS, "eval": " "}, {**CHECKS, "tracing": False}, {**CHECKS, "eval_result": "0.8"}):
        assert env.put("invoice_ap", {"on": True, "reason": "fixed", "checks": bad}).status_code == 422
    assert env.put("nonsense", {"on": True, "reason": "x", "checks": CHECKS}).status_code == 404
    assert env.put("invoice_ap", {"on": True, "reason": "x", "checks": CHECKS}, env.buyer).status_code == 403
    asked = env.put("invoice_ap", {"on": True, "reason": "fixed", "checks": CHECKS})
    assert asked.status_code == 202 and not env.enabled("invoice_ap")
    assert env.put("invoice_ap", {"on": True, "reason": "again", "checks": CHECKS}).status_code == 409

    pid = asked.json()["proposal_id"]
    own = client.post(f"/api/admin/proposals/{pid}/approve", json={}, headers=env.h(env.admin))
    assert own.status_code == 403 and not env.enabled("invoice_ap")
    ok = client.post(f"/api/admin/proposals/{pid}/approve", json={}, headers=env.h(env.admin2))
    assert ok.status_code == 200 and env.enabled("invoice_ap")
    env.db.rollback()
    saved = env.db.execute(text("select checks from ai_capability where code = 'invoice_ap'")).scalar()
    assert saved["eval"] == "E101" and saved["tracing"] is True
    history = env.db.execute(
        text(
            "select count(*) from setup_change where area = 'capability' and key = 'invoice_ap' and by_user <> 'migration'"
        )
    ).scalar()
    assert history == 2  # off, then on


def test_another_company_cannot_see_or_switch_this_one(env: Env) -> None:
    other = Env()
    try:
        r = client.put(
            "/api/setup/capabilities/assistant", json={"on": False, "reason": "x"}, headers=other.h(other.admin)
        )
        assert r.status_code == 200 and env.enabled("assistant") and not other.enabled("assistant")
    finally:
        other.db.close()


class Write(BaseModel):
    ref: str


def _registry() -> ToolRegistry:
    r = ToolRegistry()
    r.register(
        Tool("sourcing_write", "Write", Write, lambda db, p, a: {"ok": True}, writes=True, decision_type="rfq.award",
             required_permission="rfq.create", business_key=lambda p: str(p.ref))  # type: ignore[attr-defined]
    )  # fmt: skip
    return r


def _decide(*decisions: Decision) -> Any:
    queue = list(decisions)

    def decide(messages: list[Message], specs: list[dict[str, Any]]) -> Decision:
        return queue.pop(0)

    return decide


def test_switching_off_hands_every_piece_of_open_work_to_a_person(env: Env) -> None:
    db = env.db
    worker.enqueue(env.slug, "read_supplier_reply", {"email_id": 999}, key="email:999")
    workflows.join_or_start(db, "rfq.draft", "REQ-1", capability="sourcing")
    loose = recommendations.record(
        db, capability="sourcing", kind="rfq.draft", business_key="REQ-2", summary="Ask three",
        evidence=[{"source": "tool", "ref": "x", "value": 1}], engine_figures={"total": "1"},
        actor=Actor("agent", "sourcing"),
    )  # fmt: skip
    db.commit()
    rt = AgentRuntime(env.slug, db, _registry(), _decide(Decision(tool="sourcing_write", args={"ref": "RFQ-9"})),
                      agent="sourcing", capability="sourcing")  # fmt: skip
    thread = "t-" + uuid.uuid4().hex[:8]
    waiting = rt.start("award", thread_id=thread)
    assert waiting.status == "waiting"

    r = env.put("sourcing", {"on": False, "reason": "incident"})

    assert r.json()["handed_over"] == {"tasks": 3, "jobs": 1, "runs": 1, "workflows": 1, "recommendations": 1}
    db.rollback()
    tasks = db.execute(text("select assignee, payload->>'kind' from task where kind = 'ai_handover' order by id")).all()
    assert len(tasks) == 3 and {t[0] for t in tasks} == {env.buyer}
    assert db.execute(text("select status from agent_run where thread_id = :t"), {"t": thread}).scalar() == "failed"
    assert (
        db.execute(text("select status from proposal where id = :p"), {"p": waiting.proposal_id}).scalar() == "pending"
    )
    assert db.execute(text("select status from recommendation where id = :i"), {"i": loose}).scalar() == "handed_over"
    assert db.execute(text("select status from workflow")).scalar() == "handed_over"
    with tenants.control_engine().connect() as c:
        job = c.execute(
            text("select status, last_error from job where tenant_slug = :t and kind = 'read_supplier_reply'"),
            {"t": env.slug},
        ).one()
    assert job[0] == "failed" and "switched off" in job[1]
    rows = client.get("/api/exceptions?kind=ai_handover", headers=env.h(env.buyer, "Buyer")).json()["rows"]
    assert len(rows) == 3

    proposals.approve(db, int(waiting.proposal_id or 0), env.buyer)  # the person still decides; the agent stops
    with pytest.raises(proposals.ProposalError, match="switched off"):
        rt.resume(thread, int(waiting.proposal_id or 0))


def test_a_reading_of_a_switched_off_capability_asks_no_model_and_goes_to_a_person(env: Env) -> None:
    class Never:
        def extract(self, *a: Any, **kw: Any) -> Any:
            raise AssertionError("no model may be asked")

    class Reading(BaseModel):
        total: str

    env.put("invoice_ap", {"on": False, "reason": "incident"})
    step = AiStep("invoice_reading", "v1", "sys", "{source}", Reading)
    got = run_step(env.db, env.slug, step, "text", gateway=Never(), review_assignee=env.ap, entity="invoice",  # type: ignore[arg-type]
                   entity_id="INV-1")  # fmt: skip

    assert got.status == "needs_person" and "switched off" in got.failures[0] and got.task_id is not None
