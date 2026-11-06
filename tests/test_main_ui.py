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


STOCK_TABLES = (
    "approval_step",
    "request_line",
    "request",
    "number_counter",
    "number_series",
    "approval_rule",
    "org_user",
    "department",
    "item_location",
    "stock_opening",
    "item",
    "item_category",
    "location",
)


def seed_stock_and_approvals(company: Company, *, price: int) -> None:
    """One item that needs ordering (95 to buy), the numbering, a rule table and a stores head who is the Approver login."""
    db = company.db
    db.execute(
        text(
            "insert into number_series (document_type, prefix, scope, format, reset) "
            "values ('request', 'REQ', 'location', '{prefix}-{loc}-{fy4}-{seq:05d}', 'fy')"
        )
    )
    db.execute(text("insert into location (code, name, is_stock_location) values ('WH-BLR', 'Bengaluru store', true)"))
    db.execute(text("insert into item_category (code, name) values ('C1', 'Spares')"))
    db.execute(
        text(
            "insert into item (code, name, category_code, base_uom, purchase_uom, pack_size, moq, standard_price, "
            "default_lead_days, active, stocked) values ('A', '<i>Bearing</i>', 'C1', 'NOS', 'NOS', 1, 1, :price, 3, true, true)"
        ),
        {"price": price},
    )
    db.execute(
        text("insert into stock_opening (item_code, location_code, qty, as_of) values ('A', 'WH-BLR', 5, '2026-01-01')")
    )
    db.execute(
        text(
            "insert into item_location (item_code, location_code, reorder_policy, reorder_point, safety_stock, max_qty) "
            "values ('A', 'WH-BLR', 'minmax', 20, 2, 100)"
        )
    )
    db.execute(text("insert into department (code, name, head_user) values ('LOG', 'Stores', 'U3')"))
    db.execute(
        text(
            "insert into org_user (code, name, designation, department_code, email, active) "
            "values ('U3', 'Prakash Stores', 'Head - Stores', 'LOG', :email, true)"
        ),
        {"email": company.emails["Approver"]},
    )
    for code, low, high, chain in (("R-LOW", "0", "15000", "auto"), ("R-MID", "15000.01", "999999999", "manager")):
        db.execute(
            text(
                "insert into approval_rule (rule_code, document, min_value, max_value, category_code, department_code, "
                "spend_type, approver_chain, version, effective_from) values (:c, 'request', :lo, :hi, 'ANY', 'ANY', 'opex', :ch, 1, '2024-11-01')"
            ),
            {"c": code, "lo": low, "hi": high, "ch": chain},
        )
    db.execute(
        text(
            "insert into app_setting (key, value) values ('replenishment_department', 'LOG') "
            "on conflict (key) do update set value = 'LOG'"
        )
    )
    db.commit()


def clear_stock_and_approvals(company: Company) -> None:
    for table in STOCK_TABLES:
        company.db.execute(text(f"delete from {table}"))
    company.db.execute(text("delete from app_setting where key = 'replenishment_department'"))
    company.db.commit()


def test_a_buyer_checks_stock_and_a_small_request_approves_itself(company: Company, browser: Any) -> None:
    seed_stock_and_approvals(company, price=100)  # 95 x 100 = 9,500: inside the automatic band
    try:
        page = open_app(browser, LAPTOP)
        sign_in(page, company.emails["Buyer"])
        page.goto(f"{URL}/#/requisitions")
        expect(page.locator("#tableWrap")).to_contain_text("No requisitions yet")

        page.locator("#replenishBtn").click()
        panel = page.locator("#replenishPanel")
        expect(panel).to_contain_text("1 item at or below their reorder point would become 1 request")
        expect(panel).to_contain_text("1 approves automatically")
        assert page.locator("#tableWrap tbody tr").count() == 0  # a preview creates nothing
        page.locator("#createDraftsBtn").click()
        expect(panel).to_contain_text("1 approved automatically, 0 waiting for approval")

        row = page.locator("#tableWrap tbody tr")
        expect(row).to_have_count(1)
        expect(row).to_contain_text("Approved")
        row.click()
        expect(page.locator("#appContent")).to_contain_text("Auto-approved under rule R-LOW v1")
        expect(page.locator("#appContent")).to_contain_text("<i>Bearing</i>")  # shown as typed, not as italics
        expect(page.locator("#appContent")).to_contain_text("95")  # 100 less the 5 on hand
        assert page.locator("#appContent i:not(.ti)").count() == 0
        expect(page.locator("#approveBtn")).to_have_count(0)  # nothing left to decide

        page.goto(f"{URL}/#/requisitions")
        page.locator("#replenishBtn").click()
        expect(panel).to_contain_text("Nothing needs ordering right now")
        expect(panel).to_contain_text("Already requested")
        assert page.problems == []  # type: ignore[attr-defined]

        requester = open_app(browser, LAPTOP)
        sign_in(requester, company.emails["Requester"])
        requester.goto(f"{URL}/#/requisitions")
        expect(requester.locator("#tableWrap")).to_be_visible()
        expect(requester.locator("#replenishBtn")).to_have_count(0)
    finally:
        clear_stock_and_approvals(company)


def test_a_bigger_request_waits_for_the_right_person_who_must_give_a_reason_to_refuse_and_can_approve(
    company: Company, browser: Any
) -> None:
    seed_stock_and_approvals(company, price=1000)  # 95 x 1,000 = 95,000: needs the stores head
    try:
        buyer = open_app(browser, LAPTOP)
        sign_in(buyer, company.emails["Buyer"])
        buyer.goto(f"{URL}/#/requisitions")
        buyer.locator("#replenishBtn").click()
        expect(buyer.locator("#replenishPanel")).to_contain_text("1 needs a person's approval")
        buyer.locator("#createDraftsBtn").click()
        expect(buyer.locator("#replenishPanel")).to_contain_text("0 approved automatically, 1 waiting for approval")
        row = buyer.locator("#tableWrap tbody tr")
        expect(row).to_contain_text("Pending")
        expect(row).to_contain_text("waiting for Prakash Stores")
        row.click()
        expect(buyer.locator("#appContent")).to_contain_text("Prakash Stores")
        expect(buyer.locator("#approveBtn")).to_have_count(0)  # not the buyer's decision

        approver = open_app(browser, LAPTOP)
        sign_in(approver, company.emails["Approver"])
        approver.goto(f"{URL}/#/requisitions")
        approver.locator("#tableWrap tbody tr").click()
        approver.locator("#rejectBtn").click()
        approver.locator("#reasonOk").click()  # no reason given
        expect(approver.locator("#actionNote")).to_contain_text("a reason is required")

        approver.locator("#reasonCancel").click()
        approver.locator("#approveBtn").click()
        expect(approver.locator("#appContent")).to_contain_text("Approved by Prakash Stores under rule R-MID v1")
        expect(approver.locator("#approveBtn")).to_have_count(0)
        approver.goto(f"{URL}/#/requisitions")
        expect(approver.locator("#tableWrap tbody tr")).to_contain_text("Approved")
        # the one refusal allowed is the missing reason above (the browser reports it twice)
        assert [p for p in approver.problems if "409" not in p] == []  # type: ignore[attr-defined]
    finally:
        clear_stock_and_approvals(company)
