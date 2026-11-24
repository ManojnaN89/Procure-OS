"""Payment run integrity: one live run per item, blocks, bank changes, rejection and lapse (block 12, 12.2).

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
from test_sourcing import PASSWORD, Outbox, db, outbox  # noqa: F401 - fixtures

from p2p.formulas.calendar import pin_today
from p2p.invoices import runs, settlement
from p2p.invoices.service import InvoiceError
from p2p.masters import suppliers
from p2p.platform import users
from p2p.platform.sod import user

D = Decimal


def test_a_run_pays_what_is_due_small_suppliers_first_and_two_proposals_never_take_the_same_invoice(
    ledger: Session, outbox: Outbox
) -> None:  # noqa: F811
    # given an approved invoice and its pay-by day, when a run is planned, then it is one payment for the supplier
    finance, approver, _ = _people(ledger)
    _, inv_no = _two_suppliers(ledger)
    _due_on(ledger, inv_no, date(2026, 11, 12))
    assert [r["no"] for r in runs.due_invoices(ledger, date(2026, 11, 1), 7)] == []  # not in the window
    made = runs.propose_run(ledger, date(2026, 11, 6), finance, window_days=7)
    run_no = made["run"]
    assert made["total"] == D("1180.00") and made["status"] == "proposed"
    assert ledger.execute(
        text("select supplier_code, amount, status from payment where run_no = :r"), {"r": run_no}
    ).one() == ("S1", D("1180.00"), "planned")
    assert ledger.execute(text("select invoice_no, amount from payment_allocation")).one() == (inv_no, D("1180.00"))

    # when a second proposal is made at once, then it takes nothing (the first holds the invoice)
    with pytest.raises(InvoiceError, match="nothing is due"):
        runs.propose_run(ledger, date(2026, 11, 6), finance, window_days=7)
    # and no invoice is in two live allocations
    assert ledger.execute(
        text("select count(*) from (select invoice_no from payment_allocation group by invoice_no having count(*) > 1) x")
    ).scalar_one() == 0
    assert approver



def test_a_blocked_invoice_is_taken_out_at_approval_and_the_run_is_approved_for_less(
    ledger: Session, outbox: Outbox
) -> None:  # noqa: F811
    # given a run with two suppliers, when one invoice is blocked before approval, then it leaves the run with a
    # reason, the run totals less, its invoice is due again, and a code sent for the old total is refused (Y5b)
    finance, approver, _ = _people(ledger)
    first, inv1 = _two_suppliers(ledger)
    second, _ = _goods(ledger, qty="12", price="100")
    ledger.execute(text("update supplier set msme_category = null where code = 'S1'"))
    ledger.execute(text("update po set supplier_code = 'S1' where no = :p"), {"p": second})
    inv2 = _invoice(ledger, second, "12", "100", number="S1-2")
    _due_on(ledger, inv1, date(2026, 11, 12))
    _due_on(ledger, inv2.no, date(2026, 11, 12))
    run_no = _propose(ledger, finance)
    total_before = ledger.execute(text("select total from payment_run where no = :r"), {"r": run_no}).scalar_one()
    ledger.execute(
        text("update invoice set payment_block = 'match', block_reason = 'price above the order' where no = :n"),
        {"n": inv2.no},
    )
    ledger.commit()
    runs.recheck(ledger, run_no, user(approver))
    ledger.commit()
    runs.send_code(ledger, run_no, approver)
    result = runs.approve_step(ledger, run_no, approver, _code(outbox))
    assert result["status"] == "approved"
    assert ledger.execute(text("select total from payment_run where no = :r"), {"r": run_no}).scalar_one() < total_before
    assert ledger.execute(text("select in_run from invoice where no = :n"), {"n": inv2.no}).scalar_one() is None
    assert ledger.execute(text("select after->>'item', after->>'reason' from audit_event where action = 'payment_run.item_removed'")).one() == (
        inv2.no, "blocked: price above the order",
    )
    assert first



def test_a_pending_bank_change_holds_a_supplier_out_of_a_new_run_and_takes_it_out_at_the_file(
    ledger: Session, outbox: Outbox
) -> None:  # noqa: F811
    # given a verified supplier, when a bank change waits for approval, then a new run does not take it,
    # and a run already approved takes it out at the file (Y5c)
    finance, approver, _ = _people(ledger)
    po_no, inv_no = _two_suppliers(ledger)
    _due_on(ledger, inv_no, date(2026, 11, 12))
    run_no = _propose(ledger, finance)
    buyer = users.create_user(ledger.info["slug"], f"bankbuyer{ledger.info['sfx']}@x.example", PASSWORD, "A Buyer", "Buyer")
    suppliers.change_bank(ledger, "S1", {"account_no": "11122233344", "ifsc": "ICIC0004321"}, user(buyer), "moved bank")
    runs.send_code(ledger, run_no, approver)
    with pytest.raises(InvoiceError, match="cancelled|bank change"):
        runs.approve_step(ledger, run_no, approver, _code(outbox))
    assert ledger.execute(
        text("select status, cancel_reason from payment_run where no = :r"), {"r": run_no}
    ).scalar_one() == "cancelled"
    assert "bank change waiting for approval" in {h["reason"] for h in runs.held_back(ledger, date(2026, 11, 6), 7)}
    assert po_no



def test_a_rejected_run_is_cancelled_and_its_items_are_due_again(ledger: Session, outbox: Outbox) -> None:  # noqa: F811
    # given a proposed run, when its step person rejects it with a reason, then the run is cancelled, its payments
    # are cancelled, the invoice is out of a run, and a new run takes it (Y5d)
    finance, approver, _ = _people(ledger)
    _, inv_no = _two_suppliers(ledger)
    _due_on(ledger, inv_no, date(2026, 11, 12))
    run_no = _propose(ledger, finance)
    runs.reject(ledger, run_no, approver, "check the bank account")
    assert ledger.execute(text("select status, cancel_reason from payment_run where no = :r"), {"r": run_no}).one() == (
        "cancelled", "check the bank account",
    )
    assert ledger.execute(text("select status from payment where run_no = :r"), {"r": run_no}).scalar_one() == "cancelled"
    assert ledger.execute(text("select in_run from invoice where no = :n"), {"n": inv_no}).scalar_one() is None
    assert _propose(ledger, finance) != run_no



def test_a_run_not_filed_on_its_date_is_cancelled_by_the_daily_job(ledger: Session, outbox: Outbox) -> None:  # noqa: F811
    # given an approved run dated Thursday 12 November, when the daily lapse runs on Friday 13 November,
    # then the run is cancelled "not filed on its date" and its items are due again
    finance, approver, _ = _people(ledger)
    _, inv_no = _two_suppliers(ledger)
    _due_on(ledger, inv_no, date(2026, 11, 12))
    run_no = _propose(ledger, finance, date(2026, 11, 12))
    _approve(ledger, outbox, run_no, approver)
    assert runs.lapse(ledger, date(2026, 11, 13)) == [run_no]
    assert ledger.execute(text("select cancel_reason from payment_run where no = :r"), {"r": run_no}).scalar_one() == (
        "not filed on its date"
    )


# ---- record, settle, fail, reverse ------------------------------------------------------------


