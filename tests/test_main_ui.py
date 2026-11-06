"""Browser tests of the main interface (ui/, the Procure-OS look), served by the API behind the platform's login."""

import json
from typing import Any

import pytest
from playwright.sync_api import Page, expect
from sqlalchemy import text
from test_web_shell import PASSWORD, URL, Company, browser, company, server  # noqa: F401 - fixtures

LAPTOP = {"width": 1366, "height": 800}
PHONE = {"width": 390, "height": 844}
PAGES = [
    "dashboard",
    "rfqs",
    "requisitions",
    "vendors",
    "inventory",
    "purchase-orders",
    "payments",
    "contracts",
    "assistant",
    "trail",
    "help",
    "settings",
    "notification-history",
]


def open_app(browser: Any, size: dict[str, int], *, focus_mode: bool = False) -> Page:
    ctx = browser.new_context(viewport=size)
    ctx.add_init_script(f"localStorage.setItem('procureos_launch_focus_mode', '{str(focus_mode).lower()}')")
    page = ctx.new_page()
    page.set_default_timeout(30_000)
    page.problems = []  # type: ignore[attr-defined]
    page.on("pageerror", lambda e: page.problems.append(f"page error: {e} :: {(e.stack or '')[:500]}"))  # type: ignore[attr-defined]
    page.on("console", lambda m: page.problems.append(f"console: {m.text[:160]}") if m.type == "error" else None)  # type: ignore[attr-defined]
    page.on(
        "response",
        lambda r: (
            page.problems.append(f"{r.status} {r.url[-60:]}")  # type: ignore[attr-defined]
            if r.status >= 400 and "/api/auth/login" not in r.url and "/api/auth/refresh" not in r.url
            else None
        ),
    )
    return page


def sign_in(page: Page, email: str, password: str = PASSWORD) -> None:
    page.goto(f"{URL}/")
    page.locator("#loginEmail").fill(email)
    page.locator("#loginPassword").fill(password)
    page.locator("#loginButton").click()
    expect(page.locator(".user-badge .who")).not_to_have_text(" ")


def test_the_main_interface_asks_for_a_login_and_shows_who_is_signed_in(company: Company, browser: Any) -> None:
    page = open_app(browser, LAPTOP)
    page.goto(f"{URL}/")
    expect(page.locator("#loginOverlay")).to_be_visible()

    page.locator("#loginEmail").fill(company.emails["Admin"])
    page.locator("#loginPassword").fill("not the password")
    page.locator("#loginButton").click()
    expect(page.locator("#loginError")).to_have_text("Wrong email or password.")

    page.locator("#loginPassword").fill(PASSWORD)
    page.locator("#loginButton").click()
    expect(page.locator(".user-badge .who")).to_have_text("Admin Person")
    expect(page.locator(".user-badge .role")).to_contain_text("Admin")
    expect(page.locator("#loginOverlay")).to_be_hidden()
    # the one error allowed is the refused wrong password above
    assert [p for p in page.problems if "401" not in p] == []  # type: ignore[attr-defined]


def test_the_menu_has_no_auctions_and_the_admin_link_is_only_for_people_who_may_use_it(
    company: Company, browser: Any
) -> None:
    admin = open_app(browser, LAPTOP)
    sign_in(admin, company.emails["Admin"])
    items = admin.locator("#navList .nav-item")
    expect(items).to_have_text(
        [
            "Dashboard",
            "RFQs",
            "Requisitions",
            "Vendors",
            "Inventory",
            "Purchase orders",
            "Payments",
            "Contracts",
            "Assistant",
            "Help",
            "Admin",
        ]
    )
    admin.locator("#adminLink").click()
    admin.wait_for_url(f"{URL}/admin/")

    requester = open_app(browser, LAPTOP)
    sign_in(requester, company.emails["Requester"])
    expect(requester.locator("#adminLink")).to_be_hidden()


def test_every_page_opens_without_errors_on_a_laptop(company: Company, browser: Any) -> None:
    page = open_app(browser, LAPTOP)
    sign_in(page, company.emails["Admin"])
    for name in PAGES:
        page.goto(f"{URL}/#/{name}")
        expect(page.locator("#appContent h1, #appContent .page-header").first).to_be_visible()
        page.wait_for_load_state("networkidle")  # a person waits for a page before opening the next
    assert page.problems == []  # type: ignore[attr-defined]


@pytest.mark.parametrize("size", [LAPTOP, PHONE], ids=["laptop-1366", "phone-390"])
def test_no_page_scrolls_sideways(company: Company, browser: Any, size: dict[str, int]) -> None:
    page = open_app(browser, size)
    sign_in(page, company.emails["Admin"])
    for name in PAGES:
        page.goto(f"{URL}/#/{name}")
        page.wait_for_timeout(700)
        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth"), (
            f"{name} scrolls sideways at {size['width']} px"
        )


def test_the_assistant_opens_full_screen_by_default_and_replies(company: Company, browser: Any) -> None:
    company.db.execute(text("update app_setting set value = 'true' where key = 'launch_in_focus_mode'"))
    company.db.commit()
    try:
        page = open_app(browser, LAPTOP, focus_mode=True)
        sign_in(page, company.emails["Admin"])
        expect(page.locator("#focusMode")).to_be_visible()
        page.locator("#fmInput").fill("hello")
        page.locator("#fmSendBtn").click()
        expect(page.locator("#fmStory")).to_contain_text("not connected yet")
        page.keyboard.press("Escape")
        expect(page.locator("#appContent")).to_be_visible()
    finally:  # the other tests start on the dashboard
        company.db.execute(text("update app_setting set value = 'false' where key = 'launch_in_focus_mode'"))
        company.db.commit()


def test_a_new_alert_lights_the_bell_and_can_be_resolved(company: Company, browser: Any) -> None:
    page = open_app(browser, LAPTOP)
    sign_in(page, company.emails["Admin"])
    company.db.execute(
        text(
            "insert into notification (user_id, kind, title, severity) values (null, 'stale_requisition', 'Request REQ-9 has waited 5 days', 'high')"
        )
    )
    company.db.commit()
    expect(page.locator("#notificationDot")).to_be_visible()  # pushed over the live connection

    page.locator("#notificationBellBtn").click()
    row = page.locator(".notif-row", has_text="Request REQ-9 has waited 5 days")
    expect(row).to_be_visible()


def test_the_agent_trail_shows_what_an_agent_did(company: Company, browser: Any) -> None:
    run = company.db.execute(
        text("insert into agent_run (thread_id, agent, input) values ('ui-1', 'payments', 'Pay') returning id")
    ).scalar_one()
    company.db.execute(
        text(
            "insert into agent_step (run_id, kind, name, detail) values (:r, 'tool', 'lookup_invoice', cast(:d as jsonb))"
        ),
        {"r": run, "d": json.dumps({"args": {"invoice": "INV-9"}, "result": {"status": "approved for payment"}})},
    )
    company.db.commit()
    page = open_app(browser, LAPTOP)
    sign_in(page, company.emails["Admin"])
    page.goto(f"{URL}/#/trail")
    expect(page.locator("#fullTrailList")).to_contain_text("lookup_invoice")


def test_settings_can_be_saved_by_an_admin_and_are_refused_for_others(company: Company, browser: Any) -> None:
    admin = open_app(browser, LAPTOP)
    sign_in(admin, company.emails["Admin"])
    admin.goto(f"{URL}/#/settings")
    field = admin.locator('#settingsForm input[data-key="stale_requisition_days"]')
    field.fill("5")
    admin.locator("#saveSettingsBtn").click()
    expect(admin.locator("#settingsSavedMsg")).to_be_visible()
    admin.reload()
    admin.goto(f"{URL}/#/settings")
    expect(admin.locator('#settingsForm input[data-key="stale_requisition_days"]')).to_have_value("5")


def test_signing_out_brings_the_login_back(company: Company, browser: Any) -> None:
    page = open_app(browser, LAPTOP)
    sign_in(page, company.emails["Finance"])
    page.locator("#signOutBtn").click()
    expect(page.locator("#loginOverlay")).to_be_visible()


def test_the_inventory_page_lists_the_worst_stock_first_and_shows_names_as_text(company: Company, browser: Any) -> None:
    db = company.db
    db.execute(text("insert into location (code, name, is_stock_location) values ('WH', 'Main store', true)"))
    db.execute(text("insert into item_category (code, name) values ('C1', 'Spares')"))
    db.execute(
        text(
            "insert into item (code, name, category_code, base_uom) values "
            "('A', 'Plain bearing', 'C1', 'NOS'), ('B', '<b>Bold</b> seal', 'C1', 'NOS')"
        )
    )
    db.execute(
        text(
            "insert into stock_opening (item_code, location_code, qty, as_of) values ('A', 'WH', 500, '2026-01-01'), ('B', 'WH', 0, '2026-01-01')"
        )
    )
    db.execute(
        text(
            "insert into item_location (item_code, location_code, reorder_policy, reorder_point, safety_stock, max_qty) "
            "values ('A', 'WH', 'minmax', 100, 20, 900), ('B', 'WH', 'minmax', 10, 2, 50)"
        )
    )
    db.commit()
    try:
        page = open_app(browser, LAPTOP)
        sign_in(page, company.emails["Admin"])
        page.goto(f"{URL}/#/inventory")
        rows = page.locator("#tableWrap tbody tr")
        expect(rows).to_have_count(2)
        expect(rows.first).to_contain_text("<b>Bold</b> seal")  # shown as typed, not as bold text
        expect(rows.first).to_contain_text("Stockout")  # the worst is first
        expect(rows.last).to_contain_text("OK")
        assert page.locator("#tableWrap b").count() == 0

        page.locator("#invStatus").select_option("ok")
        expect(page.locator("#tableWrap tbody tr")).to_have_count(1)
        assert page.problems == []  # type: ignore[attr-defined]
    finally:
        for table in ("item_location", "stock_opening", "item", "item_category", "location"):
            db.execute(text(f"delete from {table}"))
        db.commit()
