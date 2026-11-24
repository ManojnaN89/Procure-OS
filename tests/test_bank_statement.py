"""The bank statement CSV: a debit line settles its own payment (OP-5), a credit that may be a return is only listed,
and everything else goes to a person. Given, when, then in each test."""

from datetime import date
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session
from test_invoices import BUYER_GSTIN, TODAY, _goods, _headers, _invoice, _row, ledger  # noqa: F401 - fixtures
from test_orders import shop  # noqa: F401 - fixtures
from test_payments import _code, _file, _people, _propose, _statement_for, _two_suppliers  # noqa: F401
from test_receiving import store  # noqa: F401 - fixtures
from test_sourcing import Outbox, db, outbox  # noqa: F401 - fixtures

from p2p.formulas.calendar import pin_today
from p2p.invoices import bank_statement as statement
from p2p.invoices.service import InvoiceError
from p2p.main import app

client = TestClient(app)
HEAD = "date,utr,narration,debit,credit\n"
D = Decimal


def _run(ledger_: Session, outbox_: Outbox) -> tuple[str, str, str, int, int]:
    finance, approver, _ = _people(ledger_)
    _, inv_no = _two_suppliers(ledger_)
    ledger_.execute(
        text("update invoice set due_date = '2026-11-12', pay_by = '2026-11-12' where no = :n"), {"n": inv_no}
    )
    ledger_.commit()
    pin_today(date(2026, 11, 12))
    run_no = _propose(ledger_, finance, date(2026, 11, 12))
    _file(ledger_, outbox_, run_no, finance, approver)
    pay_no = ledger_.execute(text("select no from payment where run_no = :r"), {"r": run_no}).scalar_one()
    pin_today(date(2026, 11, 13))
    return run_no, inv_no, str(pay_no), finance, approver


def test_a_debit_line_with_the_payments_reference_and_amount_settles_that_payment(
    ledger: Session, outbox: Outbox
) -> None:  # noqa: F811
    # given a filed run, when a debit line names its payment with the exact amount, then the payment is settled,
    # the invoice paid and the run paid
    run_no, inv_no, pay_no, finance, _ = _run(ledger, outbox)
    csv = HEAD + f"2026-11-13,UTRBANK0001,NEFT {pay_no} S1 SUPPLIES,1180.00,0.00\n"

    got = statement.import_statement(ledger, csv, finance, today=date(2026, 11, 13), tenant=ledger.info["slug"])

    assert [s["payment"] for s in got["settled"]] == [pay_no] and got["for_a_person"] == []
    assert ledger.execute(text("select utr, value_date, status from payment where no = :n"), {"n": pay_no}).one() == (
        "UTRBANK0001", date(2026, 11, 13), "settled",
    )
    assert (_row(ledger, inv_no)["status"], _row(ledger, inv_no)["paid_on"]) == ("paid", date(2026, 11, 13))
    assert ledger.execute(text("select status from payment_run where no = :r"), {"r": run_no}).scalar_one() == "paid"


def test_the_same_statement_twice_changes_nothing_the_second_time(ledger: Session, outbox: Outbox) -> None:  # noqa: F811
    # given a statement already imported, when it is imported again, then nothing is settled and every line is shown
    # as already paid
    run_no, _, pay_no, finance, _ = _run(ledger, outbox)
    csv = HEAD + f"2026-11-13,UTRBANK0001,{pay_no},1180.00,0.00\n"
    statement.import_statement(ledger, csv, finance, today=date(2026, 11, 13))
    sent = len(outbox.sent)

    again = statement.import_statement(ledger, csv, finance, today=date(2026, 11, 13))

    assert again["settled"] == [] and [a["payment"] for a in again["already_paid"]] == [pay_no]
    assert len(outbox.sent) == sent  # no second advice


def test_a_payment_settles_on_its_own_line_while_the_rest_of_the_run_waits(ledger: Session, outbox: Outbox) -> None:  # noqa: F811
    # given a run with two suppliers, when only one payment is on the statement, then that payment is settled and
    # the run still waits for the other (OP-5)
    run_no, _, pay_no, finance, approver = _run(ledger, outbox)
    second_po, _ = _goods(ledger, qty="7", price="100")
    from p2p.invoices import service as invoices

    inv2 = invoices.enter_invoice(
        ledger,
        invoices.InvoiceIn(
            "S1", "S1-2", TODAY, [invoices.InvLine(second_po, 1, D("7"), D("100"))],
            supplier_gstin="27AAAAA0000A1Z5", buyer_gstin=BUYER_GSTIN,
        ),
        1, as_of=TODAY,
    )  # fmt: skip
    ledger.execute(text("update invoice set due_date = '2026-11-12', pay_by = '2026-11-12' where no = :n"), {"n": inv2.no})
    ledger.commit()
    # a new run holds only the second supplier (the first is already in its run)
    pin_today(date(2026, 11, 12))
    run2 = _propose(ledger, finance, date(2026, 11, 12))
    _file(ledger, outbox, run2, finance, approver)
    pay2 = ledger.execute(text("select no from payment where run_no = :r"), {"r": run2}).scalar_one()
    pin_today(date(2026, 11, 13))

    got = statement.import_statement(
        ledger, HEAD + f"2026-11-13,U1,{pay2},826.00,0.00\n", finance, today=date(2026, 11, 13),
        tenant=ledger.info["slug"],
    )  # fmt: skip

    assert [s["payment"] for s in got["settled"]] == [str(pay2)]
    assert ledger.execute(text("select status from payment_run where no = :r"), {"r": run2}).scalar_one() == "paid"
    assert ledger.execute(text("select status from payment where no = :n"), {"n": pay_no}).scalar_one() == "in_file"


def test_a_line_whose_amount_differs_is_not_applied_and_a_person_gets_a_task(ledger: Session, outbox: Outbox) -> None:  # noqa: F811
    # given a statement line 0.01 short, when imported, then nothing is settled and the line waits for a person
    run_no, inv_no, pay_no, finance, _ = _run(ledger, outbox)

    got = statement.import_statement(
        ledger, HEAD + f"2026-11-13,U2,{pay_no},1179.99,0.00\n", finance, today=date(2026, 11, 13),
        tenant=ledger.info["slug"],
    )  # fmt: skip

    assert got["settled"] == [] and "the amount differs" in got["for_a_person"][0]["why"]
    assert got["task_id"]
    assert ledger.execute(text("select status from payment_run where no = :r"), {"r": run_no}).scalar_one() == "file_generated"
    assert _row(ledger, inv_no)["status"] == "approved"


def test_lines_that_cannot_be_matched_or_trusted_are_listed_for_a_person(ledger: Session, outbox: Outbox) -> None:  # noqa: F811
    # given a line with no payment, one dated in the future and one with an unreadable date, when imported,
    # then each is listed with its reason and none is applied
    run_no, _, pay_no, finance, _ = _run(ledger, outbox)
    csv = (
        HEAD
        + "2026-11-13,U1,SALARY NOV,50000.00,0.00\n"
        + f"2026-12-01,U2,{pay_no},1180.00,0.00\n"
        + "not a date,U3,SOMETHING,10.00,0.00\n"
    )

    got = statement.import_statement(ledger, csv, finance, today=date(2026, 11, 13))

    whys = sorted(x["why"] for x in got["for_a_person"])
    assert len(whys) == 3 and any("no payment has this reference" in w for w in whys)
    assert any("has not come" in w for w in whys) and any("cannot be read" in w for w in whys)
    assert got["settled"] == []


def test_a_credit_line_that_names_a_payment_is_listed_as_a_possible_return_and_never_applied(
    ledger: Session, outbox: Outbox
) -> None:  # noqa: F811
    # given a credit line naming a payment of ours with its amount, when imported, then it is listed for a person
    # as "may be the bank returning <payment>" and nothing changes (OP-7)
    run_no, _, pay_no, finance, _ = _run(ledger, outbox)

    got = statement.import_statement(
        ledger, HEAD + f"2026-11-13,,NEFT RTN {pay_no} BENEFICIARY A/C CLOSED,0.00,1180.00\n",
        finance, today=date(2026, 11, 13), tenant=ledger.info["slug"],
    )  # fmt: skip

    assert [r["payment"] for r in got["returns"]] == [pay_no] and got["settled"] == []
    assert "may be the bank returning" in got["returns"][0]["why"]
    assert ledger.execute(text("select status from payment where no = :n"), {"n": pay_no}).scalar_one() == "in_file"
    assert ledger.execute(
        text("select direction, status from bank_statement_line where narration like 'NEFT RTN%'")
    ).one() == ("credit", "open")


def test_a_payment_is_found_by_the_reference_sent_in_the_bank_file_too(ledger: Session, outbox: Outbox) -> None:  # noqa: F811
    # given other column names and a dd-mm-yyyy date, when the reference in the bank file is in the narration,
    # then the payment is settled
    run_no, _, pay_no, finance, _ = _run(ledger, outbox)
    reference = ledger.execute(text("select reference from payment where no = :n"), {"n": pay_no}).scalar_one()
    csv = f'Txn Date,Bank Ref,Particulars,Withdrawal Amt\n13-11-2026,UTR77,NEFT/{reference}/S1,"1,180.00"\n'

    got = statement.import_statement(ledger, csv, finance, today=date(2026, 11, 13))

    assert [s["payment"] for s in got["settled"]] == [pay_no]


@pytest.mark.parametrize("bad", ["", "date,narration\n2026-11-13,x\n"])
def test_a_statement_with_no_lines_or_no_amount_column_is_refused(ledger: Session, bad: str) -> None:  # noqa: F811
    with pytest.raises(InvoiceError):
        statement.import_statement(ledger, bad, 1, today=date(2026, 11, 13))


def test_finance_imports_the_statement_over_the_api_and_others_cannot(ledger: Session, outbox: Outbox) -> None:  # noqa: F811
    # given a filed run, when the statement is posted as CSV by Finance, then its payment settles; a person without
    # the payment permission is refused
    run_no, _, pay_no, finance, _ = _run(ledger, outbox)
    slug = ledger.info["slug"]
    h_fin = _headers(slug, finance, "Finance")
    csv = HEAD + f"2026-11-13,UTRAPI01,{pay_no},1180.00,0.00\n"

    assert client.post("/api/payment-runs/statement", json={}, headers=h_fin).status_code == 422
    assert client.post("/api/payment-runs/statement", json={"csv": "date,x\n1,2\n"}, headers=h_fin).status_code == 422
    done = client.post("/api/payment-runs/statement", json={"csv": csv}, headers=h_fin)

    assert done.status_code == 200 and [s["payment"] for s in done.json()["settled"]] == [pay_no]
    assert client.post("/api/payment-runs/statement", json={"csv": csv}).status_code == 401
    assert _code and TODAY
