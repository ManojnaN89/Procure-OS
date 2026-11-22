"""Maker-checker on the company's controls (D-22): a change one administrator makes to a policy, a rule, automation, a
role or a limit waits until a different administrator approves it. The proposer can never approve it, nothing changes
before the approval, a rejection keeps the old value, stopping automation is immediate while starting it again waits,
and the history and the audit log say who proposed, who approved and what changed."""

from datetime import timedelta

from fastapi.testclient import TestClient
from sqlalchemy import text
from test_approvals import PASSWORD, db  # noqa: F401 - the fixture
from test_company_setup import Setup, s  # noqa: F401 - the fixture

from p2p.agents import autonomy
from p2p.main import app
from p2p.platform import users

client = TestClient(app)


def _stored(s: Setup, key: str) -> str | None:
    s.db.expire_all()
    return s.db.execute(text("select value from org_setting where key = :k"), {"k": key}).scalar()


def _approve(s: Setup, pid: int, headers: dict[str, str]) -> object:
    return client.post(f"/api/admin/proposals/{pid}/approve", json={}, headers=headers)


def _audit(s: Setup, entity_id: str) -> list[tuple[str, str]]:
    s.db.expire_all()
    rows = s.db.execute(
        text("select action, actor_id from audit_event where entity_id = :e order by id"), {"e": entity_id}
    )
    return [(r[0], r[1]) for r in rows]


def test_a_policy_change_waits_and_the_proposer_cannot_approve_it(s: Setup) -> None:  # noqa: F811
    r = client.put("/api/setup/policies/budget_policy", json={"value": "block", "reason": "Tighter"}, headers=s.a)

    assert r.status_code == 202 and r.json()["status"] == "waiting"
    assert (r.json()["old"], r.json()["new"]) == ({"value": "warn"}, {"value": "block"})
    pid = r.json()["proposal_id"]
    assert _stored(s, "budget_policy") is None and s.changes() == []  # nothing applied yet
    (waiting,) = client.get("/api/setup/pending", headers=s.a2).json()
    assert (waiting["proposal_id"], waiting["old"], waiting["new"], waiting["reason"]) == (
        pid,
        {"value": "warn"},
        {"value": "block"},
        "Tighter",
    )
    assert waiting["by_name"] == "An Admin" and waiting["mine"] is False
    assert client.get("/api/setup/pending", headers=s.a).json()[0]["mine"] is True

    own = _approve(s, pid, s.a)

    assert own.status_code == 403  # the proposer never approves
    assert _stored(s, "budget_policy") is None and s.changes() == []
    assert client.get("/api/setup/pending", headers=s.a2).json()[0]["proposal_id"] == pid  # still waiting


def test_another_administrator_approves_and_the_change_applies_with_history_and_audit(s: Setup) -> None:  # noqa: F811
    pid = client.put(
        "/api/setup/policies/budget_policy", json={"value": "block", "reason": "Tighter"}, headers=s.a
    ).json()["proposal_id"]

    done = _approve(s, pid, s.a2)

    assert done.status_code == 200 and done.json()["execution"] == "ok"  # type: ignore[attr-defined]
    assert _stored(s, "budget_policy") == "block"
    (change,) = s.changes("policy", "budget_policy")
    assert change["by_user"] == str(s.admin) and change["reason"] == "Tighter"  # the change is the proposer's
    assert (change["old_value"], change["new_value"]) == ({"value": "warn"}, {"value": "block"})
    assert _audit(s, str(pid)) == [
        ("proposal.create", str(s.admin)),
        ("proposal.approved", str(s.admin2)),
        ("proposal.executed_ok", str(s.admin2)),
    ]
    assert ("setup.policy", str(s.admin)) in _audit(s, "budget_policy")
    assert client.get("/api/setup/pending", headers=s.a2).json() == []


def test_a_rejected_change_keeps_the_old_value(s: Setup) -> None:  # noqa: F811
    pid = client.put("/api/setup/tolerances/price_pct", json={"value": "9", "reason": "Looser"}, headers=s.a).json()[
        "proposal_id"
    ]
    before = s.db.execute(
        text("select value from tolerance where key = 'price_pct' and category_code = 'ANY'")
    ).scalar()

    r = client.post(f"/api/admin/proposals/{pid}/reject", json={"reason": "Too loose"}, headers=s.a2)

    assert r.status_code == 200
    s.db.expire_all()
    after = s.db.execute(text("select value from tolerance where key = 'price_pct' and category_code = 'ANY'")).scalar()
    assert after == before and s.changes() == []
    assert _approve(s, pid, s.a2).status_code == 409  # decided: it can never run later  # type: ignore[attr-defined]


def test_one_waiting_tolerance_change_holds_the_whole_set(s: Setup) -> None:  # noqa: F811
    # owner decision 10 (22 Nov 2026): the tolerances are one setting
    first = client.put("/api/setup/tolerances/price_pct", json={"value": "9", "reason": "Looser"}, headers=s.a)

    other = client.put("/api/setup/tolerances/qty_pct", json={"value": "4", "reason": "Looser"}, headers=s.a2)

    assert first.status_code == 202 and other.status_code == 409
    assert "a change to the tolerances is already waiting" in other.json()["detail"]


def test_several_tolerance_values_apply_together_when_another_admin_approves(s: Setup) -> None:  # noqa: F811
    values = [{"key": "price_pct", "value": "9"}, {"key": "qty_pct", "value": "4"}]
    r = client.put("/api/setup/tolerances", json={"values": values, "reason": "Season"}, headers=s.a)
    assert r.status_code == 202
    s.db.expire_all()
    versions = s.db.execute(text("select count(*) from tolerance_version")).scalar_one()

    assert _approve(s, r.json()["proposal_id"], s.a2).status_code == 200  # type: ignore[attr-defined]

    s.db.expire_all()
    latest = s.db.execute(text("select max(version) from tolerance_version")).scalar_one()
    got = dict(
        s.db.execute(
            text("select key, value from tolerance where version = :v and key in ('price_pct', 'qty_pct')"),
            {"v": latest},
        ).all()
    )
    assert s.db.execute(text("select count(*) from tolerance_version")).scalar_one() == versions + 1
    assert {k: float(v) for k, v in got.items()} == {"price_pct": 9.0, "qty_pct": 4.0}
    assert {c["key"] for c in s.changes("tolerance")} == {"price_pct:ANY", "qty_pct:ANY"}


def test_stopping_automation_is_immediate_and_starting_it_again_waits_for_a_second_admin(s: Setup) -> None:  # noqa: F811
    on = client.put("/api/setup/automation-kill-switch", json={"on": True, "reason": "Wrong emails"}, headers=s.a)

    assert on.status_code == 200 and on.json() == {"kill_switch": True}
    assert autonomy.gate(s.db, "rfq.send", value=None, confidence=1.0).allowed is False

    off = client.put("/api/setup/automation-kill-switch", json={"on": False, "reason": "Fixed"}, headers=s.a)

    assert off.status_code == 202
    s.db.expire_all()
    assert s.db.execute(text("select kill_switch from autonomy_global where id = 1")).scalar_one() is True
    assert _approve(s, off.json()["proposal_id"], s.a).status_code == 403
    assert _approve(s, off.json()["proposal_id"], s.a2).status_code == 200
    s.db.expire_all()
    assert s.db.execute(text("select kill_switch from autonomy_global where id = 1")).scalar_one() is False
    assert [c["new_value"] for c in s.changes("automation", "kill_switch")] == [{"on": True}, {"on": False}]


def test_rules_automation_roles_and_limits_all_wait_for_approval(s: Setup) -> None:  # noqa: F811
    writes = [
        ("put", "/api/setup/automation/rfq.award", {"level": "auto", "reason": "Clear winners"}),
        ("put", "/api/setup/roles/Buyer", {"permissions": ["rfq.send"], "reason": "Narrower"}),
        ("put", f"/api/setup/people/{s.approver}/limits", {"document": "po", "max_value": "5", "reason": "Up"}),
        ("post", f"/api/setup/people/{s.buyer}/roles", {"role": "Store", "reason": "Cover"}),
        ("put", "/api/setup/approvers/owner", {"source": "requester_manager", "reason": "New role"}),
        ("put", "/api/setup/departments/PUR/head", {"person_code": "U2", "reason": "New head"}),
        ("post", "/api/setup/touchless", {"price_pct": "1", "effective_from": "2030-01-01", "reason": "Tighter"}),
    ]
    for verb, path, body in writes:
        r = getattr(client, verb)(path, json=body, headers=s.a)
        assert r.status_code == 202, (path, r.text)

    assert s.changes() == []
    assert len(client.get("/api/setup/pending", headers=s.a2).json()) == len(writes)
    s.db.expire_all()
    assert s.db.execute(text("select count(*) from autonomy_setting where decision_type = 'rfq.award'")).scalar() == 0
    assert s.db.execute(text("select count(*) from user_limit where user_id = :u"), {"u": s.approver}).scalar() == 0


def test_nobody_approves_a_change_to_their_own_roles(s: Setup) -> None:  # noqa: F811
    r = client.post(f"/api/setup/people/{s.admin2}/roles", json={"role": "Store", "reason": "Cover"}, headers=s.a)
    pid = r.json()["proposal_id"]

    assert _approve(s, pid, s.a2).status_code == 403  # the person it is for
    third = users.create_user(s.slug, f"admin3{s.db.info['sfx']}@x.example", PASSWORD, "Third Admin", "Admin")
    assert _approve(s, pid, s.h(third, "Admin")).status_code == 200
    assert [c["new_value"]["role"] for c in s.changes("role_grant", str(s.admin2))] == ["Store"]


def test_a_change_that_no_longer_fits_when_approved_does_not_apply_and_says_why(s: Setup) -> None:  # noqa: F811
    pid = client.put(
        "/api/setup/policies/budget_policy", json={"value": "block", "reason": "Tighter"}, headers=s.a
    ).json()["proposal_id"]
    s.db.execute(text("insert into org_setting (key, value) values ('budget_policy', 'block')"))  # set meanwhile
    s.db.commit()

    late = _approve(s, pid, s.a2)

    assert late.json()["execution"] == "failed" and "already the value" in late.json()["result"]["error"]  # type: ignore[attr-defined]
    assert s.changes("policy", "budget_policy") == []


def test_a_second_change_to_a_setting_that_already_waits_is_refused_at_once(s: Setup) -> None:  # noqa: F811
    body = {"value": "block", "reason": "Tighter"}
    first = client.put("/api/setup/policies/budget_policy", json=body, headers=s.a).json()["proposal_id"]

    second = client.put("/api/setup/policies/budget_policy", json={**body, "value": "warn"}, headers=s.a2)

    assert second.status_code == 409 and f"already waiting for approval (proposal {first})" in second.json()["detail"]
    assert [p["proposal_id"] for p in client.get("/api/setup/pending", headers=s.a).json()] == [first]
    other = client.put(
        "/api/setup/policies/default_rfq_response_days", json={"value": "7", "reason": "Longer"}, headers=s.a2
    )
    assert other.status_code == 202, other.text  # another setting is not held up
    assert _approve(s, first, s.a2).status_code == 200
    again = client.put("/api/setup/policies/budget_policy", json={**body, "value": "warn"}, headers=s.a2)
    assert again.status_code == 202, again.text  # once decided, the setting can change again


def test_a_change_not_approved_within_seven_days_expires_and_nothing_applies(s: Setup) -> None:  # noqa: F811
    pid = client.put(
        "/api/setup/policies/budget_policy", json={"value": "block", "reason": "Tighter"}, headers=s.a
    ).json()["proposal_id"]
    s.db.expire_all()
    made, expires = s.db.execute(text("select created_at, expires_at from proposal where id = :i"), {"i": pid}).one()
    assert abs(expires - made - timedelta(days=7)) < timedelta(minutes=1)
    s.db.execute(text("update proposal set expires_at = now() - interval '1 minute' where id = :i"), {"i": pid})
    s.db.commit()  # seven days have gone by

    (shown,) = client.get("/api/setup/pending", headers=s.a2).json()
    late = _approve(s, pid, s.a2)

    assert shown["proposal_id"] == pid and shown["expired"] is True and shown["new"] == {"value": "block"}
    assert late.status_code == 409 and "already expired" in late.json()["detail"]  # type: ignore[attr-defined]
    assert _stored(s, "budget_policy") is None and s.changes() == []
    assert "proposal.expired" in [action for action, _ in _audit(s, str(pid))]
    again = client.put("/api/setup/policies/budget_policy", json={"value": "block", "reason": "Again"}, headers=s.a)
    assert again.status_code == 202  # an expired change does not hold the setting


def test_deactivating_a_person_waits_for_a_second_admin(s: Setup) -> None:  # noqa: F811
    email = f"buyer{s.db.info['sfx']}@x.example"
    r = client.post(f"/api/setup/people/{s.buyer}/active", json={"active": False, "reason": "Left"}, headers=s.a)

    assert r.status_code == 202 and (r.json()["old"], r.json()["new"]) == ({"active": True}, {"active": False})
    assert client.post("/api/auth/login", json={"email": email, "password": PASSWORD}).status_code == 200  # not yet
    assert s.changes() == []
    assert _approve(s, r.json()["proposal_id"], s.a).status_code == 403  # not by the proposer

    assert _approve(s, r.json()["proposal_id"], s.a2).json()["execution"] == "ok"  # type: ignore[attr-defined]

    assert client.post("/api/auth/login", json={"email": email, "password": PASSWORD}).status_code in (401, 403)
    (change,) = s.changes("person", str(s.buyer))
    assert (change["by_user"], change["new_value"]) == (str(s.admin), {"active": False})
    again = client.post(f"/api/setup/people/{s.buyer}/active", json={"active": False, "reason": "x"}, headers=s.a)
    assert again.status_code == 409  # already inactive
    own = client.post(f"/api/setup/people/{s.admin}/active", json={"active": False, "reason": "x"}, headers=s.a)
    assert own.status_code == 409 and "yourself" in own.json()["detail"]


def test_nobody_approves_their_own_deactivation(s: Setup) -> None:  # noqa: F811
    off = client.post(f"/api/setup/people/{s.admin2}/active", json={"active": False, "reason": "x"}, headers=s.a)

    assert _approve(s, off.json()["proposal_id"], s.a2).status_code == 403
    s.db.expire_all()
    status = s.db.execute(text("select status from proposal where id = :i"), {"i": off.json()["proposal_id"]})
    assert status.scalar() == "pending"


def test_changing_a_persons_manager_waits_for_a_second_admin(s: Setup) -> None:  # noqa: F811
    r = client.put(f"/api/setup/people/{s.keeper}/manager", json={"manager_code": "U5", "reason": "Moved"}, headers=s.a)

    def manager() -> str | None:
        s.db.expire_all()
        return s.db.execute(text("select manager_code from org_user where code = 'U4'")).scalar_one()

    assert r.status_code == 202 and r.json()["new"] == {"manager_code": "U5"}
    assert manager() != "U5" and s.changes() == []

    assert _approve(s, r.json()["proposal_id"], s.a2).json()["execution"] == "ok"  # type: ignore[attr-defined]

    assert manager() == "U5"
    assert [c["new_value"] for c in s.changes("manager", "U4")] == [{"manager_code": "U5"}]
    bad = client.put(f"/api/setup/people/{s.keeper}/manager", json={"manager_code": "U4", "reason": "x"}, headers=s.a)
    assert bad.status_code == 422  # still checked when proposed


def test_the_history_shows_who_approved_each_change(s: Setup) -> None:  # noqa: F811
    pid = client.put(
        "/api/setup/policies/budget_policy", json={"value": "block", "reason": "Tighter"}, headers=s.a
    ).json()["proposal_id"]
    _approve(s, pid, s.a2)
    client.put("/api/setup/automation-kill-switch", json={"on": True, "reason": "Stop"}, headers=s.a)  # at once

    stop, policy = client.get("/api/setup/changes", headers=s.a).json()

    assert (policy["area"], policy["by_name"], policy["approved_by_name"]) == ("policy", "An Admin", "Second Admin")
    assert policy["approved_by"] == str(s.admin2)
    assert (stop["key"], stop["by_name"], stop["approved_by_name"]) == ("kill_switch", "An Admin", None)


def test_with_one_administrator_a_change_waits_and_nonsense_is_still_refused_at_once(s: Setup) -> None:  # noqa: F811
    bad = client.put("/api/setup/policies/budget_policy", json={"value": "maybe", "reason": "x"}, headers=s.a)
    assert bad.status_code == 422  # checked when proposed, not only when approved

    r = client.put("/api/setup/policies/budget_policy", json={"value": "block", "reason": "x"}, headers=s.a)

    assert r.status_code == 202 and _stored(s, "budget_policy") is None


def test_a_new_administrator_is_never_made_in_one_step(s: Setup) -> None:  # noqa: F811
    body = {"email": "twin@x.example", "password": PASSWORD, "display_name": "Twin", "role": "Admin", "reason": "help"}

    r = client.post("/api/setup/people", json=body, headers=s.a)

    assert r.status_code == 403 and "another administrator" in r.json()["detail"]
    assert s.changes() == []


def test_services_used_by_the_demo_reset_still_apply_at_once(s: Setup) -> None:  # noqa: F811
    """The demo reset loads settings and automation through the services as the system, not through the screen."""
    from p2p.platform import audit

    autonomy.configure(s.db, audit.SYSTEM, "po.issue", level="auto")

    s.db.expire_all()
    assert s.db.execute(text("select level from autonomy_setting where decision_type = 'po.issue'")).scalar() == "auto"
    assert client.get("/api/setup/pending", headers=s.a).json() == []


def test_the_worker_expires_a_waiting_control_change_after_seven_days_with_nobody_opening_the_list(
    s: Setup,  # noqa: F811
) -> None:
    from datetime import UTC, datetime

    from p2p.platform import setting_changes, worker

    r = client.put("/api/setup/policies/budget_policy", json={"value": "block", "reason": "Tighter"}, headers=s.a)
    pid = r.json()["proposal_id"]
    s.db.expire_all()
    with worker.tenants.control_engine().connect() as c:  # the job waits in the queue, for this company
        run_at = c.execute(
            text("select run_at from job where tenant_slug = :t and kind = :k and payload->>'proposal_id' = :p"),
            {"t": s.slug, "k": setting_changes.EXPIRE_JOB, "p": str(pid)},
        ).scalar_one()
    assert abs(run_at - datetime.now(UTC) - setting_changes.EXPIRES_AFTER) < timedelta(minutes=1)

    worker.HANDLERS[setting_changes.EXPIRE_JOB](
        worker.JobContext(id=-1, tenant=s.slug, payload={"proposal_id": pid}, now=run_at + timedelta(seconds=1))
    )

    s.db.expire_all()
    assert s.db.execute(text("select status from proposal where id = :i"), {"i": pid}).scalar_one() == "expired"
    assert _stored(s, "budget_policy") is None  # nothing was applied
