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
from p2p.rules import engine as rules

pytest.importorskip("playwright")
from playwright.sync_api import Page, expect, sync_playwright  # noqa: E402

# A slow machine (the full suite also loads OCR models) must not turn into a false failure.
expect.set_options(timeout=20_000)

DIST = REPO_ROOT / "frontend" / "dist" / "index.html"
PORT = 18080
URL = f"http://127.0.0.1:{PORT}"
ADMIN = f"{URL}/admin"  # the admin app lives under /admin
PASSWORD = "correct horse battery"
SHOTS = Path(os.environ["SHOT_DIR"]) if os.environ.get("SHOT_DIR") else None
LAPTOP = {"width": 1366, "height": 768}
PHONE = {"width": 390, "height": 844}

pytestmark = pytest.mark.skipif(not DIST.is_file(), reason="frontend is not built (cd frontend && npm run build)")


@pytest.fixture(scope="module")
def server(control_db: str) -> Iterator[subprocess.Popen[bytes]]:
    py = REPO_ROOT / "backend" / ".venv" / ("Scripts" if sys.platform == "win32" else "bin") / "python"
    proc = subprocess.Popen(  # noqa: S603
        [str(py), "-m", "uvicorn", "p2p.main:app", "--port", str(PORT), "--log-level", "warning"],
        cwd=REPO_ROOT / "backend",
        env={**os.environ, "EXTRA_MODULES": "web_test_support", "PYTHONPATH": str(REPO_ROOT / "tests")},
    )
    deadline = time.time() + 30
    while time.time() < deadline:
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
        for role in ("Admin", "Finance", "Requester"):
            self.emails[role] = f"{role.lower()}{uuid.uuid4().hex[:5]}@example.com"
            uid = users.create_user(self.slug, self.emails[role], PASSWORD, f"{role} Person", role)
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
        ["Home", "Rules", "Users", "Audit", "Agent runs", "Rule decisions", "Model calls"]
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
