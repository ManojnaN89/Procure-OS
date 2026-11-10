"""The bank statement CSV: payments are settled when the reference and the amount agree, and everything else goes to a person."""

from datetime import date
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session
from test_invoices import TODAY, _headers, _row, ledger  # noqa: F401 - fixtures
from test_orders import shop  # noqa: F401 - fixtures
from test_payments import _paid_run, _people, _two_suppliers
from test_receiving import store  # noqa: F401 - fixtures
from test_sourcing import Outbox, db, outbox  # noqa: F401 - fixtures

from p2p.invoices import bank_statement as statement
from p2p.invoices.service import InvoiceError
from p2p.main import app

client = TestClient(app)
D = Decimal
HEAD = "date,utr,narration,debit,credit\n"


def _run(ledger_: Session) -> tuple[str, str, str, int, int]:
    finance, owner, _ = _people(ledger_)
    _, inv_no, _ = _two_suppliers(ledger_)
    run = _paid_run(ledger_, finance, owner)
    pay_no = ledger_.execute(text("select no from payment where run_no = :r"), {"r": run}).scalar_one()
    return run, inv_no, pay_no, finance, owner


def test_a_line_with_the_payment_reference_and_the_exact_amount_settles_the_run(
    ledger: Session, outbox: Outbox
) -> None:  # noqa: F811
    run, inv_no, pay_no, finance, _ = _run(ledger)
    csv = HEAD + f"2026-11-05,UTRBANK0001,NEFT {pay_no} S1 SUPPLIES,1180.00,\n"

    got = statement.import_statement(ledger, csv, finance, today=TODAY)

    assert got["runs_paid"] == [run] and got["for_a_person"] == [] and got["task_id"] is None
    assert [m["payment"] for m in got["matched"]] == [pay_no]
    assert ledger.execute(text("select utr, paid_on from payment where no = :n"), {"n": pay_no}).one() == (
        "UTRBANK0001",
        date(2026, 11, 5),
    )
    assert (_row(ledger, inv_no)["status"], _row(ledger, inv_no)["paid_on"]) == ("paid", date(2026, 11, 5))
    assert ledger.execute(text("select status from payment_run")).scalar_one() == "paid"
    assert (
        ledger.execute(text("select count(*) from audit_event where action = 'payment_run.statement'")).scalar_one()
        == 1
    )


def test_the_same_statement_twice_changes_nothing_the_second_time(ledger: Session, outbox: Outbox) -> None:  # noqa: F811
    run, _, pay_no, finance, _ = _run(ledger)
    csv = HEAD + f"2026-11-05,UTRBANK0001,{pay_no},1180.00,\n"
    statement.import_statement(ledger, csv, finance, today=TODAY)
    sent = len(outbox.sent)

    again = statement.import_statement(ledger, csv, finance, today=TODAY)

    assert (
        again["runs_paid"] == []
        and again["matched"] == []
        and [a["payment"] for a in again["already_paid"]] == [pay_no]
    )
    assert len(outbox.sent) == sent  # no second remittance advice


def test_a_line_whose_amount_differs_is_not_applied_and_a_person_gets_a_task(ledger: Session, outbox: Outbox) -> None:  # noqa: F811
    run, inv_no, pay_no, finance, _ = _run(ledger)
    csv = HEAD + f"2026-11-05,UTRBANK0002,{pay_no},1179.99,\n"

    got = statement.import_statement(ledger, csv, finance, today=TODAY, tenant=ledger.info["slug"])

    assert got["runs_paid"] == [] and got["matched"] == []
    assert "the amount differs" in got["for_a_person"][0]["why"] and pay_no in got["for_a_person"][0]["why"]
    assert got["task_id"]
    assert ledger.execute(text("select status from payment_run")).scalar_one() == "file_generated"
    assert _row(ledger, inv_no)["status"] != "paid"


def test_lines_that_cannot_be_matched_read_or_trusted_are_listed_for_a_person(ledger: Session, outbox: Outbox) -> None:  # noqa: F811
    run, _, pay_no, finance, _ = _run(ledger)
    csv = (
        HEAD
        + "2026-11-05,U1,SALARY NOV,50000.00,\n"  # no payment has this reference
        + f"2026-11-20,U2,{pay_no},1180.00,\n"  # a date that has not come
        + "not a date,U3,SOMETHING,10.00,\n"  # an unreadable date
        + "2026-11-05,U4,INTEREST,,12.00\n"  # a credit: not a payment
    )

    got = statement.import_statement(ledger, csv, finance, today=TODAY)

    whys = sorted(x["why"] for x in got["for_a_person"])
    assert len(whys) == 3 and any("no payment has this reference" in w for w in whys)
    assert any("has not come" in w for w in whys) and any("cannot be read" in w for w in whys)
    assert got["skipped"] == 1 and got["runs_paid"] == []
    assert ledger.execute(text("select status from payment_run")).scalar_one() == "file_generated"


def test_a_run_is_settled_only_when_every_payment_in_it_is_on_the_statement(ledger: Session, outbox: Outbox) -> None:  # noqa: F811
    run, _, pay_no, finance, _ = _run(ledger)
    ledger.execute(
        text(
            "insert into payment (no, run_no, type, supplier_code, amount, mode, reference) "
            "select 'PAY-2627-000099', run_no, type, supplier_code, 500, mode, 'REF-99' from payment where no = :n"
        ),
        {"n": pay_no},
    )
    ledger.commit()

    half = statement.import_statement(
        ledger, HEAD + f"2026-11-05,U1,{pay_no},1180.00,\n", finance, today=TODAY, tenant=ledger.info["slug"]
    )

    assert half["runs_paid"] == [] and half["waiting"][0]["payment"] == pay_no and half["task_id"]
    assert ledger.execute(text("select status from payment_run")).scalar_one() == "file_generated"


def test_a_payment_is_found_by_the_reference_sent_in_the_bank_file_too(ledger: Session, outbox: Outbox) -> None:  # noqa: F811
    run, _, pay_no, finance, _ = _run(ledger)
    reference = ledger.execute(text("select reference from payment where no = :n"), {"n": pay_no}).scalar_one()
    assert reference
    csv = f'Txn Date,Bank Ref,Particulars,Withdrawal Amt\n05-11-2026,UTR77,NEFT/{reference}/S1,"1,180.00"\n'

    got = statement.import_statement(ledger, csv, finance, today=TODAY)

    assert got["runs_paid"] == [run]  # other column names, a dd-mm-yyyy date and an amount with a comma are read


@pytest.mark.parametrize("bad", ["", "date,narration\n2026-11-05,x\n"])
def test_a_statement_with_no_lines_or_no_amount_column_is_refused(ledger: Session, bad: str) -> None:  # noqa: F811
    with pytest.raises(InvoiceError):
        statement.import_statement(ledger, bad, 1, today=TODAY)


def test_finance_imports_the_statement_over_the_api_and_others_cannot(ledger: Session, outbox: Outbox) -> None:  # noqa: F811
    run, _, pay_no, finance, owner = _run(ledger)
    slug = ledger.info["slug"]
    h_fin, h_owner = _headers(slug, finance, "Finance"), _headers(slug, owner, "Approver")
    csv = HEAD + f"2026-11-05,UTRAPI01,{pay_no},1180.00,\n"

    assert client.post("/api/payment-runs/statement", json={"csv": csv}, headers=h_owner).status_code == 403
    assert client.post("/api/payment-runs/statement", json={}, headers=h_fin).status_code == 422
    assert client.post("/api/payment-runs/statement", json={"csv": "date,x\n1,2\n"}, headers=h_fin).status_code == 422
    done = client.post("/api/payment-runs/statement", json={"csv": csv}, headers=h_fin)

    assert done.status_code == 200 and done.json()["runs_paid"] == [run]
    assert client.post("/api/payment-runs/statement", json={"csv": csv}).status_code == 401
