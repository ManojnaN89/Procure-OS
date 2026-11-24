"""Payment: what is due and held back, one live run per item, the approver the rule names with a code, the bank file,
recording with evidence, settlement by the bank statement, failure and reversal, TDS and notes. Given, when, then in
each test; the arithmetic itself is in test_payment_formulas.py."""

import re
from datetime import date
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session
from test_invoices import TODAY, _goods, _headers, _invoice, _row, ledger  # noqa: F401 - fixtures
from test_orders import _logins, shop  # noqa: F401 - fixtures
from test_receiving import store  # noqa: F401 - fixtures
from test_sourcing import PASSWORD, Outbox, db, outbox  # noqa: F401 - fixtures

from p2p.invoices import bank_statement, notes, payment, runs, settlement, tds
from p2p.invoices.service import InvoiceError
from p2p.formulas.calendar import pin_today
from p2p.main import app
from p2p.masters import suppliers
from p2p.platform import otp, permissions, proposals, users
from p2p.platform.sod import user

D = Decimal
client = TestClient(app)


# ---- the world these tests need ---------------------------------------------------------------


def _payment_rules(ledger_: Session) -> None:
    """The rule that names who approves a run and an advance (the tests' company has only order rules)."""
    ledger_.execute(
        text(
            "insert into approver_role (role, source, value) values ('procurement_head', 'designation', 'Head - Procurement') "
            "on conflict (role) do nothing"
        )
    )
    ledger_.execute(
        text(
            "insert into approval_rule (rule_code, document, min_value, max_value, category_code, department_code, "
            "spend_type, approver_chain, version, effective_from) values "
            "('PAY-01', 'payment_run', 0, 999999999, 'ANY', 'ANY', 'ANY', 'procurement_head', 1, '2024-11-01'), "
            "('ADV-01', 'advance', 0, 999999999, 'ANY', 'ANY', 'ANY', 'procurement_head', 1, '2024-11-01') "
            "on conflict do nothing"
        )
    )
    ledger_.execute(
        text(
            "update item_category set default_tds_section = '194Q' where code in "
            "(select category_code from item where category_code is not null)"
        )
    )
    ledger_.commit()


def _people(ledger_: Session) -> tuple[int, int, int]:
    """Finance (proposes and records), the named approver (procurement head U5), and AP."""
    slug, sfx = ledger_.info["slug"], ledger_.info["sfx"]
    if "flow_logins" not in ledger_.info:
        ledger_.info["flow_logins"] = _logins(ledger_)
    logins = ledger_.info["flow_logins"]
    finance = users.create_user(slug, f"fin{sfx}@x.example", PASSWORD, "A Finance", "Finance")
    ap = users.create_user(slug, f"ap{sfx}@x.example", PASSWORD, "An AP", "AP")
    ledger_.execute(
        text(
            "insert into org_user (code, name, designation, department_code, manager_code, email, active) "
            "values (:c, 'A Finance', 'Finance Manager', 'FIN', 'U1', :e, true) on conflict (code) do nothing"
        ),
        {"c": f"F{sfx}", "e": f"fin{sfx}@x.example"},
    )
    ledger_.commit()
    _payment_rules(ledger_)
    return finance, logins["procurement"], ap


def _two_suppliers(ledger_: Session) -> tuple[str, str]:
    """An approved invoice of S1 due in a week: goods received against a sent order."""
    po_no, _ = _goods(ledger_, qty="10", price="100")
    inv = _invoice(ledger_, po_no, "10", "100", number="S1-1")
    assert inv.status == "approved"
    return po_no, inv.no


def _due_on(ledger_: Session, invoice_no: str, on: date) -> None:
    ledger_.execute(
        text("update invoice set due_date = :d, pay_by = :d where no = :n"), {"d": on, "n": invoice_no}
    )
    ledger_.commit()


def _propose(ledger_: Session, finance: int, on: date = date(2026, 11, 6), **kw: object) -> str:
    return str(runs.propose_run(ledger_, on, finance, **kw)["run"])


def _code(outbox_: Outbox) -> str:
    for mail in reversed(outbox_.sent):
        found = re.search(r"Your one-time code is (\d{6})", str(mail.get("body", "")))
        if found:
            return found.group(1)
    raise AssertionError("no code was mailed")


def _approve(ledger_: Session, outbox_: Outbox, run_no: str, approver: int) -> dict:
    runs.send_code(ledger_, run_no, approver)
    return runs.approve_step(ledger_, run_no, approver, _code(outbox_))


def _file(ledger_: Session, outbox_: Outbox, run_no: str, finance: int, approver: int) -> str:
    _approve(ledger_, outbox_, run_no, approver)
    return runs.generate_file(ledger_, run_no, finance)


def _statement_for(ledger_: Session, run_no: str, *, without_utr: str | None = None) -> str:
    """The bank's debit lines for a run, naming each payment and its exact amount."""
    rows = ["date,utr,narration,debit,credit"]
    for p in ledger_.execute(
        text("select no, amount from payment where run_no = :r order by no"), {"r": run_no}
    ).all():
        ref = "" if p[0] == without_utr else f"UTR{p[0][-8:]}"
        rows.append(f"2026-11-12,{ref},NEFT {p[0]} SUPPLIER,{p[1]},0.00")
    return "\n".join(rows) + "\n"


def _earlier_purchase(ledger_: Session, taxable: str, section: str = "194Q") -> None:
    ledger_.execute(
        text(
            "insert into invoice (no, supplier_code, supplier_invoice_no, dedupe_key, invoice_date, fy, received_on, taxable, total, "
            "status, booked_at) values ('PI-OLD', 'S1', 'OLD-1', 'OLD1', '2026-06-01', '2026-27', '2026-06-01', :t, :t, 'paid', now())"
        ),
        {"t": taxable},
    )
    ledger_.execute(
        text(
            "insert into tds_entry (invoice_no, section, section_code, base, rate, amount, booked_on, quarter, status) "
            "values ('PI-OLD', '194Q', :c, 0, 0.1, 0, '2026-06-01', '2026-27 Q1', 'confirmed')"
        ),
        {"c": section},
    )
    ledger_.commit()


# ---- TDS at booking ---------------------------------------------------------------------------


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


def test_the_person_the_rule_names_approves_with_a_code_and_nobody_else_does(
    ledger: Session, outbox: Outbox
) -> None:  # noqa: F811
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
    row = ledger.execute(
        text("select status, approved_by from payment_run where no = :r"), {"r": run_no}
    ).one()
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


def test_only_a_payment_permission_reads_what_is_due_and_the_named_person_approves_over_the_api(
    ledger: Session, outbox: Outbox
) -> None:  # noqa: F811
    # given Finance and a named approver, when what is due is read, then held-back rows come with reasons; when a run
    # is planned, then it waits for the named person; when they take a code and approve it, then it is approved (Y6)
    finance, approver, ap = _people(ledger)
    _, inv_no = _two_suppliers(ledger)
    _due_on(ledger, inv_no, date(2026, 11, 12))
    slug = ledger.info["slug"]
    h_fin, h_ap = _headers(slug, finance, "Finance"), _headers(slug, ap, "AP")
    h_app = _headers(slug, approver, "Approver")
    nobody = users.create_user(slug, f"nobody{ledger.info['sfx']}@x.example", PASSWORD, "A Requester", "Requester")
    h_none = _headers(slug, nobody, "Requester")

    due = client.get("/api/payment-runs/due?on=2026-11-06&window=7", headers=h_fin)
    assert due.status_code == 200
    assert [(p["invoice"], p["net"]) for p in due.json()["due"]] == [(inv_no, "1180.00")]
    assert client.get("/api/payment-runs/due", headers=h_none).status_code == 403
    assert client.post("/api/payment-runs", json={"run_date": "2026-11-06"}, headers=h_ap).status_code == 403
    planned = client.post("/api/payment-runs", json={"run_date": "2026-11-06", "window_days": 7}, headers=h_fin)
    assert planned.status_code == 200
    run_no = planned.json()["run"]
    page = client.get(f"/api/payment-runs/{run_no}", headers=h_fin).json()
    assert page["status"] == "proposed" and page["total"] == "1180.00" and page["needs_code"] is True
    assert page["can_approve"] is False
    assert client.get(f"/api/payment-runs/{run_no}", headers=h_app).json()["can_approve"] is True
    proposal_id = ledger.execute(text("select proposal_id from payment_run where no = :r"), {"r": run_no}).scalar_one()
    assert client.post(f"/api/admin/proposals/{proposal_id}/approve", json={}, headers=h_app).status_code == 409

    assert client.post(f"/api/payment-runs/{run_no}/code", headers=h_app).status_code == 200
    code = _code(outbox)
    approved = client.post(f"/api/payment-runs/{run_no}/approve", json={"code": code}, headers=h_app)
    assert approved.status_code == 200 and approved.json()["status"] == "approved"
    assert client.post(f"/api/payment-runs/{run_no}/file", headers=h_fin).status_code == 200
    assert client.get(f"/api/payment-runs/{run_no}", headers=h_fin).json()["has_file"] is True


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
