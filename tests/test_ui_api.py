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
    assert (d["purchase_orders"], d["total_po_value"], d["spend_by_category"], d["blocked_invoices"]) == (0, "0", {}, 0)
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


def test_a_changed_setting_is_logged_with_who_changed_it_and_the_old_value_and_an_unchanged_one_is_not(
    env: Env,
) -> None:
    admin = env.h(env.admin, "Admin")

    client.post("/api/settings", json={"stale_requisition_days": "9"}, headers=admin)  # default is 3
    client.post("/api/settings", json={"stale_requisition_days": "9"}, headers=admin)  # the same again: no change
    client.post("/api/settings", json={"stale_requisition_days": "4", "auto_replenishment": "true"}, headers=admin)
    client.post("/api/settings", json={"stale_requisition_days": "abc"}, headers=admin)  # refused: no change

    env.db.expire_all()
    rows = env.db.execute(
        text(
            "select entity_id, actor_id, before->>'value', after->>'value' from audit_event "
            "where action = 'setting.change' order by id"
        )
    ).all()
    who = str(env.admin)
    assert rows == [
        ("stale_requisition_days", who, "3", "9"),
        ("stale_requisition_days", who, "9", "4"),
        ("auto_replenishment", who, "false", "true"),
    ]


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


def test_the_assistant_streams_its_reply_and_can_be_switched_off_for_a_company(env: Env) -> None:
    env.db.execute(
        text(
            "insert into app_setting (key, value) values ('assistant_enabled', 'false') on conflict (key) do update set value = 'false'"
        )
    )
    env.db.commit()

    r = client.post("/api/chat", json={"message": "hello"}, headers=env.h(env.admin, "Admin"))

    events = [json.loads(line[5:]) for line in r.text.splitlines() if line.startswith("data:")]
    assert [e["type"] for e in events] == ["session", "token", "suggestions", "done"]
    assert events[1]["content"] == "The assistant is switched off for this company."
    assert client.post("/api/chat", json={"message": "hello"}).status_code == 401
    empty = client.post("/api/chat", json={"message": "  "}, headers=env.h(env.admin, "Admin"))
    assert "Ask me about a request" in empty.text  # nothing asked: a hint, and no model is called


def test_the_vendor_pages_show_the_imported_suppliers_and_can_be_searched(env: Env) -> None:
    env.db.execute(text("insert into payment_terms (code, name, days) values ('N30', 'Net 30', 30)"))
    env.db.execute(text("insert into item_category (code, name) values ('PKG', 'Packaging')"))
    env.db.execute(
        text(
            "insert into supplier (code, legal_name, trade_name, status, msme_category, payment_terms_code) "
            "values ('S-1', 'Shree Packaging Pvt Ltd', 'Shree Pack', 'active', 'Small', 'N30'), "
            "('S-2', 'Draft Traders', null, 'draft', null, 'N30')"
        )
    )
    env.db.execute(text("insert into supplier_category (supplier_code, category_code) values ('S-1', 'PKG')"))
    env.db.execute(
        text(
            "insert into supplier_contact (supplier_code, name, email, is_primary) values ('S-1', 'Asha', 'asha@shree.example', true)"
        )
    )
    env.db.commit()
    h = env.h(env.admin, "Admin")

    listed = {v["id"]: v for v in client.get("/api/vendors", headers=h).json()}
    assert listed["S-1"]["name"] == "Shree Pack" and listed["S-1"]["email"] == "asha@shree.example"
    assert listed["S-1"]["category"] == "Packaging" and listed["S-1"]["active"] is True
    assert listed["S-2"]["under_review"] is True and listed["S-2"]["active"] is False

    detail = client.get("/api/vendors/S-1", headers=h).json()
    assert (
        detail["risk"]["risk_level"] == "medium" and "Shree Packaging" in detail["verification_note"]
    )  # no history: unknown, not low
    assert client.get("/api/vendors/nope", headers=h).status_code == 404

    found = client.get("/api/search?q=shree", headers=h).json()
    assert [f["id"] for f in found] == ["S-1"]
