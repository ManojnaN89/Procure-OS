"""Company setup: only an Admin can change it, every change needs a reason and is written to the history, the rules that
protect the platform (Admin stays a setup role, payment runs stay with a person) cannot be switched off, and what is
changed really changes what the platform does."""

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
from fastapi.testclient import TestClient
from setup_changes import change
from sqlalchemy import text
from sqlalchemy.orm import Session
from test_approvals import PASSWORD, db  # noqa: F401 - the fixture

from p2p.agents import autonomy
from p2p.approvals import rules as approval_rules
from p2p.main import app
from p2p.platform import permissions, sod, users
from p2p.platform.setup_api import ADMIN_REQUIRED, PERMISSIONS, policy_value_on
from p2p.platform.tokens import create_access_token
from p2p.receiving import goods

client = TestClient(app)
D = Decimal
TODAY = date(2026, 11, 6)  # the date the tests pin the business day to


class Setup:
    """A company with an administrator, a buyer, an approver and the people of the approvals test organisation."""

    def __init__(self, session: Session) -> None:
        self.db = session
        slug, sfx = session.info["slug"], session.info["sfx"]
        self.slug = slug
        self.admin = users.create_user(slug, f"admin{sfx}@x.example", PASSWORD, "An Admin", "Admin")
        self.admin2 = users.create_user(slug, f"admin2{sfx}@x.example", PASSWORD, "Second Admin", "Admin")
        self.buyer = users.create_user(slug, f"buyer{sfx}@x.example", PASSWORD, "A Buyer", "Buyer")
        self.approver = users.create_user(slug, f"approver{sfx}@x.example", PASSWORD, "An Approver", "Approver")
        self.director = users.create_user(
            slug, f"dir{sfx}@x.example", PASSWORD, "Dir Rajesh", "Approver"
        )  # org person U1
        self.keeper = users.create_user(
            slug, f"keeper{sfx}@x.example", PASSWORD, "Keeper Mahesh", "Store"
        )  # org person U4

    def h(self, user_id: int, role: str) -> dict[str, str]:
        return {"Authorization": f"Bearer {create_access_token(user_id, self.slug, role)}"}

    @property
    def a(self) -> dict[str, str]:
        return self.h(self.admin, "Admin")

    @property
    def a2(self) -> dict[str, str]:
        return self.h(self.admin2, "Admin")

    # A change by the first administrator, approved by the second when it waits for approval (D-22).
    def put(self, path: str, **kw: Any) -> Any:
        return change(client, "put", path, self.a, self.a2, **kw)

    def post(self, path: str, **kw: Any) -> Any:
        return change(client, "post", path, self.a, self.a2, **kw)

    def delete(self, path: str, **kw: Any) -> Any:
        return change(client, "delete", path, self.a, self.a2, **kw)

    def changes(self, area: str | None = None, key: str | None = None) -> list[dict]:
        self.db.expire_all()
        rows = self.db.execute(
            text(
                "select area, key, old_value, new_value, reason, by_user from setup_change "
                "where (cast(:a as text) is null or area = :a) and (cast(:k as text) is null or key = :k) order by id"
            ),
            {"a": area, "k": key},
        ).mappings()
        return [dict(r) for r in rows]


@pytest.fixture
def s(db: Session) -> Setup:  # noqa: F811
    db.execute(text("insert into item_category (code, name) values ('C1', 'Spares'), ('PKG', 'Packaging')"))
    db.commit()
    return Setup(db)


# ---- who may change the setup ------------------------------------------------------------------

READS = [
    "/api/setup/people",
    "/api/setup/roles",
    "/api/setup/approvers",
    "/api/setup/approval-rules",
    "/api/setup/policies",
    "/api/setup/automation",
    "/api/setup/changes",
]


@pytest.mark.parametrize("path", READS)
def test_the_setup_screens_are_for_an_admin_only(s: Setup, path: str) -> None:
    assert client.get(path).status_code == 401
    for user_id, role in ((s.buyer, "Buyer"), (s.approver, "Approver"), (s.keeper, "Store")):
        assert client.get(path, headers=s.h(user_id, role)).status_code == 403, role
    assert client.get(path, headers=s.a).status_code == 200


def test_no_change_is_possible_without_the_admin_role_or_without_a_reason(s: Setup) -> None:
    buyer = s.h(s.buyer, "Buyer")
    attempts = [
        ("put", f"/api/setup/people/{s.buyer}/limits", {"document": "po", "max_value": "1", "reason": "x"}),
        ("put", "/api/setup/roles/Buyer", {"permissions": [], "reason": "x"}),
        ("put", "/api/setup/policies/budget_policy", {"value": "block", "reason": "x"}),
        ("put", "/api/setup/tolerances/price_pct", {"value": "1", "reason": "x"}),
        ("put", "/api/setup/automation/rfq.send", {"level": "auto", "reason": "x"}),
        ("put", "/api/setup/automation-kill-switch", {"on": True, "reason": "x"}),
        ("put", "/api/setup/approvers/manager", {"source": "requester_manager", "reason": "x"}),
    ]
    for verb, path, body in attempts:
        assert getattr(client, verb)(path, json=body, headers=buyer).status_code == 403, path
        no_reason = {k: v for k, v in body.items() if k != "reason"}
        r = getattr(client, verb)(path, json=no_reason, headers=s.a)
        assert r.status_code == 422, (path, r.text)
    assert s.changes() == []


# ---- people ------------------------------------------------------------------------------------


def test_a_person_is_added_with_a_role_and_the_history_says_who_and_why(s: Setup) -> None:
    r = client.post(
        "/api/setup/people",
        json={
            "email": "new@x.example",
            "password": PASSWORD,
            "display_name": "New Buyer",
            "role": "Buyer",
            "reason": "Joined purchasing",
        },
        headers=s.a,
    )

    assert r.status_code == 201
    listed = {p["user_id"]: p for p in client.get("/api/setup/people", headers=s.a).json()["people"]}
    assert listed[r.json()["user_id"]]["roles"][0]["role"] == "Buyer" and listed[r.json()["user_id"]]["active"] is True
    (change,) = s.changes("person")
    assert change["reason"] == "Joined purchasing" and change["by_user"] == str(s.admin) and change["old_value"] is None
    assert (
        client.post(
            "/api/setup/people",
            json={"email": "new@x.example", "password": PASSWORD, "display_name": "x", "reason": "r"},
            headers=s.a,
        ).status_code
        == 422
    )
    assert (
        client.post(
            "/api/setup/people",
            json={"email": "z@x.example", "password": "short", "display_name": "x", "reason": "r"},
            headers=s.a,
        ).status_code
        == 422
    )


def test_a_deactivated_person_cannot_log_in_and_nobody_can_deactivate_themselves(s: Setup) -> None:
    email = f"buyer{s.db.info['sfx']}@x.example"
    assert client.post("/api/auth/login", json={"email": email, "password": PASSWORD}).status_code == 200

    off = s.post(f"/api/setup/people/{s.buyer}/active", json={"active": False, "reason": "Left the company"})

    assert off.status_code == 201  # applied once the second administrator approved it
    assert client.post("/api/auth/login", json={"email": email, "password": PASSWORD}).status_code in (401, 403)
    assert s.post(f"/api/setup/people/{s.admin}/active", json={"active": False, "reason": "x"}).status_code == 409
    assert s.post(f"/api/setup/people/{s.buyer}/active", json={"active": True, "reason": "Returned"}).status_code == 201
    assert client.post("/api/auth/login", json={"email": email, "password": PASSWORD}).status_code == 200
    assert s.post("/api/setup/people/99999/active", json={"active": False, "reason": "x"}).status_code == 404
    assert [c["new_value"] for c in s.changes("person", str(s.buyer))] == [{"active": False}, {"active": True}]


def test_roles_are_given_and_taken_back_and_the_last_admin_cannot_lose_the_role(s: Setup) -> None:
    body = {"role": "Store", "location": "WH-BLR", "reason": "Covers the warehouse"}
    assert s.post(f"/api/setup/people/{s.buyer}/roles", json=body).status_code == 201
    assert s.post(f"/api/setup/people/{s.buyer}/roles", json=body).status_code == 409  # already has it
    assert s.post(f"/api/setup/people/{s.buyer}/roles", json={**body, "role": "Chief"}).status_code == 422
    people = {p["user_id"]: p for p in client.get("/api/setup/people", headers=s.a).json()["people"]}
    row_id = next(r["id"] for r in people[s.buyer]["roles"] if r["role"] == "Store")
    assert s.delete(f"/api/setup/people/{s.buyer}/roles/{row_id}").status_code == 422  # no reason
    assert s.delete(f"/api/setup/people/{s.buyer}/roles/{row_id}?reason=Moved").status_code == 200

    own = next(r["id"] for r in people[s.admin]["roles"] if r["role"] == "Admin")
    assert s.delete(f"/api/setup/people/{s.admin}/roles/{own}?reason=x").status_code == 409  # not their own
    other = next(r["id"] for r in people[s.admin2]["roles"] if r["role"] == "Admin")
    assert s.delete(f"/api/setup/people/{s.admin2}/roles/{other}?reason=Step down").status_code == 200
    assert s.delete(f"/api/setup/people/{s.admin}/roles/{own}?reason=x").status_code == 409  # the last one
    assert [c["area"] for c in s.changes()] == ["role_grant", "role_revoke", "role_revoke"]


def test_an_approval_limit_is_replaced_not_added_to_and_can_be_removed(s: Setup) -> None:
    put = lambda value: s.put(  # noqa: E731
        f"/api/setup/people/{s.approver}/limits", json={"document": "po", "max_value": value, "reason": "Review"}
    )
    assert put("500000").status_code == 200 and put("750000").status_code == 200
    s.db.expire_all()
    assert s.db.execute(
        text("select max_value from user_limit where user_id = :u"), {"u": s.approver}
    ).scalars().all() == [D("750000")]
    assert permissions.within_limit(s.db, s.approver, "po", D("750000")) is True
    assert permissions.within_limit(s.db, s.approver, "po", D("750000.01")) is False
    assert [(c["old_value"], c["new_value"]) for c in s.changes("limit")] == [
        ({"max_value": None}, {"max_value": "500000"}),
        ({"max_value": "500000.00"}, {"max_value": "750000"}),
    ]
    assert (
        s.put(
            f"/api/setup/people/{s.approver}/limits", json={"document": "po", "max_value": "-1", "reason": "r"}
        ).status_code
        == 422
    )
    assert (
        s.put(
            f"/api/setup/people/{s.approver}/limits", json={"document": "nope", "max_value": "1", "reason": "r"}
        ).status_code
        == 422
    )
    people = {p["user_id"]: p for p in client.get("/api/setup/people", headers=s.a).json()["people"]}
    limit_id = people[s.approver]["limits"][0]["id"]
    assert s.delete(f"/api/setup/people/{s.approver}/limits/{limit_id}?reason=Left").status_code == 200
    assert permissions.within_limit(s.db, s.approver, "po", D("1")) is False  # no limit row: cannot approve


def test_a_persons_manager_is_set_checked_for_loops_and_follows_the_login_address(s: Setup) -> None:
    put = lambda user_id, manager: s.put(  # noqa: E731
        f"/api/setup/people/{user_id}/manager", json={"manager_code": manager, "reason": "Reorganised"}
    )
    assert put(s.keeper, "U5").status_code == 200  # the storekeeper now reports to the procurement head
    s.db.expire_all()
    assert s.db.execute(text("select manager_code from org_user where code = 'U4'")).scalar_one() == "U5"
    assert put(s.keeper, "NOBODY").status_code == 422
    assert put(s.keeper, "U4").status_code == 422  # their own manager
    s.db.execute(text("update org_user set manager_code = 'U4' where code = 'U5'"))
    s.db.commit()
    assert put(s.keeper, "U5").status_code == 422  # U5 reports to U4: a loop
    assert put(s.buyer, "U1").status_code == 409  # this login is not a person in the organisation
    assert [c["new_value"] for c in s.changes("manager")] == [{"manager_code": "U5"}]


# ---- what each role may do ---------------------------------------------------------------------


def test_every_permission_a_role_holds_can_be_set_on_the_roles_page(s: Setup) -> None:
    held = {r[0] for r in s.db.execute(text("select distinct action from role_permission"))}

    assert held <= set(PERMISSIONS), sorted(held - set(PERMISSIONS))


def test_the_permissions_of_a_role_are_changed_and_the_platform_follows(s: Setup) -> None:
    assert not permissions.can_anywhere(s.db, s.buyer, "supplier.activate")
    wanted = [
        p["action"]
        for p in client.get("/api/setup/roles", headers=s.a).json()["permissions"]
        if p["action"] == "supplier.activate"
    ]
    current = next(
        r["permissions"] for r in client.get("/api/setup/roles", headers=s.a).json()["roles"] if r["role"] == "Buyer"
    )

    r = s.put(
        "/api/setup/roles/Buyer",
        json={"permissions": [*current, *wanted], "reason": "Procurement head is also the buyer"},
    )

    assert r.status_code == 200 and r.json() == {"role": "Buyer", "added": ["supplier.activate"], "removed": []}
    assert permissions.can_anywhere(s.db, s.buyer, "supplier.activate")
    (change,) = s.changes("role_permission", "Buyer")
    assert "supplier.activate" in change["new_value"] and "supplier.activate" not in change["old_value"]
    again = s.put("/api/setup/roles/Buyer", json={"permissions": current, "reason": "Back again"})
    assert again.json()["removed"] == ["supplier.activate"] and not permissions.can_anywhere(
        s.db, s.buyer, "supplier.activate"
    )


def test_admin_stays_a_setup_role_and_keeps_what_it_needs(s: Setup) -> None:
    put = lambda role, perms: s.put(  # noqa: E731
        f"/api/setup/roles/{role}", json={"permissions": perms, "reason": "Change"}
    )  # noqa: E731
    required = sorted(ADMIN_REQUIRED)

    assert put("Admin", [*required, "po.approve"]).status_code == 422  # a business permission
    assert put("Admin", ["audit.view"]).status_code == 422  # would lock everyone out of the setup
    assert put("Admin", required).status_code == 200
    assert put("Buyer", ["admin.users"]).status_code == 422  # the setup belongs to Admin only
    assert put("Buyer", ["rfq.send", "invented.permission"]).status_code == 422
    assert put("Chief", []).status_code == 404
    got = {r["role"]: r["permissions"] for r in client.get("/api/setup/roles", headers=s.a).json()["roles"]}
    assert not any(a.startswith(("po.", "request.", "invoice.", "payment.")) for a in got["Admin"])


# ---- who approves -------------------------------------------------------------------------------


def test_the_role_to_person_mapping_is_listed_with_who_it_resolves_to_and_edited_with_checks(s: Setup) -> None:
    listed = client.get("/api/setup/approvers", headers=s.a).json()
    by_role = {r["role"]: r for r in listed["roles"]}
    assert by_role["director"]["resolves_to"] == "Whoever is Managing Director: Dir Rajesh"
    assert by_role["manager"]["resolves_to"] == "The requester's own manager"
    assert by_role["procurement_head"]["resolves_to"] == "The head of PUR: Procure Vikram"
    assert by_role["manager"]["in_use"] is True and "Dir Rajesh" in {p["name"] for p in listed["people"]}

    put = lambda role, body: s.put(f"/api/setup/approvers/{role}", json={"reason": "Change", **body})  # noqa: E731
    assert put("owner", {"source": "designation", "value": "Managing Director"}).status_code == 200
    assert put("owner", {"source": "designation", "value": "Chief Dreamer"}).status_code == 422
    assert put("owner", {"source": "department_head_of", "value": "NOPE"}).status_code == 422
    assert put("owner", {"source": "department_head_of"}).status_code == 422  # needs a value
    assert put("owner", {"source": "whoever"}).status_code == 422
    assert put("Bad Name", {"source": "requester_manager"}).status_code == 422
    assert put("auto", {"source": "requester_manager"}).status_code == 422
    assert put("owner", {"source": "department_head_of", "value": "FIN"}).status_code == 200
    assert [c["new_value"] for c in s.changes("approver_role", "owner")] == [
        {"source": "designation", "value": "Managing Director"},
        {"source": "department_head_of", "value": "FIN"},
    ]
    assert s.delete("/api/setup/approvers/manager?reason=x").status_code == 409  # a chain uses it
    assert s.delete("/api/setup/approvers/owner?reason=Not needed").status_code == 200
    assert s.delete("/api/setup/approvers/owner?reason=Not needed").status_code == 404


def test_changing_the_head_of_a_department_changes_who_the_chain_resolves_to(s: Setup) -> None:
    before = approval_rules.resolve_chain(s.db, ("procurement_head",), requester="U4", department="LOG", on_date=TODAY)
    assert [x.approver for x in before] == ["U5"]

    r = s.put("/api/setup/departments/PUR/head", json={"person_code": "U2", "reason": "New procurement head"})

    assert r.status_code == 200
    s.db.expire_all()
    after = approval_rules.resolve_chain(s.db, ("procurement_head",), requester="U4", department="LOG", on_date=TODAY)
    assert [x.approver for x in after] == ["U2"]
    assert s.put("/api/setup/departments/PUR/head", json={"person_code": "NOBODY", "reason": "x"}).status_code == 422
    assert s.put("/api/setup/departments/NOPE/head", json={"person_code": "U2", "reason": "x"}).status_code == 404
    assert s.changes("department_head")[0]["old_value"] == {"head_user": "U5"}


# ---- approval chains ---------------------------------------------------------------------------


def _rule(s: Setup, **body: object) -> object:
    base = {"effective_from": (TODAY + timedelta(days=1)).isoformat(), "reason": "Review of limits"}
    return s.post("/api/setup/approval-rules", json={**base, **body})


def test_a_new_version_of_a_rule_starts_tomorrow_and_yesterdays_decisions_keep_the_old_one(s: Setup) -> None:
    r = _rule(
        s,
        rule_code="REQ-02",
        document="request",
        min_value="15000.01",
        max_value="100000",
        spend_type="opex",
        approver_chain="manager>dept_head",
    )

    assert r.status_code == 201 and r.json() == {"rule_code": "REQ-02", "version": 3}  # type: ignore[attr-defined]
    s.db.expire_all()
    today = approval_rules.find_rule(s.db, "request", D("20000"), TODAY, spend_type="opex")
    tomorrow = approval_rules.find_rule(s.db, "request", D("20000"), TODAY + timedelta(days=1), spend_type="opex")
    assert (today.version, today.roles) == (2, ("manager",))  # a decision made today is reproducible
    assert (tomorrow.version, tomorrow.roles) == (3, ("manager", "dept_head"))
    assert (
        s.db.execute(
            text("select effective_to from approval_rule where rule_code = 'REQ-02' and version = 2")
        ).scalar_one()
        == TODAY
    )
    (change,) = s.changes("approval_rule")
    assert (
        change["key"] == "REQ-02:v3"
        and change["old_value"]["version"] == 2
        and change["new_value"]["chain"] == ["manager", "dept_head"]
    )


def test_a_rule_cannot_start_in_the_past_use_an_unknown_role_or_leave_a_gap_or_an_overlap(s: Setup) -> None:
    ok = {"rule_code": "REQ-02", "document": "request", "spend_type": "opex"}
    past = _rule(
        s,
        **ok,
        min_value="15000.01",
        max_value="100000",
        approver_chain="manager",
        effective_from=(TODAY - timedelta(days=1)).isoformat(),
    )
    assert past.status_code == 422 and "today or later" in past.text  # type: ignore[attr-defined]
    assert _rule(s, **ok, min_value="15000.01", max_value="100000", approver_chain="manager>wizard").status_code == 422
    assert _rule(s, **ok, min_value="15000.01", max_value="100000", approver_chain="manager>manager").status_code == 422
    assert _rule(s, **ok, min_value="100", max_value="50", approver_chain="manager").status_code == 422
    assert (
        _rule(
            s, rule_code="REQ-99", document="request", min_value="1", max_value="2", approver_chain="manager"
        ).status_code
        == 404
    )
    gap = _rule(
        s, **ok, min_value="15000.01", max_value="80000", approver_chain="manager"
    )  # 80,000.01 to 100,000 uncovered
    assert gap.status_code == 422 and "nothing covers" in gap.text  # type: ignore[attr-defined]
    overlap = _rule(s, **ok, min_value="15000.01", max_value="150000", approver_chain="manager")
    assert overlap.status_code == 422 and "overlap" in overlap.text  # type: ignore[attr-defined]
    s.db.expire_all()
    assert (
        s.db.execute(text("select count(*) from approval_rule where rule_code = 'REQ-02'")).scalar_one() == 2
    )  # untouched
    assert (
        s.db.execute(
            text("select effective_to from approval_rule where rule_code = 'REQ-02' and version = 2")
        ).scalar_one()
        is None
    )
    assert s.changes("approval_rule") == []


def test_a_new_rule_gets_the_next_code_and_the_screen_reports_problems_in_the_table(s: Setup) -> None:
    r = _rule(
        s,
        document="request",
        min_value="999999999.01",
        max_value="9999999999",
        spend_type="opex",
        approver_chain="manager>dept_head>director",
    )

    assert r.status_code == 201 and r.json()["rule_code"] == "REQ-06"  # type: ignore[attr-defined]
    listed = client.get("/api/setup/approval-rules", headers=s.a).json()
    assert listed["problems"] == [] and "director" in listed["roles"]
    in_force = {x["rule_code"] for x in listed["rules"] if x["in_force"]}
    assert {"REQ-01", "REQ-02", "REQ-03", "REQ-04"} <= in_force and "REQ-06" not in in_force  # starts tomorrow
    s.db.execute(text("delete from approval_rule where rule_code = 'REQ-03'"))  # someone edited the table by hand
    s.db.commit()
    assert any("nothing covers" in p for p in client.get("/api/setup/approval-rules", headers=s.a).json()["problems"])


# ---- policies and tolerances -------------------------------------------------------------------


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("budget_policy", "maybe"),
        ("po_send_requires_confirmation", "yes"),
        ("rfq_reminder_after_days", "-1"),
        ("rfq_reminder_after_days", "2.5"),
        ("rfq_reminder_after_days", "abc"),
        ("rfq_reminder_after_days", "NaN"),
        ("rfq_reminder_after_days", "31"),
        ("price_band_pct", "100.01"),
        ("default_rfq_response_days", "0"),
        ("rfq_max_reminders", "11"),
    ],
)
def test_a_policy_value_that_makes_no_sense_is_refused(s: Setup, key: str, value: str) -> None:
    r = s.put(f"/api/setup/policies/{key}", json={"value": value, "reason": "Trying"})

    assert r.status_code == 422, r.text
    assert s.changes("policy") == []


def test_a_policy_change_is_stored_logged_and_read_back_by_the_code_that_uses_it(s: Setup) -> None:
    listed = {p["key"]: p for p in client.get("/api/setup/policies", headers=s.a).json()["settings"]}
    assert listed["budget_policy"]["value"] == "warn" and listed["budget_policy"]["set"] is False  # the default
    put = lambda key, value, reason="Tighter control": s.put(  # noqa: E731
        f"/api/setup/policies/{key}", json={"value": value, "reason": reason}
    )  # noqa: E731

    assert put("budget_policy", "block").status_code == 200
    assert put("payment_run_window_days", "14").status_code == 200
    assert put("rfq_max_reminders", "3").status_code == 200  # held in the other settings table
    assert put("budget_policy", "block").status_code == 409  # nothing changes
    assert put("nonsense", "1").status_code == 404

    s.db.expire_all()
    assert s.db.execute(text("select value from org_setting where key = 'budget_policy'")).scalar_one() == "block"
    from p2p.approvals import budget

    assert budget._policy_setting(s.db) == "block"  # the budget check reads the stored value
    assert s.db.execute(text("select value from app_setting where key = 'rfq_max_reminders'")).scalar_one() == "3"
    (change,) = s.changes("policy", "budget_policy")
    assert (change["old_value"], change["new_value"], change["reason"], change["by_user"]) == (
        {"value": "warn"},
        {"value": "block"},
        "Tighter control",
        str(s.admin),
    )
    audited = s.db.execute(
        text(
            "select before->>'value', after->>'value', reason from audit_event where action = 'setup.policy' and entity_id = 'budget_policy'"
        )
    ).one()
    assert audited == ("warn", "block", "Tighter control")
    changes = client.get("/api/setup/changes?area=policy&key=budget_policy", headers=s.a).json()
    assert changes[0]["by_name"] == "An Admin" and changes[0]["reason"] == "Tighter control"


def test_the_order_terms_change_only_when_a_second_administrator_agrees_and_a_broken_template_is_refused(
    s: Setup,
) -> None:
    # given the order terms, a setting two administrators agree to change (D-22); the standard text until then
    listed = {p["key"]: p for p in client.get("/api/setup/policies", headers=s.a).json()["settings"]}
    assert listed["po_terms_template"]["kind"] == "text" and listed["po_terms_template"]["set"] is False
    path = "/api/setup/policies/po_terms_template"

    # a template that does not render, or reaches outside the sandbox, is refused before anyone is asked
    for broken in ("{{ unclosed", "{{ ''.__class__.__mro__ }}"):
        r = client.put(path, json={"value": broken, "reason": "New terms"}, headers=s.a)
        assert r.status_code == 422 and "the template does not work" in r.json()["detail"], r.text
    assert s.changes("policy", "po_terms_template") == []

    # one administrator's change waits for the other; nothing is stored until then
    waiting = client.put(
        path, json={"value": "Pay {{ payment_terms }} for {{ po_no }}.", "reason": "Legal"}, headers=s.a
    )
    assert waiting.status_code == 202 and waiting.json()["status"] == "waiting"
    s.db.expire_all()
    assert s.db.execute(text("select value from org_setting where key = 'po_terms_template'")).first() is None
    done = client.post(f"/api/admin/proposals/{waiting.json()['proposal_id']}/approve", json={}, headers=s.a2)
    assert done.status_code == 200 and done.json()["execution"] == "ok"

    # then the order document uses it, and the history says who and why
    s.db.expire_all()
    from p2p.orders import document  # noqa: PLC0415

    terms = document.terms_text(s.db, {"no": "PO-1", "payment_terms_code": "NET30", "delivery_terms": None})
    assert terms == "Pay NET30 for PO-1."
    (logged,) = s.changes("policy", "po_terms_template")
    assert (logged["new_value"], logged["reason"]) == ({"value": "Pay {{ payment_terms }} for {{ po_no }}.\n"}, "Legal")


def test_what_a_policy_was_on_any_day_can_be_read_back(s: Setup, owner_session: Any) -> None:
    put = lambda value: s.put(  # noqa: E731
        "/api/setup/policies/price_band_pct", json={"value": value, "reason": "Review"}
    )  # noqa: E731
    put("10")
    put("20")
    put("25")
    # the history is insert only (migration 0059): back-dating it needs the database owner to switch the triggers off
    with owner_session(s.slug) as owner:
        owner.execute(text("set local session_replication_role = replica"))
        for n, day in enumerate(
            (datetime(2026, 1, 1, tzinfo=UTC), datetime(2026, 6, 1, tzinfo=UTC), datetime(2026, 9, 1, tzinfo=UTC))
        ):
            owner.execute(
                text(
                    "update setup_change set at = :t where id = "
                    "(select id from setup_change order by id offset :n limit 1)"
                ),
                {"t": day, "n": n},
            )
        owner.commit()
    s.db.commit()

    at = lambda y, m, d: policy_value_on(s.db, "price_band_pct", datetime(y, m, d, tzinfo=UTC))  # noqa: E731
    assert (at(2026, 1, 1), at(2026, 3, 1), at(2026, 6, 1), at(2026, 7, 1), at(2026, 12, 31)) == (
        "10",
        "10",
        "20",
        "20",
        "25",
    )
    assert at(2025, 12, 31) == "15"  # before any change: what it was originally (the default)
    assert policy_value_on(s.db, "min_quotes_default", datetime(2026, 1, 1, tzinfo=UTC)) is None  # never changed


def test_a_tolerance_is_set_for_a_category_and_the_goods_receipt_uses_it(s: Setup) -> None:
    assert goods.over_pct(s.db, "PKG") == D(0)
    put = lambda category, value, key="receipt_over_pct": s.put(  # noqa: E731
        f"/api/setup/tolerances/{key}", json={"category": category, "value": value, "reason": "Packaging varies"}
    )

    assert put("PKG", "10").status_code == 200 and put("ANY", "5").status_code == 200
    assert put("PKG", "12").status_code == 200  # each change is a new version of the set (task 3.2)
    s.db.expire_all()
    assert goods.over_pct(s.db, "PKG") == D("12") and goods.over_pct(s.db, "C1") == D("5")
    assert (
        s.db.execute(
            text(
                "select count(*) from tolerance where key = 'receipt_over_pct' and category_code = 'PKG' "
                "and version = (select max(version) from tolerance_version)"
            )
        ).scalar_one()
        == 1
    )
    assert (
        put("NOPE", "1").status_code == 422
        and put("PKG", "101").status_code == 422
        and put("PKG", "-1").status_code == 422
    )
    assert put("PKG", "1", key="invented").status_code == 404
    assert [(c["key"], c["old_value"], c["new_value"]) for c in s.changes("tolerance")][-1] == (
        "receipt_over_pct:PKG",
        {"value": "10"},
        {"value": "12"},
    )
    shown = {t["key"]: t for t in client.get("/api/setup/policies", headers=s.a).json()["tolerances"]}
    assert {v["category"]: v["value"] for v in shown["receipt_over_pct"]["values"]} == {"PKG": "12", "ANY": "5"}


# ---- automation --------------------------------------------------------------------------------


def test_the_automation_table_lists_every_decision_and_locks_the_four_that_stay_with_a_person(s: Setup) -> None:
    got = client.get("/api/setup/automation", headers=s.a).json()
    rows = {r["decision_type"]: r for r in got["decisions"]}

    assert got["kill_switch"] is False
    assert {"rfq.send", "rfq.award", "quote.read", "po.send"} <= set(rows)
    for locked in ("payment_run.approve", "supplier.set_status", "invoice.release_block", "rfq.send"):
        assert (
            rows[locked]["locked"] is True
            and rows[locked]["levels"] == ["propose"]
            and rows[locked]["level"] == "propose"
        )
    assert rows["rfq.send"]["note"].startswith("D42: a buyer confirms every new RFQ")  # D42: never automatic
    assert rows["rfq.award"]["locked"] is False and rows["rfq.award"]["levels"] == ["off", "propose", "auto"]
    assert rows["po.send"]["level"] == "propose"  # a buyer sends orders until the company says otherwise


def test_a_decision_is_set_to_act_within_limits_and_the_gate_obeys_the_new_setting(s: Setup) -> None:
    gate = lambda value, conf: autonomy.gate(s.db, "rfq.award", value=D(value), confidence=conf)  # noqa: E731
    assert gate("1000", 1.0).allowed is False  # propose by default

    r = s.put(
        "/api/setup/automation/rfq.award",
        json={
            "level": "auto",
            "value_cap": "50000",
            "min_confidence": "0.95",
            "max_wrong_rate": "0.01",
            "reason": "Trusted after a month",
        },
    )

    assert r.status_code == 200
    s.db.expire_all()
    assert gate("50000", 0.95).allowed is True
    assert (
        gate("50000.01", 0.95).allowed is False and gate("1000", 0.94).allowed is False
    )  # the cap and the confidence bite
    row = next(
        x
        for x in client.get("/api/setup/automation", headers=s.a).json()["decisions"]
        if x["decision_type"] == "rfq.award"
    )
    assert (row["level"], row["value_cap"], row["min_confidence"]) == ("auto", "50000.00", "0.950")
    (change,) = s.changes("automation", "rfq.award")
    assert (
        change["reason"] == "Trusted after a month"
        and change["old_value"] is None
        and change["new_value"]["level"] == "auto"
    )
    back = s.put("/api/setup/automation/rfq.award", json={"level": "propose", "reason": "Two wrong awards"})
    assert back.status_code == 200 and not gate("1", 1.0).allowed
    assert s.changes("automation", "rfq.award")[-1]["old_value"]["level"] == "auto"


@pytest.mark.parametrize(
    "body",
    [
        {"level": "sometimes"},
        {"level": "auto", "value_cap": "-1"},
        {"level": "auto", "min_confidence": "1.5"},
        {"level": "auto", "max_wrong_rate": "-0.1"},
    ],
)
def test_a_nonsense_automation_setting_is_refused(s: Setup, body: dict) -> None:
    assert s.put("/api/setup/automation/rfq.award", json={**body, "reason": "Try"}).status_code == 422
    assert s.put("/api/setup/automation/nonsense", json={"level": "auto", "reason": "Try"}).status_code in (404, 422)
    assert s.changes("automation") == []


def test_payment_runs_vendor_status_releasing_a_block_and_sending_an_rfq_can_never_act_alone(s: Setup) -> None:
    for locked in ("payment_run.approve", "supplier.set_status", "invoice.release_block", "rfq.send"):
        r = s.put(f"/api/setup/automation/{locked}", json={"level": "auto", "reason": "Faster"})
        assert r.status_code == 422 and "always decided by a person" in r.text, locked
        with pytest.raises(ValueError, match="always decided by a person"):
            autonomy.configure(s.db, sod.user(s.admin), locked, level="auto")
        # even a row written straight into the table cannot make the gate say yes
        s.db.execute(
            text(
                "insert into autonomy_setting (decision_type, level, min_confidence, max_wrong_rate) values (:d, 'auto', 0, 1) on conflict (decision_type) do update set level = 'auto'"
            ),
            {"d": locked},
        )
        s.db.commit()
        assert autonomy.gate(s.db, locked, value=D(1), confidence=1.0).allowed is False, locked
    assert s.changes("automation") == []


def test_orders_are_sent_by_a_person_or_automatically_never_by_nobody(s: Setup) -> None:
    put = lambda level: s.put(  # noqa: E731
        "/api/setup/automation/po.send", json={"level": level, "reason": "Policy"}
    )  # noqa: E731

    assert put("off").status_code == 422
    assert put("auto").status_code == 200
    s.db.expire_all()
    assert (
        s.db.execute(text("select value from org_setting where key = 'po_send_requires_confirmation'")).scalar_one()
        == "false"
    )
    row = next(
        x
        for x in client.get("/api/setup/automation", headers=s.a).json()["decisions"]
        if x["decision_type"] == "po.send"
    )
    assert row["level"] == "auto" and row["levels"] == ["propose", "auto"]
    assert put("propose").status_code == 200
    assert [c["new_value"] for c in s.changes("policy", "po_send_requires_confirmation")] == [
        {"value": "false"},
        {"value": "true"},
    ]


def test_the_kill_switch_is_set_from_the_screen_stops_automatic_action_and_is_logged(s: Setup) -> None:
    s.put("/api/setup/automation/rfq.award", json={"level": "auto", "reason": "Go"})
    s.db.expire_all()
    assert autonomy.gate(s.db, "rfq.award", value=D(1), confidence=1.0).allowed is True

    on = s.put("/api/setup/automation-kill-switch", json={"on": True, "reason": "Wrong emails went out"})

    assert on.status_code == 200 and on.json() == {"kill_switch": True}
    s.db.expire_all()
    gate = autonomy.gate(s.db, "rfq.award", value=D(1), confidence=1.0)
    assert gate.allowed is False and "kill switch" in gate.reason
    assert client.get("/api/setup/automation", headers=s.a).json()["kill_switch"] is True
    assert s.put("/api/setup/automation-kill-switch", json={"on": False, "reason": "Fixed"}).status_code == 200
    assert [(c["old_value"], c["new_value"]) for c in s.changes("automation", "kill_switch")] == [
        ({"on": False}, {"on": True}),
        ({"on": True}, {"on": False}),
    ]


def test_the_history_can_be_filtered_and_every_change_is_also_in_the_audit_log(s: Setup) -> None:
    s.put("/api/setup/policies/budget_policy", json={"value": "block", "reason": "One"})
    s.put("/api/setup/policies/min_quotes_default", json={"value": "3", "reason": "Two"})
    s.put(f"/api/setup/people/{s.approver}/limits", json={"document": "po", "max_value": "1000", "reason": "Three"})

    everything = client.get("/api/setup/changes", headers=s.a).json()
    assert [c["reason"] for c in everything] == ["Three", "Two", "One"]  # newest first
    assert [c["key"] for c in client.get("/api/setup/changes?area=policy", headers=s.a).json()] == [
        "min_quotes_default",
        "budget_policy",
    ]
    assert [c["key"] for c in client.get("/api/setup/changes?key=budget_policy", headers=s.a).json()] == [
        "budget_policy"
    ]
    assert len(client.get("/api/setup/changes?limit=1", headers=s.a).json()) == 1
    audited = s.db.execute(text("select count(*) from audit_event where action like 'setup.%'")).scalar_one()
    assert audited == 3


def test_admin_can_see_the_flow_but_not_act_in_it(s: Setup) -> None:
    given_views = ["request.view", "invoice.view", "payment.view", "stock.view"]
    required = sorted(ADMIN_REQUIRED)

    assert (
        s.put("/api/setup/roles/Admin", json={"permissions": [*required, *given_views], "reason": "See"}).status_code
        == 200
    )
    assert (
        s.put(
            "/api/setup/roles/Admin", json={"permissions": [*required, "request.approve"], "reason": "Act"}
        ).status_code
        == 422
    )


def test_an_administrator_cannot_give_themselves_a_role_or_an_approval_limit(s: Setup) -> None:
    grant = s.post(f"/api/setup/people/{s.admin}/roles", json={"role": "Finance", "reason": "pay myself"})
    limit = s.put(
        f"/api/setup/people/{s.admin}/limits",
        json={"document": "payment_run", "max_value": "10000000", "reason": "pay myself"},
    )

    assert grant.status_code == 403 and "another administrator" in grant.json()["detail"]
    assert limit.status_code == 403
    assert s.changes() == []  # nothing was written
    assert (
        s.post(f"/api/setup/people/{s.buyer}/roles", json={"role": "Store", "reason": "cover"}).status_code == 201
    )  # another person, as before
