"""The payment run page in a browser: Finance settles a run from the bank's statement, and what does not match is listed."""

from typing import Any

from playwright.sync_api import expect
from sqlalchemy import text
from test_bank_statement import HEAD, _run
from test_invoices import ledger  # noqa: F401 - fixtures
from test_main_ui import LAPTOP, open_app, sign_in
from test_orders import shop  # noqa: F401 - fixtures
from test_receiving import store  # noqa: F401 - fixtures
from test_sourcing import Outbox, db, outbox  # noqa: F401 - fixtures
from test_web_shell import URL, browser, server  # noqa: F401 - fixtures


def _upload(page: Any, csv: str) -> None:
    page.set_input_files("#stmtFile", {"name": "statement.csv", "mimeType": "text/csv", "buffer": csv.encode()})
    page.locator("#importStmt").click()


def test_finance_imports_the_statement_and_the_matching_run_is_settled(
    ledger: Any,
    outbox: Outbox,
    server: Any,
    browser: Any,  # noqa: F811
) -> None:
    run, _, pay_no, _, _ = _run(ledger)
    page = open_app(browser, LAPTOP)
    sign_in(page, f"fin{ledger.info['sfx']}@x.example")
    page.goto(f"{URL}/#/payments/{run}")

    # a statement with a wrong amount: nothing is settled and the line is listed for a person
    expect(page.locator("#importStmt")).to_be_visible()
    _upload(page, HEAD + f"2026-11-05,UTR1,{pay_no},1179.00,\n")
    expect(page.locator("#stmtResult")).to_contain_text("0 payment(s) matched")
    expect(page.locator("#stmtResult")).to_contain_text("the amount differs")
    assert ledger.execute(text("select status from payment_run")).scalar_one() == "file_generated"

    # the right statement settles the run and the page shows it paid
    _upload(page, HEAD + f"2026-11-05,UTRSCREEN1,{pay_no},1180.00,\n")
    expect(page.locator("#appContent")).to_contain_text("UTRSCREEN1")
    ledger.rollback()
    assert ledger.execute(text("select status from payment_run")).scalar_one() == "paid"
    assert page.problems == []  # type: ignore[attr-defined]


def test_the_import_panel_is_not_offered_to_someone_who_cannot_record_payments(
    ledger: Any,
    outbox: Outbox,
    server: Any,
    browser: Any,  # noqa: F811
) -> None:
    run, _, _, _, _ = _run(ledger)
    page = open_app(browser, LAPTOP)
    sign_in(page, f"own{ledger.info['sfx']}@x.example")  # the owner approves runs but does not pay them
    page.goto(f"{URL}/#/payments/{run}")

    expect(page.locator("#appContent")).to_contain_text(run)
    expect(page.locator("#importStmt")).to_have_count(0)
