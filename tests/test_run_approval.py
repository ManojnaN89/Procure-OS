"""Who approves a payment run, with a code, and who may not pay what they approved (block 12, 12.1).

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
    _file,
    _people,
    _propose,
    _two_suppliers,
)
from test_receiving import store  # noqa: F401 - fixtures
from test_sourcing import Outbox, db, outbox  # noqa: F401 - fixtures

from p2p.formulas.calendar import pin_today
from p2p.invoices import runs, settlement
from p2p.invoices.service import InvoiceError

D = Decimal


def test_the_person_the_rule_names_approves_with_a_code_and_nobody_else_does(ledger: Session, outbox: Outbox) -> None:  # noqa: F811
    # given a proposed run, when Finance (its proposer) or another approver tries, then the run waits for U5;
    # when U5 tries without the code, then a code is demanded; with the right code the run is approved once
    finance, approver, _ = _people(ledger)
    logins = ledger.info["flow_logins"]
    _, inv_no = _two_suppliers(ledger)
    _due_on(ledger, inv_no, date(2026, 11, 12))
    run_no = _propose(ledger, finance)
    with pytest.raises(InvoiceError, match="this run waits for"):
        runs.send_code(ledger, run_no, finance)
    with pytest.raises(InvoiceError, match="this run waits for"):
        runs.send_code(ledger, run_no, logins["director"])
    with pytest.raises(InvoiceError, match="needs a one-time code"):
        runs.approve_step(ledger, run_no, approver, None)
    runs.send_code(ledger, run_no, approver)
    with pytest.raises(InvoiceError, match="4 tries left"):
        runs.approve_step(ledger, run_no, approver, "000000")
    step = runs.approve_step(ledger, run_no, approver, _code(outbox))
    assert step == {"status": "approved", "step": 1, "next": None}
    row = ledger.execute(text("select status, approved_by from payment_run where no = :r"), {"r": run_no}).one()
    assert row[0] == "approved" and row[1] == "U5"
    assert ledger.execute(
        text("select approved_at is not null, code_id is not null from payment_run_approval where run_no = :r"),
        {"r": run_no},
    ).one() == (True, True)


def test_a_run_leaves_out_an_invoice_its_approver_would_pay(ledger: Session, outbox: Outbox) -> None:  # noqa: F811
    # given an invoice whose approved_by is the run approver's own login, when a run is planned,
    # then the item is left out (a payer never pays an invoice they approved) and the invoice is free again
    if not ledger.execute(
        text("select 1 from information_schema.columns where table_name = 'invoice' and column_name = 'approved_by'")
    ).first():
        pytest.skip("NEEDS block 10: invoice.approved_by is added by 0170")
    finance, approver, _ = _people(ledger)
    _, inv_no = _two_suppliers(ledger)
    ledger.execute(text("update invoice set approved_by = :u where no = :n"), {"u": str(approver), "n": inv_no})
    ledger.commit()
    with pytest.raises(InvoiceError, match="every item waits"):
        _propose(ledger, finance)
    assert ledger.execute(text("select in_run from invoice where no = :n"), {"n": inv_no}).scalar_one() is None


def test_a_chain_of_two_steps_needs_each_person_and_their_own_code(ledger: Session, outbox: Outbox) -> None:  # noqa: F811
    # given a run rule of two steps, when the first person approves, then the run still waits for the second;
    # each step needs a code of its own
    finance, first, _ = _people(ledger)
    ledger.execute(
        text("update approval_rule set approver_chain = 'procurement_head>director' where rule_code = 'PAY-01'")
    )
    _, inv_no = _two_suppliers(ledger)
    _due_on(ledger, inv_no, date(2026, 11, 12))
    ledger.commit()
    run_no = _propose(ledger, finance)
    second = ledger.info["flow_logins"]["director"]
    with pytest.raises(InvoiceError, match="this run waits for"):
        runs.send_code(ledger, run_no, second)  # the first step is not the director's
    assert _approve(ledger, outbox, run_no, first)["status"] == "proposed"
    assert (
        ledger.execute(text("select status from payment_run where no = :r"), {"r": run_no}).scalar_one() == "proposed"
    )
    assert _approve(ledger, outbox, run_no, second)["status"] == "approved"
    steps = ledger.execute(
        text(
            "select step, approver, approved_by, code_id is not null from payment_run_approval where run_no = :r order by step"
        ),
        {"r": run_no},
    ).all()
    assert [(s[0], s[1], s[2]) for s in steps] == [(1, "U5", "U5"), (2, "U1", "U1")]
    assert all(s[3] for s in steps)


def test_a_deputy_approves_with_their_own_code(ledger: Session, outbox: Outbox) -> None:  # noqa: F811
    # given the named approver is away on the run date, when their deputy approves, then the deputy's own code lets
    # the run through and the step records the deputy
    finance, named, _ = _people(ledger)
    deputy = ledger.info["flow_logins"]["director"]
    ledger.execute(
        text(
            "insert into delegation (from_user, to_user, start_date, end_date, reason) "
            "values ('U5', 'U1', '2026-11-06', '2026-11-06', 'away')"
        )
    )
    _, inv_no = _two_suppliers(ledger)
    _due_on(ledger, inv_no, date(2026, 11, 12))
    ledger.commit()
    run_no = _propose(ledger, finance)
    assert _approve(ledger, outbox, run_no, deputy)["status"] == "approved"
    assert ledger.execute(
        text("select delegate, approved_by from payment_run_approval where run_no = :r"), {"r": run_no}
    ).one() == ("U1", "U1")


def test_the_person_who_approved_the_run_cannot_record_its_payment(ledger: Session, outbox: Outbox) -> None:  # noqa: F811
    # given a filed run, when the run's approver records a payment, then the recorder rule refuses
    finance, approver, _ = _people(ledger)
    _, inv_no = _two_suppliers(ledger)
    _due_on(ledger, inv_no, date(2026, 11, 12))
    pin_today(date(2026, 11, 12))
    run_no = _propose(ledger, finance, date(2026, 11, 12))
    _file(ledger, outbox, run_no, finance, approver)
    pay_no = ledger.execute(text("select no from payment where run_no = :r"), {"r": run_no}).scalar_one()
    with pytest.raises(InvoiceError, match="this person approved the run"):
        settlement.record(ledger, pay_no, approver, utr="HDFC000001", value_date=date(2026, 11, 12), evidence=None)
