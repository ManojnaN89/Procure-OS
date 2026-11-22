"""Email approvals (plan step 4, D36): the approval matrix by value, category, department and location; the approval email
with its signed links; one reminder after a working day and escalation after two; a one-time code above Rs 5,00,000; a
deputy while the approver is away."""

import uuid
from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session
from test_approvals import PASSWORD, TODAY, Logins, _audit, _request, _row, _submit, db, logins  # noqa: F401 - fixtures
from test_sourcing import _headers

from p2p.approvals import chasing, deputies, email_links, rules, workflow
from p2p.formulas.calendar import IST, BusinessCalendar, pin_today
from p2p.main import app
from p2p.platform import mail, otp, routing, tenants, users, worker

D = Decimal
client = TestClient(app)

# ---- the matrix: D36 defaults, a category row, a location row, a row that adds ----------------------------------

D36 = [
    # code, min, max, category, department, location, spend, chain
    ("REQ-01", "0.00", "10000.00", "ANY", "ANY", "ANY", "ANY", "auto"),
    ("REQ-02", "10000.01", "100000.00", "ANY", "ANY", "ANY", "ANY", "dept_head"),
    ("REQ-03", "100000.01", "999999999.00", "ANY", "ANY", "ANY", "ANY", "director"),
    ("REQ-IT", "10000.01", "100000.00", "ITH", "ANY", "ANY", "ANY", "it_head"),
    ("REQ-P2", "10000.01", "100000.00", "ANY", "ANY", "PLT-2", "ANY", "plant_head"),
    ("REQ-CX", "100000.01", "999999999.00", "ANY", "ANY", "ANY", "capex", "+cfo"),
    ("REQ-CA", "0.00", "10000.00", "ANY", "ANY", "ANY", "capex", "+plant_head"),
]


@pytest.fixture(scope="module")
def d36(control_db: str) -> Iterator[Session]:
    """A company with the D36 rules: heads of two departments, an IT head, a director, a CFO, and two plants."""
    slug = "m" + uuid.uuid4().hex[:8]
    assert tenants.create_tenant(slug, load_reference=False).ok
    with Session(routing.engine_for(slug)) as s:
        s.execute(
            text(
                "insert into org_user (code, name, designation, department_code, manager_code, email, active) values "
                "('D1', 'Director', 'Director', 'PRD', null, 'd1@x.example', true), "
                "('C1', 'Cfo', 'CFO', 'PRD', 'D1', 'c1@x.example', true), "
                "('H1', 'Head Prod', 'Head', 'PRD', 'D1', 'h1@x.example', true), "
                "('I1', 'It Head', 'IT Head', 'PRD', 'D1', 'i1@x.example', true), "
                "('P2', 'Plant Two Head', 'Plant Head', 'PRD', 'D1', 'p2@x.example', true), "
                "('R1', 'Asker', 'Engineer', 'PRD', 'H1', 'r1@x.example', true)"
            )
        )
        s.execute(text("insert into department (code, name, head_user) values ('PRD', 'Production', 'H1')"))
        s.execute(
            text(
                "insert into location (code, name, head_user) values ('PLT-1', 'Plant one', null), "
                "('PLT-2', 'Plant two', 'P2')"
            )
        )
        s.execute(
            text(
                "insert into approver_role (role, source, value) values ('it_head', 'designation', 'IT Head'), "
                "('plant_head', 'location_head', null), ('director', 'designation', 'Director'), "
                "('cfo', 'designation', 'CFO') on conflict (role) do update set source = excluded.source, value = excluded.value"
            )
        )
        for r in D36:
            s.execute(
                text(
                    "insert into approval_rule (rule_code, document, min_value, max_value, category_code, department_code, "
                    "location_code, spend_type, approver_chain, version, effective_from) values "
                    "(:a, 'request', :b, :c, :d, :e, :f, :g, :h, 1, '2025-04-01')"
                ),
                dict(zip("abcdefgh", r, strict=True)),
            )
        s.commit()
        yield s
    routing.clear_engine_cache()


def _find(s: Session, value: str, **kw: Any) -> rules.ApprovalRule:
    kw.setdefault("spend_type", "opex")
    return rules.find_rule(s, "request", D(value), TODAY, **kw)


@pytest.mark.parametrize(
    ("value", "code", "roles"),
    [
        ("0.00", "REQ-01", ()),
        ("10000.00", "REQ-01", ()),  # the top of the automatic band approves itself
        ("10000.01", "REQ-02", ("dept_head",)),  # one paisa more goes to the head
        ("100000.00", "REQ-02", ("dept_head",)),
        ("100000.01", "REQ-03", ("director",)),  # one paisa above a lakh goes to the director
    ],
)
def test_given_the_d36_bands_when_a_value_is_at_an_edge_then_the_band_holding_it_decides(
    d36: Session, value: str, code: str, roles: tuple[str, ...]
) -> None:
    rule = _find(d36, value, location="PLT-1")

    assert (rule.code, rule.roles) == (code, roles)
    assert [s.code for s in rule.sources] == [code] * len(roles)  # each role knows the row it came from


def test_given_a_category_row_when_the_request_is_for_that_category_then_it_beats_the_default(d36: Session) -> None:
    assert _find(d36, "50000", category="ITH", location="PLT-1").roles == ("it_head",)
    assert _find(d36, "50000", category="RAW", location="PLT-1").roles == ("dept_head",)


def test_given_a_location_row_when_goods_go_to_that_plant_then_its_own_head_approves(d36: Session) -> None:
    rule = _find(d36, "50000", location="PLT-2")
    steps = rules.resolve_chain(
        d36, rule.roles, requester="R1", department="PRD", on_date=TODAY, location="PLT-2", sources=rule.sources
    )

    assert rule.code == "REQ-P2"
    assert [(s.role, s.approver) for s in steps] == [("plant_head", "P2")]  # the head of PLT-2, from the location
    assert steps[0].source is not None and "location PLT-2" in steps[0].source.why
    assert _find(d36, "50000", location="PLT-1").code == "REQ-02"  # the other plant keeps the default


def test_given_both_a_category_and_a_location_row_when_the_precedence_setting_changes_then_the_other_wins(
    d36: Session,
) -> None:
    assert _find(d36, "50000", category="ITH", location="PLT-2").code == "REQ-IT"  # category first by default
    d36.execute(
        text(
            "insert into org_setting (key, value) values ('approval_precedence', 'location>category>department>spend_type')"
        )
    )
    try:
        assert _find(d36, "50000", category="ITH", location="PLT-2").code == "REQ-P2"
        d36.execute(text("update org_setting set value = 'location>category' where key = 'approval_precedence'"))
        with pytest.raises(rules.ApprovalRuleError, match="approval precedence setting"):
            _find(d36, "50000", location="PLT-2")
    finally:
        d36.rollback()


def test_given_a_row_that_adds_when_it_fits_then_its_role_joins_the_chain_with_its_own_row(d36: Session) -> None:
    rule = _find(d36, "200000", spend_type="capex", location="PLT-1")

    assert (rule.code, rule.roles) == ("REQ-03", ("director", "cfo"))
    assert [s.code for s in rule.sources] == ["REQ-03", "REQ-CX"]
    assert "adds an approver" in rule.sources[1].why
    assert _find(d36, "200000", spend_type="opex", location="PLT-1").roles == ("director",)
    small = _find(d36, "5000", spend_type="capex", location="PLT-2")  # added to the automatic band
    assert (small.code, small.roles, small.is_automatic) == ("REQ-01", ("plant_head",), False)


# ---- each step keeps its rule row; the approval email ------------------------------------------------------------


def _jobs(slug: str, kind: str) -> list[dict[str, Any]]:
    with tenants.control_engine().connect() as c:
        return [
            dict(r[0])
            for r in c.execute(
                text("select payload from job where tenant_slug = :t and kind = :k order by id"), {"t": slug, "k": kind}
            )
        ]


def _step(db: Session, no: str) -> dict[str, Any]:
    return dict(
        db.execute(
            text(
                "select s.* from approval_step s join request r on r.no = s.request_no and r.approval_round = s.round "
                "where s.request_no = :n and s.status = 'pending'"
            ),
            {"n": no},
        )
        .mappings()
        .one()
    )


def _gasket(db: Session, no: str, total: str, requester: str | None = "U4") -> str:
    _request(db, no, total, requester=requester)
    db.execute(
        text("update request_line set free_text = 'Gasket', qty = 1, est_price = :p where request_no = :n"),
        {"p": total, "n": no},
    )
    db.commit()
    assert _submit(db, no).status == "submitted"
    return no


def test_given_a_submitted_request_when_its_step_opens_then_it_keeps_the_rule_row_and_why(
    db: Session,
    logins: Logins,  # noqa: F811
) -> None:
    no = _gasket(db, "R-W1", "20000.00")

    step = _step(db, no)
    row_id = db.execute(text("select id from approval_rule where rule_code = 'REQ-02' and version = 2")).scalar_one()
    assert (step["rule_id"], step["rule_code"], step["rule_version"]) == (row_id, "REQ-02", 2)
    assert "REQ-02 v2" in step["route_reason"] and "₹ 20,000.00" in step["route_reason"]
    assert step["opened_at"] is not None and step["reminded_at"] is None
    (submit,) = _audit(db, "request.submit", no)
    assert submit["after"]["rows"] == [row_id]


def test_given_a_step_opens_when_the_email_goes_then_it_has_the_lines_total_budget_why_and_three_links(
    db: Session,
    logins: Logins,  # noqa: F811
) -> None:
    no = _gasket(db, "R-W2", "20000.00")

    (mail_job,) = [j for j in _jobs(db.info["slug"], "send_email") if no in j["subject"]]
    body, html = mail_job["body"], mail_job["html"]
    assert mail_job["subject"] == f"Approve request {no} (₹ 20,000.00)"
    for part in ("Gasket: 1", "Estimated total: ₹ 20,000.00", "Budget: No budget was checked", "Why you:", "REQ-02 v2"):
        assert part in body
    links = {
        a: next(ln for ln in body.splitlines() if f"?do={a}" in ln).split()[-1] for a in ("approve", "reject", "return")
    }
    tokens = {a: u.split("/approve/")[1].split("?")[0] for a, u in links.items()}
    assert len(set(tokens.values())) == 1 and email_links.read(tokens["approve"]).request_no == no
    assert all(f"?do={a}" in html for a in ("approve", "reject", "return")) and "Send back for changes" in html
    assert db.execute(text("select count(*) from notification where body like '%/approve/%'")).scalar_one() == 0
    (sent,) = _audit(db, "request.approval_email", no)
    assert sent["after"]["kind"] == "new" and "/approve/" not in str(sent["after"])  # the link is never stored


def test_given_the_reject_or_return_button_when_the_page_opens_then_it_asks_for_a_reason_and_decides_once(
    db: Session,
    logins: Logins,  # noqa: F811
    outbox: list[dict[str, Any]],
) -> None:
    no = _gasket(db, "R-W3", "20000.00")
    token = email_links.make(db.info["slug"], no, 1, 1, logins.stores)

    page = client.get(f"/approve/{token}?do=return")
    assert page.status_code == 200 and "Send back for changes" in page.text and "required" in page.text
    assert ">Approve<" not in page.text  # only the one decision on this small page
    client.post(f"/approve/{token}/code")
    code = _code(outbox)  # every decision from a link needs the code (D-07)
    assert client.post(f"/approve/{token}", data={"action": "return", "code": code}).status_code == 409  # no reason
    done = client.post(f"/approve/{token}", data={"action": "return", "reason": "Wrong gasket size", "code": code})
    assert done.status_code == 200 and "Sent back for changes" in done.text
    db.rollback()
    assert _row(db, no)["status"] == "returned"
    (row,) = _audit(db, "request.return", no)
    assert row["after"]["via"] == "emailed link" and row["reason"] == "Wrong gasket size"
    again = client.post(f"/approve/{token}", data={"action": "approve", "code": code})
    assert again.status_code == 409  # the link worked once


def test_given_the_requester_holds_a_link_when_they_approve_their_own_request_then_it_is_refused(
    db: Session,
    logins: Logins,  # noqa: F811
    outbox: list[dict[str, Any]],
) -> None:
    no = _gasket(db, "R-W4", "20000.00", requester="U3")  # the stores head asks; their manager (the CFO) decides
    db.execute(text("update approval_step set approver = 'U3' where request_no = :n"), {"n": no})  # as if data changed
    db.commit()
    token = email_links.make(db.info["slug"], no, 1, 1, logins.stores)
    client.post(f"/approve/{token}/code")

    r = client.post(f"/approve/{token}", data={"action": "approve", "code": _code(outbox)})  # a code never replaces SoD

    assert r.status_code == 409 and "segregation of duties" in r.text
    db.rollback()
    assert _row(db, no)["status"] == "submitted"


def test_given_two_companies_when_a_link_names_the_other_company_then_it_touches_nothing(
    db: Session,
    logins: Logins,  # noqa: F811
) -> None:
    no = _gasket(db, "R-W5", "20000.00")
    other = "b" + uuid.uuid4().hex[:8]
    assert tenants.create_tenant(other, load_reference=False).ok
    try:
        foreign = email_links.make(other, no, 1, 1, logins.stores)  # same number, same login id, other company
        assert client.get(f"/approve/{foreign}").status_code == 409
        assert client.post(f"/approve/{foreign}", data={"action": "approve"}).status_code == 409
        db.rollback()
        assert _row(db, no)["status"] == "submitted"
    finally:
        routing.clear_engine_cache()


# ---- one reminder after a working day, escalation after two ----------------------------------------------------


def test_one_working_day_is_counted_over_the_weekend_and_a_holiday() -> None:
    friday = datetime(2026, 11, 6, 11, 0, tzinfo=IST)
    plain, with_holiday = BusinessCalendar.of(), BusinessCalendar.of([date(2026, 11, 9)])

    assert plain.working_days_later(friday, 1) == datetime(2026, 11, 9, 11, 0, tzinfo=IST)  # Monday
    assert with_holiday.working_days_later(friday, 1) == datetime(2026, 11, 10, 11, 0, tzinfo=IST)  # Tuesday
    assert with_holiday.working_days_later(friday, 2) == datetime(2026, 11, 11, 11, 0, tzinfo=IST)
    assert plain.working_days_later(datetime(2026, 11, 7, 10, 0, tzinfo=IST), 1) == datetime(
        2026, 11, 9, 18, 0, tzinfo=IST
    )  # opened on a Saturday: the whole of Monday


def _remind(slug: str, no: str, now: datetime) -> None:
    chasing._remind(worker.JobContext(0, slug, {"request_no": no, "round": 1, "step": 1}, now))


def test_given_a_holiday_monday_when_a_step_waits_from_friday_then_the_reminder_goes_once_on_tuesday(
    db: Session,
    logins: Logins,  # noqa: F811
) -> None:
    slug = db.info["slug"]
    db.execute(
        text(
            "insert into ref_holiday (id, holiday_date, name, kind, scope) values (1, '2026-11-09', 'Test day', 'closed', 'central')"
        )
    )
    no = _gasket(db, "R-W6", "20000.00")
    friday = datetime(2026, 11, 6, 11, 0, tzinfo=IST)
    db.execute(text("update approval_step set opened_at = :t where request_no = :n"), {"t": friday, "n": no})
    db.commit()
    due = datetime(2026, 11, 10, 11, 0, tzinfo=IST)
    before = len(_jobs(slug, "send_email"))

    _remind(slug, no, due - timedelta(seconds=1))  # Monday was a holiday: not yet
    db.rollback()
    assert _step(db, no)["reminded_at"] is None and _audit(db, "request.remind", no) == []
    _remind(slug, no, due)
    _remind(slug, no, due + timedelta(hours=3))  # run twice: still one reminder
    db.rollback()

    assert _step(db, no)["reminded_at"] == due
    (row,) = _audit(db, "request.remind", no)
    assert (
        row["after"]["automatic"] is True
        and row["after"]["approver"] == "U3"
        and row["actor_id"] == "approval-reminder"
    )
    reminders = [j for j in _jobs(slug, "send_email")[before:] if j["subject"].startswith("Reminder: request")]
    assert len(reminders) == 1 and "/approve/" in reminders[0]["body"]


def test_given_a_decided_step_when_the_reminder_time_comes_then_nobody_is_reminded(
    db: Session,
    logins: Logins,  # noqa: F811
) -> None:
    no = _gasket(db, "R-W7", "20000.00")
    workflow.approve(db, db.info["slug"], no, logins.stores, "fine")

    _remind(db.info["slug"], no, datetime.now(UTC) + timedelta(days=10))

    db.rollback()
    assert _audit(db, "request.remind", no) == []


def test_given_two_working_days_without_a_decision_then_it_escalates_to_the_manager_with_the_email(
    db: Session,
    logins: Logins,  # noqa: F811
) -> None:
    slug = db.info["slug"]
    no = _gasket(db, "R-W8", "20000.00")
    step = _step(db, no)
    task = db.execute(text("select due_at, escalation_chain from task where id = :i"), {"i": step["task_id"]}).one()
    cal = chasing.calendar(db)

    assert task.due_at == cal.working_days_later(step["opened_at"], 2)  # the setting's default, on the calendar
    assert list(task.escalation_chain) == [logins.cfo]  # the stores head's own manager
    worker.run_once(now=task.due_at - timedelta(minutes=1), tenant=slug)  # the reminder (one working day) only
    db.rollback()
    assert _step(db, no)["delegate"] is None and len(_audit(db, "request.remind", no)) == 1
    worker.run_once(now=task.due_at + timedelta(minutes=1), tenant=slug)
    db.rollback()

    assert _step(db, no)["delegate"] == "U2"
    escalated = [j for j in _jobs(slug, "send_email") if j["subject"].startswith("Escalated to you: request")]
    assert len(escalated) == 1 and "/approve/" in escalated[0]["body"]
    assert "standing in for Stores Prakash" in escalated[0]["body"]


def test_given_an_approver_without_a_manager_then_it_escalates_to_the_next_approver(
    db: Session,
    logins: Logins,  # noqa: F811
) -> None:
    slug = db.info["slug"]
    procure = users.create_user(slug, f"pur{db.info['sfx']}@x.example", PASSWORD, "Procure Vikram", "Approver")
    no = _gasket(db, "R-W9", "20000.00", requester=None)
    db.execute(
        text(
            "insert into approval_step (request_no, round, step, role, approver, status) values (:n, 1, 2, 'x', 'U5', 'waiting')"
        ),
        {"n": no},
    )
    db.commit()

    assert chasing.escalation_target(db, slug, no, 1, 1, "U1") == procure  # the director has no manager
    assert chasing.escalation_target(db, slug, no, 1, 1, "U3") == logins.cfo  # a manager comes first


# ---- a one-time code above Rs 5,00,000 -------------------------------------------------------------------------


@pytest.fixture
def outbox(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    sent: list[dict[str, Any]] = []

    def fake(to: str, subject: str, body: str, **kw: Any) -> str:
        sent.append({"to": to, "subject": subject, "body": body})
        return "id"

    monkeypatch.setattr(mail, "send_email", fake)
    return sent


def _code(sent: list[dict[str, Any]]) -> str:
    return sent[-1]["body"].split("code is ")[1][:6]


@pytest.mark.parametrize("action", ["approve", "reject", "return"])
def test_given_a_link_below_the_limit_when_deciding_without_a_code_then_it_is_refused_and_with_it_it_works(
    db: Session,
    logins: Logins,  # noqa: F811
    outbox: list[dict[str, Any]],
    action: str,
) -> None:
    # D-07: every email decision needs a login or a one-time code, whatever the amount (the limit now governs only
    # the order approval code in the app)
    assert otp.needed(db, D("500000.00")) is False and otp.needed(db, D("500000.01")) is True
    no = _gasket(db, f"R-O1{action[0]}", "20000.00")
    token = email_links.make(db.info["slug"], no, 1, 1, logins.stores)
    form = {"action": action, "reason": "Checked"}

    refused = client.post(f"/approve/{token}", data=form)
    assert refused.status_code == 409 and "needs a one-time code" in refused.text
    client.post(f"/approve/{token}/code")
    done = client.post(f"/approve/{token}", data={**form, "code": _code(outbox)})

    assert done.status_code == 200
    db.rollback()
    assert _row(db, no)["status"] in ("approved", "submitted", "rejected", "returned")
    assert [r["action"] for r in _audit(db, "otp.use", f"approval:{no}:1:1")] == ["otp.use"]


def test_given_a_forwarded_link_then_the_code_goes_only_to_the_named_login_and_never_shows_on_the_page(
    db: Session,
    logins: Logins,  # noqa: F811
    outbox: list[dict[str, Any]],
) -> None:
    no = _gasket(db, "R-O3", "20000.00")
    token = email_links.make(db.info["slug"], no, 1, 1, logins.stores)

    page = client.post(f"/approve/{token}/code")  # whoever holds the link asks

    (mail_sent,) = outbox
    code = _code(outbox)
    assert mail_sent["to"].startswith("stores") and code not in page.text  # only the named person's mailbox has it
    assert code not in client.get(f"/approve/{token}").text
    assert f"/#/requisitions/{no}" in page.text and "Open the request in the app" in page.text
    (mail_job,) = [j for j in _jobs(db.info["slug"], "send_email") if no in j["subject"]]
    assert f"/#/requisitions/{no}" in mail_job["body"] and "needs a one-time code" in mail_job["body"]
    assert f"/#/requisitions/{no}" in mail_job["html"]


def test_given_a_request_above_the_limit_when_approving_by_link_then_the_emailed_code_is_needed_once(
    db: Session,
    logins: Logins,  # noqa: F811
    outbox: list[dict[str, Any]],
) -> None:
    no = _gasket(db, "R-O2", "500000.01")
    token = email_links.make(db.info["slug"], no, 1, 1, logins.stores)

    assert "one-time code" in client.get(f"/approve/{token}").text
    refused = client.post(f"/approve/{token}", data={"action": "approve"})
    assert refused.status_code == 409 and "needs a one-time code" in refused.text
    sent = client.post(f"/approve/{token}/code")
    assert sent.status_code == 200 and "A code was sent" in sent.text and outbox[-1]["to"].startswith("stores")
    code = _code(outbox)
    db.rollback()
    (kept,) = db.execute(text("select code_hash from one_time_code")).one()
    assert len(kept) == 64 and code not in kept  # only a keyed hash is kept

    done = client.post(f"/approve/{token}", data={"action": "approve", "code": code})

    assert done.status_code == 200 and "next approver" in done.text  # the director comes next (above 5 lakh)
    db.rollback()
    assert [r["action"] for r in _audit(db, "otp.use", f"approval:{no}:1:1")] == ["otp.use"]
    (row,) = _audit(db, "request.approve", no)
    assert row["after"]["via"] == "emailed link"
    with pytest.raises(otp.CodeError, match="ask for one"):  # the same code again
        email_links.CODES.verify(db, f"{no}:1:1", logins.stores, code)


def test_given_a_code_when_it_is_wrong_five_times_or_late_then_it_no_longer_works(
    db: Session,
    logins: Logins,  # noqa: F811
    outbox: list[dict[str, Any]],
) -> None:
    codes, now = otp.OneTimeCodes("test"), datetime.now(UTC)
    codes.issue(db, "R-X", logins.stores, subject="code", now=now)
    good = _code(outbox)
    wrong = "000000" if good != "000000" else "111111"

    for left in (4, 3, 2, 1):
        with pytest.raises(otp.CodeError, match=f"{left} tries left"):
            codes.verify(db, "R-X", logins.stores, wrong, now=now)
    with pytest.raises(otp.CodeError, match="wrong code 5 times"):
        codes.verify(db, "R-X", logins.stores, wrong, now=now)
    with pytest.raises(otp.CodeError, match="ask for one"):
        codes.verify(db, "R-X", logins.stores, good, now=now)  # locked: even the right code
    assert len(_audit(db, "otp.wrong", "test:R-X")) == 5

    later = now + timedelta(minutes=1)
    codes.issue(db, "R-X", logins.stores, subject="code", now=later)
    fresh = _code(outbox)
    with pytest.raises(otp.CodeError, match="expired"):
        codes.verify(db, "R-X", logins.stores, fresh, now=later + timedelta(minutes=10, seconds=1))
    with pytest.raises(otp.CodeError, match="a moment ago"):
        codes.issue(db, "R-X", logins.stores, subject="code", now=later + timedelta(seconds=5))


def test_given_a_new_code_when_the_old_one_is_typed_then_only_the_new_one_works(
    db: Session,
    logins: Logins,  # noqa: F811
    outbox: list[dict[str, Any]],
) -> None:
    codes, now = otp.OneTimeCodes("test"), datetime.now(UTC)
    codes.issue(db, "R-Y", logins.stores, subject="code", now=now)
    old = _code(outbox)
    codes.issue(db, "R-Y", logins.stores, subject="code", now=now + timedelta(minutes=1))
    new = _code(outbox)
    if old != new:
        with pytest.raises(otp.CodeError, match="wrong code"):
            codes.verify(db, "R-Y", logins.stores, old, now=now + timedelta(minutes=2))
    codes.verify(db, "R-Y", logins.stores, new, now=now + timedelta(minutes=2))
    db.commit()
    with pytest.raises(otp.CodeError, match="ask for one"):
        codes.verify(db, "R-Y", logins.stores, new, now=now + timedelta(minutes=3))


# ---- a deputy while the approver is away -----------------------------------------------------------------------


def test_given_an_approver_names_a_deputy_when_a_request_comes_then_the_deputy_gets_it(
    db: Session,
    logins: Logins,  # noqa: F811
) -> None:
    slug = db.info["slug"]
    pin_today(TODAY)
    try:
        with pytest.raises(deputies.DeputyError, match="someone else"):
            deputies.name_deputy(db, slug, logins.stores, "U3", TODAY, TODAY)
        with pytest.raises(deputies.DeputyError, match="cannot approve"):
            deputies.name_deputy(db, slug, logins.stores, "U4", TODAY, TODAY)  # the storekeeper only requests
        with pytest.raises(deputies.DeputyError, match="before the first"):
            deputies.name_deputy(db, slug, logins.stores, "U2", TODAY, TODAY - timedelta(days=1))
        did = deputies.name_deputy(db, slug, logins.stores, "U2", TODAY, TODAY + timedelta(days=5), "Leave")
        assert [d["to_user"] for d in deputies.mine(db, logins.stores)] == ["U2"]
        (made,) = _audit(db, "delegation.create", str(did))
        assert made["after"]["to"] == "U2"

        no = _gasket(db, "R-D1", "20000.00")
        assert _step(db, no)["delegate"] == "U2"
        assert workflow.approve(db, slug, no, logins.cfo, "standing in") == "approved"
        (row,) = _audit(db, "request.approve", no)
        assert row["after"]["on_behalf_of"] == "U3"

        r = client.get("/api/me/deputies", headers=_headers(slug, logins.stores, "Approver"))
        assert r.status_code == 200 and r.json()["deputies"][0]["to_name"] == "Cfo Meenakshi"
        gone = client.delete(f"/api/me/deputies/{did}", headers=_headers(slug, logins.stores, "Approver"))
        assert gone.status_code == 200 and deputies.mine(db, logins.stores) == []
        keeper = client.get("/api/me/deputies", headers=_headers(slug, logins.keeper, "Requester"))
        assert keeper.status_code == 403
    finally:
        pin_today(None)
