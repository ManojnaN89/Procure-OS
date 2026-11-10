"""The Vendors screens in a browser: the onboarding form, the details panel, a scan kept with a document, and who may do what."""

from typing import Any

from playwright.sync_api import expect
from sqlalchemy import text
from test_main_ui import LAPTOP, open_app, sign_in
from test_web_shell import URL, Company, browser, company, server  # noqa: F401 - fixtures

from p2p.formulas.gstin import gstin_check_char

PAN = "ABCDE0000F"
_first14 = f"27{PAN}1Z"
GSTIN = _first14 + gstin_check_char(_first14)


def _fill(page: Any, field: str, value: str) -> None:
    page.locator(f'[data-f="{field}"]').first.fill(value)


def test_a_buyer_onboards_a_vendor_adds_a_document_with_its_scan_and_a_second_person_verifies_it(
    company: Company, browser: Any
) -> None:  # noqa: F811
    company.db.execute(
        text("insert into payment_terms (code, name, days, baseline) values ('NET30', 'Net 30', 30, 'invoice_date')")
    )
    company.db.commit()
    page = open_app(browser, LAPTOP)
    sign_in(page, company.emails["Buyer"])
    page.goto(f"{URL}/#/vendors")

    # the form opens, and a record with a problem is refused with the problem named
    page.locator("#newVendorBtn").click()
    expect(page.locator("#vendorSave")).to_be_visible()
    _fill(page, "legal_name", "Kaveri Fasteners Pvt Ltd")
    _fill(page, "pan", "BAD")
    page.locator("#vendorSave").click()
    expect(page.locator("#vendorNote")).to_contain_text("pan:")

    # a good one is saved as a draft and its page opens with the details panel
    _fill(page, "pan", PAN)
    page.locator('[data-f="gst_reg_type"]').select_option("regular")
    _fill(page, "gstin", GSTIN)
    page.locator('[data-f="msme_category"]').select_option("none")
    page.locator('[data-f="payment_terms_code"]').select_option("NET30")
    _fill(page, "email", "sales@kaveri.example")
    _fill(page, "account_no", "000123456789")
    _fill(page, "ifsc", "HDFC0001234")
    page.locator("#vendorSave").click()
    expect(page).to_have_url(f"{URL}/#/vendors/SUP-001")
    expect(page.locator("#appContent h1")).to_contain_text("Kaveri Fasteners")
    expect(page.locator("#appContent h1")).to_contain_text("under review")
    details = page.locator("#detailsPanel")
    expect(details.locator('[data-f="gstin"]')).to_have_value(GSTIN)
    expect(details).to_contain_text("****6789")
    expect(page.locator("#docNote")).to_have_text("")

    # a document is added, and its scan is kept with it
    page.locator("#docType").select_option("pan")
    page.locator("#docNumber").fill(PAN)
    page.locator("#docAdd").click()
    expect(page.locator(".doc-attach")).to_have_count(1)
    page.locator(".doc-attach").set_input_files(
        {"name": "pan.pdf", "mimeType": "application/pdf", "buffer": b"%PDF-1.4 a scan"}
    )
    expect(page.locator("a.doc-open")).to_have_text("pan.pdf")

    # the buyer added it, so the buyer cannot verify it; the approver can
    expect(page.locator(".doc-verify")).to_have_count(0)
    other = open_app(browser, LAPTOP)
    sign_in(other, company.emails["Approver"])
    other.goto(f"{URL}/#/vendors/SUP-001")
    other.locator(".doc-verify").click()
    expect(other.locator("a.doc-open")).to_have_text("pan.pdf")
    expect(other.locator(".doc-verify")).to_have_count(0)
    status = company.db.execute(text("select status from supplier where code = 'SUP-001'")).scalar_one()
    assert status == "draft"  # a verified document does not activate anyone: that is a separate decision
    assert [p for p in page.problems if "422" not in p] == []  # type: ignore[attr-defined]
    assert other.problems == []  # type: ignore[attr-defined]


def test_a_person_who_cannot_onboard_sees_the_details_but_no_form_and_no_edit(company: Company, browser: Any) -> None:  # noqa: F811
    company.db.execute(
        text(
            "insert into supplier (code, legal_name, trade_name, pan, gst_reg_type, msme_category, status) "
            "values ('SUP-009', 'Raju Traders', 'Raju Traders', :p, 'unregistered', 'none', 'active')"
        ),
        {"p": PAN},
    )
    company.db.commit()
    page = open_app(browser, LAPTOP)
    sign_in(page, company.emails["Requester"])
    page.goto(f"{URL}/#/vendors/SUP-009")

    expect(page.locator("#appContent h1")).to_contain_text("Raju Traders")
    expect(page.locator("#detailsPanel")).to_be_visible()
    expect(page.locator("#detailSave")).to_have_count(0)
    expect(page.locator("#bankSave")).to_have_count(0)
    expect(page.locator('#detailsPanel [data-f="legal_name"]')).to_be_disabled()
    assert page.problems == []  # type: ignore[attr-defined]
