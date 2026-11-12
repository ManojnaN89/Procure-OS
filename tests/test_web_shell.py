"""Browser tests of the web app (Playwright, headless Chromium) against the real API and the built frontend.

Needs `npm run build` in frontend/. They check that login works, navigation follows the role, money shows in lakh and
crore, an approval can be decided, and that no page scrolls sideways on a 1366 px laptop or a 390 px phone.
"""

import os
import subprocess
import sys
import time
import uuid
from collections.abc import Iterator
from decimal import Decimal
from pathlib import Path
from typing import Any

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

from p2p.config import REPO_ROOT
from p2p.platform import proposals, routing, tenants, users
from p2p.platform.sod import Actor
from p2p.platform.tokens import create_access_token
from p2p.rules import engine as rules

pytest.importorskip("playwright")
from playwright.sync_api import Page, expect, sync_playwright  # noqa: E402

# A slow machine (the full suite also loads OCR models) must not turn into a false failure.
expect.set_options(timeout=20_000)

DIST = REPO_ROOT / "frontend" / "dist" / "index.html"
PORT = int(os.environ.get("P2P_WEB_TEST_PORT", "18080"))  # a second run at the same time sets its own
URL = f"http://127.0.0.1:{PORT}"
ADMIN = f"{URL}/admin"  # the admin app lives under /admin
PASSWORD = "correct horse battery"
SHOTS = Path(os.environ["SHOT_DIR"]) if os.environ.get("SHOT_DIR") else None
LAPTOP = {"width": 1366, "height": 768}
PHONE = {"width": 390, "height": 844}

pytestmark = pytest.mark.skipif(not DIST.is_file(), reason="frontend is not built (cd frontend && npm run build)")


@pytest.fixture(scope="module")
def server(control_db: str) -> Iterator[subprocess.Popen[bytes]]:
    try:
        httpx.get(f"{URL}/api/health", timeout=1)
        pytest.exit(f"port {PORT} is already in use (another test run?): set P2P_WEB_TEST_PORT", returncode=3)
    except httpx.HTTPError:
        pass  # free, as it should be: otherwise the tests would talk to another run's server and database
    py = REPO_ROOT / "backend" / ".venv" / ("Scripts" if sys.platform == "win32" else "bin") / "python"
    proc = subprocess.Popen(  # noqa: S603
        [str(py), "-m", "uvicorn", "p2p.main:app", "--port", str(PORT), "--log-level", "warning"],
        cwd=REPO_ROOT / "backend",
        env={**os.environ, "EXTRA_MODULES": "web_test_support", "PYTHONPATH": str(REPO_ROOT / "tests")},
    )
    deadline = time.time() + 90  # a busy machine can take over 30 s to start
    while time.time() < deadline and proc.poll() is None:
        try:
            if httpx.get(f"{URL}/api/health", timeout=1).status_code == 200:
                break
        except httpx.HTTPError:
            time.sleep(0.3)
    else:
        proc.terminate()
        pytest.fail("the API did not start")
    yield proc
    proc.terminate()
    proc.wait(timeout=10)


class Company:
    def __init__(self) -> None:
        self.slug = "t" + uuid.uuid4().hex[:8]
        tenants.create_tenant(self.slug, "Demo Engineering Pvt Ltd", load_reference=False)
        self.db = Session(routing.engine_for(self.slug))
        self.emails: dict[str, str] = {}
        self.ids: dict[str, int] = {}
        for role in ("Admin", "Finance", "Requester", "Buyer", "Approver", "Store"):
            self.emails[role] = f"{role.lower()}{uuid.uuid4().hex[:5]}@example.com"
            uid = users.create_user(self.slug, self.emails[role], PASSWORD, f"{role} Person", role)
            self.ids[role] = uid
            if role == "Finance":
                users.set_limit(self.slug, uid, "payment_run", Decimal("50000000"))
        from test_rules_engine import approval_table  # noqa: PLC0415

        admin = Actor("user", "1")
        rules.save_draft(self.db, "approval", approval_table(), admin)
        rules.publish(self.db, "approval", admin)
        self.db.execute(text("insert into app_setting (key, value) values ('launch_in_focus_mode', 'false')"))
        self.db.commit()
        proposals.propose(
            self.db,
            Actor("agent", "payments"),
            action="test.web_release",
            summary="Release payment for INV-77",
            payload={"invoice": "INV-77"},
            required_permission="payment.approve",
            document="payment_run",
            value=Decimal("1250000"),
            risk="high",
        )


@pytest.fixture(scope="module")
def company(control_db: str, server: subprocess.Popen[bytes]) -> Iterator[Company]:
    c = Company()
    yield c
    c.db.close()
    routing.clear_engine_cache()


@pytest.fixture(scope="module")
def browser() -> Iterator[Any]:
    with sync_playwright() as p:
        b = p.chromium.launch()
        yield b
        b.close()


def open_page(browser: Any, size: dict[str, int]) -> Page:
    page = browser.new_context(viewport=size).new_page()
    page.set_default_timeout(30_000)
    page.errors = []  # type: ignore[attr-defined]
    page.on("pageerror", lambda e: page.errors.append(str(e)))  # type: ignore[attr-defined]
    page.console_errors = []  # type: ignore[attr-defined]
    page.on("console", lambda m: page.console_errors.append(m.text) if m.type == "error" else None)  # type: ignore[attr-defined]
    return page


def sign_in(page: Page, email: str) -> None:
    page.goto(f"{ADMIN}/login")
    page.get_by_label("Email").fill(email)
    page.get_by_label("Password").fill(PASSWORD)
    page.get_by_role("button", name="Sign in").click()
    page.wait_for_url(f"{ADMIN}/")


def no_sideways_scroll(page: Page) -> bool:
    return bool(page.evaluate("document.documentElement.scrollWidth <= window.innerWidth"))


def shot(page: Page, name: str) -> None:
    if SHOTS:
        SHOTS.mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(SHOTS / f"{name}.png"), full_page=False)


# ---------------------------------------------------------------------------------------------


def test_login_rejects_a_wrong_password_and_accepts_the_right_one(company: Company, browser: Any) -> None:
    page = open_page(browser, LAPTOP)
    page.goto(f"{ADMIN}/login")
    page.get_by_label("Email").fill(company.emails["Admin"])
    page.get_by_label("Password").fill("not the password")
    page.get_by_role("button", name="Sign in").click()
    assert page.get_by_role("alert").inner_text() == "Wrong email or password."

    page.get_by_label("Password").fill(PASSWORD)
    page.get_by_role("button", name="Sign in").click()
    page.wait_for_url(f"{ADMIN}/")
    expect(page.get_by_text("Welcome, Admin Person")).to_be_visible()
    expect(page.get_by_text("Demo Engineering Pvt Ltd").first).to_be_visible()  # the company name is in the header
    assert page.errors == []  # type: ignore[attr-defined]


def test_a_visitor_without_a_login_is_sent_to_the_login_page(company: Company, browser: Any) -> None:
    page = open_page(browser, LAPTOP)
    page.goto(f"{ADMIN}/audit")
    page.wait_for_url(f"{ADMIN}/login")


def test_the_menu_follows_the_role(company: Company, browser: Any) -> None:
    requester = open_page(browser, LAPTOP)
    sign_in(requester, company.emails["Requester"])
    nav = requester.get_by_label("Main")
    page_ready = requester.get_by_text("Welcome, Requester Person")
    expect(page_ready).to_be_visible()
    expect(nav.get_by_role("link")).to_have_text(["Home"])

    admin = open_page(browser, LAPTOP)
    sign_in(admin, company.emails["Admin"])
    expect(admin.get_by_label("Main").get_by_role("link")).to_have_text(
        [
            "Home",
            "Company setup",
            "Automation",
            "Audit",
            "Agent runs",
            "Rule decisions",
            "Model calls",
            "Rules",
            "Users",
        ]
    )
    shot(admin, "laptop_home_admin")


def test_a_finance_user_sees_the_amount_in_lakh_and_can_approve(company: Company, browser: Any) -> None:
    page = open_page(browser, LAPTOP)
    sign_in(page, company.emails["Finance"])
    page.get_by_role("link", name="Approvals").first.click()

    expect(page.get_by_text("Release payment for INV-77")).to_be_visible()
    expect(page.get_by_text("₹ 12.50 lakh")).to_be_visible()  # 12,50,000 in the Indian way
    shot(page, "laptop_approvals")

    page.get_by_role("button", name="Approve", exact=True).click()
    expect(page.get_by_text("Nothing is waiting for you.")).to_be_visible()
    assert page.errors == []  # type: ignore[attr-defined]


def test_the_audit_page_shows_the_trail(company: Company, browser: Any) -> None:
    page = open_page(browser, LAPTOP)
    sign_in(page, company.emails["Admin"])
    page.get_by_role("link", name="Audit").click()
    page.get_by_label("Action").fill("rule.publish")
    page.get_by_role("button", name="Search").click()

    expect(page.get_by_role("cell", name="rule.publish").first).to_be_visible()
    page.get_by_role("button", name="Details").first.click()
    expect(page.get_by_text("approval@1").first).to_be_visible()


def test_the_decision_table_editor_opens(company: Company, browser: Any) -> None:
    page = open_page(browser, LAPTOP)
    sign_in(page, company.emails["Admin"])
    page.get_by_role("link", name="Rules").click()
    page.get_by_role("link", name="Open editor").click()

    try:
        page.get_by_role("button", name="Save draft").wait_for(timeout=20000)
    except Exception as exc:
        shot(page, "laptop_rule_editor_failed")
        raise AssertionError(
            f"editor did not open: errors={page.errors} console={page.console_errors[:3]} body={page.inner_text('body')[:500]!r}"
        ) from exc  # type: ignore[attr-defined]
    page.wait_for_timeout(1500)  # let the graph draw
    assert page.locator(".react-flow, .grl-dg").count() > 0
    shot(page, "laptop_rule_editor")


@pytest.mark.parametrize("size", [LAPTOP, PHONE], ids=["laptop-1366", "phone-390"])
def test_no_page_scrolls_sideways(company: Company, browser: Any, size: dict[str, int]) -> None:
    page = open_page(browser, size)
    sign_in(page, company.emails["Admin"])
    name = "laptop" if size is LAPTOP else "phone"
    for path in ("/", "/users", "/audit", "/runs", "/decisions", "/model-calls", "/rules"):
        page.goto(f"{ADMIN}{path}")
        page.wait_for_load_state("networkidle")
        assert no_sideways_scroll(page), f"{path} scrolls sideways at {size['width']} px"
        shot(page, f"{name}{path.replace('/', '_') or '_home'}")


def test_on_a_phone_the_menu_opens_from_a_button(company: Company, browser: Any) -> None:
    page = open_page(browser, PHONE)
    sign_in(page, company.emails["Admin"])
    page.get_by_role("button", name="Menu").click()
    page.get_by_role("link", name="Users").last.click()

    page.wait_for_url(f"{ADMIN}/users")
    expect(page.get_by_role("heading", name="Users and roles")).to_be_visible()
    shot(page, "phone_users_after_menu")


# ---- Company setup and Automation controls ---------------------------------------------------

REASON = "Reason for the change you are about to make (needed for every change)"


def _row(page: Page, text_in_row: str) -> Any:
    return page.get_by_role("row").filter(has_text=text_in_row)


def test_company_setup_opens_every_tab_for_the_admin(company: Company, browser: Any) -> None:
    page = open_page(browser, LAPTOP)
    sign_in(page, company.emails["Admin"])
    page.get_by_role("link", name="Company setup").click()
    expect(page.get_by_role("heading", name="Company setup")).to_be_visible()

    expect(page.get_by_role("tab")).to_have_text(
        ["People", "Roles", "Approvers", "Approval chains", "Policies", "History"]
    )
    expect(page.get_by_role("button", name="Add person")).to_be_visible()  # People opens first
    expect(page.get_by_text("Finance Person").first).to_be_visible()
    for tab, probe in (
        ("Roles", "Save Admin"),  # one box per role, the Admin first
        ("Approvers", "Set head"),
        ("Approval chains", "Save new version"),
        ("Policies", "Save tolerance"),
    ):
        page.get_by_role("tab", name=tab).click()
        expect(page.get_by_role("button", name=probe)).to_be_visible()
        assert page.get_by_role("tab", name=tab).get_attribute("aria-selected") == "true"
    page.get_by_role("tab", name="History").click()
    expect(page.get_by_text("Every change, newest first")).to_be_visible()
    assert page.errors == []  # type: ignore[attr-defined]


def test_a_policy_change_needs_a_reason_is_checked_and_shows_in_the_history_and_the_audit_page(
    company: Company, browser: Any
) -> None:
    page = open_page(browser, LAPTOP)
    sign_in(page, company.emails["Admin"])
    page.get_by_role("link", name="Company setup").click()
    page.get_by_role("tab", name="Policies").click()
    days = _row(page, "Days suppliers get to quote")
    days.get_by_role("textbox").fill("6")

    days.get_by_role("button", name="Save").click()
    expect(page.get_by_role("alert")).to_have_text("Write the reason for the change at the top first.")

    page.get_by_label(REASON).fill("suppliers asked for a day more")
    days.get_by_role("button", name="Save").click()
    expect(days).to_contain_text("suppliers asked for a day more")  # the row says who changed it last and why

    box = _row(page, "Requests mailbox")  # the two mailboxes live here too
    box.get_by_role("textbox").fill("not an address")
    box.get_by_role("button", name="Save").click()
    expect(page.get_by_role("alert")).to_contain_text("must be an email address")
    box.get_by_role("textbox").fill("Requests@Plant.example")
    box.get_by_role("button", name="Save").click()
    expect(box).to_contain_text("suppliers asked for a day more")
    expect(box.get_by_role("textbox")).to_have_value("requests@plant.example")  # stored in lower case

    page.get_by_role("tab", name="History").click()
    expect(_row(page, "request_mailbox")).to_contain_text("requests@plant.example")
    expect(_row(page, "default_rfq_response_days")).to_contain_text("suppliers asked for a day more")

    page.get_by_role("link", name="Audit").click()
    page.get_by_label("Action").fill("setup.policy")
    page.get_by_role("button", name="Search").click()
    expect(page.get_by_role("cell", name="setup.policy").first).to_be_visible()
    assert page.get_by_role("cell", name="setup.policy").count() >= 2
    assert page.errors == []  # type: ignore[attr-defined]


def test_a_person_is_added_from_company_setup_with_a_reason(company: Company, browser: Any) -> None:
    page = open_page(browser, LAPTOP)
    sign_in(page, company.emails["Admin"])
    page.get_by_role("link", name="Company setup").click()
    page.get_by_label("Email", exact=True).fill(f"new{uuid.uuid4().hex[:5]}@example.com")
    page.get_by_label("Name", exact=True).fill("New Joiner")
    page.get_by_label("Password (at least 10 characters)").fill(PASSWORD)

    page.get_by_role("button", name="Add person").click()
    expect(page.get_by_role("alert")).to_have_text("Write the reason for the change at the top first.")
    page.get_by_label(REASON).fill("new store assistant joined")
    page.get_by_role("button", name="Add person").click()

    expect(page.get_by_role("cell", name="New Joiner").first).to_be_visible()
    page.get_by_role("tab", name="History").click()
    expect(page.get_by_role("cell", name="new store assistant joined").first).to_be_visible()


def test_automation_controls_set_a_level_with_a_reason_and_the_locked_ones_stay_with_a_person(
    company: Company, browser: Any
) -> None:
    page = open_page(browser, LAPTOP)
    sign_in(page, company.emails["Admin"])
    page.get_by_role("link", name="Automation").click()
    expect(page.get_by_role("heading", name="Automation controls")).to_be_visible()

    # paying, activating a supplier and releasing a block have no level to choose
    expect(page.get_by_text("person only")).to_have_count(3)
    for locked in ("Approve a payment run", "Activate or block a supplier", "Release a payment block on an invoice"):
        row = _row(page, locked)
        assert row.get_by_role("combobox").count() == 0 and row.get_by_role("button", name="Save").count() == 0

    quote = _row(page, "Accept a quote read from an email")
    quote.get_by_role("combobox").select_option("propose")
    quote.get_by_role("button", name="Save").click()
    expect(page.get_by_role("alert")).to_have_text("Write the reason for the change first.")

    page.get_by_label(REASON).fill("a person checks every quote for now")
    quote.get_by_role("button", name="Save").click()
    page.wait_for_load_state("networkidle")
    page.reload()
    expect(_row(page, "Accept a quote read from an email").get_by_role("combobox")).to_have_value("propose")

    # the server refuses the locked ones even when asked directly
    token = create_access_token(company.ids["Admin"], company.slug, "Admin")
    asked = httpx.put(
        f"{URL}/api/setup/automation/payment_run.approve",
        json={"level": "auto", "min_confidence": "0.9", "max_wrong_rate": "0.02", "reason": "try"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert asked.status_code == 422 and "can never be set to act alone" in asked.json()["detail"]
    assert page.errors == []  # type: ignore[attr-defined]


def test_the_kill_switch_stops_everything_and_switches_back(company: Company, browser: Any) -> None:
    page = open_page(browser, LAPTOP)
    sign_in(page, company.emails["Admin"])
    page.get_by_role("link", name="Automation").click()
    expect(page.get_by_text("The kill switch is off.")).to_be_visible()

    page.get_by_role("button", name="Stop all automation").click()
    expect(page.get_by_role("alert")).to_have_text("Write the reason for the change first.")
    page.get_by_label(REASON).fill("checking the supplier agent")
    page.get_by_role("button", name="Stop all automation").click()
    expect(page.get_by_text("The kill switch is ON: nothing runs automatically.")).to_be_visible()
    expect(page.get_by_text("checking the supplier agent")).to_be_visible()

    page.get_by_label(REASON).fill("check finished")
    page.get_by_role("button", name="Switch off").click()
    expect(page.get_by_text("The kill switch is off.")).to_be_visible()


def test_a_person_who_opens_a_page_of_another_role_gets_a_plain_message_and_no_failed_request(
    company: Company, browser: Any
) -> None:
    page = open_page(browser, LAPTOP)
    refused: list[str] = []
    page.on("response", lambda r: refused.append(r.url) if r.status == 403 else None)
    sign_in(page, company.emails["Requester"])
    for path in ("audit", "rules", "users", "runs", "approvals"):
        page.goto(f"{ADMIN}/{path}")
        expect(page.get_by_text("This page is not for your role")).to_be_visible()
    assert refused == []  # the screens do not ask the server for what the role may not see


def test_a_person_who_is_not_the_admin_sees_neither_page(company: Company, browser: Any) -> None:
    for role in ("Requester", "Buyer", "Finance"):
        page = open_page(browser, LAPTOP)
        sign_in(page, company.emails[role])
        names = page.get_by_label("Main").get_by_role("link").all_inner_texts()
        assert "Company setup" not in names and "Automation" not in names, role

        page.goto(f"{ADMIN}/setup")
        expect(page.get_by_text("You do not have access to the company setup.")).to_be_visible()
        assert page.get_by_role("button", name="Add person").count() == 0
        page.goto(f"{ADMIN}/automation")
        expect(page.get_by_text("This page is for the Admin.")).to_be_visible()
        assert page.get_by_role("button", name="Stop all automation").count() == 0

        token = create_access_token(company.ids[role], company.slug, role)
        for path in ("/api/setup/people", "/api/setup/policies", "/api/setup/automation"):
            refused = httpx.get(f"{URL}{path}", headers={"Authorization": f"Bearer {token}"})
            assert refused.status_code == 403, (role, path)
