import json
import uuid
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session
from starlette.websockets import WebSocketDisconnect

from p2p.main import app
from p2p.platform import routing, tenants, users
from p2p.platform.tokens import create_access_token

client = TestClient(app)
PASSWORD = "correct horse battery"


class Env:
    def __init__(self) -> None:
        self.slug = "t" + uuid.uuid4().hex[:8]
        tenants.create_tenant(self.slug, load_reference=False)
        self.db = Session(routing.engine_for(self.slug))
        self.admin = users.create_user(self.slug, f"a{uuid.uuid4().hex[:6]}@example.com", PASSWORD, "Admin", "Admin")
        self.other = users.create_user(self.slug, f"r{uuid.uuid4().hex[:6]}@example.com", PASSWORD, "Req", "Requester")

    def token(self, uid: int, role: str) -> str:
        return create_access_token(uid, self.slug, role)

    def h(self, uid: int, role: str) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.token(uid, role)}"}

    def alert(self, title: str, *, user: int | None, severity: str = "low") -> int:
        return int(
            self.db.execute(
                text(
                    "insert into notification (user_id, kind, title, severity) values (:u, 'price_flag', :t, :s) returning id"
                ),
                {"u": user, "t": title, "s": severity},
            ).scalar_one()
        )


@pytest.fixture
def env(control_db: str) -> Iterator[Env]:
    e = Env()
    yield e
    e.db.commit()
    e.db.close()
    routing.clear_engine_cache()


def test_every_data_endpoint_needs_a_login(control_db: str) -> None:
    for path in (
        "/api/dashboard",
        "/api/settings",
        "/api/notifications",
        "/api/trail",
        "/api/briefing",
        "/api/vendors",
    ):
        assert client.get(path).status_code == 401, path


def test_the_pages_without_a_service_yet_answer_with_empty_data_in_the_expected_shape(env: Env) -> None:
    h = env.h(env.admin, "Admin")
    d = client.get("/api/dashboard", headers=h).json()
    assert d["headline"]["total_spend_trend"] == [0] * 6 and d["inline_stats"]["contract_coverage_pct"] == 0
    for name in ("rfqs", "requisitions", "vendors", "purchase-orders", "payments", "contracts"):
        assert client.get(f"/api/{name}", headers=h).json() == []
        assert client.get(f"/api/{name}/1", headers=h).status_code == 404
    assert client.get("/api/search?q=abc", headers=h).json() == []


def test_settings_have_defaults_can_be_changed_by_an_admin_and_are_validated(env: Env) -> None:
    admin, other = env.h(env.admin, "Admin"), env.h(env.other, "Requester")
    assert client.get("/api/settings", headers=other).json()["currency_preset"] == "INR"

    assert client.post("/api/settings", json={"exception_flag_threshold_pct": 25}, headers=other).status_code == 403
    assert client.post("/api/settings", json={"exception_flag_threshold_pct": 25}, headers=admin).json() == {
        "updated": ["exception_flag_threshold_pct"]
    }
    assert client.get("/api/settings", headers=other).json()["exception_flag_threshold_pct"] == "25"
    assert "Unknown" in client.post("/api/settings", json={"nonsense": 1}, headers=admin).json()["error"]
    assert (
        "currency_preset"
        in client.post("/api/settings", json={"currency_preset": "XYZ"}, headers=admin).json()["error"]
    )
    assert (
        "number" in client.post("/api/settings", json={"stale_requisition_days": "abc"}, headers=admin).json()["error"]
    )


def test_alerts_for_one_person_and_for_everyone_are_listed_read_and_resolved(env: Env) -> None:
    mine = env.alert("Only for the admin", user=env.admin, severity="medium")
    everyone = env.alert("For the whole company", user=None, severity="high")
    env.db.commit()
    admin, other = env.h(env.admin, "Admin"), env.h(env.other, "Requester")

    assert {n["message"] for n in client.get("/api/notifications", headers=admin).json()} == {
        "Only for the admin",
        "For the whole company",
    }
    assert [n["message"] for n in client.get("/api/notifications", headers=other).json()] == ["For the whole company"]
    assert client.get("/api/notifications/unread-count", headers=admin).json() == {"count": 2}

    assert client.post(f"/api/notifications/{mine}/read", headers=admin).json() == {"id": mine, "read": True}
    assert client.get("/api/notifications/unread-count", headers=admin).json() == {"count": 1}
    assert "error" in client.post(f"/api/notifications/{mine}/read", headers=other).json()  # not theirs

    assert client.post(f"/api/notifications/{everyone}/resolve", headers=other).json() == {
        "id": everyone,
        "resolved": True,
    }
    assert [n["message"] for n in client.get("/api/notifications", headers=admin).json()] == ["Only for the admin"]


def test_the_briefing_puts_the_most_serious_alert_first(env: Env) -> None:
    env.alert("minor", user=None, severity="low")
    env.alert("serious", user=None, severity="high")
    env.alert("middling", user=env.admin, severity="medium")
    env.db.commit()
    b = client.get("/api/briefing", headers=env.h(env.admin, "Admin")).json()

    assert b["top_priority_signal"]["message"] == "serious"
    assert [s["message"] for s in b["signals"]] == ["serious", "middling", "minor"]
    assert b["signal_counts"] == {"high": 1, "medium": 1, "low": 1} and b["total_unresolved"] == 3


def test_the_trail_shows_what_an_agent_did_in_the_shape_the_interface_expects(env: Env) -> None:
    run = env.db.execute(
        text("insert into agent_run (thread_id, agent, input) values ('t-1', 'payments', 'Pay') returning id")
    ).scalar_one()
    steps: list[tuple[str, str | None, dict[str, Any]]] = [
        ("model", None, {"final": None}),
        ("tool", "lookup_invoice", {"args": {"invoice": "INV-9"}, "result": {"status": "approved"}}),
        ("gate", "release_payment", {"allowed": False, "reason": "set to propose"}),
        ("wait", "release_payment", {"proposal_id": 7}),
    ]
    for kind, name, detail in steps:
        env.db.execute(
            text("insert into agent_step (run_id, kind, name, detail) values (:r, :k, :n, cast(:d as jsonb))"),
            {"r": run, "k": kind, "n": name, "d": json.dumps(detail)},
        )
    env.db.commit()

    rows = client.get("/api/trail", headers=env.h(env.admin, "Admin")).json()

    assert [r["skill"] for r in reversed(rows)] == [
        "lookup_invoice",
        "autonomy_gate (release_payment)",
        "awaiting_approval (release_payment)",
    ]
    first = rows[-1]
    assert (first["agent"], first["session_id"], first["args"], first["result"]) == (
        "payments",
        "t-1",
        {"invoice": "INV-9"},
        {"status": "approved"},
    )
    assert client.get("/api/trail?agent=nobody", headers=env.h(env.admin, "Admin")).json() == []


def test_a_new_alert_is_pushed_to_an_open_connection_and_only_to_the_right_person(env: Env) -> None:
    with client.websocket_connect(f"/ws/notifications?token={env.token(env.other, 'Requester')}") as ws:
        env.alert("secret of the admin", user=env.admin)
        env.alert("for everyone", user=None, severity="high")
        env.db.commit()
        got = ws.receive_json()  # the pusher checks every two seconds

    assert got["message"] == "for everyone" and got["severity"] == "high" and got["read"] is False


def test_a_connection_without_a_valid_token_is_refused(env: Env) -> None:
    for token in ("", "garbage"):
        with pytest.raises(WebSocketDisconnect), client.websocket_connect(f"/ws/notifications?token={token}") as ws:
            ws.receive_json()


def test_the_assistant_answers_over_server_sent_events_until_it_is_connected(env: Env) -> None:
    r = client.post("/api/chat", json={"message": "hello"}, headers=env.h(env.admin, "Admin"))
    events = [json.loads(line[5:]) for line in r.text.splitlines() if line.startswith("data:")]
    assert [e["type"] for e in events] == ["session", "token", "done"]
