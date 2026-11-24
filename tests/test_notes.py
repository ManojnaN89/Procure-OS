"""Credit and debit notes: a second person approves, netting and refunds (block 12, 12.7).

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
from p2p.invoices import notes, runs, settlement
from p2p.invoices.service import InvoiceError
from p2p.platform.sod import user

D = Decimal


def _credit_note(ledger_: Session, ref: str, qty: str, price: str, *, number: str = "CN-9") -> str:
    from p2p.invoices import service as invoices

    done = invoices.enter_invoice(
        ledger_,
        invoices.InvoiceIn(
            supplier_code="S1", supplier_invoice_no=number, invoice_date=TODAY, type="credit_note",
            ref_invoice_no=ref, lines=[invoices.InvLine(_po_of(ledger_, ref), 1, D(qty), D(price))],
            supplier_gstin="27AAAAA0000A1Z5", buyer_gstin="29BBBBB0000B1Z5",
        ),
        1, as_of=TODAY,
    )  # fmt: skip
    return done.no



def _po_of(ledger_: Session, invoice_no: str) -> str:
    return str(
        ledger_.execute(
            text("select po_no from invoice_line where invoice_no = :n limit 1"), {"n": invoice_no}
        ).scalar_one()
    )




def test_a_credit_note_waits_for_a_second_person_and_is_netted_in_the_next_run(
    ledger: Session, outbox: Outbox
) -> None:  # noqa: F811
    # given a credit note entered by AP, when AP tries to approve it, then it is refused; Finance approves it,
    # and the next run pays the invoice less the note (Y10)
    finance, approver, ap = _people(ledger)
    _, inv_no = _two_suppliers(ledger)
    note = _credit_note(ledger, inv_no, "1", "100", number="CN-1")
    assert ledger.execute(text("select status from invoice where no = :n"), {"n": note}).scalar_one() == "received"
    with pytest.raises(InvoiceError, match="cannot approve"):
        notes.approve(ledger, note, 1)
    notes.approve(ledger, note, finance)
    _due_on(ledger, inv_no, date(2026, 11, 12))
    run_no = _propose(ledger, finance)
    assert ledger.execute(text("select amount from payment where run_no = :r"), {"r": run_no}).scalar_one() == D("1062.00")
    assert ledger.execute(text("select amount from payment_allocation where invoice_no = :n"), {"n": note}).scalar_one() == (
        D("-118.00")
    )
    assert approver and ap



def test_a_debit_note_from_a_recovery_claim_is_named_on_the_claim_and_settles_it_when_paid(
    ledger: Session, outbox: Outbox, owner_session
) -> None:  # noqa: F811
    # NEEDS block 9: recovery_claim and receiving/returns.py raise it; this test waits for that table
    pytest.skip("NEEDS block 9: the recovery claim record and its debit note call")


# ---- the API and its reads --------------------------------------------------------------------


