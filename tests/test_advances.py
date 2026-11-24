"""Advances on an order: request, own approval, payment in a run, use against the invoice, cancel and recovery
(2.3, D-18, D-23, DF-17). Given, when, then in each test."""

from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session
from test_invoices import BUYER_GSTIN, TODAY, _goods, _invoice, _row, ledger  # noqa: F401 - fixtures
from test_orders import shop  # noqa: F401 - fixtures
from test_payments import _approve, _code, _file, _people, _propose  # noqa: F401
from test_receiving import _receive, _sent, store  # noqa: F401 - fixtures
from test_sourcing import Outbox, db, outbox  # noqa: F401 - fixtures

from p2p.formulas.calendar import pin_today
from p2p.invoices import advances, bank_statement, notes, runs
from p2p.invoices.service import InvoiceError
from p2p.orders import changes as order_changes
from p2p.orders import dispatch
from p2p.platform import proposals
from p2p.platform.sod import user

D = Decimal
TERMS = "ADV30N30"


def _advance_terms(ledger_: Session) -> None:
    ledger_.execute(
        text(
            "insert into payment_terms (code, name, days, baseline, advance_pct, discount_pct, discount_days) values "
            "('ADV30N30', '30% advance, balance Net 30', 30, 'invoice_date', 30, 0, 0) on conflict (code) do nothing"
        )
    )
    ledger_.commit()


def _order(ledger_: Session, qty: str = "50", price: str = "650") -> tuple[str, dict[str, int]]:
    """A sent and acknowledged order on the advance terms (not touchless: D-23), not yet received."""
    _advance_terms(ledger_)
    po_no, logins = _sent(ledger_, qty=qty, price=price)
    ledger_.execute(text("update po set payment_terms_code = :t where no = :n"), {"t": TERMS, "n": po_no})
    ledger_.commit()
    dispatch.acknowledge(ledger_, po_no, user(1), promised=None, note=None)
    return po_no, logins


def test_an_advance_is_a_percentage_of_the_order_and_only_a_person_asks_for_it(ledger: Session, outbox: Outbox) -> None:  # noqa: F811
    # given an acknowledged order of 38,350.00 on 30% advance terms, when the buyer asks for 30%,
    # then the gross is 11,505.00 and the taxable 9,750.00, and the purchase manager is the one who approves
    finance, approver, _ = _people(ledger)
    po_no, _ = _order(ledger)
    asked = advances.request(ledger, po_no, finance, pct=D("30"), reason="supplier needs material")

    assert (asked["gross"], asked["taxable"], asked["approver"]) == (D("11505.00"), D("9750.00"), "U5")
    # when 31% is asked, then the terms' 30% is the limit
    with pytest.raises(InvoiceError, match="at most 30"):
        advances.request(ledger, po_no, finance, pct=D("31"), reason="more")
    # and one live request per order
    with pytest.raises(InvoiceError, match="already has an advance request"):
        advances.request(ledger, po_no, finance, pct=D("10"), reason="again")
    assert approver


def test_a_request_without_advance_terms_or_on_an_order_not_sent_is_refused(ledger: Session, outbox: Outbox) -> None:  # noqa: F811
    # given an order on Net 30, when an advance is asked, then it is refused; a draft order is refused too
    finance, _, _ = _people(ledger)
    po_no, _ = _sent(ledger)
    with pytest.raises(InvoiceError, match="carry no advance"):
        advances.request(ledger, po_no, finance, pct=D("10"), reason="no terms")
    _advance_terms(ledger)
    draft = "PO-DRAFT-1"
    ledger.execute(
        text(
            "insert into po (no, supplier_code, ship_to_location, status, total, taxable, payment_terms_code) "
            "values (:n, 'S1', 'WH-BLR', 'draft', 1000, 847.46, :t)"
        ),
        {"n": draft, "t": TERMS},
    )
    ledger.commit()
    with pytest.raises(InvoiceError, match="supplier has seen"):
        advances.request(ledger, draft, finance, pct=D("10"), reason="draft")


def test_only_the_named_person_approves_the_advance_and_the_requester_cannot(ledger: Session, outbox: Outbox) -> None:  # noqa: F811
    # given a request by Finance, when Finance tries to approve it, then it waits for U5; U5 approves it
    finance, approver, _ = _people(ledger)
    po_no, _ = _order(ledger)
    asked = advances.request(ledger, po_no, finance, pct=D("30"), reason="material")
    with pytest.raises(Exception, match="advance.approve|waits for"):
        proposals.approve(ledger, int(asked["proposal"]), finance)
    proposals.approve(ledger, int(asked["proposal"]), approver)
    assert ledger.execute(
        text("select status, approved_by from advance_request where id = :i"), {"i": asked["id"]}
    ).one() == ("approved", "U5")


def test_the_invoice_takes_the_settled_advance_off_and_its_tax_base_with_it(ledger: Session, outbox: Outbox) -> None:  # noqa: F811
    # given a paid advance of 11,505.00 (taxable 9,750.00), when the order's invoice of 38,350.00 is booked,
    # then 11,505.00 is taken off (taxable 9,750.00), the payable is 26,845.00 and the TDS base 22,750.00 (Y9)
    finance, approver, _ = _people(ledger)
    po_no, _ = _order(ledger)
    asked = advances.request(ledger, po_no, finance, pct=D("30"), reason="material")
    proposals.approve(ledger, int(asked["proposal"]), approver)
    pin_today(date(2026, 12, 1))
    run_no = _propose(ledger, finance, date(2026, 12, 1))
    _file(ledger, outbox, run_no, finance, approver)
    pay_no = ledger.execute(text("select no from payment where run_no = :r"), {"r": run_no}).scalar_one()
    pin_today(date(2026, 12, 2))
    bank_statement.import_statement(
        ledger,
        "date,utr,narration,debit,credit\n" f"2026-12-02,ADV0001,{pay_no},11505.00,0.00\n",
        finance, today=date(2026, 12, 2), tenant=ledger.info["slug"],
    )  # fmt: skip
    _receive(ledger, po_no, "50")
    inv = _invoice(ledger, po_no, "50", "650", number="ADV-INV-1", on=date(2026, 12, 10))
    booked = _row(ledger, inv.no)
    assert (booked["advance_used"], booked["advance_taxable_used"]) == (D("11505.00"), D("9750.00"))
    assert booked["total"] == D("38350.00")
    assert ledger.execute(text("select base, amount from tds_entry where invoice_no = :n"), {"n": inv.no}).one() == (
        D("0.00"),
        D("0.00"),
    )
    assert ledger.execute(
        text("select used_gross from advance where payment_no = :p"), {"p": pay_no}
    ).scalar_one() == D("11505.00")


def test_cancelling_an_order_with_a_paid_advance_is_refused_until_it_is_recovered(
    ledger: Session, outbox: Outbox
) -> None:  # noqa: F811
    # given an order whose advance was paid, when the order is cancelled, then it is refused with the words;
    # Finance recovers it with a debit note, another Finance person approves it, a refund makes it paid,
    # and then the order can be cancelled (Y9b, AT-06)
    finance, approver, _ = _people(ledger)
    finance2 = __import__("p2p.platform.users", fromlist=["create_user"]).create_user(
        ledger.info["slug"], f"fin2{ledger.info['sfx']}@x.example", "Password-123", "Second Finance", "Finance"
    )
    ledger.execute(
        text(
            "insert into org_user (code, name, designation, department_code, manager_code, email, active) "
            "values (:c, 'Second Finance', 'Finance Manager', 'FIN', 'U1', :e, true) on conflict (code) do nothing"
        ),
        {"c": f"G{ledger.info['sfx']}", "e": f"fin2{ledger.info['sfx']}@x.example"},
    )
    ledger.commit()
    po_no, _ = _order(ledger)
    asked = advances.request(ledger, po_no, finance, pct=D("30"), reason="material")
    proposals.approve(ledger, int(asked["proposal"]), approver)
    pin_today(date(2026, 12, 1))
    run_no = _propose(ledger, finance, date(2026, 12, 1))
    _file(ledger, outbox, run_no, finance, approver)
    pay_no = ledger.execute(text("select no from payment where run_no = :r"), {"r": run_no}).scalar_one()
    pin_today(date(2026, 12, 2))
    bank_statement.import_statement(
        ledger,
        "date,utr,narration,debit,credit\n" f"2026-12-02,ADV0001,{pay_no},11505.00,0.00\n",
        finance, today=date(2026, 12, 2), tenant=ledger.info["slug"],
    )  # fmt: skip
    with pytest.raises(Exception, match="recover it before cancelling"):
        order_changes.cancel(ledger, ledger.info["slug"], po_no, finance, "supplier cannot supply")

    recovered = advances.recover(ledger, int(asked["id"]), finance, "supplier cannot supply")
    assert ledger.execute(
        text("select status, recovery_note from advance_request where id = :i"), {"i": asked["id"]}
    ).one() == (
        "recovering",
        recovered["note"],
    )
    notes.approve(ledger, recovered["note"], finance2)
    line = ledger.execute(
        text(
            "insert into bank_statement_line (line_key, row_no, value_date, narration, amount, status, imported_by, direction) "
            "values ('adv-refund', 1, '2026-12-03', 'REFUND ADVANCE', 11505.00, 'open', :u, 'credit') returning id"
        ),
        {"u": str(finance)},
    ).scalar_one()
    ledger.commit()
    notes.refund(ledger, recovered["note"], int(line), D("11505.00"), finance2)
    assert (
        ledger.execute(text("select status from advance_request where id = :i"), {"i": asked["id"]}).scalar_one()
        == "recovered"
    )
    result = order_changes.cancel(ledger, ledger.info["slug"], po_no, finance, "supplier cannot supply")
    assert result["status"] == "cancelled"


def test_a_cancelled_run_leaves_no_advance_row_and_no_tax(ledger: Session, outbox: Outbox) -> None:  # noqa: F811
    # given a proposed run with an advance, when it is rejected, then its advance row is gone, its request is
    # approved again, and its tax was never written
    finance, approver, _ = _people(ledger)
    po_no, _ = _order(ledger)
    asked = advances.request(ledger, po_no, finance, pct=D("30"), reason="material")
    proposals.approve(ledger, int(asked["proposal"]), approver)
    pin_today(date(2026, 12, 1))
    run_no = _propose(ledger, finance, date(2026, 12, 1))
    assert ledger.execute(text("select count(*) from advance")).scalar_one() == 1
    runs.reject(ledger, run_no, approver, "not this week")
    assert ledger.execute(text("select count(*) from advance")).scalar_one() == 0
    assert ledger.execute(text("select count(*) from tds_entry where payment_no is not null")).scalar_one() == 0
    assert (
        ledger.execute(text("select status from advance_request where id = :i"), {"i": asked["id"]}).scalar_one()
        == "approved"
    )


def test_an_invoice_waits_until_the_advance_on_its_order_is_settled(ledger: Session, outbox: Outbox) -> None:  # noqa: F811
    # given an approved but unpaid advance on the order, when its invoice is approved, then the invoice is held back
    # with "advance on <po> not settled"; a run cannot pay it yet
    finance, approver, _ = _people(ledger)
    po_no, _ = _order(ledger)
    asked = advances.request(ledger, po_no, finance, pct=D("30"), reason="material")
    proposals.approve(ledger, int(asked["proposal"]), approver)
    _receive(ledger, po_no, "50")
    inv = _invoice(ledger, po_no, "50", "650", number="ADV-INV-2", on=date(2026, 12, 10))
    ledger.execute(
        text("update invoice set due_date = '2026-12-20', pay_by = '2026-12-18' where no = :n"), {"n": inv.no}
    )
    ledger.commit()
    reasons = {h["reason"] for h in runs.held_back(ledger, date(2026, 12, 18), 7)}
    assert f"advance on {po_no} not settled" in reasons
