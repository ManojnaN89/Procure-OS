"""Month-end and year-end close (block 13 R7, ME1/ME2): preview, the frozen GRNI, the final export, the lock, and the
order rule. The business date is pinned before the clock."""

from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session
from test_invoices import _goods, _invoice, ledger  # noqa: F401 - fixtures
from test_orders import shop  # noqa: F401 - fixture
from test_receiving import store  # noqa: F401 - fixture
from test_sourcing import db  # noqa: F401 - fixtures

from p2p.approvals import budget_periods
from p2p.formulas.calendar import pin_today
from p2p.invoices import accounting_export, period_close

D = Decimal


@pytest.fixture
def closing(ledger: Session) -> Session:  # noqa: F811
    """The invoice world with the months before November locked and the ledger map the export reads."""
    for month in ("2026-04", "2026-05", "2026-06", "2026-07", "2026-08", "2026-09", "2026-10"):
        ledger.execute(
            text("insert into budget_period_lock (period, locked_by, reason) values (:p, 'U2', 'earlier month')"),
            {"p": month},
        )
    ledger.execute(text("update item set default_gl = 1410 where code = 'A'"))
    for code, name in (("1410", "Raw materials"), ("2110", "Creditors MSME"), ("2120", "Creditors"),
                       ("1510", "GST input CGST"), ("1520", "GST input SGST"), ("1530", "GST input IGST"),
                       ("2220", "RCM payable"), ("2210", "TDS payable"), ("1610", "Advances"),
                       ("2310", "GRNI"), ("5220", "Freight inward")):  # fmt: skip
        ledger.execute(text("insert into gl_account (code, name, type) values (:c, :n, 'liability') "
                            "on conflict (code) do nothing"), {"c": code, "n": name})  # fmt: skip
    for purpose, gl, name in (
        ("payable_msme", "2110", "Sundry creditors: micro and small"),
        ("payable_other", "2120", "Sundry creditors: others"),
        ("gst_input_cgst", "1510", "GST input CGST"),
        ("gst_input_sgst", "1520", "GST input SGST"),
        ("gst_input_igst", "1530", "GST input IGST"),
        ("rcm_payable", "2220", "RCM payable"),
        ("tds_payable", "2210", "TDS payable"),
        ("advance", "1610", "Advances"),
        ("grni", "2310", "Goods received not invoiced"),
        ("freight_inward", "5220", "Freight inward"),
        ("round_off", None, "Round Off"),
        ("msme_interest", None, "Interest on delayed payment"),
        ("bank:CBA-01", None, "HDFC Current A/c 0201"),
    ):
        ledger.execute(
            text("insert into ledger_map (purpose, gl_code, ledger_name, version, effective_from) "
                 "values (:p, :g, :n, 1, '2000-01-01')"),
            {"p": purpose, "g": gl, "n": name},
        )  # fmt: skip
    ledger.commit()
    return ledger


def test_given_the_last_day_of_the_month_when_previewed_then_it_has_not_ended_and_the_first_is_open(
    closing: Session,
) -> None:
    pin_today(date(2026, 11, 30))
    assert "has not ended" in " ".join(period_close.preview(closing, "2026-11")["blocking"])

    pin_today(date(2026, 12, 1))

    assert period_close.preview(closing, "2026-11")["blocking"] == []


def test_given_a_month_not_ended_or_out_of_order_when_closed_then_it_is_refused(closing: Session) -> None:
    pin_today(date(2026, 12, 3))
    with pytest.raises(period_close.PeriodCloseError, match="has not ended"):
        period_close.close(closing, "2026-12", 1, "Early")
    closing.execute(text("delete from budget_period_lock where period = '2026-09'"))
    closing.commit()
    with pytest.raises(period_close.PeriodCloseError, match="cannot be closed"):
        period_close.close(closing, "2026-11", 1, "Out of order")


def test_given_received_and_not_invoiced_when_november_is_closed_then_grni_is_frozen_and_the_export_holds_it_once(
    closing: Session,
) -> None:
    pin_today(date(2026, 12, 3))
    po_no, _ = _goods(closing, qty="10", price="100")  # received, not invoiced: 1,000.00 at the order price

    seen = period_close.preview(closing, "2026-11")
    done = period_close.close(closing, "2026-11", 1, "Month-end close", today=date(2026, 12, 3))

    assert any(w["code"] == "not_booked" for w in seen["warnings"]) is False
    assert done["grni_total"] == "1000.00"
    frozen = period_close.grni(closing, "2026-11")
    assert frozen is not None and frozen["total"] == "1000.00"
    assert frozen["lines"][0]["gl_code"] == "1410"

    first = accounting_export.file(closing, "2026-11", "final", 1)
    again = accounting_export.file(closing, "2026-11", "final", 1)
    assert first["content"] == again["content"]
    assert "ACCRUAL-2026-11" in first["content"] and "2310" in first["content"]
    assert first["debit"] == first["credit"] == D("1000.00")
    assert closing.execute(text("select count(*) from budget_period_lock where period = '2026-11'")
                          ).scalar_one() == 1  # fmt: skip
    assert closing.execute(text("select count(*) from audit_event where action = 'period.close'")
                          ).scalar_one() == 1  # fmt: skip
    assert closing.execute(text("select export_id is not null from period_close where period = '2026-11'")
                          ).scalar_one() is True  # fmt: skip


def test_given_a_month_with_no_documents_when_closed_then_it_has_zero_vouchers_and_still_closes(
    closing: Session,
) -> None:
    pin_today(date(2026, 12, 3))

    done = period_close.close(closing, "2026-11", 1, "Nothing happened", today=date(2026, 12, 3))

    assert done["grni_total"] == "0.00" and done["export"]["vouchers"] == 0
    assert budget_periods.is_locked(closing, "2026-11")


def test_the_block_three_lock_route_does_the_same_work(closing: Session) -> None:
    pin_today(date(2026, 12, 3))
    _goods(closing, qty="10", price="100")

    done = budget_periods.lock(closing, "2026-11", 1, "Month-end through the budget panel", date(2026, 12, 3))

    assert done["status"] == "locked"
    assert closing.execute(text("select count(*) from period_close where period = '2026-11'")
                          ).scalar_one() == 1  # fmt: skip


def test_given_a_document_dated_in_a_closed_month_when_exported_later_then_it_lands_in_the_first_open_month(
    closing: Session,
) -> None:
    """ME2: the November final export does not change; the invoice dated 30 November is in December's export."""
    pin_today(date(2026, 12, 3))
    done = period_close.close(closing, "2026-11", 1, "Closed before the invoice came", today=date(2026, 12, 3))
    november = accounting_export.file(closing, "2026-11", "final", 1)["content"]
    po_no, _ = _goods(closing, qty="10", price="100")
    pin_today(date(2026, 12, 4))
    late = _invoice(closing, po_no, "10", "100", number="LATE-1", on=date(2026, 11, 30))

    december = accounting_export.final(closing, "2026-12", 1)

    assert accounting_export.file(closing, "2026-11", "final", 1)["content"] == november
    assert late.no in december["content"] and "2026-11" in december["content"]
    assert done["grni_total"] == "0.00"


def test_given_an_export_that_cannot_be_made_when_the_month_is_closed_then_nothing_of_the_close_stands(
    closing: Session,
) -> None:
    """R7.3: the measures, snapshot, export, close row and lock are one transaction."""
    pin_today(date(2026, 12, 3))
    po_no, _ = _goods(closing, qty="10", price="100")
    _invoice(closing, po_no, "10", "100", number="ATOM-1")
    closing.execute(text("delete from ledger_map where purpose = 'payable_other'"))
    closing.commit()

    with pytest.raises(accounting_export.ExportError, match="map 'payable_other'"):
        period_close.close(closing, "2026-11", 1, "Should not half-close", today=date(2026, 12, 3))
    closing.rollback()

    assert closing.execute(text("select count(*) from period_close")).scalar_one() == 0
    assert closing.execute(text("select count(*) from budget_period_lock where period = '2026-11'")
                          ).scalar_one() == 0  # fmt: skip
    assert closing.execute(text("select count(*) from accounting_export")).scalar_one() == 0
    assert closing.execute(text("select count(*) from price_measure")).scalar_one() == 0
