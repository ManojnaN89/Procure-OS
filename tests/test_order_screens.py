"""The order page in a real browser: the order document, its version, and what a buyer may change after the order was made
(change a quantity, cancel it, close what is still to come); a store person sees the document but none of the controls."""

import re
from typing import Any

from playwright.sync_api import expect
from sqlalchemy import text
from test_main_ui import LAPTOP, clear_stock_and_approvals, open_app, seed_stock_and_approvals, sign_in
from test_web_shell import URL, Company, browser, company, server  # noqa: F401 - fixtures

from p2p.orders import versions


def _order(company: Company, no: str, status: str, received: int) -> None:
    """A sent order of ten bearings from S1 (a line of a request that is already ordered)."""
    db = company.db
    db.execute(
        text(
            "insert into request (no, ship_to_location, type, channel, status, total_value, department_code) "
            "values (:r, 'WH-BLR', 'goods', 'planning', 'closed', 1000, 'LOG')"
        ),
        {"r": f"REQ-{no}"},
    )
    db.execute(
        text(
            "insert into request_line (request_no, line_no, item_code, qty, uom, category_code, need_by, status) "
            "values (:r, 1, 'A', 10, 'NOS', 'C1', '2026-12-20', 'ordered')"
        ),
        {"r": f"REQ-{no}"},
    )
    db.execute(
        text(
            "insert into po (no, supplier_code, supplier_gstin, buyer_gstin, ship_to_location, request_no, status, taxable, igst, total) "
            "values (:n, 'S1', '27AAAAA0000A1Z5', '29BBBBB0000B1Z5', 'WH-BLR', :r, :s, 1000, 180, 1180)"
        ),
        {"n": no, "r": f"REQ-{no}", "s": status},
    )
    db.execute(
        text(
            "insert into po_line (po_no, line_no, request_no, request_line_no, item_code, description, qty, uom, unit_price, "
            "gst_rate, taxable, igst, need_by, received_qty, status, source) values "
            "(:n, 1, :r, 1, 'A', 'Bearing', 10, 'NOS', 100, 18, 1000, 180, '2026-12-20', :got, :ls, 'contract')"
        ),
        {"n": no, "r": f"REQ-{no}", "got": received, "ls": "part_received" if received else "open"},
    )
    versions.record(db, no, made_by="backfill")  # as the migration keeps an order made before versions were kept
    db.commit()


def test_a_buyer_opens_the_document_changes_cancels_and_closes_and_a_store_person_cannot(
    company: Company, browser: Any
) -> None:  # noqa: F811
    seed_stock_and_approvals(company, price=100)
    db = company.db
    db.execute(text("update location set gstin = '29BBBBB0000B1Z5', state_code = '29' where code = 'WH-BLR'"))
    db.execute(text("update item set gst_rate_before = 18, gst_rate_after = 18 where code = 'A'"))
    db.execute(
        text(
            "insert into supplier (code, legal_name, status, state_code, gstin, gst_reg_type) "
            "values ('S1', 'Alpha Traders', 'active', '27', '27AAAAA0000A1Z5', 'regular')"
        )
    )
    db.execute(
        text(
            "insert into supplier_contact (supplier_code, name, email, is_primary) "
            "values ('S1', 'Contact S1', 's1@supplier.example', true)"
        )
    )
    _order(company, "PO-OC-1", "sent", 0)
    _order(company, "PO-OC-2", "part_received", 4)
    try:
        buyer = open_app(browser, LAPTOP)
        sign_in(buyer, company.emails["Buyer"])

        # the document and the version are on the page; nothing has been received, so cancelling is offered, closing is not
        buyer.goto(f"{URL}/#/purchase-orders/PO-OC-1")
        panel = buyer.locator("#poChangesBox")
        expect(panel).to_contain_text("Order document and changes")
        expect(panel).to_contain_text("Version 1")
        expect(buyer.locator("#poCancelOpen")).to_have_count(1)
        expect(buyer.locator("#poCloseOpen")).to_have_count(0)
        with buyer.expect_response(re.compile(r"/api/purchase-orders/PO-OC-1/pdf$")) as opened:
            buyer.locator("#poPdf").click()
        assert opened.value.status == 200 and opened.value.headers["content-type"] == "application/pdf"

        # a smaller quantity is a new version with a lower total; the reason is asked for
        buyer.locator("#poReviseOpen").click()
        expect(buyer.locator("#poChangeForm")).to_contain_text("go for approval again")
        buyer.locator('#poChangeForm tr[data-line="1"] .rev-qty').fill("8")
        buyer.locator("#poRevGo").click()
        expect(buyer.locator("#poChangeNote")).to_contain_text("reason is needed")
        buyer.locator("#poChangeReason").fill("Production plan cut")
        buyer.locator("#poRevGo").click()
        expect(buyer.locator("#poChangesBox")).to_contain_text("Version 2")
        expect(buyer.locator("#poChangesBox")).to_contain_text("Production plan cut")
        expect(buyer.locator("#appContent")).to_contain_text("₹944")  # 8 x 100 plus 18%
        # the versions panel compares the two versions side by side (block 7)
        expect(buyer.locator("#poVersionsBox")).to_contain_text("Version 1")
        expect(buyer.locator("#poCmpOut")).to_contain_text("Quantity")

        # cancelling needs a reason too, and ends the order
        buyer.locator("#poCancelOpen").click()
        buyer.locator("#poCancelGo").click()
        expect(buyer.locator("#poChangeNote")).to_contain_text("reason is needed")
        buyer.locator("#poChangeReason").fill("The plant no longer needs it")
        buyer.locator("#poCancelGo").click()
        expect(buyer.locator("#appContent .page-header p")).to_contain_text(re.compile("cancelled", re.I))
        expect(buyer.locator("#poCancelOpen")).to_have_count(0)
        expect(buyer.locator("#appContent")).to_contain_text("Cancelled: Cancelled by")

        # part of the other order has arrived: it cannot be cancelled, what is still to come can be closed
        buyer.goto(f"{URL}/#/purchase-orders/PO-OC-2")
        expect(buyer.locator("#poCancelOpen")).to_have_count(0)
        buyer.locator("#poCloseOpen").click()
        buyer.locator("#poChangeReason").fill("The supplier stopped making it")
        buyer.locator("#poCloseGo").click()
        expect(buyer.locator("#appContent .page-header p")).to_contain_text(re.compile("closed", re.I))
        assert db.execute(text("select short_closed_qty from po_line where po_no = 'PO-OC-2'")).scalar_one() == 6

        # an approver sees the versions and compares them before deciding, like anyone who opens the order (block 7)
        approver = open_app(browser, LAPTOP)
        sign_in(approver, company.emails["Approver"])
        approver.goto(f"{URL}/#/purchase-orders/PO-OC-2")
        expect(approver.locator("#poVersionsBox .panel-title")).to_have_text("Versions")
        expect(approver.locator("#poCmpGo")).to_have_count(1)
        expect(approver.locator("#poCmpOut")).to_contain_text("nothing to compare yet")  # one version so far

        # a store person may open the document but is offered no change
        store = open_app(browser, LAPTOP)
        sign_in(store, company.emails["Store"])
        store.goto(f"{URL}/#/purchase-orders/PO-OC-2")
        expect(store.locator("#poPdf")).to_have_count(1)
        for control in ("poReviseOpen", "poCancelOpen", "poCloseOpen"):
            expect(store.locator(f"#{control}")).to_have_count(0)
        # the refusals asked for above (a missing reason) are the only failed requests
        assert [x for x in buyer.problems if "409" not in x] == [] and store.problems == []  # type: ignore[attr-defined]
        assert approver.problems == []  # type: ignore[attr-defined]
    finally:
        clear_stock_and_approvals(company)
