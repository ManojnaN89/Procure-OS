import io
import json
import logging
import uuid
from collections.abc import Iterator
from decimal import Decimal
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session

from p2p.ai_steps.adapters import RawResponse
from p2p.ai_steps.gateway import Gateway
from p2p.main import app
from p2p.platform import proposals, routing, tenants, users
from p2p.platform.sod import Actor
from p2p.platform.tokens import create_access_token
from p2p.rules import engine as rules

client = TestClient(app)
PASSWORD = "correct horse battery"


class Env:
    def __init__(self) -> None:
        self.slug = "t" + uuid.uuid4().hex[:8]
        tenants.create_tenant(self.slug, load_reference=False)
        self.db = Session(routing.engine_for(self.slug))
        self.admin = self.user("Admin")
        self.buyer = self.user("Buyer")
        self.finance = self.user("Finance")
        users.set_limit(self.slug, self.finance, "payment_run", Decimal("1000000"))

    def user(self, role: str) -> int:
        return users.create_user(self.slug, f"{role}{uuid.uuid4().hex[:6]}@example.com", PASSWORD, role, role)

    def auth(self, uid: int, role: str) -> dict[str, str]:
        return {"Authorization": f"Bearer {create_access_token(uid, self.slug, role)}"}

    @property
    def as_admin(self) -> dict[str, str]:
        return self.auth(self.admin, "Admin")

    @property
    def as_buyer(self) -> dict[str, str]:
        return self.auth(self.buyer, "Buyer")

    @property
    def as_finance(self) -> dict[str, str]:
        return self.auth(self.finance, "Finance")


@pytest.fixture
def env(control_db: str) -> Iterator[Env]:
    e = Env()
    yield e
    e.db.close()
    routing.clear_engine_cache()


@proposals.executor("test.admin_release")
def _release(db: Session, payload: dict[str, Any], approver: Actor) -> dict[str, Any]:
    return {"released": payload["invoice"]}


def test_the_viewers_need_the_audit_permission(env: Env) -> None:
    for path in ("/api/admin/audit", "/api/admin/agent-runs", "/api/admin/model-calls", "/api/admin/rule-evaluations"):
        assert client.get(path, headers=env.as_admin).status_code == 200
        assert client.get(path, headers=env.as_buyer).status_code == 403
        assert client.get(path).status_code == 401


def test_an_agent_run_can_be_followed_step_by_step(env: Env) -> None:
    run_id = env.db.execute(
        text(
            "insert into agent_run (thread_id, agent, input, status) values ('t1', 'payments', 'Pay INV-1', 'waiting') returning id"
        )
    ).scalar_one()
    for kind, name in (
        ("model", None),
        ("tool", "lookup_invoice"),
        ("gate", "release_payment"),
        ("wait", "release_payment"),
    ):
        env.db.execute(
            text("insert into agent_step (run_id, kind, name, detail) values (:r, :k, :n, cast(:d as jsonb))"),
            {"r": run_id, "k": kind, "n": name, "d": json.dumps({"k": kind})},
        )
    env.db.commit()

    listing = client.get("/api/admin/agent-runs", headers=env.as_admin).json()
    detail = client.get(f"/api/admin/agent-runs/{run_id}", headers=env.as_admin).json()

    assert listing[0]["agent"] == "payments" and listing[0]["status"] == "waiting"
    assert [s["kind"] for s in detail["steps"]] == ["model", "tool", "gate", "wait"]
    assert client.get("/api/admin/agent-runs/99999", headers=env.as_admin).status_code == 404


def test_model_calls_are_listed_with_totals_and_can_be_filtered_by_step(env: Env) -> None:
    class Fake:
        def chat(
            self, model: str, messages: Any, *, temperature: float, max_tokens: int, json_output: bool
        ) -> RawResponse:
            return RawResponse("ok", 100, 25, "stop")

    gw = Gateway({"f": Fake()}, {"default": [("f", "m")], "other": [("f", "m")]}, sleep=lambda s: None)
    gw.complete("default", [{"role": "user", "content": "a"}], db=env.db, prompt_version="p1")
    gw.complete("other", [{"role": "user", "content": "b"}], db=env.db)

    both = client.get("/api/admin/model-calls", headers=env.as_admin).json()
    one = client.get("/api/admin/model-calls", params={"step": "other"}, headers=env.as_admin).json()

    assert both["totals"]["calls"] == 2 and both["totals"]["tokens_in"] == 200 and both["totals"]["tokens_out"] == 50
    assert [c["step"] for c in one["calls"]] == ["other"] and one["totals"]["calls"] == 1


def test_rule_evaluations_and_audit_can_be_searched(env: Env) -> None:
    from test_rules_engine import approval_table

    actor = Actor("user", str(env.admin))
    rules.save_draft(env.db, "approval", approval_table(), actor)
    rules.publish(env.db, "approval", actor)
    rules.evaluate(env.db, "approval", {"value": 60000}, actor, case_ref="REQ-5")

    evals = client.get("/api/admin/rule-evaluations", params={"key": "approval"}, headers=env.as_admin).json()
    audit = client.get(
        "/api/admin/audit", params={"entity": "rule_table", "action": "rule.publish"}, headers=env.as_admin
    ).json()

    assert evals[0]["output"] == {"approver": "dept_head"} and evals[0]["case_ref"] == "REQ-5"
    detail = client.get(f"/api/admin/rule-evaluations/{evals[0]['id']}", headers=env.as_admin).json()
    assert detail["trace"] and detail["version"] == 1  # the trace says which row of the table matched
    assert client.get("/api/admin/rule-evaluations/99999", headers=env.as_admin).status_code == 404
    assert client.get(f"/api/admin/rule-evaluations/{evals[0]['id']}", headers=env.as_buyer).status_code == 403
    assert [a["action"] for a in audit] == ["rule.publish"] and audit[0]["rule_version"] == "approval@1"


def _propose(env: Env) -> int:
    return proposals.propose(
        env.db,
        Actor("agent", "payments"),
        action="test.admin_release",
        summary="Release INV-1",
        payload={"invoice": "INV-1"},
        required_permission="payment.approve",
        document="payment_run",
        value=Decimal(50000),
    )


def test_a_person_sees_and_decides_only_proposals_they_are_allowed_to(env: Env) -> None:
    pid = _propose(env)

    assert [p["id"] for p in client.get("/api/admin/proposals", headers=env.as_finance).json()] == [pid]
    assert client.get("/api/admin/proposals", headers=env.as_buyer).json() == []
    assert client.post(f"/api/admin/proposals/{pid}/approve", json={}, headers=env.as_buyer).status_code == 403

    done = client.post(f"/api/admin/proposals/{pid}/approve", json={"note": "ok"}, headers=env.as_finance)
    assert done.status_code == 200 and done.json()["status"] == "approved"
    assert client.post(f"/api/admin/proposals/{pid}/approve", json={}, headers=env.as_finance).status_code == 409


def test_a_proposal_can_be_edited_or_rejected_through_the_api(env: Env) -> None:
    edited, rejected = _propose(env), _propose(env)

    r = client.post(
        f"/api/admin/proposals/{edited}/approve", json={"edited_payload": {"invoice": "INV-2"}}, headers=env.as_finance
    )
    assert r.json()["status"] == "edited"
    assert (
        client.post(f"/api/admin/proposals/{rejected}/reject", json={}, headers=env.as_finance).status_code == 409
    )  # no reason
    assert (
        client.post(
            f"/api/admin/proposals/{rejected}/reject", json={"reason": "wrong amount"}, headers=env.as_finance
        ).status_code
        == 200
    )


def test_decision_tables_can_be_edited_and_published_by_an_admin_only(env: Env) -> None:
    from test_rules_engine import approval_table

    body = {"jdm": approval_table(), "note": "first"}
    assert client.put("/api/admin/rules/approval/draft", json=body, headers=env.as_buyer).status_code == 403
    assert (
        client.put("/api/admin/rules/approval/draft", json={"jdm": {"nodes": "bad"}}, headers=env.as_admin).status_code
        == 422
    )
    assert client.put("/api/admin/rules/approval/draft", json=body, headers=env.as_admin).json() == {"version": 1}
    assert client.post("/api/admin/rules/approval/publish", headers=env.as_admin).json() == {"version": 1}

    assert client.get("/api/admin/rules", headers=env.as_admin).json() == [
        {"key": "approval", "published_version": 1, "draft_version": None}
    ]
    assert client.get("/api/admin/rules/approval", headers=env.as_admin).json()[0]["status"] == "published"
    assert (
        client.post("/api/admin/rules/approval/publish", headers=env.as_admin).status_code == 409
    )  # nothing left to publish


def test_an_admin_manages_users_roles_and_limits(env: Env) -> None:
    new = {
        "email": f"new{uuid.uuid4().hex[:6]}@example.com",
        "password": PASSWORD,
        "display_name": "New Buyer",
        "role": "Buyer",
    }
    assert client.post("/api/admin/users", json=new, headers=env.as_buyer).status_code == 403
    uid = client.post("/api/admin/users", json=new, headers=env.as_admin).json()["user_id"]
    assert client.post("/api/admin/users", json={**new, "password": "short"}, headers=env.as_admin).status_code == 422

    assert (
        client.post(
            f"/api/admin/users/{uid}/roles", json={"role": "Store", "location": "PLT-VAD"}, headers=env.as_admin
        ).status_code
        == 201
    )
    assert (
        client.post(
            f"/api/admin/users/{uid}/limits", json={"document": "po", "max_value": "250000"}, headers=env.as_admin
        ).status_code
        == 201
    )

    row = next(u for u in client.get("/api/admin/users", headers=env.as_admin).json() if u["user_id"] == uid)
    assert row["display_name"] == "New Buyer" and {r["role"] for r in row["roles"]} == {"Buyer", "Store"}
    audited = client.get(
        "/api/admin/audit", params={"entity": "user", "entity_id": str(uid)}, headers=env.as_admin
    ).json()
    assert {a["action"] for a in audited} == {"user.create", "role.grant", "limit.set"}
    assert all(a["actor_id"] == str(env.admin) for a in audited)  # the admin's name is on every change


def test_every_request_gets_an_id_and_one_json_log_line() -> None:
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    from p2p.platform.logs import JsonFormatter

    handler.setFormatter(JsonFormatter())
    logging.getLogger("p2p").addHandler(handler)
    try:
        r = client.get("/api/health", headers={"X-Request-ID": "abc123"})
    finally:
        logging.getLogger("p2p").removeHandler(handler)

    line = json.loads(stream.getvalue().strip().splitlines()[-1])
    assert r.headers["x-request-id"] == "abc123"
    assert (line["msg"], line["path"], line["status"], line["request_id"]) == ("request", "/api/health", 200, "abc123")
