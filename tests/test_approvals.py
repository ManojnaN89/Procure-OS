"""Approval of requests by the rule tables: which rule applies (at every edge), who the chain turns into, and the life of
a request through submit, approve, reject and return."""

import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session
from test_replenishment import _company as replenishment_company  # the stock company from the replenishment tests

from p2p.approvals import rules, workflow
from p2p.main import app
from p2p.platform import permissions, routing, sod, tenants, users
from p2p.platform.tokens import create_access_token
from p2p.requests import replenishment

D = Decimal
client = TestClient(app)
TODAY = date(2026, 11, 6)
PASSWORD = "correct horse battery"

# the rules of the demo company, shortened: value band, document, category, department, spend type, chain, version, dates
RULES = [
    ("REQ-01", "request", "0.00", "10000.00", "ANY", "ANY", "opex", "auto", 1, "2024-11-01", "2026-03-31"),
    ("REQ-01", "request", "0.00", "15000.00", "ANY", "ANY", "opex", "auto", 2, "2026-04-01", None),
    ("REQ-02", "request", "10000.01", "100000.00", "ANY", "ANY", "opex", "manager", 1, "2024-11-01", "2026-03-31"),
    ("REQ-02", "request", "15000.01", "100000.00", "ANY", "ANY", "opex", "manager", 2, "2026-04-01", None),
    ("REQ-03", "request", "100000.01", "500000.00", "ANY", "ANY", "opex", "manager>dept_head", 1, "2024-11-01", None),
    (
        "REQ-04",
        "request",
        "500000.01",
        "999999999.00",
        "ANY",
        "ANY",
        "opex",
        "manager>dept_head>director",
        1,
        "2024-11-01",
        None,
    ),
    ("REQ-05", "request", "0.01", "500000.00", "ANY", "ANY", "capex", "manager>dept_head>cfo", 1, "2024-11-01", None),
]


def _org(db: Session, sfx: str) -> None:
    """A small company: a managing director, a CFO, a stores head with a storekeeper, and a procurement head."""
    db.execute(
        text(
            f"insert into org_user (code, name, designation, department_code, manager_code, email, active) values "
            f"('U1', 'Dir Rajesh', 'Managing Director', 'MGMT', null, 'dir{sfx}@x.example', true), "
            f"('U2', 'Cfo Meenakshi', 'Chief Financial Officer', 'FIN', 'U1', 'cfo{sfx}@x.example', true), "
            f"('U3', 'Stores Prakash', 'Head - Stores', 'LOG', 'U2', 'stores{sfx}@x.example', true), "
            f"('U4', 'Keeper Mahesh', 'Storekeeper', 'LOG', 'U3', 'keeper{sfx}@x.example', true), "
            f"('U5', 'Procure Vikram', 'Head - Procurement', 'PUR', 'U1', 'pur{sfx}@x.example', true)"
        )
    )
    db.execute(
        text(
            "insert into department (code, name, head_user) values "
            "('MGMT', 'Management', 'U1'), ('FIN', 'Finance', 'U2'), ('LOG', 'Stores', 'U3'), ('PUR', 'Procurement', 'U5')"
        )
    )
    for r in RULES:
        db.execute(
            text(
                "insert into approval_rule (rule_code, document, min_value, max_value, category_code, department_code, "
                "spend_type, approver_chain, version, effective_from, effective_to) values "
                "(:a, :b, :c, :d, :e, :f, :g, :h, :i, :j, :k)"
            ),
            dict(zip("abcdefghijk", r, strict=True)),
        )
    db.commit()


@pytest.fixture
def db(control_db: str) -> Iterator[Session]:
    slug = "a" + uuid.uuid4().hex[:8]
    assert tenants.create_tenant(slug, load_reference=False).ok
    with Session(routing.engine_for(slug)) as session:
        session.info["slug"] = slug
        session.info["sfx"] = uuid.uuid4().hex[:6]
        _org(session, session.info["sfx"])
        yield session
    routing.clear_engine_cache()


def _find(db: Session, value: str, on: date = TODAY, spend_type: str = "opex", **kw: str | None) -> rules.ApprovalRule:
    return rules.find_rule(db, "request", D(value), on, spend_type=spend_type, **kw)  # type: ignore[arg-type]


# ---- which rule applies -----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "on", "code", "version", "roles"),
    [
        ("0.00", TODAY, "REQ-01", 2, ()),
        ("15000.00", TODAY, "REQ-01", 2, ()),  # the top of the automatic band is automatic
        ("15000.01", TODAY, "REQ-02", 2, ("manager",)),  # one paisa more needs a manager
        ("100000.00", TODAY, "REQ-02", 2, ("manager",)),
        ("100000.01", TODAY, "REQ-03", 1, ("manager", "dept_head")),
        ("500000.00", TODAY, "REQ-03", 1, ("manager", "dept_head")),
        ("500000.01", TODAY, "REQ-04", 1, ("manager", "dept_head", "director")),
        (
            "12000.00",
            date(2026, 3, 31),
            "REQ-02",
            1,
            ("manager",),
        ),  # the last day of version 1: 12,000 needed a manager
        ("12000.00", date(2026, 4, 1), "REQ-01", 2, ()),  # the first day of version 2: the same amount approves itself
        ("10000.00", date(2026, 3, 31), "REQ-01", 1, ()),
        ("10000.01", date(2026, 3, 31), "REQ-02", 1, ("manager",)),
    ],
)
def test_the_rule_is_chosen_by_value_and_by_the_version_in_force_on_the_date(
    db: Session, value: str, on: date, code: str, version: int, roles: tuple[str, ...]
) -> None:
    rule = _find(db, value, on)

    assert (rule.code, rule.version, rule.roles) == (code, version, roles)


def test_a_capex_request_follows_the_capex_rule_not_the_opex_ones(db: Session) -> None:
    assert _find(db, "500000.00", spend_type="capex").roles == ("manager", "dept_head", "cfo")
    assert _find(db, "500000.00", spend_type="opex").code == "REQ-03"


def test_a_rule_that_names_a_category_beats_one_that_says_any(db: Session) -> None:
    db.execute(
        text(
            "insert into approval_rule (rule_code, document, min_value, max_value, category_code, department_code, "
            "spend_type, approver_chain, version, effective_from) values "
            "('REQ-CHM', 'request', 0, 999999999, 'CHM', 'ANY', 'ANY', 'dept_head>cfo', 1, '2024-11-01')"
        )
    )
    db.commit()

    assert _find(db, "20000", spend_type="opex", category="CHM").code == "REQ-CHM"
    assert _find(db, "20000", spend_type="opex", category="MRO").code == "REQ-02"


def test_a_value_or_date_no_rule_covers_is_refused_not_guessed(db: Session) -> None:
    with pytest.raises(rules.ApprovalRuleError, match="no approval rule"):
        _find(db, "0", spend_type="capex")  # the capex band starts at one paisa
    with pytest.raises(rules.ApprovalRuleError, match="no approval rule"):
        _find(db, "5000", date(2024, 10, 31))  # before any rule was in force


def test_two_different_rules_that_fit_equally_well_are_refused(db: Session) -> None:
    db.execute(
        text(
            "insert into approval_rule (rule_code, document, min_value, max_value, category_code, department_code, "
            "spend_type, approver_chain, version, effective_from) values "
            "('REQ-DUP', 'request', 0, 999999999, 'ANY', 'ANY', 'opex', 'cfo', 1, '2024-11-01')"
        )
    )
    db.commit()

    with pytest.raises(rules.ApprovalRuleError, match="ambiguous"):
        _find(db, "20000", spend_type="opex")


# ---- from roles to people ---------------------------------------------------------------------


def _chain(db: Session, roles: tuple[str, ...], requester: str | None = None, dept: str | None = "LOG") -> list[tuple]:
    steps = rules.resolve_chain(db, roles, requester=requester, department=dept, on_date=TODAY)
    return [(s.role, s.approver, s.delegate) for s in steps]


def test_each_role_becomes_the_right_person(db: Session) -> None:
    got = _chain(db, ("manager", "dept_head", "procurement_head", "director", "cfo"), requester="U4")

    # U4's manager and the stores head are the same person, so they are one step
    assert got == [
        ("manager", "U3", None),
        ("procurement_head", "U5", None),
        ("director", "U1", None),
        ("cfo", "U2", None),
    ]


def test_a_request_nobody_raised_goes_to_the_head_of_its_department(db: Session) -> None:
    assert _chain(db, ("manager", "dept_head", "director"), requester=None) == [
        ("manager", "U3", None),
        ("director", "U1", None),
    ]


def test_the_requester_never_approves_their_own_request(db: Session) -> None:
    # the stores head raises it: the manager step is the CFO, the department head step (the requester) is skipped
    assert _chain(db, ("manager", "dept_head"), requester="U3") == [("manager", "U2", None)]
    with pytest.raises(rules.ApprovalRuleError, match="only approver"):
        _chain(db, ("dept_head",), requester="U3")


def test_a_person_on_leave_is_stood_in_for_by_their_delegate_only_during_the_leave(db: Session) -> None:
    db.execute(
        text(
            "insert into delegation (from_user, to_user, start_date, end_date, reason) "
            "values ('U3', 'U5', '2026-11-02', '2026-11-13', 'Annual leave')"
        )
    )
    db.commit()

    assert _chain(db, ("dept_head",), requester=None) == [("dept_head", "U3", "U5")]
    later = rules.resolve_chain(db, ("dept_head",), requester=None, department="LOG", on_date=date(2026, 11, 14))
    assert later[0].delegate is None


@pytest.mark.parametrize(
    ("roles", "dept", "message"),
    [
        (("treasurer",), "LOG", "not defined in approver_role"),
        (("dept_head",), None, "cannot find who 'dept_head' is"),  # no department on the request
        (("manager",), None, "cannot find who 'manager' is"),
    ],
)
def test_a_chain_that_cannot_be_turned_into_people_is_refused_with_the_reason(
    db: Session, roles: tuple[str, ...], dept: str | None, message: str
) -> None:
    with pytest.raises(rules.ApprovalRuleError, match=message):
        _chain(db, roles, requester=None, dept=dept)


# ---- submitting and deciding ------------------------------------------------------------------


@dataclass
class Logins:
    stores: int  # U3, the stores head
    director: int  # U1
    cfo: int  # U2
    keeper: int  # U4, a storekeeper with a login but only the Requester role


def _make_logins(slug: str, sfx: str) -> Logins:
    return Logins(
        stores=users.create_user(slug, f"stores{sfx}@x.example", PASSWORD, "Stores Prakash", "Approver"),
        director=users.create_user(slug, f"dir{sfx}@x.example", PASSWORD, "Dir Rajesh", "Approver"),
        cfo=users.create_user(slug, f"cfo{sfx}@x.example", PASSWORD, "Cfo Meenakshi", "Approver"),
        keeper=users.create_user(slug, f"keeper{sfx}@x.example", PASSWORD, "Keeper Mahesh", "Requester"),
    )


@pytest.fixture
def logins(db: Session) -> Logins:
    return _make_logins(db.info["slug"], db.info["sfx"])


def _request(
    db: Session, no: str, total: str, *, dept: str | None = "LOG", requester: str | None = None, kind: str = "goods"
) -> str:
    if not db.execute(text("select 1 from location where code = 'WH-BLR'")).first():
        db.execute(text("insert into location (code, name, is_stock_location) values ('WH-BLR', 'Store', true)"))
    db.execute(
        text(
            "insert into request (no, requester, department_code, ship_to_location, type, channel, status, total_value) "
            "values (:no, :req, :dep, 'WH-BLR', :kind, 'form', 'draft', :total)"
        ),
        {"no": no, "req": requester, "dep": dept, "kind": kind, "total": total},
    )
    db.execute(
        text(
            "insert into request_line (request_no, line_no, free_text, qty, status) values (:no, 1, 'Something', 1, 'open')"
        ),
        {"no": no},
    )
    db.commit()
    return no


def _submit(db: Session, no: str) -> workflow.Submitted:
    return workflow.submit(db, db.info["slug"], no, sod.user(1), today=TODAY)


def _row(db: Session, no: str) -> dict:
    return dict(db.execute(text("select * from request where no = :n"), {"n": no}).mappings().one())


def _audit(db: Session, action: str, no: str) -> list[dict]:
    rows = db.execute(
        text("select * from audit_event where action = :a and entity_id = :n order by id"), {"a": action, "n": no}
    )
    return [dict(r) for r in rows.mappings()]


def test_a_small_request_approves_itself_and_says_why(db: Session, logins: Logins) -> None:
    no = _request(db, "R-1", "15000.00")

    result = _submit(db, no)

    assert (result.status, result.rule_code, result.rule_version) == ("approved", "REQ-01", 2)
    row = _row(db, no)
    assert row["status"] == "approved" and row["decided_at"] is not None
    assert (row["rule_code"], row["rule_version"]) == ("REQ-01", 2)
    assert "Auto-approved under rule REQ-01 v2" in row["decision_reason"]
    assert "No budget check was applied" in row["decision_reason"]  # honest about what was not checked
    (auto,) = _audit(db, "request.auto_approve", no)
    assert auto["actor_type"] == "rule" and auto["actor_id"] == "REQ-01" and auto["rule_version"] == "REQ-01:v2"
    assert len(_audit(db, "request.submit", no)) == 1
    assert db.execute(text("select count(*) from approval_step")).scalar_one() == 0


def test_a_request_just_over_the_limit_waits_for_the_person_the_rule_names(db: Session, logins: Logins) -> None:
    no = _request(db, "R-2", "15000.01")

    result = _submit(db, no)

    assert (result.status, result.approvers) == ("submitted", ["U3"])
    assert _row(db, no)["status"] == "submitted"
    assert "Stores Prakash" in _row(db, no)["decision_reason"]
    task = db.execute(
        text("select kind, assignee, status from task where payload->>'request_no' = :n"), {"n": no}
    ).one()
    assert task == ("approval", logins.stores, "open")  # the stores head has a task, due within the SLA
    assert workflow.can_act(db, no, logins.stores) is True
    assert workflow.can_act(db, no, logins.director) is False


def test_a_chain_of_two_people_is_worked_through_in_order(db: Session, logins: Logins) -> None:
    no = _request(db, "R-3", "600000.00")  # manager > dept head > director: the first two are the same person
    assert _submit(db, no).approvers == ["U3", "U1"]
    slug = db.info["slug"]

    with pytest.raises(workflow.ApprovalError, match="not your turn"):
        workflow.approve(db, slug, no, logins.director)  # the director has to wait for the stores head
    assert workflow.approve(db, slug, no, logins.stores, "ok for the plant") == "submitted"
    assert workflow.can_act(db, no, logins.director) is True
    assert (
        _row(db, no)["decision_reason"] == "Waiting for approval by Dir Rajesh under rule REQ-04 v1"
    )  # names who is next
    assert workflow.approve(db, slug, no, logins.director) == "approved"

    row = _row(db, no)
    assert row["status"] == "approved"
    assert row["decision_reason"] == "Approved by Stores Prakash, Dir Rajesh under rule REQ-04 v1"
    steps = workflow.steps_of(db, no)
    assert [(s["approver"], s["status"], s["acted_by"]) for s in steps] == [
        ("U3", "approved", "U3"),
        ("U1", "approved", "U1"),
    ]
    assert steps[0]["comment"] == "ok for the plant"
    assert len(_audit(db, "request.approve", no)) == 2
    assert (
        db.execute(
            text("select count(*) from task where payload->>'request_no' = :n and status = 'done'"), {"n": no}
        ).scalar_one()
        == 2
    )


def test_a_login_that_is_not_the_approver_a_stranger_or_the_wrong_role_cannot_approve(
    db: Session, logins: Logins
) -> None:
    no = _request(db, "R-4", "50000.00")
    _submit(db, no)
    slug = db.info["slug"]
    sfx = db.info["sfx"]
    stranger = users.create_user(slug, f"nobody{sfx}@x.example", PASSWORD, "Nobody", "Approver")
    wrong_role = users.create_user(slug, f"wrongrole{sfx}@x.example", PASSWORD, "Wrong Role", "Requester")

    with pytest.raises(workflow.ApprovalError, match="not linked to a person"):
        workflow.approve(db, slug, no, stranger)  # a login nobody in the organisation record matches
    assert _row(db, no)["status"] == "submitted"

    # the stores head's record now carries the Requester-role login: the right person, but the role may not approve
    db.execute(text("update org_user set email = :e where code = 'U3'"), {"e": f"wrongrole{sfx}@x.example"})
    db.commit()
    with pytest.raises(permissions.PermissionDenied):
        workflow.approve(db, slug, no, wrong_role)
    with pytest.raises(workflow.ApprovalError, match="not linked"):
        workflow.approve(db, slug, no, logins.stores)  # and the old login no longer matches any record


def test_the_requester_cannot_approve_even_if_the_rules_change_after_submitting(db: Session, logins: Logins) -> None:
    no = _request(db, "R-5", "50000.00", requester="U3")  # the stores head raised it
    db.execute(
        text(
            "insert into approval_step (request_no, round, step, role, approver, status) values (:n, 1, 1, 'manager', 'U3', 'pending')"
        ),
        {"n": no},
    )
    db.execute(text("update request set status = 'submitted', approval_round = 1 where no = :n"), {"n": no})
    db.commit()

    with pytest.raises(workflow.ApprovalError, match="segregation of duties"):
        workflow.approve(db, db.info["slug"], no, logins.stores)


def test_rejecting_needs_a_reason_and_stops_the_chain(db: Session, logins: Logins) -> None:
    no = _request(db, "R-6", "600000.00")
    _submit(db, no)
    slug = db.info["slug"]
    workflow.approve(db, slug, no, logins.stores)

    with pytest.raises(workflow.ApprovalError, match="reason is required"):
        workflow.reject(db, no, logins.director, "  ")
    assert workflow.reject(db, no, logins.director, "Not needed this quarter") == "rejected"

    row = _row(db, no)
    assert row["status"] == "rejected" and "Not needed this quarter" in row["decision_reason"]
    assert [s["status"] for s in workflow.steps_of(db, no)] == ["approved", "rejected"]
    (event,) = _audit(db, "request.reject", no)
    assert event["reason"] == "Not needed this quarter" and event["actor_id"] == str(logins.director)
    with pytest.raises(workflow.ApprovalError, match="is rejected"):
        workflow.approve(db, slug, no, logins.director)  # a decided request cannot be decided again


def test_a_returned_request_can_be_submitted_again_as_a_new_round(db: Session, logins: Logins) -> None:
    no = _request(db, "R-7", "50000.00")
    _submit(db, no)
    slug = db.info["slug"]
    assert workflow.return_for_changes(db, no, logins.stores, "Split by category") == "returned"
    assert _row(db, no)["status"] == "returned"

    again = _submit(db, no)

    assert again.status == "submitted"
    row = _row(db, no)
    assert row["approval_round"] == 2
    steps = workflow.steps_of(db, no)  # only the latest round shows
    assert [(s["approver"], s["status"]) for s in steps] == [("U3", "pending")]
    assert db.execute(text("select count(*) from approval_step where request_no = :n"), {"n": no}).scalar_one() == 2
    assert workflow.approve(db, slug, no, logins.stores) == "approved"


def test_only_a_draft_or_returned_request_can_be_submitted(db: Session, logins: Logins) -> None:
    no = _request(db, "R-8", "50000.00")
    _submit(db, no)

    with pytest.raises(workflow.ApprovalError, match="only a draft or a returned request"):
        _submit(db, no)
    with pytest.raises(workflow.ApprovalError, match="does not exist"):
        _submit(db, "NOPE")


def test_a_request_the_rules_cannot_route_stays_a_draft_with_nothing_written(db: Session, logins: Logins) -> None:
    no = _request(db, "R-9", "50000.00", dept=None)  # no department: the stores head cannot be found

    with pytest.raises(rules.ApprovalRuleError, match="cannot find who 'manager'"):
        _submit(db, no)

    db.rollback()
    assert _row(db, no)["status"] == "draft"
    assert db.execute(text("select count(*) from approval_step")).scalar_one() == 0
    assert _audit(db, "request.submit", no) == []


def test_during_a_leave_the_delegate_gets_the_step_and_the_audit_says_on_whose_behalf(
    db: Session, logins: Logins
) -> None:
    slug = db.info["slug"]
    db.execute(
        text(
            "insert into delegation (from_user, to_user, start_date, end_date) values ('U3', 'U2', '2026-11-02', '2026-11-13')"
        )
    )
    db.commit()
    no = _request(db, "R-10", "50000.00")
    _submit(db, no)

    assert workflow.can_act(db, no, logins.cfo) is True  # the CFO stands in
    assert workflow.can_act(db, no, logins.stores) is True  # and the stores head can still act
    assert workflow.approve(db, slug, no, logins.cfo, "covering") == "approved"
    (event,) = _audit(db, "request.approve", no)
    assert event["after"]["on_behalf_of"] == "U3" and event["after"]["approver"] == "U2"


# ---- replenishment through approval -----------------------------------------------------------


@pytest.fixture
def stock_company(control_db: str) -> Iterator[tuple[str, Session, Logins]]:
    slug, db = replenishment_company()
    db.info["slug"] = slug
    sfx = db.info["sfx"] = uuid.uuid4().hex[:6]
    _org(db, sfx)
    db.execute(text("insert into app_setting (key, value) values ('replenishment_department', 'LOG')"))
    db.commit()
    yield slug, db, _make_logins(slug, sfx)
    db.close()
    routing.clear_engine_cache()


def test_replenishment_previews_how_each_request_will_be_approved_and_then_does_exactly_that(
    stock_company: tuple[str, Session, Logins],
) -> None:
    slug, db, _ = stock_company
    db.execute(text("update item set standard_price = 3000 where code = 'B'"))  # 8 cartons x 3,000 = 24,000
    db.commit()

    preview = replenishment.run(db, as_of=TODAY, dry_run=True)

    # spares (9,725) are within the automatic band; packaging (24,000) is above it and needs the stores head
    assert {(r.category_code, r.route_kind) for r in preview.requests} == {("C1", "auto"), ("C2", "approval")}
    summary = replenishment.summary(preview)
    assert (summary["auto_approved"], summary["needs_approval"], summary["blocked"]) == (1, 1, 0)
    assert db.execute(text("select count(*) from request")).scalar_one() == 0  # a preview writes nothing


def test_a_run_that_submits_approves_the_small_and_hands_the_rest_to_the_right_person(
    stock_company: tuple[str, Session, Logins],
) -> None:
    slug, db, logins = stock_company
    # make the packaging request big enough to need a manager
    db.execute(text("update item set standard_price = 3000 where code = 'B'"))  # 8 cartons x 3,000 = 24,000
    db.commit()

    result = replenishment.run(db, as_of=TODAY, tenant=slug, submit=True)

    by_category = {r.category_code: r for r in result.requests}
    assert by_category["C1"].outcome == "approved" and by_category["C1"].route_kind == "auto"
    assert by_category["C2"].outcome == "submitted" and by_category["C2"].route_kind == "approval"
    assert "Stores Prakash" in by_category["C2"].route
    statuses = {r["no"]: r["status"] for r in db.execute(text("select no, status from request")).mappings()}
    assert sorted(statuses.values()) == ["approved", "submitted"]
    assert {r["department_code"] for r in db.execute(text("select department_code from request")).mappings()} == {"LOG"}
    # the person it is waiting for can approve it, and it then counts as an approved request
    waiting = by_category["C2"].no
    assert workflow.approve(db, slug, waiting, logins.stores) == "approved"


def test_without_a_replenishment_department_the_requests_stay_drafts_and_the_run_says_why(
    stock_company: tuple[str, Session, Logins],
) -> None:
    slug, db, _ = stock_company
    db.execute(text("delete from app_setting where key = 'replenishment_department'"))
    db.execute(text("update item set standard_price = 3000 where code = 'B'"))
    db.commit()

    result = replenishment.run(db, as_of=TODAY, tenant=slug, submit=True)

    by_category = {r.category_code: r for r in result.requests}
    assert by_category["C1"].outcome == "approved"  # no chain needed for an automatic approval
    blocked = by_category["C2"]
    assert blocked.route_kind == "blocked" and blocked.outcome is None
    assert blocked.submit_error and "cannot find who 'manager'" in blocked.submit_error
    assert db.execute(text("select status from request where no = :n"), {"n": blocked.no}).scalar_one() == "draft"
    assert replenishment.summary(result)["blocked"] == 1


def test_an_approved_request_still_counts_as_stock_on_the_way_but_a_rejected_one_does_not(
    stock_company: tuple[str, Session, Logins],
) -> None:
    from p2p.receiving.stock import stock_levels

    slug, db, logins = stock_company
    db.execute(text("update item set standard_price = 3000 where code = 'B'"))
    db.commit()
    result = replenishment.run(db, as_of=TODAY, tenant=slug, submit=True)
    cartons = next(r for r in result.requests if r.category_code == "C2")

    def on_order() -> Decimal:
        return next(x for x in stock_levels(db) if x.item_code == "B").on_order

    assert on_order() == D("40")

    assert workflow.reject(db, cartons.no, logins.stores, "Use the old stock first") == "rejected"
    assert on_order() == D("0")  # free to be requested again
    assert next(x for x in stock_levels(db) if x.item_code == "B").status == "stockout"


# ---- the screen's data ------------------------------------------------------------------------


def _headers(slug: str, user_id: int, role: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {create_access_token(user_id, slug, role)}"}


def test_the_requisition_page_data_shows_the_trail_and_who_may_act(db: Session, logins: Logins) -> None:
    slug = db.info["slug"]
    no = _request(db, "R-20", "600000.00")
    _submit(db, no)
    stores, director = _headers(slug, logins.stores, "Approver"), _headers(slug, logins.director, "Approver")

    listed = client.get("/api/requisitions", headers=stores).json()
    assert listed[0]["status"] == "pending" and listed[0]["waiting_for"] == "Stores Prakash"
    detail = client.get(f"/api/requisitions/{no}", headers=stores).json()
    assert detail["approval"]["rule_code"] == "REQ-04" and detail["approval"]["rule_version"] == 1
    assert [(s["name"], s["status"]) for s in detail["approval"]["steps"]] == [
        ("Stores Prakash", "pending"),
        ("Dir Rajesh", "waiting"),
    ]
    assert detail["approval"]["can_act"] is True and detail["approval"]["can_submit"] is False
    assert client.get(f"/api/requisitions/{no}", headers=director).json()["approval"]["can_act"] is False


def test_approving_over_the_api_checks_the_turn_the_role_and_the_reason(db: Session, logins: Logins) -> None:
    slug = db.info["slug"]
    no = _request(db, "R-21", "600000.00")
    _submit(db, no)
    stores, director = _headers(slug, logins.stores, "Approver"), _headers(slug, logins.director, "Approver")
    keeper = _headers(slug, logins.keeper, "Requester")

    assert (
        client.post(f"/api/requisitions/{no}/approve", json={}, headers=keeper).status_code == 403
    )  # no such permission
    early = client.post(f"/api/requisitions/{no}/approve", json={}, headers=director)
    assert early.status_code == 409 and "not your turn" in early.json()["detail"]
    assert client.post(f"/api/requisitions/{no}/approve", json={"comment": "fine"}, headers=stores).json() == {
        "status": "submitted"
    }
    assert (
        client.post(f"/api/requisitions/{no}/reject", json={}, headers=director).status_code == 409
    )  # a reason is needed
    assert client.post(f"/api/requisitions/{no}/reject", json={"reason": "No budget"}, headers=director).json() == {
        "status": "rejected"
    }
    assert client.post(f"/api/requisitions/{no}/approve", json={}, headers=stores).status_code == 409
    assert client.post(f"/api/requisitions/{no}/approve", json={}).status_code == 401


def test_a_draft_can_be_submitted_over_the_api_by_someone_allowed_to(db: Session, logins: Logins) -> None:
    slug = db.info["slug"]
    no = _request(db, "R-22", "9000.00")
    keeper = _headers(slug, logins.keeper, "Requester")  # a Requester may submit
    other = _headers(slug, logins.cfo, "Approver")  # an Approver may not

    assert client.post(f"/api/requisitions/{no}/submit", headers=other).status_code == 403
    assert client.get(f"/api/requisitions/{no}", headers=keeper).json()["approval"]["can_submit"] is True
    assert client.post(f"/api/requisitions/{no}/submit", headers=keeper).json() == {"status": "approved"}
    assert client.post(f"/api/requisitions/{no}/submit", headers=keeper).status_code == 409  # already approved

    blocked = _request(db, "R-23", "50000.00", dept=None)
    refused = client.post(f"/api/requisitions/{blocked}/submit", headers=keeper)
    assert refused.status_code == 422 and "cannot find who" in refused.json()["detail"]


def test_the_replenishment_department_setting_must_name_a_real_department(db: Session, logins: Logins) -> None:
    slug = db.info["slug"]
    admin = users.create_user(slug, f"admin{db.info['sfx']}@x.example", PASSWORD, "Admin", "Admin")
    h = _headers(slug, admin, "Admin")

    bad = client.post("/api/settings", json={"replenishment_department": "NOPE"}, headers=h).json()
    assert "no department with code NOPE" in bad["error"]
    assert client.post("/api/settings", json={"replenishment_department": "LOG"}, headers=h).json() == {
        "updated": ["replenishment_department"]
    }
    assert client.get("/api/settings", headers=h).json()["replenishment_department"] == "LOG"
