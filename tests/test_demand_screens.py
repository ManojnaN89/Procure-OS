"""The screens of the first part of the flow, in a real browser: a requester raises, submits, withdraws, cancels and copies a request,
a list is exported as a file, and the store issues stock and corrects it after a count."""

import re
from typing import Any

from playwright.sync_api import expect
from sqlalchemy import text
from test_main_ui import LAPTOP, clear_stock_and_approvals, open_app, seed_stock_and_approvals, sign_in
from test_web_shell import URL, Company, browser, company, server  # noqa: F401 - fixtures


def _person(company: Company) -> None:
    """The requester as a person of the company (the request is raised in this name), under the stores head."""
    company.db.execute(
        text(
            "insert into org_user (code, name, designation, department_code, manager_code, location_code, email, active) "
            "values ('U4', 'Requester Person', 'Engineer', 'LOG', 'U3', 'WH-BLR', :e, true)"
        ),
        {"e": company.emails["Requester"]},
    )
    company.db.commit()


def _status(page: Any) -> Any:
    return page.locator("#appContent .page-header p").first


def test_a_requester_raises_withdraws_cancels_and_copies_a_request_and_exports_the_list(
    company: Company, browser: Any
) -> None:  # noqa: F811
    seed_stock_and_approvals(
        company, price=100
    )  # 200 x 100 = 20,000: above the automatic band, so the stores head decides
    _person(company)
    try:
        page = open_app(browser, LAPTOP)
        sign_in(page, company.emails["Requester"])

        # raise it on the form: saved as a draft
        page.goto(f"{URL}/#/requisitions")
        page.get_by_role("link", name="New request").click()
        page.locator(".req-item").select_option("A")
        page.locator(".req-qty").fill("200")
        page.locator("#reqSave").click()
        expect(page.locator("#appContent h1")).to_contain_text("Bearing")
        expect(_status(page)).to_contain_text(re.compile("draft", re.I))
        expect(page.locator("#withdrawBtn")).to_have_count(0)  # nothing to take back from approval yet
        expect(page.locator("#cancelReqBtn")).to_have_count(1)
        expect(page.locator("#copyBtn")).to_have_count(1)

        # submit, change your mind, take it back, submit again
        page.locator("#submitBtn").click()
        expect(_status(page)).to_contain_text(re.compile("pending", re.I))
        expect(page.locator("#withdrawBtn")).to_have_count(1)
        expect(page.locator("#submitBtn")).to_have_count(0)
        page.locator("#withdrawBtn").click()
        expect(_status(page)).to_contain_text(re.compile("draft", re.I))
        page.locator("#submitBtn").click()
        expect(_status(page)).to_contain_text(re.compile("pending", re.I))

        # the stores head sees the decision waiting, and sees no way to withdraw or cancel someone else's request
        head = open_app(browser, LAPTOP)
        sign_in(head, company.emails["Approver"])
        head.goto(f"{URL}/#/requisitions")
        head.locator("#tableWrap tbody tr").first.click()
        expect(head.locator("#approveBtn")).to_have_count(1)
        expect(head.locator("#withdrawBtn")).to_have_count(0)
        expect(head.locator("#cancelReqBtn")).to_have_count(0)

        # cancel it with a reason (asked for, not optional)
        page.locator("#cancelReqBtn").click()
        page.locator("#reasonOk").click()
        expect(page.locator("#actionNote")).to_contain_text("reason")
        page.locator("#reasonText").fill("Needed no more")
        page.locator("#reasonOk").click()
        expect(_status(page)).to_contain_text(re.compile("cancelled", re.I))
        expect(page.locator("#cancelReqBtn")).to_have_count(0)

        # copy it: a new draft with the same lines
        page.locator("#copyBtn").click()
        expect(page.locator("#appContent h1")).to_contain_text("Bearing")
        expect(_status(page)).to_contain_text(re.compile("draft", re.I))
        expect(page.locator("#appContent")).to_contain_text("Copy of")
        expect(page.locator("#appContent")).to_contain_text("200")

        # the list can be exported: a file with a name, a header and both requests, as exact text
        page.goto(f"{URL}/#/requisitions")
        page.locator("#tableWrap tbody tr").first.wait_for()
        with page.expect_download() as download:
            page.locator("[data-export='requests']").click()
        saved = download.value
        assert re.fullmatch(r"requests-\d{4}-\d{2}-\d{2}\.csv", saved.suggested_filename)
        body = open(saved.path(), encoding="utf-8-sig").read()
        assert body.splitlines()[0].startswith("id,title,reason,items,amount")
        assert body.count("\n") >= 3 and "20000.00" in body and "cancelled" in body
        # the one refusal allowed is the cancel without a reason above (the browser reports it twice)
        assert [p for p in page.problems if "409" not in p] == []  # type: ignore[attr-defined]
        assert head.problems == []  # type: ignore[attr-defined]
    finally:
        company.db.rollback()
        clear_stock_and_approvals(company)


def test_the_store_issues_stock_and_corrects_it_after_a_count_and_nobody_else_is_offered_it(
    company: Company, browser: Any
) -> None:  # noqa: F811
    seed_stock_and_approvals(company, price=100)  # 5 on hand of item A
    try:
        store = open_app(browser, LAPTOP)
        sign_in(store, company.emails["Store"])
        store.goto(f"{URL}/#/inventory")
        row = store.locator("#tableWrap tbody tr").first
        expect(row).to_contain_text("5")

        # issue 2: the books say 3
        row.get_by_text("Issue", exact=True).click()
        store.locator("#stockSave").click()
        expect(store.locator("#stockNote")).to_contain_text("must be a number")  # nothing entered
        store.locator("#stockQty").fill("9")
        store.locator("#stockSave").click()
        expect(store.locator("#stockNote")).to_contain_text("only 5 is on the shelf")  # never below zero
        store.locator("#stockQty").fill("2")
        store.locator("#stockWhy").fill("Press 3 repair")
        store.locator("#stockSave").click()
        expect(store.locator("#stockAction")).to_contain_text("Stock issued")
        expect(store.locator("#tableWrap tbody tr").first.locator("td").nth(2)).to_contain_text("3")

        # count the shelf: 7 on it, the books are made to say 7 and the reason is kept
        store.locator("#tableWrap tbody tr").first.get_by_text("Count", exact=True).click()
        store.locator("#stockQty").fill("7")
        store.locator("#stockSave").click()
        expect(store.locator("#stockNote")).to_contain_text("say why")
        store.locator("#stockWhy").fill("Found a box behind the rack")
        store.locator("#stockSave").click()
        expect(store.locator("#stockAction")).to_contain_text("now match the count")
        expect(store.locator("#tableWrap tbody tr").first.locator("td").nth(2)).to_contain_text("7")
        company.db.rollback()
        assert (
            company.db.execute(text("select reason from audit_event where action = 'stock.adjust'")).scalar_one()
            == "Found a box behind the rack"
        )

        # a requester can see the stock but is not offered the buttons
        requester = open_app(browser, LAPTOP)
        sign_in(requester, company.emails["Requester"])
        requester.goto(f"{URL}/#/inventory")
        expect(requester.locator("#tableWrap tbody tr").first).to_contain_text("7")
        expect(requester.locator("[data-stock]")).to_have_count(0)
        # the refusals allowed are the ones provoked above: nothing entered, too much, no reason (reported twice each)
        assert [p for p in store.problems if "409" not in p and "422" not in p] == [] and requester.problems == []  # type: ignore[attr-defined]
    finally:
        company.db.rollback()
        company.db.execute(text("delete from stock_movement"))
        company.db.commit()
        clear_stock_and_approvals(company)
