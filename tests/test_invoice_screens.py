"""The Invoices screen in a browser: the blocked list is the matching exceptions screen, with the reason and the next action."""

from typing import Any

from playwright.sync_api import expect
from test_invoices import _goods, _invoice, ledger  # noqa: F401 - fixtures
from test_main_ui import LAPTOP, open_app, sign_in
from test_orders import shop  # noqa: F401 - fixtures
from test_receiving import store  # noqa: F401 - fixtures
from test_sourcing import PASSWORD, Outbox, db, outbox  # noqa: F401 - fixtures
from test_web_shell import URL, browser, server  # noqa: F401 - fixtures

from p2p.platform import users


def test_the_blocked_tab_lists_each_blocked_invoice_with_its_reason_and_what_to_do_next(
    ledger: Any,
    outbox: Outbox,
    server: Any,
    browser: Any,  # noqa: F811
) -> None:
    slug, sfx = ledger.info["slug"], ledger.info["sfx"]
    ap = f"ap{sfx}@x.example"
    users.create_user(slug, ap, PASSWORD, "An AP Clerk", "AP")
    # a price 6% over the order price: blocked, and the fix is a corrected invoice
    price_po, logins = _goods(ledger, qty="10", price="1000")
    blocked = _invoice(ledger, price_po, "10", "1060", number="INV-PRICE")
    # a clean invoice for the same supplier: it must not appear on the Blocked tab
    clean_po, _ = _goods(ledger, qty="11", price="100", logins=logins)
    clean = _invoice(ledger, clean_po, "11", "100", number="INV-CLEAN")
    assert (blocked.status, clean.status) == ("blocked", "approved")

    page = open_app(browser, LAPTOP)
    sign_in(page, ap)
    page.goto(f"{URL}/#/invoices")
    page.locator('#invFilter [data-s="blocked"]').click()

    row = page.locator("tr[data-id]")
    expect(row).to_have_count(1)
    expect(row).to_contain_text(blocked.no)
    expect(row).to_contain_text("Blocked")
    expect(row).to_contain_text("Price above the order price")  # the reason
    expect(row.locator(".next-action")).to_contain_text("Ask the supplier for a corrected invoice")  # what to do next
    expect(page.locator("#tableWrap")).not_to_contain_text(clean.no)
    # opening it shows why, with the match result per line
    row.click()
    expect(page.locator("#appContent")).to_contain_text("Why it is blocked")
    expect(page.locator("#appContent")).to_contain_text("Price above the order price")
    assert page.problems == []  # type: ignore[attr-defined]


def test_the_list_gives_a_next_action_for_a_blocked_invoice_and_none_for_the_others(
    ledger: Any, outbox: Outbox
) -> None:  # noqa: F811
    from fastapi.testclient import TestClient
    from test_invoices import _headers

    from p2p.main import app

    slug, sfx = ledger.info["slug"], ledger.info["sfx"]
    ap = users.create_user(slug, f"ap{sfx}@x.example", PASSWORD, "An AP Clerk", "AP")
    po_no, logins = _goods(ledger, qty="10", price="1000")
    blocked = _invoice(ledger, po_no, "10", "1060", number="INV-A")
    clean_po, _ = _goods(ledger, qty="11", price="100", logins=logins)
    clean = _invoice(ledger, clean_po, "11", "100", number="INV-B")

    listed = {r["id"]: r for r in TestClient(app).get("/api/invoices", headers=_headers(slug, ap, "AP")).json()}

    assert listed[blocked.no]["next_action"] == "Ask the supplier for a corrected invoice"
    assert listed[clean.no]["next_action"] is None
