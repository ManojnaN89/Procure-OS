"""The budget check when a request is submitted: how much is left, what counts against it, and what happens when a
request does not fit (warn, block, and no automatic approval)."""

from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session
from test_approvals import TODAY, Logins, _request, _row, _submit, db, logins  # noqa: F401

from p2p.approvals import budget, workflow
from p2p.formulas.budget import check_budget

D = Decimal


# ---- the arithmetic ---------------------------------------------------------------------------


def test_a_request_equal_to_what_is_left_fits_and_one_paisa_more_does_not() -> None:
    assert check_budget(D("1000"), D("400"), D("600")).status == "warn"  # fits, at 100% used it warns
    over = check_budget(D("1000"), D("400"), D("600.01"))
    assert over.status == "over" and over.over_by == D("0.01") and over.available == D("600")


def test_no_budget_means_not_checked_and_commitments_beyond_the_budget_leave_nothing() -> None:
    assert check_budget(None, D("0"), D("5")).status == "unchecked"
    assert check_budget(D("100"), D("150"), D("1")).status == "over"  # already overspent
    assert check_budget(D("100"), D("150"), D("0")).status == "ok"  # nothing asked, nothing over


@pytest.mark.parametrize(
    ("requested", "status", "used"),
    [
        ("499.00", "ok", "89.99"),  # 89.99% used
        ("500.00", "warn", "90.00"),  # exactly 90%: the warning starts (D36)
        ("1500.00", "warn", "100.00"),  # exactly 100%: within budget, still a warning
        ("1501.00", "over", "100.01"),  # 100.01%: over
    ],
)
def test_the_budget_warns_at_90_percent_used_and_is_over_above_100(requested: str, status: str, used: str) -> None:
    # given a budget of 10,000 of which 8,500 is committed
    got = check_budget(D("10000"), D("8500"), D(requested))

    # then the share used decides: 90% warns, above 100% is over
    assert got.status == status
    assert got.used_pct is not None and got.used_pct.quantize(D("0.01")) == D(used)
    assert got.over_by == (D("1.00") if status == "over" else D("0.00"))


def test_the_thresholds_are_settings_not_fixed() -> None:
    # a company that warns at 80% and allows 110%
    assert check_budget(D("1000"), D("0"), D("799.99"), warn_pct=D(80), block_pct=D(110)).status == "ok"
    assert check_budget(D("1000"), D("0"), D("800"), warn_pct=D(80), block_pct=D(110)).status == "warn"
    assert check_budget(D("1000"), D("0"), D("1100"), warn_pct=D(80), block_pct=D(110)).status == "warn"
    over = check_budget(D("1000"), D("0"), D("1100.01"), warn_pct=D(80), block_pct=D(110))
    assert over.status == "over" and over.over_by == D("0.01")
    assert check_budget(D("1000"), D("950"), D("0")).status == "ok"  # asking for nothing never warns


# ---- a company with budgets -------------------------------------------------------------------


def _budgets(db: Session, policy: str = "warn") -> None:
    """CC1 and CC2 are at the store (WH-BLR), CC9 elsewhere. GL G1 has 100,000 a month in CC1 and 50,000 in CC2,
    from April 2026 to March 2027."""
    x = db.execute
    if not x(text("select 1 from location where code = 'WH-BLR'")).first():
        x(text("insert into location (code, name, is_stock_location) values ('WH-BLR', 'Store', true)"))
    x(text("insert into location (code, name) values ('HO', 'Head office')"))
    x(text("insert into gl_account (code, name) values ('G1', 'Materials'), ('G2', 'Services')"))
    x(
        text(
            "insert into cost_centre (code, name, location_code, active) values "
            "('CC1', 'Production', 'WH-BLR', true), ('CC2', 'Maintenance', 'WH-BLR', true), ('CC9', 'Office', 'HO', true)"
        )
    )
    for month in range(12):
        year, mon = (2026, 4 + month) if month < 9 else (2027, month - 8)
        for cc, amount in (("CC1", "100000"), ("CC2", "50000")):
            x(
                text(
                    "insert into budget (cost_centre_code, gl_code, period, amount, version, policy) "
                    "values (:c, 'G1', :p, :a, 1, :pol)"
                ),
                {"c": cc, "p": f"{year}-{mon:02d}", "a": amount, "pol": policy},
            )
    db.commit()


def _ask(
    db: Session,
    no: str,
    value: str,
    *,
    gl: str | None = "G1",
    cc: str | None = None,
    status: str = "draft",
    created: str | None = None,
) -> str:
    """A request for one line of the given value, in the stores department (a 15,000 line approves itself)."""
    _request(db, no, value)
    db.execute(
        text(
            "update request_line set gl_code = :g, cost_centre_code = :c, qty = 1, est_price = :v where request_no = :n"
        ),
        {"g": gl, "c": cc, "v": value, "n": no},
    )
    db.execute(
        text(
            "update request set status = :s, created_at = coalesce(cast(:d as timestamptz), created_at) where no = :n"
        ),
        {"s": status, "d": created, "n": no},
    )
    db.commit()
    return no


def _check(db: Session, no: str, today: date = TODAY) -> list[budget.GroupCheck]:
    return budget.check_request(db, no, today)


def test_the_budget_counts_from_april_to_the_current_month_not_the_whole_year(
    db: Session,
) -> None:  # noqa: F811
    _budgets(db)
    # April to November is eight months: 800,000 in CC1
    (fits,) = _check(db, _ask(db, "R-1", "800000", cc="CC1"))
    assert (fits.check.status, fits.check.budget) == ("warn", D("800000"))  # fits, all of it used
    (over,) = _check(db, _ask(db, "R-2", "800000.01", cc="CC1"))
    assert over.check.status == "over" and over.check.over_by == D("0.01")
    # the same request a month earlier has one month less
    (october,) = _check(db, "R-1", date(2026, 10, 31))
    assert october.check.budget == D("700000") and october.check.status == "over"


def test_a_line_without_a_cost_centre_is_held_against_all_the_cost_centres_at_its_location(
    db: Session,
) -> None:  # noqa: F811
    _budgets(db)

    (group,) = _check(db, _ask(db, "R-1", "1000", cc=None))

    assert group.cost_centres == ("CC1", "CC2")
    assert group.check.budget == D("1200000")  # 8 months of 150,000


def test_requests_already_approved_or_waiting_use_up_the_budget_and_drafts_do_not(
    db: Session,
) -> None:  # noqa: F811
    _budgets(db)
    _ask(db, "R-A", "300000", status="approved")
    _ask(db, "R-B", "200000", status="submitted", cc="CC2")  # a named cost centre of the same location still counts
    _ask(db, "R-C", "100000", status="closed")  # every line ordered: its orders count, not its estimate (FR27)
    _ask(db, "R-D", "999999", status="draft")
    _ask(db, "R-E", "999999", status="rejected")
    _ask(db, "R-F", "999999", status="cancelled")
    _ask(db, "R-G", "999999", status="approved", gl="G2")  # another GL account
    _ask(db, "R-H", "100000", status="approved", created="2025-10-01")  # last year, still open: carried (D-09)
    _ask(db, "R-I", "999999", status="approved", cc="CC9")  # another location's cost centre

    (group,) = _check(db, _ask(db, "R-N", "50000"))

    assert group.check.committed == D("600000") and group.check.status == "ok"
    assert group.check.available == D("600000")


def test_a_request_within_budget_goes_its_way_and_keeps_the_check(db: Session) -> None:  # noqa: F811
    _budgets(db)
    no = _ask(db, "R-1", "15000", cc="CC1")

    result = _submit(db, no)

    assert result.status == "approved" and "Within budget." in result.reason
    saved = _row(db, no)["budget_check"]
    assert saved[0]["status"] == "ok" and saved[0]["gl_code"] == "G1" and saved[0]["requested"] == "15000.00"


def test_a_small_request_over_budget_does_not_approve_itself(db: Session) -> None:  # noqa: F811
    _budgets(db)
    _ask(db, "R-A", "795000", cc="CC1", status="approved")  # 5,000 left in CC1
    no = _ask(db, "R-1", "12000", cc="CC1")  # within the automatic band, but more than is left

    result = _submit(db, no)

    assert (result.status, result.rule_code) == (
        "submitted",
        "REQ-02",
    )  # the first rule above the automatic band
    assert "Over budget: GL G1 by ₹ 7,000.00" in _row(db, no)["decision_reason"]
    assert "cannot approve itself" in _row(db, no)["decision_reason"]
    assert db.execute(text("select count(*) from approval_step where request_no = :n"), {"n": no}).scalar_one() == 1


def test_the_company_can_allow_automatic_approval_over_budget_and_the_warning_stays(
    db: Session,
) -> None:  # noqa: F811
    _budgets(db)
    db.execute(text("insert into org_setting (key, value) values ('auto_approve_requires_budget', 'false')"))
    _ask(db, "R-A", "795000", cc="CC1", status="approved")
    no = _ask(db, "R-1", "12000", cc="CC1")

    result = _submit(db, no)

    assert result.status == "approved" and "Over budget: GL G1" in result.reason


def test_a_request_that_needs_people_anyway_is_sent_with_a_warning(db: Session) -> None:  # noqa: F811
    _budgets(db)
    no = _ask(db, "R-1", "900000", cc="CC1")  # 100,000 more than eight months of budget

    result = _submit(db, no)

    assert result.status == "submitted" and result.rule_code == "REQ-04"
    assert "Over budget: GL G1 by ₹ 1,00,000.00" in _row(db, no)["decision_reason"]


def test_when_the_policy_is_block_an_over_budget_request_cannot_be_submitted(
    db: Session,
) -> None:  # noqa: F811
    _budgets(db, policy="block")
    no = _ask(db, "R-1", "900000", cc="CC1")

    with pytest.raises(workflow.ApprovalError, match="policy blocks it"):
        _submit(db, no)

    db.rollback()
    assert _row(db, no)["status"] == "draft"
    assert db.execute(text("select count(*) from approval_step")).scalar_one() == 0
    assert db.execute(text("select count(*) from audit_event where action = 'request.submit'")).scalar_one() == 0
    assert _submit(db, _ask(db, "R-2", "15000", cc="CC1")).status == "approved"  # one that fits goes through


def test_the_company_setting_can_block_even_when_the_budget_rows_only_warn(
    db: Session,
) -> None:  # noqa: F811
    _budgets(db)
    db.execute(text("insert into org_setting (key, value) values ('budget_policy', 'block')"))
    no = _ask(db, "R-1", "900000", cc="CC1")

    with pytest.raises(workflow.ApprovalError):
        _submit(db, no)


def test_lines_with_no_budget_set_are_said_to_be_unchecked(db: Session) -> None:  # noqa: F811
    _budgets(db)
    no = _ask(db, "R-1", "15000", gl="G2")  # G2 has no budget
    no2 = _ask(db, "R-2", "15000", gl=None)  # no GL account at all

    assert "No budget was checked" in _submit(db, no).reason
    assert "No budget was checked" in _submit(db, no2).reason
    assert _row(db, no)["budget_check"][0]["status"] == "unchecked"


def test_the_request_page_shows_the_budget_check(db: Session) -> None:  # noqa: F811
    from fastapi.testclient import TestClient

    from p2p.main import app
    from p2p.platform import users
    from p2p.platform.tokens import create_access_token

    _budgets(db)
    no = _ask(db, "R-1", "900000", cc="CC1")
    _submit(db, no)
    slug, sfx = db.info["slug"], db.info["sfx"]
    uid = users.create_user(slug, f"b{sfx}@x.example", "correct horse battery", "Buyer", "Buyer")

    r = TestClient(app).get(
        f"/api/requisitions/{no}", headers={"Authorization": f"Bearer {create_access_token(uid, slug, 'Buyer')}"}
    )

    (shown,) = r.json()["approval"]["budget"]
    assert (shown["status"], shown["budget"], shown["over_by"], shown["cost_centres"]) == (
        "over",
        "800000.00",
        "100000.00",
        ["CC1"],
    )


# ---- budget against what is held and spent (the variance view) ---------------------------------


def test_a_request_that_is_cancelled_or_withdrawn_gives_its_budget_back(db: Session) -> None:  # noqa: F811
    from p2p.requests import lifecycle

    _budgets(db)
    _ask(db, "R-W", "400000", cc="CC1", status="submitted")
    (before,) = _check(db, _ask(db, "R-N1", "10000", cc="CC1"))
    assert before.check.committed == D("400000")

    db.execute(text("update request set requester = 'U4' where no = 'R-W'"))
    db.commit()
    lifecycle.cancel(db, db.info["slug"], "R-W", _requester_login(db), "no longer needed")

    (after,) = _check(db, "R-N1")
    assert after.check.committed == D("0")  # the 400,000 is free again


def _requester_login(db: Session) -> int:
    from p2p.platform import users

    return users.create_user(
        db.info["slug"], f"keeper{db.info['sfx']}@x.example", "correct horse battery", "Keeper", "Requester"
    )


def test_the_variance_shows_budget_held_and_spent_for_each_cost_centre_and_account(db: Session) -> None:  # noqa: F811
    _budgets(db)
    _ask(db, "R-V1", "30000", cc="CC1", status="approved")
    _ask(db, "R-V2", "20000", cc="CC1", status="submitted")
    _ask(db, "R-V3", "999999", cc="CC1", status="draft")  # a draft holds nothing
    _ask(db, "R-V4", "7000", cc="CC2", status="closed")  # every line ordered: its orders hold the budget, not it
    _ask(db, "R-V5", "5000", status="approved")  # no cost centre: listed apart
    db.execute(
        text("insert into supplier (code, legal_name, trade_name, status) values ('SV', 'Vendor', null, 'active')")
    )
    db.execute(
        text(
            "insert into invoice (no, type, supplier_code, supplier_invoice_no, invoice_date, fy, received_on, taxable, total, "
            "status, dedupe_key) values ('PI-V', 'invoice', 'SV', 'X1', '2026-11-02', '2026-27', '2026-11-02', 12000, 12000, 'paid', 'X1')"
        )
    )
    db.execute(
        text(
            "insert into budget_actual (invoice_no, cost_centre_code, gl_code, period, amount) values "
            "('PI-V', 'CC1', 'G1', '2026-11', 12000), ('PI-V', 'CC1', 'G1', '2027-01', 999)"  # a later month is not counted yet
        )
    )
    db.commit()

    got = budget.variance(db, TODAY)

    assert (got["from"], got["to"]) == ("2026-04", "2026-11")
    cc1, cc2 = got["rows"]
    # 8 months (April to November) of 100,000 and 50,000
    # left = budget - held by requests - held by orders - spent (task 3.7: spent now counts against what is left)
    assert (cc1["cost_centre"], cc1["budget"], cc1["held_by_requests"], cc1["held_by_orders"], cc1["spent"]) == (
        "CC1", "800000.00", "50000.00", "0.00", "12000.00",
    )  # fmt: skip
    assert (cc1["committed"], cc1["left"], cc1["over"]) == ("50000.00", "738000.00", False)
    assert (cc2["budget"], cc2["committed"], cc2["spent"]) == ("400000.00", "0.00", "0.00")
    assert got["unassigned"] == [{"gl_code": "G1", "location": "WH-BLR", "committed": "5000.00"}]


def test_only_those_who_may_see_budgets_get_the_variance_over_the_api(db: Session) -> None:  # noqa: F811
    from fastapi.testclient import TestClient

    from p2p.main import app
    from p2p.platform import users
    from p2p.platform.tokens import create_access_token

    _budgets(db)
    slug, sfx = db.info["slug"], db.info["sfx"]
    finance = users.create_user(slug, f"f{sfx}@x.example", "correct horse battery", "Finance", "Finance")
    buyer = users.create_user(slug, f"b{sfx}@x.example", "correct horse battery", "Buyer", "Buyer")
    client = TestClient(app)

    def get(uid: int, role: str) -> int:
        return client.get(
            "/api/budgets/variance", headers={"Authorization": f"Bearer {create_access_token(uid, slug, role)}"}
        ).status_code

    assert get(finance, "Finance") == 200
    assert get(buyer, "Buyer") == 403


# ---- budget per location, per department, and on approval (P1-31) -------------------------------


def _location_budget(db: Session, location: str, monthly: str, policy: str = "warn") -> None:
    """The location's own budget for GL G1, April 2026 to March 2027."""
    for month in range(12):
        year, mon = (2026, 4 + month) if month < 9 else (2027, month - 8)
        db.execute(
            text(
                "insert into budget (location_code, gl_code, period, amount, version, policy) "
                "values (:l, 'G1', :p, :a, 1, :pol)"
            ),
            {"l": location, "p": f"{year}-{mon:02d}", "a": monthly, "pol": policy},
        )
    db.commit()


def test_a_line_within_its_cost_centre_can_still_be_over_the_location_budget(db: Session) -> None:  # noqa: F811
    # given the store's own budget of 120,000 a month (960,000 to November), below its two cost centres' 150,000
    _budgets(db)
    _location_budget(db, "WH-BLR", "120000", policy="block")
    _ask(db, "R-A", "200000", cc="CC2", status="approved")  # Maintenance has used 200,000 of the location's money

    # when Production asks for 770,000, within its own 800,000
    no = _ask(db, "R-1", "770000", cc="CC1")
    checks = _check(db, no)

    # then the cost centre is nearly used, and the location is over by 10,000, its budget named in words
    by_level = {c.level: c for c in checks}
    assert set(by_level) == {"cost_centre", "location"}
    assert by_level["cost_centre"].check.status == "warn"
    loc = by_level["location"]
    assert (loc.location, loc.check.budget, loc.check.committed, loc.check.over_by) == (
        "WH-BLR", D("960000"), D("200000"), D("10000.00"),
    )  # fmt: skip
    said = budget.summary(checks)
    assert "GL G1 by ₹ 10,000.00 (location WH-BLR, Apr 2026 to Nov 2026 (FY 2026-27))" in said

    # and the location's policy blocks the submit
    with pytest.raises(workflow.ApprovalError, match="location WH-BLR"):
        _submit(db, no)


def test_a_location_with_no_budget_of_its_own_is_checked_by_its_cost_centres_only(db: Session) -> None:  # noqa: F811
    _budgets(db)

    checks = _check(db, _ask(db, "R-1", "1000", cc="CC1"))

    assert [c.level for c in checks] == ["cost_centre"]


def test_a_line_without_a_cost_centre_is_charged_to_its_departments_cost_centres_at_the_location(
    db: Session,  # noqa: F811
) -> None:
    # given CC2 belongs to the stores department (LOG), CC1 to production
    _budgets(db)
    db.execute(text("insert into department (code, name) values ('PRD', 'Production') on conflict do nothing"))
    db.execute(text("update cost_centre set department_code = 'PRD' where code = 'CC1'"))
    db.execute(text("update cost_centre set department_code = 'LOG' where code = 'CC2'"))
    db.commit()

    # when a stores request without a cost centre is checked
    (group,) = _check(db, _ask(db, "R-1", "1000", cc=None))

    # then only the department's cost centre holds it: 8 months of 50,000
    assert (group.level, group.department, group.cost_centres, group.check.budget) == (
        "department", "LOG", ("CC2",), D("400000"),
    )  # fmt: skip
    assert group.holder == "department LOG at WH-BLR"


def test_a_request_near_its_budget_is_sent_with_the_warning(db: Session) -> None:  # noqa: F811
    _budgets(db)
    _ask(db, "R-A", "700000", cc="CC1", status="approved")

    no = _ask(db, "R-1", "50000", cc="CC1")  # 750,000 of 800,000: 93.75%
    result = _submit(db, no)

    assert result.status == "submitted"
    reason = _row(db, no)["decision_reason"]
    assert "nearly used up: GL G1 93.75% used (cost centre CC1, Apr 2026 to Nov 2026 (FY 2026-27))" in reason
    (saved,) = _row(db, no)["budget_check"]
    assert (saved["status"], saved["used_pct"], saved["warn_pct"], saved["block_pct"]) == ("warn", "93.75", "90", "100")


def test_the_warning_threshold_is_a_company_setting(db: Session) -> None:  # noqa: F811
    _budgets(db)
    db.execute(text("insert into org_setting (key, value) values ('budget_warn_pct', '95')"))
    db.commit()
    _ask(db, "R-A", "700000", cc="CC1", status="approved")

    (group,) = _check(db, _ask(db, "R-1", "50000", cc="CC1"))  # 93.75% is below 95

    assert group.check.status == "ok" and group.warn_pct == D(95)


def test_the_budget_is_checked_again_when_the_approver_decides(db: Session, logins: Logins) -> None:  # noqa: F811
    # given a request of 600,000 that fitted when submitted (policy block)
    _budgets(db, policy="block")
    no = _ask(db, "R-1", "600000", cc="CC1")
    assert _submit(db, no).status == "submitted"

    # when another request of 250,000 is approved meanwhile
    _ask(db, "R-A", "250000", cc="CC1", status="approved")

    # then the approver cannot approve it: the budget is now over by 50,000, and nothing changed
    with pytest.raises(workflow.ApprovalError, match="cannot be approved now: Over budget: GL G1 by ₹ 50,000.00"):
        workflow.approve(db, db.info["slug"], no, logins.stores)
    assert _row(db, no)["status"] == "submitted"
    assert db.execute(text("select count(*) from audit_event where action = 'request.approve'")).scalar_one() == 0

    # when the other request is cancelled, then the approval goes through and the check is kept with it
    db.execute(text("update request set status = 'cancelled' where no = 'R-A'"))
    db.commit()
    assert workflow.approve(db, db.info["slug"], no, logins.stores) == "submitted"  # the director is next
    assert _row(db, no)["budget_check"][0]["used_pct"] == "75.00"  # 600,000 of 800,000
    approved = db.execute(
        text("select after->>'budget' from audit_event where action = 'request.approve' and entity_id = :n"), {"n": no}
    ).scalar_one()
    assert approved.startswith("Within budget")
