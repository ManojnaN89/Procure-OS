"""Recording a payment and settling, failing and reversing it only on the bank's evidence (block 12, 12.3-12.4, 12.9).

Given, when and then in each test; the helpers live in test_payments.py.
"""

from datetime import date
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session
from test_invoices import TODAY, _goods, _headers, _invoice, _row, ledger  # noqa: F401 - fixtures
from test_notes import _credit_note
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
from p2p.invoices import bank_statement, notes, runs, settlement
from p2p.invoices.service import InvoiceError
from p2p.main import app
from p2p.platform.sod import user

D = Decimal
client = TestClient(app)


def test_a_payment_is_recorded_with_its_bank_reference_and_settled_only_by_the_bank(
    ledger: Session, outbox: Outbox
) -> None:  # noqa: F811
    # given a filed run, when Finance records the payment with a UTR and value date, then it is recorded, the invoice
    # is still approved, the advice carries the typed UTR, and the bank statement settles it (Y7)
    pin_today(date(2026, 11, 13))
    finance, approver, _ = _people(ledger)
    ledger.execute(text("update item set stocked = false where code = 'A'"))
    ledger.commit()
    po_no, inv_no = _two_suppliers(ledger)
    _due_on(ledger, inv_no, date(2026, 11, 12))
    pin_today(date(2026, 11, 12))
    run_no = _propose(ledger, finance, date(2026, 11, 12))
    _file(ledger, outbox, run_no, finance, approver)
    pay_no = ledger.execute(text("select no from payment where run_no = :r"), {"r": run_no}).scalar_one()
    outbox.sent.clear()
    pin_today(date(2026, 11, 13))
    settlement.record(ledger, pay_no, finance, utr="HDFC26122400000101", value_date=date(2026, 11, 12), evidence=None)
    assert ledger.execute(text("select status, utr, value_date from payment where no = :n"), {"n": pay_no}).one() == (
        "recorded", "HDFC26122400000101", date(2026, 11, 12),
    )
    assert _row(ledger, inv_no)["status"] == "approved"
    assert ledger.execute(text("select count(*) from budget_actual where source = 'payment'")).scalar_one() == 0
    assert any("HDFC26122400000101" in m["body"] for m in outbox.sent if m["subject"] == f"Payment advice {pay_no}")

    # when the bank statement names the payment, then it is settled and the invoice is paid and the case closed
    csv = (
        "date,utr,narration,debit,credit\n"
        f"2026-11-12,HDFC26122400000101,NEFT {pay_no} SUPPLIER,1180.00,0.00\n"
    )
    done = bank_statement.import_statement(
        ledger, csv, finance, today=date(2026, 11, 13), tenant=ledger.info["slug"]
    )
    assert [s["payment"] for s in done["settled"]] == [pay_no]
    assert _row(ledger, inv_no)["status"] == "paid"
    assert ledger.execute(text("select status from payment_run where no = :r"), {"r": run_no}).scalar_one() == "paid"
    assert ledger.execute(text("select status from po where no = :p"), {"p": po_no}).scalar_one() == "closed"
    assert ledger.execute(text("select count(*) from budget_actual where source = 'payment'")).scalar_one() == 1
    # a second import of the same statement changes nothing
    again = bank_statement.import_statement(
        ledger, csv, finance, today=date(2026, 11, 13), tenant=ledger.info["slug"]
    )
    assert again["settled"] == [] and again["already_paid"]



def test_marking_a_run_paid_by_hand_is_gone_and_a_reference_is_never_invented(ledger: Session, outbox: Outbox) -> None:  # noqa: F811
    # given a filed run, when anyone calls the old mark-paid endpoint, then it answers 410;
    # and a statement line without a UTR leaves the payment's UTR empty (DF-01)
    pin_today(date(2026, 11, 13))
    finance, approver, _ = _people(ledger)
    _, inv_no = _two_suppliers(ledger)
    _due_on(ledger, inv_no, date(2026, 11, 12))
    pin_today(date(2026, 11, 12))
    run_no = _propose(ledger, finance, date(2026, 11, 12))
    _file(ledger, outbox, run_no, finance, approver)
    h_fin = _headers(ledger.info["slug"], finance, "Finance")
    assert client.post(f"/api/payment-runs/{run_no}/paid", json={}, headers=h_fin).status_code == 410
    pay_no = ledger.execute(text("select no from payment where run_no = :r"), {"r": run_no}).scalar_one()
    pin_today(date(2026, 11, 13))
    bank_statement.import_statement(
        ledger, _statement_for(ledger, run_no, without_utr=pay_no), finance, today=date(2026, 11, 13),
        tenant=ledger.info["slug"],
    )  # fmt: skip
    assert ledger.execute(text("select utr, status from payment where no = :n"), {"n": pay_no}).one() == (None, "settled")



def test_a_returned_payment_reopens_its_invoices_and_a_run_pays_them_again(ledger: Session, outbox: Outbox) -> None:  # noqa: F811
    # given a settled payment, when the bank returns it, then the payment is failed, the invoice is approved and due
    # again, a task asks Finance to pay again, and a new run takes the same amount (Y8)
    pin_today(date(2026, 11, 12))
    finance, approver, _ = _people(ledger)
    _, inv_no = _two_suppliers(ledger)
    _due_on(ledger, inv_no, date(2026, 11, 12))
    run_no = _propose(ledger, finance, date(2026, 11, 12))
    _file(ledger, outbox, run_no, finance, approver)
    pay_no = ledger.execute(text("select no from payment where run_no = :r"), {"r": run_no}).scalar_one()

    pin_today(date(2026, 11, 16))
    credit_line = ledger.execute(
        text(
            "insert into bank_statement_line (line_key, row_no, value_date, narration, amount, status, imported_by, direction) "
            "values ('return-1', 1, '2026-11-14', 'NEFT RTN BENEFICIARY A/C CLOSED', 1180.00, 'open', :u, 'credit') returning id"
        ),
        {"u": str(finance)},
    ).scalar_one()
    ledger.commit()
    settlement.failed(
        ledger, pay_no, finance, reason="beneficiary account closed", value_date=date(2026, 11, 14),
        bank_line_id=int(credit_line), tenant=ledger.info["slug"],
    )  # fmt: skip
    assert ledger.execute(text("select status, fail_reason from payment where no = :n"), {"n": pay_no}).one() == (
        "failed", "beneficiary account closed",
    )
    assert (_row(ledger, inv_no)["status"], _row(ledger, inv_no)["in_run"]) == ("approved", None)
    assert ledger.execute(text("select count(*) from task where kind = 'payment_failed'")).scalar_one() == 1
    again = _propose(ledger, finance, date(2026, 11, 16))
    assert ledger.execute(text("select amount from payment where run_no = :r"), {"r": again}).scalar_one() == D("1180.00")



def test_a_reversed_settlement_takes_the_budget_back_and_reopens_the_order_line(
    ledger: Session, outbox: Outbox
) -> None:  # noqa: F811
    # given a settled payment that closed its order, when the settlement is reversed, then the invoice is approved,
    # a negative budget row is kept, and the order line is reopened to received (Y8)
    pin_today(date(2026, 11, 12))
    finance, approver, _ = _people(ledger)
    ledger.execute(text("update item set stocked = false where code = 'A'"))
    ledger.commit()
    po_no, inv_no = _two_suppliers(ledger)
    _due_on(ledger, inv_no, date(2026, 11, 12))
    run_no = _propose(ledger, finance, date(2026, 11, 12))
    _file(ledger, outbox, run_no, finance, approver)
    pay_no = ledger.execute(text("select no from payment where run_no = :r"), {"r": run_no}).scalar_one()
    pin_today(date(2026, 11, 13))
    bank_statement.import_statement(
        ledger, _statement_for(ledger, run_no), finance, today=date(2026, 11, 13), tenant=ledger.info["slug"]
    )  # fmt: skip
    assert ledger.execute(text("select status from po_line")).scalar_one() == "closed"

    settlement.reversed(
        ledger, pay_no, finance, reason="wrong beneficiary",
        evidence=settlement.Evidence(b"%PDF-1.4 bank note", "return.pdf", "application/pdf"),
    )  # fmt: skip
    assert (_row(ledger, inv_no)["status"], _row(ledger, inv_no)["paid_on"]) == ("approved", None)
    assert ledger.execute(text("select status from po_line")).scalar_one() == "received"
    amounts = ledger.execute(
        text("select amount from budget_actual where source = 'payment' order by id")
    ).scalars().all()
    assert any(D(a) > 0 for a in amounts) and any(D(a) < 0 for a in amounts)
    assert ledger.execute(text("select count(*) from audit_event where action = 'po.reopen'")).scalar_one() == 1


# ---- notes ------------------------------------------------------------------------------------



def test_a_bank_reference_must_be_unique_and_a_value_date_cannot_come_before_the_run(
    ledger: Session, outbox: Outbox
) -> None:  # noqa: F811
    # given a filed run with one payment, when it is recorded with the wrong date or with a UTR already used,
    # then the service refuses; the right record goes through and the recorded state is not settled (Y7)
    pin_today(date(2026, 11, 12))
    finance, approver, _ = _people(ledger)
    _, inv_no = _two_suppliers(ledger)
    _due_on(ledger, inv_no, date(2026, 11, 12))
    run_no = _propose(ledger, finance, date(2026, 11, 12))
    _file(ledger, outbox, run_no, finance, approver)
    pay_no = ledger.execute(text("select no from payment where run_no = :r"), {"r": run_no}).scalar_one()
    with pytest.raises(InvoiceError, match="before the run date"):
        settlement.record(ledger, pay_no, finance, utr="HDFC000001", value_date=date(2026, 11, 1), evidence=None)
    with pytest.raises(InvoiceError, match="letters and digits"):
        settlement.record(ledger, pay_no, finance, utr="BAD-1", value_date=date(2026, 11, 12), evidence=None)
    with pytest.raises(InvoiceError, match="bank reference or a proof"):
        settlement.record(ledger, pay_no, finance, utr=None, value_date=date(2026, 11, 12), evidence=None)
    settlement.record(ledger, pay_no, finance, utr="HDFC000001", value_date=date(2026, 11, 12), evidence=None)
    # a second payment with the same UTR is refused
    second_po, _ = _goods(ledger, qty="11", price="100")
    inv2 = _invoice(ledger, second_po, "11", "100", number="S1-2")
    _due_on(ledger, inv2.no, date(2026, 11, 12))
    run2 = _propose(ledger, finance, date(2026, 11, 12))
    _file(ledger, outbox, run2, finance, approver)
    pay2 = ledger.execute(text("select no from payment where run_no = :r"), {"r": run2}).scalar_one()
    with pytest.raises(InvoiceError, match="already on"):
        settlement.record(ledger, pay2, finance, utr="HDFC000001", value_date=date(2026, 11, 12), evidence=None)


def test_a_payment_fully_covered_by_a_note_is_settled_when_the_file_is_made(
    ledger: Session, outbox: Outbox
) -> None:  # noqa: F811
    # given a credit note as large as the invoice, when the run is filed, then the payment is 0.00 and settles with
    # nothing to pay, the invoice is paid, and no bank line is needed (12.3.4)
    finance, approver, _ = _people(ledger)
    _, inv_no = _two_suppliers(ledger)
    note = _credit_note(ledger, inv_no, "10", "100", number="CN-ZERO")
    notes.approve(ledger, note, finance)
    _due_on(ledger, inv_no, date(2026, 11, 12))
    pin_today(date(2026, 11, 12))
    run_no = _propose(ledger, finance, date(2026, 11, 12))
    pay_no = ledger.execute(text("select no, amount from payment where run_no = :r"), {"r": run_no}).one()
    assert pay_no.amount == D("0.00")
    _file(ledger, outbox, run_no, finance, approver)
    assert ledger.execute(text("select status, value_date from payment where no = :n"), {"n": pay_no.no}).one() == (
        "settled", date(2026, 11, 12),
    )
    assert _row(ledger, inv_no)["status"] == "paid"
    assert ledger.execute(text("select count(*) from bank_statement_line")).scalar_one() == 0
    assert ledger.execute(
        text("select after->>'how' from audit_event where action = 'payment.settle' and entity_id = :n"),
        {"n": pay_no.no},
    ).scalar_one() == "nothing to pay"
