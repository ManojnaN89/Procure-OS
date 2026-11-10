"""The wording of the quotation request email is the company's own, and a template with a mistake never reaches a supplier."""

import pytest
from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.orm import Session
from test_sourcing import Outbox, _auto_send, _request, _source, db, outbox  # noqa: F401 - fixtures

from p2p.platform import setup_api
from p2p.sourcing import service, templates

GOOD = "Hello {contact},\n{company} wants prices for {items}\nRef {reference}, by {due_date}, to {deliver_to}.\n"


def test_the_standard_template_is_valid_and_fills_every_field() -> None:
    assert templates.problems(templates.DEFAULT_RFQ_TEMPLATE) == []

    body = templates.render(
        templates.DEFAULT_RFQ_TEMPLATE,
        {
            "contact": "Asha",
            "company": "CBC",
            "reference": "RFQ-1",
            "due_date": "13 Nov 2026",
            "deliver_to": "Pune",
            "items": "1. Seal",
        },
    )

    assert "{" not in body and "Dear Asha," in body and "Reference: RFQ-1" in body and "1. Seal" in body


@pytest.mark.parametrize(
    ("template", "fragment"),
    [
        ("Prices please {items}", "must contain {reference}"),
        ("Ref {reference}", "must contain {items}"),
        ("{items} {reference} {price}", "{price} is not a field"),
        ("{items} {reference} {", "curly brace is left over"),
        ("{items} {reference} }", "curly brace is left over"),
        ("{items} {reference} {}", "{} is not a field"),
        ("{items} {reference} " + "x" * 4000, "longer than 4000"),
    ],
)
def test_a_template_with_a_mistake_is_described_in_words(template: str, fragment: str) -> None:
    assert any(fragment in problem for problem in templates.problems(template))


def test_a_field_with_no_value_is_left_empty_and_nothing_is_evaluated() -> None:
    assert templates.render("a {items} {contact} {__class__}", {"items": "X"}) == "a X  "


def test_the_policy_screen_refuses_a_bad_template_and_stores_a_good_one_tidily() -> None:
    with pytest.raises(HTTPException) as refused:
        setup_api._validate("text", "no fields here", None, None, (), "Wording")
    assert refused.value.status_code == 422 and "must contain {items}" in str(refused.value.detail)

    assert setup_api._validate("text", "\r\n" + GOOD.replace("\n", "\r\n") + "\r\n", None, None, (), "Wording") == GOOD
    assert setup_api._validate("text", "   ", None, None, (), "Wording") == ""  # empty means the standard wording


def test_the_supplier_gets_the_companys_own_wording(db: Session, outbox: Outbox) -> None:  # noqa: F811
    db.execute(text("insert into org_setting (key, value) values ('rfq_email_template', :t)"), {"t": GOOD})
    db.commit()
    _auto_send(db)
    no = _request(db, "R-13", [("B", "5", None, "C1")])

    _source(db, no)

    first = next(m for m in outbox.sent if m["to"] == "s1@supplier.example")
    assert first["body"].startswith(
        "Hello Contact S1,\nCBC wants prices for   1. Seal (B) - 5 NOS - needed by 20 Nov 2026"
    )
    assert "Ref RFQ-2627-00001, by 13 Nov 2026, to Bengaluru store." in first["body"]
    assert first["subject"] == "Request for quotation RFQ-2627-00001: Spares"  # the subject and reply address stay ours


def test_a_saved_template_that_has_become_unusable_falls_back_to_the_standard_wording(db: Session) -> None:  # noqa: F811
    db.execute(text("insert into org_setting (key, value) values ('rfq_email_template', 'forgot the fields')"))
    db.commit()

    assert service.rfq_template(db) == templates.DEFAULT_RFQ_TEMPLATE


def test_with_nothing_saved_the_standard_wording_is_used(db: Session) -> None:  # noqa: F811
    assert service.rfq_template(db) == templates.DEFAULT_RFQ_TEMPLATE


def test_every_request_carries_an_excel_form_with_the_items_that_reads_back_as_a_table(
    db: Session,  # noqa: F811
    outbox: Outbox,  # noqa: F811
) -> None:
    import io

    import openpyxl

    from p2p.platform import parsing

    _auto_send(db)
    no = _request(db, "R-13", [("B", "5", None, "C1"), (None, "2.5", "Special gasket", "C1")])

    _source(db, no)

    mails = [m for m in outbox.sent if m["subject"].startswith("Request for quotation")]
    assert len(mails) == 3
    for mail in mails:
        ((name, kind, data),) = mail["attachments"]
        assert name == "RFQ-2627-00001-quotation-form.xlsx" and kind == service.quote_sheet.CONTENT_TYPE
    ws = openpyxl.load_workbook(io.BytesIO(data)).active
    assert ws is not None
    rows = [[c.value for c in r] for r in ws.iter_rows()]
    assert rows[3][:5] == ["Line", "Item", "Quantity", "Unit", "Needed by"]
    assert rows[3][5:] == ["Unit price (Rs)", "Discount %", "GST %", "Delivery days", "Minimum order", "Remarks"]
    assert [r[1] for r in rows[4:6]] == ["Seal (B)", "Special gasket"]
    assert [r[2] for r in rows[4:6]] == ["5", "2.5"]
    assert all(v is None for r in rows[4:6] for v in r[5:])  # the supplier fills in the price columns
    parsed = parsing.parse_document(data, name)
    assert "Seal (B)" in parsed.text + "\n".join(" | ".join(c) for t in parsed.tables for c in t.rows)


def test_the_standard_wording_tells_the_supplier_about_the_form() -> None:
    assert "An Excel form is attached" in templates.DEFAULT_RFQ_TEMPLATE
