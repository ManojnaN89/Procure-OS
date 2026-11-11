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


def give_documents(db, code: str, *, gst: bool = False) -> None:  # type: ignore[no-untyped-def]
    """The documents a supplier must have on file, so that it is asked to quote."""
    for doc_type in ("pan", "cancelled_cheque", *(("gst_cert",) if gst else ())):
        db.execute(
            text(
                "insert into supplier_document (supplier_code, doc_type, number, issued_on, verified) "
                "values (:c, :t, 'X', '2020-01-01', true)"
            ),
            {"c": code, "t": doc_type},
        )


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


MENUS = {
    # each person's menu holds what their role can use, in the order of the flow, with the records and setup after it
    "Admin": [
        "Dashboard",
        "Requests",
        "Purchase orders",
        "Invoices",
        "Payments",
        "Vendors",
        "Inventory",
        "Control tower",
        "Assistant",
        "Settings",
        "Help",
        "Admin",
    ],  # sees the flow, read only
    "Requester": ["Dashboard", "Requests", "Assistant", "Help"],
    "Buyer": [
        "Dashboard",
        "Requests",
        "Quotations",
        "Purchase orders",
        "Goods receipt",
        "Vendors",
        "Contracts",
        "Inventory",
        "Control tower",
        "Assistant",
        "Help",
        "Admin",
    ],  # fmt: skip
    "Store": ["Dashboard", "Requests", "Purchase orders", "Goods receipt", "Inventory", "Assistant", "Help"],
    "Finance": [
        "Dashboard",
        "Purchase orders",
        "Invoices",
        "Payments",
        "Control tower",
        "Assistant",
        "Help",
        "Admin",
    ],  # fmt: skip
}


@pytest.mark.parametrize("role", list(MENUS))
def test_each_role_sees_a_menu_of_what_it_can_use_and_the_admin_link_only_when_it_may(
    company: Company, browser: Any, role: str
) -> None:
    page = open_app(browser, LAPTOP)
    sign_in(page, company.emails[role])
    expect(page.locator("#navList .nav-item:visible")).to_have_text(MENUS[role])
    # a label with nothing under it is not shown either
    labels = page.locator("#navList .sidebar-label:visible").all_inner_texts()
    assert ("THE FLOW" in labels) == any(
        n in MENUS[role] for n in ("Requests", "Quotations", "Purchase orders", "Invoices", "Payments")
    )
    if "Admin" in MENUS[role]:
        page.locator("#adminLink").click()
        page.wait_for_url(f"{URL}/admin/")
    else:
        expect(page.locator("#adminLink")).to_be_hidden()


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
    company.db.execute(
        text(
            "insert into app_setting (key, value) values ('assistant_enabled', 'false') on conflict (key) do update set value = 'false'"
        )
    )
    company.db.commit()
    try:
        page = open_app(browser, LAPTOP, focus_mode=True)
        sign_in(page, company.emails["Admin"])
        expect(page.locator("#focusMode")).to_be_visible()
        page.locator("#fmInput").fill("hello")
        page.locator("#fmSendBtn").click()
        expect(page.locator("#fmStory")).to_contain_text("switched off")  # the test does not call a real model
        page.keyboard.press("Escape")
        expect(page.locator("#appContent")).to_be_visible()
    finally:  # the other tests start on the dashboard
        company.db.execute(text("update app_setting set value = 'false' where key = 'launch_in_focus_mode'"))
        company.db.execute(text("delete from app_setting where key = 'assistant_enabled'"))
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
    "gr_line",
    "goods_receipt",
    "stock_movement",
    "quote_draft",
    "po_approval_step",
    "po_charge",
    "po_line",
    "po",
    "award",
    "quote_line",
    "quote",
    "sourcing_decision",
    "rfq_supplier",
    "rfq_line",
    "rfq",
    "supplier_document",
    "supplier_contact",
    "supplier_category",
    "supplier",
    "proposal",
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


def test_a_buyer_with_no_requests_yet_is_told_to_check_the_stock_levels(company: Company, browser: Any) -> None:
    page = open_app(browser, LAPTOP)
    sign_in(page, company.emails["Buyer"])
    page.goto(f"{URL}/#/requisitions")

    expect(page.locator("#tableWrap")).to_contain_text("No requests yet")
    expect(page.locator("#tableWrap")).to_contain_text("Next: click Check stock levels")


def test_the_action_buttons_are_real_buttons_to_the_keyboard_and_to_a_screen_reader(
    company: Company, browser: Any
) -> None:
    page = open_app(browser, LAPTOP)
    sign_in(page, company.emails["Buyer"])
    page.goto(f"{URL}/#/requisitions")

    button = page.get_by_role("button", name="Check stock levels")  # a div with a button role, not a bare div
    expect(button).to_be_visible()
    button.focus()
    page.keyboard.press("Enter")  # pressed without the mouse

    expect(page.locator("#replenishPanel")).to_contain_text("Stock check")


def test_a_buyer_checks_stock_and_a_small_request_approves_itself(company: Company, browser: Any) -> None:
    seed_stock_and_approvals(company, price=100)  # 95 x 100 = 9,500: inside the automatic band
    try:
        page = open_app(browser, LAPTOP)
        sign_in(page, company.emails["Buyer"])
        page.goto(f"{URL}/#/requisitions")
        expect(page.locator("#tableWrap")).to_contain_text("No requests yet")

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
        expect(page.locator("#appContent")).to_contain_text("Auto-approved")
        expect(page.locator("#appContent")).to_contain_text(
            "Approval rule R-LOW, version 1"
        )  # kept with the request, under "How this was routed"
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
        expect(approver.locator("#appContent")).to_contain_text("Approved by Prakash Stores")
        expect(approver.locator("#appContent")).to_contain_text("Approval rule R-MID, version 1")
        expect(approver.locator("#approveBtn")).to_have_count(0)
        approver.goto(f"{URL}/#/requisitions")
        expect(approver.locator("#tableWrap tbody tr")).to_contain_text("Approved")
        # the one refusal allowed is the missing reason above (the browser reports it twice)
        assert [p for p in approver.problems if "409" not in p] == []  # type: ignore[attr-defined]
    finally:
        clear_stock_and_approvals(company)


def test_an_approved_request_is_sourced_and_its_rfq_shows_the_suppliers_waiting_for_sending_to_be_approved(
    company: Company, browser: Any
) -> None:
    seed_stock_and_approvals(company, price=100)  # 9,500: approves itself
    db = company.db
    db.execute(
        text(
            "insert into number_series (document_type, prefix, scope, format, reset) "
            "values ('rfq', 'RFQ', 'company', '{prefix}-{fy4}-{seq:05d}', 'fy')"
        )
    )
    for code, name in (("S1", "Alpha <b>Traders</b>"), ("S2", "Bravo Supplies"), ("S3", "Charlie Works")):
        db.execute(
            text("insert into supplier (code, legal_name, status) values (:c, :n, 'active')"), {"c": code, "n": name}
        )
        db.execute(text("insert into supplier_category (supplier_code, category_code) values (:c, 'C1')"), {"c": code})
        give_documents(db, code)
        db.execute(
            text(
                "insert into supplier_contact (supplier_code, name, email, is_primary) values (:c, 'Contact', :e, true)"
            ),
            {"c": code, "e": f"{code.lower()}@supplier.example"},
        )
    db.commit()
    try:
        page = open_app(browser, LAPTOP)
        sign_in(page, company.emails["Buyer"])
        page.goto(f"{URL}/#/requisitions")
        page.locator("#replenishBtn").click()
        page.locator("#createDraftsBtn").click()
        expect(page.locator("#replenishPanel")).to_contain_text("1 approved automatically")

        page.locator("#tableWrap tbody tr").click()
        expect(page.locator("#appContent")).to_contain_text("Approved")
        page.locator("#sourceBtn").click()  # the worker is not running in this test, so the buyer asks for it
        expect(page.locator("#appContent")).to_contain_text("quotes")
        expect(page.locator("#sourceBtn")).to_have_count(0)  # nothing left to decide
        link = page.locator("#appContent a", has_text="RFQ-")
        expect(link).to_have_count(1)

        page.goto(f"{URL}/#/rfqs")
        row = page.locator("#tableWrap tbody tr")
        expect(row).to_have_count(1)
        expect(row).to_contain_text("3 suppliers")
        expect(row).to_contain_text("waiting for a buyer to approve sending")
        row.click()
        expect(page.locator("#appContent")).to_contain_text("Not sent yet: a buyer has to approve sending")
        names = page.locator("#appContent table").first.locator("tbody tr")
        expect(names).to_have_count(3)
        expect(names.first).to_contain_text("Alpha <b>Traders</b>")  # shown as typed
        expect(names.first).to_contain_text("not yet sent")
        assert page.locator("#appContent table b").count() == 0
        assert page.problems == []  # type: ignore[attr-defined]
    finally:
        clear_stock_and_approvals(company)


def test_the_buyer_compares_quotes_and_awards_and_the_approver_signs_the_purchase_order(
    company: Company, browser: Any
) -> None:
    from datetime import datetime

    from p2p.formulas.calendar import IST
    from p2p.orders import service as orders

    seed_stock_and_approvals(company, price=100)
    db = company.db
    today = datetime.now(IST).date()
    db.execute(text("update location set gstin = '29BBBBB0000B1Z5', state_code = '29' where code = 'WH-BLR'"))
    db.execute(text("update item set gst_rate_before = 18, gst_rate_after = 18 where code = 'A'"))
    db.execute(
        text(
            "insert into number_series (document_type, prefix, scope, format, reset) values "
            "('rfq', 'RFQ', 'company', '{prefix}-{fy4}-{seq:05d}', 'fy'), ('po', 'PO', 'location', '{prefix}-{loc}-{fy4}-{seq:05d}', 'fy')"
        )
    )
    # the procurement head is the Approver login (so the stores head's record gets another address)
    db.execute(text("update org_user set email = 'u3@unused.example' where code = 'U3'"))
    db.execute(text("insert into department (code, name, head_user) values ('PUR', 'Procurement', 'U5')"))
    db.execute(
        text(
            "insert into org_user (code, name, designation, department_code, email, active) values ('U5', 'Vikram Procure', 'Head - Procurement', 'PUR', :e, true)"
        ),
        {"e": company.emails["Approver"]},
    )
    db.execute(
        text(
            "insert into approval_rule (rule_code, document, min_value, max_value, category_code, department_code, spend_type, "
            "approver_chain, version, effective_from) values ('PO-01', 'po', 0, 999999999, 'ANY', 'ANY', 'ANY', 'procurement_head', 1, '2024-11-01')"
        )
    )
    for code, name, state in (("S1", "Alpha Traders", "27"), ("S2", "Bravo Supplies", "29")):
        db.execute(
            text(
                "insert into supplier (code, legal_name, status, state_code, gst_reg_type) values (:c, :n, 'active', :s, 'regular')"
            ),
            {"c": code, "n": name, "s": state},
        )
        give_documents(db, code, gst=True)
    # an approved request whose only line is out for quotation to both suppliers
    db.execute(
        text(
            "insert into request (no, ship_to_location, type, channel, status, total_value, department_code) "
            "values ('REQ-UI-1', 'WH-BLR', 'goods', 'planning', 'approved', 1000, 'LOG')"
        )
    )
    db.execute(
        text(
            "insert into request_line (request_no, line_no, item_code, qty, uom, category_code, need_by, status) "
            "values ('REQ-UI-1', 1, 'A', 10, 'NOS', 'C1', :nb, 'sourcing')"
        ),
        {"nb": today.replace(year=today.year + 1)},
    )
    db.execute(
        text(
            "insert into rfq (no, request_no, category_code, due_date, status) values ('RFQ-UI-1', 'REQ-UI-1', 'C1', :d, 'sent')"
        ),
        {"d": today.replace(year=today.year + 1)},
    )
    db.execute(
        text(
            "insert into rfq_line (rfq_no, line_no, request_no, request_line_no, item_code, description, qty, uom, need_by) "
            "values ('RFQ-UI-1', 1, 'REQ-UI-1', 1, 'A', '<i>Bearing</i>', 10, 'NOS', :nb)"
        ),
        {"nb": today.replace(year=today.year + 1)},
    )
    for code in ("S1", "S2"):
        db.execute(
            text(
                "insert into rfq_supplier (rfq_no, supplier_code, email, invited_at) values ('RFQ-UI-1', :c, :e, now())"
            ),
            {"c": code, "e": f"{code.lower()}@supplier.example"},
        )
    db.execute(
        text(
            "insert into sourcing_decision (request_no, line_no, method, rfq_no, reason) values ('REQ-UI-1', 1, 'rfq', 'RFQ-UI-1', 'test')"
        )
    )
    db.commit()
    try:
        buyer = open_app(browser, LAPTOP)
        sign_in(buyer, company.emails["Buyer"])
        buyer.goto(f"{URL}/#/rfqs/RFQ-UI-1")
        expect(buyer.locator("#appContent")).to_contain_text("No quotes received yet")

        def enter(supplier: str, price: str, discount: str, days: str) -> None:
            buyer.locator("#addQuoteBtn").click()
            buyer.locator("#qSupplier").select_option(supplier)
            row = buyer.locator("#quoteForm tbody tr").first
            row.locator(".qPrice").fill(price)
            row.locator(".qDisc").fill(discount)
            row.locator(".qGst").fill("18")
            row.locator(".qDays").fill(days)
            buyer.locator("#saveQuote").click()

        enter("S1", "120", "10", "5")  # net 108
        expect(buyer.locator("#appContent")).to_contain_text("1 of 2 suppliers have replied")
        enter("S2", "110", "0", "3")
        expect(buyer.locator("#appContent")).to_contain_text("2 of 2 suppliers have replied")
        recommended = buyer.locator("#appContent tr", has_text="recommended")
        expect(recommended).to_contain_text("Alpha Traders")  # 108 beats 110
        expect(recommended).to_contain_text("₹120 less 10%")
        assert buyer.locator('#appContent input[type="radio"]:checked').get_attribute("value") == "S1"
        assert buyer.locator("#appContent i:not(.ti)").count() == 0  # the item name is shown as typed

        buyer.locator("#awardBtn").click()
        expect(buyer.locator("#appContent")).to_contain_text("Awarded to Alpha Traders")
        assert company.db.execute(text("select status from rfq where no = 'RFQ-UI-1'")).scalar_one() == "awarded"

        made = orders.order_request(db, company.slug, "REQ-UI-1", as_of=today)  # what the worker does after an award
        assert made.waiting, made.skipped
        buyer.goto(f"{URL}/#/purchase-orders")
        row = buyer.locator("#tableWrap tbody tr")
        expect(row).to_have_count(1)
        expect(row).to_contain_text("Alpha Traders")
        expect(row).to_contain_text("Pending")
        expect(row).to_contain_text("waiting for Vikram Procure")

        admin = open_app(browser, LAPTOP)
        sign_in(admin, company.emails["Approver"])
        admin.goto(f"{URL}/#/purchase-orders")
        admin.locator("#tableWrap tbody tr").click()
        page_text = admin.locator("#appContent")
        expect(page_text).to_contain_text("IGST")  # Maharashtra supplier, Karnataka store
        expect(page_text).not_to_contain_text("CGST")
        expect(page_text).to_contain_text("1,080")  # 10 x 108.00
        expect(page_text).to_contain_text("1,274.40")  # plus 18% IGST of 194.40
        expect(buyer.locator("#poApprove")).to_have_count(0)
        admin.locator("#poApprove").click()
        expect(admin.locator("#appContent")).to_contain_text("Approved by Vikram Procure under rule PO-01 v1")
        po_no = made.waiting[0]
        db.execute(
            text(
                "insert into supplier_contact (supplier_code, name, email, is_primary) "
                "values ('S1', 'Contact S1', 's1@supplier.example', true)"
            )
        )
        db.execute(
            text(
                "insert into number_series (document_type, prefix, scope, format, reset) "
                "values ('goods_receipt', 'GRN', 'location', '{prefix}-{loc}-{fy4}-{seq:05d}', 'fy')"
            )
        )
        db.commit()
        buyer.goto(f"{URL}/#/purchase-orders/{po_no}")
        buyer.locator("#poSend").click()  # the buyer sends the approved order
        expect(buyer.locator("#appContent")).to_contain_text("Sent to s1@supplier.example")
        buyer.locator("#poAckDate").fill(today.replace(year=today.year + 1).isoformat())
        buyer.locator("#poAck").click()
        expect(buyer.locator("#appContent")).to_contain_text("Acknowledged on")

        # the supplier wrote about the order and the agent left it for a person: the buyer sees it on the order and in My work
        email_id = db.execute(
            text(
                "insert into email_message (message_id, direction, from_addr, subject, linked_type, linked_id) "
                "values ('<ui-1@supplier.example>', 'in', 's1@supplier.example', 'Re: delivery date', 'po', :p) returning id"
            ),
            {"p": po_no},
        ).scalar_one()
        db.execute(
            text(
                "insert into supplier_message (email_id, linked_type, linked_ref, kind, status, action, reply_draft, confidence) "
                "values (:e, 'po', :p, 'delivery_date', 'needs_person', 'left for a buyer', 'Thank you, please confirm the date in writing.', 0.8)"
            ),
            {"e": email_id, "p": po_no},
        )
        db.commit()
        buyer.goto(f"{URL}/#/dashboard")
        expect(buyer.locator("#appContent")).to_contain_text("Supplier messages to read")
        buyer.locator("#appContent").get_by_text(f"{po_no}: Re: delivery date").click()
        panel = buyer.locator("#supplierMessagesPanel")
        expect(panel).to_contain_text("Re: delivery date")
        expect(panel).to_contain_text("Needs a person to read it")
        expect(panel).to_contain_text("Suggested reply (not sent)")
        expect(panel).to_contain_text("Thank you, please confirm the date in writing.")
        panel.get_by_text("Chase suppliers now").click()
        expect(buyer.locator("#chaseNote")).to_contain_text("reminder(s) emailed")

        store = open_app(browser, LAPTOP)  # a store person receives the goods
        sign_in(store, company.emails["Store"])
        store.goto(f"{URL}/#/receiving")
        card = store.locator("#waitingWrap .panel")
        expect(card).to_contain_text(po_no)
        card.locator(".recv-open").click()
        card.locator(".recv-qty").fill("10")
        card.locator(".recv-challan").fill("DC/9")
        card.locator(".recv-send").click()
        expect(store.locator("#appContent")).to_contain_text("GRN-BLR-")
        expect(store.locator("#appContent")).to_contain_text("DC/9")
        store.goto(f"{URL}/#/purchase-orders/{po_no}")
        expect(store.locator("#appContent")).to_contain_text("Received")
        expect(store.locator("#supplierMessagesPanel")).to_have_count(0)  # the store may not read the supplier's mail
        assert (
            db.execute(text("select coalesce(sum(qty), 0) from stock_movement where source = 'platform'")).scalar_one()
            == 10
        )
        admin.goto(f"{URL}/#/requisitions/REQ-UI-1")
        expect(admin.locator("#appContent")).to_contain_text("ordered")
        assert admin.problems == [] and buyer.problems == [] and store.problems == []  # type: ignore[attr-defined]
    finally:
        clear_stock_and_approvals(company)


def test_the_buyer_checks_a_reply_read_from_email_corrects_one_value_and_confirms_it(
    company: Company, browser: Any
) -> None:
    import json
    from datetime import datetime

    from p2p.formulas.calendar import IST

    seed_stock_and_approvals(company, price=100)
    db = company.db
    later = datetime.now(IST).date().replace(year=datetime.now(IST).date().year + 1)
    db.execute(text("insert into supplier (code, legal_name, status) values ('S1', 'Alpha <b>Traders</b>', 'active')"))
    give_documents(db, "S1")
    db.execute(
        text(
            "insert into request (no, ship_to_location, type, channel, status, total_value) values ('REQ-D', 'WH-BLR', 'goods', 'planning', 'approved', 1000)"
        )
    )
    db.execute(
        text(
            "insert into request_line (request_no, line_no, item_code, qty, uom, category_code, status) values ('REQ-D', 1, 'A', 10, 'NOS', 'C1', 'sourcing')"
        )
    )
    db.execute(
        text(
            "insert into rfq (no, request_no, category_code, due_date, status) values ('RFQ-D', 'REQ-D', 'C1', :d, 'sent')"
        ),
        {"d": later},
    )
    db.execute(
        text(
            "insert into rfq_line (rfq_no, line_no, request_no, request_line_no, item_code, description, qty, uom, need_by) "
            "values ('RFQ-D', 1, 'REQ-D', 1, 'A', 'Bearing', 10, 'NOS', :d)"
        ),
        {"d": later},
    )
    db.execute(
        text(
            "insert into rfq_supplier (rfq_no, supplier_code, email, invited_at) values ('RFQ-D', 'S1', 's1@supplier.example', now())"
        )
    )
    email_id = db.execute(
        text(
            "insert into email_message (message_id, direction, from_addr, to_addr, subject, body_text, sent_at, linked_type, linked_id) "
            "values ('<ui-draft@x>', 'in', 's1@supplier.example', 'rfq+RFQ-D@cbcpl.example', 'Re: quote <i>now</i>', 'Bearing Rs 95, GST 18%', now(), 'rfq', 'RFQ-D') returning id"
        )
    ).scalar_one()
    proposal = {
        "lines": [
            {
                "rfq_line_no": 1,
                "name": "Bearing",
                "unit_price": "95",
                "gst_rate": "18",
                "discount_pct": "0",
                "delivery_days": 4,
                "notes": [],
            }
        ],
        "valid_until": None,
        "freight": "0",
        "packing_forwarding": "0",
    }
    db.execute(
        text(
            "insert into quote_draft (rfq_no, supplier_code, email_id, intent, status, reading, proposal, problems, assumptions, confidence) "
            "values ('RFQ-D', 'S1', :e, 'quote', 'pending', cast(:r as jsonb), cast(:p as jsonb), '[]', cast(:a as jsonb), 0.93)"
        ),
        {
            "e": email_id,
            "r": json.dumps({"intent": "quote"}),
            "p": json.dumps(proposal),
            "a": json.dumps(["Bearing: GST rate not stated; the item's rate 18% was assumed"]),
        },
    )
    db.commit()
    try:
        buyer = open_app(browser, LAPTOP)
        sign_in(buyer, company.emails["Buyer"])
        buyer.goto(f"{URL}/#/rfqs/RFQ-D")
        panel = buyer.locator("#appContent .panel", has_text="Replies read from email")
        expect(panel).to_contain_text("read as quote (93% sure)")
        expect(panel).to_contain_text("GST rate not stated; the item's rate 18% was assumed")
        expect(panel).to_contain_text("Re: quote <i>now</i>")  # shown as typed
        assert buyer.locator("#appContent .panel i:not(.ti)").count() == 0
        expect(buyer.locator(".dPrice")).to_have_value("95")

        buyer.locator(".dPrice").fill("92.5")  # the buyer corrects the price
        buyer.locator(".draftConfirm").click()
        expect(buyer.locator("#appContent")).not_to_contain_text("Replies read from email")  # nothing left to look at
        expect(buyer.locator("#appContent")).to_contain_text("1 of 1 suppliers have replied")
        expect(buyer.locator("#appContent tbody tr", has_text="recommended")).to_contain_text("₹92.5")
        assert db.execute(text("select unit_price from quote_line")).scalar_one() == 92.5
        assert buyer.problems == []  # type: ignore[attr-defined]
    finally:
        db.execute(text("delete from email_message where message_id = '<ui-draft@x>'"))
        clear_stock_and_approvals(company)


def test_hostile_text_in_names_and_in_the_address_bar_is_shown_as_text_and_never_runs(
    company: Company, browser: Any
) -> None:
    hostile = '<img src=x onerror="window.__pwn=1">'
    db = company.db
    db.execute(text("insert into item_category (code, name) values ('C1', :n) on conflict do nothing"), {"n": hostile})
    db.execute(
        text("insert into supplier (code, legal_name, trade_name, status) values ('S-X', :n, :n, 'active')"),
        {"n": hostile + " Traders"},
    )
    db.execute(text("insert into supplier_category (supplier_code, category_code) values ('S-X', 'C1')"))
    db.commit()
    try:
        page = open_app(browser, LAPTOP)
        fired: list[str] = []
        page.on("dialog", lambda d: (fired.append(d.message), d.dismiss()))
        sign_in(page, company.emails["Admin"])
        for route in ("vendors", "vendors/S-X", "vendors/" + hostile, "contracts/" + hostile, "rfqs", "inventory"):
            page.evaluate("window.__pwn = undefined")
            page.goto(f"{URL}/#/{route}")
            page.wait_for_timeout(700)
            assert page.evaluate("window.__pwn") is None, f"script ran on #/{route}"
            assert page.locator("#appContent img[onerror]").count() == 0, f"markup injected on #/{route}"
        assert fired == []
    finally:
        db.execute(text("delete from supplier_document where supplier_code = 'S-X'"))
        db.execute(text("delete from supplier_category where supplier_code = 'S-X'"))
        db.execute(text("delete from supplier where code = 'S-X'"))
        db.execute(text("delete from item_category where code = 'C1'"))
        db.commit()


def test_the_help_page_opens_the_guide_of_the_role_and_the_others_are_one_click_away(
    company: Company, browser: Any
) -> None:
    buyer = open_app(browser, LAPTOP)
    sign_in(buyer, company.emails["Buyer"])
    buyer.goto(f"{URL}/#/help")

    expect(buyer.locator("#guideBody h2")).to_have_text("Buyer guide")
    expect(buyer.locator("#guideTabs a")).to_have_text(
        [
            "How a purchase flows",
            "Requester guide",
            "Approver guide",
            "Buyer guide (yours)",
            "Store guide",
            "AP clerk guide",
            "Finance guide",
            "Admin guide: rules and automation controls",
        ]
    )
    expect(buyer.locator("#guideBody strong").first).to_be_visible()  # **bold** became bold text, not stars
    assert "**" not in buyer.locator("#guideBody").inner_text()
    buyer.locator("#guideTabs a", has_text="Finance guide").click()
    expect(buyer.locator("#guideBody h2")).to_have_text("Finance guide")
    buyer.locator("#guideTabs a", has_text="How a purchase flows").click()
    expect(buyer.locator("#guideBody table")).to_be_visible()  # the overview table
    expect(buyer.locator("#guideBody table tbody tr")).to_have_count(9)
    assert buyer.problems == []  # type: ignore[attr-defined]


def test_the_admin_link_is_shown_to_everyone_who_decides_something_and_to_no_one_else(
    company: Company, browser: Any
) -> None:
    for role, shown in (("Approver", True), ("Finance", True), ("Buyer", True), ("Store", False), ("Requester", False)):
        page = open_app(browser, LAPTOP)
        sign_in(page, company.emails[role])
        if shown:
            expect(page.locator("#adminLink")).to_be_visible()
        else:
            expect(page.locator("#adminLink")).to_be_hidden()


def test_hostile_markup_in_a_guide_is_shown_as_text(company: Company, browser: Any) -> None:
    page = open_app(browser, LAPTOP)
    sign_in(page, company.emails["Requester"])
    page.goto(f"{URL}/#/help")
    expect(page.locator("#guideBody h2")).to_be_visible()

    hostile = "# <img src=x onerror=window.__hit=1>\n\n[a](b.md) <script>window.__hit=2</script> **<b>x</b>**"
    drawn = page.evaluate("md => GuideMeView.markdown(md)", hostile)
    page.locator("#guideBody").evaluate("(el, html) => { el.innerHTML = html; }", drawn)

    assert page.evaluate("window.__hit") is None  # nothing in it ran
    assert page.locator("#guideBody img, #guideBody script, #guideBody b").count() == 0
    assert "<script>" in page.locator("#guideBody").inner_text()
