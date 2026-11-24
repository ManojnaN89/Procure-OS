"""Tax at source proposed, confirmed and changed, and an advance's tax when it is paid (block 12, 12.5).

Given, when and then in each test; the helpers live in test_payments.py.
"""

from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session
from test_invoices import TODAY, _goods, _invoice, _row, ledger  # noqa: F401 - fixtures
from test_orders import shop  # noqa: F401 - fixtures
from test_payments import (
    _approve,
    _code,
    _due_on,
    _earlier_purchase,
    _file,
    _people,
    _propose,
    _statement_for,
    _two_suppliers,
)
from test_receiving import store  # noqa: F401 - fixtures
from test_sourcing import Outbox, db, outbox  # noqa: F401 - fixtures

from p2p.formulas.calendar import pin_today
from p2p.invoices import advances, bank_statement, runs, settlement, tds
from p2p.invoices.service import InvoiceError
from p2p.platform import proposals
from test_advances import _advance_terms, _order  # noqa: F401 - the advance world

from p2p.platform.sod import user

D = Decimal


def test_tax_at_source_is_proposed_per_section_and_holds_the_invoice_until_the_tax_owner_confirms(
    ledger: Session, outbox: Outbox
) -> None:  # noqa: F811
    # given a supplier bought from for 49,99,900 this year and an invoice of 1,000, when it is booked,
    # then 900.00 is above the 50 lakh threshold and 0.90 is proposed (not confirmed), and the invoice is held back
    _earlier_purchase(ledger, "4999900")
    finance, _, _ = _people(ledger)
    po_no, _ = _goods(ledger, qty="10", price="100")
    done = _invoice(ledger, po_no, "10", "100", number="S1-1")
    row = ledger.execute(
        text("select id, section, section_code, base, rate, amount, status, quarter from tds_entry where invoice_no = :n"),
        {"n": done.no},
    ).one()
    assert (row.section_code, row.base, row.rate, row.amount, row.status, row.quarter) == (
        "194Q", D("900.00"), D("0.100"), D("0.90"), "proposed", "2026-27 Q3",
    )  # fmt: skip
    assert _row(ledger, done.no)["tds_amount"] == D("0")
    _due_on(ledger, done.no, date(2026, 11, 7))
    assert runs.due_invoices(ledger, date(2026, 11, 6), 7) == []
    assert "TDS to confirm" in {h["reason"] for h in runs.held_back(ledger, date(2026, 11, 6), 7)}

    # when the tax owner confirms it, then the invoice's tax is the confirmed amount and it is payable
    tds.confirm(ledger, [int(row.id)], finance)
    ledger.commit()
    assert _row(ledger, done.no)["tds_amount"] == D("0.90")
    assert [r["no"] for r in runs.due_invoices(ledger, date(2026, 11, 6), 7)] == [done.no]



def test_a_zero_tax_under_the_item_categorys_own_section_is_confirmed_by_the_rule(
    ledger: Session, outbox: Outbox
) -> None:  # noqa: F811
    # given a supplier bought from for nothing this year, when an invoice is booked,
    # then 194Q has no base, the row is confirmed by the rule, and the invoice is payable at once
    _people(ledger)
    po_no, _ = _goods(ledger, qty="10", price="100")
    done = _invoice(ledger, po_no, "10", "100", number="S1-1")
    row = ledger.execute(
        text("select base, amount, status, confirmed_by from tds_entry where invoice_no = :n"), {"n": done.no}
    ).one()
    assert (row.base, row.amount, row.status, row.confirmed_by) == (D("0"), D("0.00"), "confirmed", "rule")
    assert _row(ledger, done.no)["tds_amount"] == D("0")



def test_a_section_is_changed_only_with_a_reason_and_the_amount_is_worked_out_again(
    ledger: Session, outbox: Outbox
) -> None:  # noqa: F811
    # given a proposed 194Q row, when the tax owner changes it without a reason, then it is refused;
    # with a reason, then the section changes and the amount comes from that section's row (never typed)
    _earlier_purchase(ledger, "4999900")
    finance, _, _ = _people(ledger)
    po_no, _ = _goods(ledger, qty="400", price="100")
    done = _invoice(ledger, po_no, "400", "100", number="S1-1")
    row_id = int(ledger.execute(text("select id from tds_entry where invoice_no = :n"), {"n": done.no}).scalar_one())
    with pytest.raises(tds.TdsError, match="reason is needed"):
        tds.confirm(ledger, [row_id], finance, section_code="194C", reason="")
    tds.confirm(ledger, [row_id], finance, section_code="194C", reason="it is a work contract")
    ledger.commit()
    assert ledger.execute(
        text("select section_code, base, rate, amount, status from tds_entry where id = :i"), {"i": row_id}
    ).one() == ("194C", D("40000.00"), D("2.000"), D("800.00"), "confirmed")


# ---- runs, the lock, the re-check -------------------------------------------------------------




def test_an_approved_advance_is_paid_first_in_a_run_and_its_tax_is_booked_when_the_bank_settles_it(
    ledger: Session, outbox: Outbox
) -> None:  # noqa: F811
    # given an approved advance, when a run pays it, then it is the first payment; nothing is written for its tax
    # before settlement; on settlement the 194Q row is booked on the value date (DF-17, Y9)
    finance, approver, _ = _people(ledger)
    po_no, _ = _order(ledger)
    asked = advances.request(ledger, po_no, finance, pct=D("30"), reason="material")
    proposals.approve(ledger, int(asked["proposal"]), approver)
    pin_today(date(2026, 12, 1))
    run_no = _propose(ledger, finance, date(2026, 12, 1))
    pay = ledger.execute(text("select no, amount, tds_amount from payment where run_no = :r"), {"r": run_no}).one()
    assert (pay.amount, pay.tds_amount) == (D("11505.00"), D("0"))
    assert ledger.execute(text("select count(*) from tds_entry where payment_no = :p"), {"p": pay.no}).scalar_one() == 0
    _file(ledger, outbox, run_no, finance, approver)
    pin_today(date(2026, 12, 2))
    csv = (
        "date,utr,narration,debit,credit\n"
        f"2026-12-02,ADV0001,NEFT {pay.no} SUPPLIER,11505.00,0.00\n"
    )
    done = bank_statement.import_statement(
        ledger, csv, finance, today=date(2026, 12, 2), tenant=ledger.info["slug"]
    )
    assert [s["payment"] for s in done["settled"]] == [pay.no]
    row = ledger.execute(
        text("select section_code, base, amount, booked_on, status from tds_entry where payment_no = :p"), {"p": pay.no}
    ).one()
    assert (row.section_code, row.base, row.amount, row.booked_on, row.status) == (
        "194Q", D("0"), D("0.00"), date(2026, 12, 2), "confirmed",
    )
    assert ledger.execute(text("select status from advance_request where id = :i"), {"i": asked["id"]}).scalar_one() == "paid"


