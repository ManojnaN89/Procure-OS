"""Reconciliation (plan step 13): the order trace, received not invoiced, supplier statements, bank lines a person matches,
and small suppliers' invoices against the MSMED Act deadline."""

import io
import uuid
from datetime import date, timedelta
from decimal import Decimal

import openpyxl
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session
from test_invoices import TODAY, _goods, _headers, _invoice, _receive, _sent, ledger  # noqa: F401 - fixtures
from test_orders import shop  # noqa: F401 - fixtures
from test_payments import _file, _people, _propose, _statement_for, _two_suppliers
from test_receiving import store  # noqa: F401 - fixtures
from test_sourcing import Outbox, db, outbox  # noqa: F401 - fixtures

from p2p.formulas import payment as pay_rules
from p2p.formulas import reconciliation as rec
from p2p.formulas.calendar import pin_today
from p2p.invoices import bank_statement, reconciliation, trace, vendor_statement
from p2p.invoices import payment as payments
from p2p.invoices.service import InvoiceError
from p2p.main import app
from p2p.platform import routing, tenants, users

client = TestClient(app)
D = Decimal
DAY = date(2026, 11, 1)


def _small(shop_: Session, code: str = "S1", *, category: str = "small", agreed: int = 30) -> None:
    """Make a supplier a micro or small enterprise with a written agreement (and the Udyam certificate that needs)."""
    shop_.execute(
        text("update supplier set msme_category = :m, msme_agreement = true, agreed_payment_days = :a where code = :c"),
        {"m": category, "a": agreed, "c": code},
    )
    shop_.execute(
        text(
            "insert into supplier_document (supplier_code, doc_type, number, issued_on, verified) values (:c, 'udyam', 'U', '2020-01-01', true)"
        ),
        {"c": code},
    )
    shop_.commit()


def _paid_run(ledger_: Session, finance: int, owner: int, outbox_: Outbox) -> str:
    """A filed run for the invoice due 12 November (not yet settled)."""
    ledger_.execute(text("update invoice set due_date = '2026-11-12', pay_by = '2026-11-12' where type = 'invoice'"))
    ledger_.commit()
    pin_today(date(2026, 11, 12))
    run = _propose(ledger_, finance, date(2026, 11, 12))
    _file(ledger_, outbox_, run, finance, owner)
    pin_today(date(2026, 11, 13))
    return run


def _audit(db_: Session, action: str) -> int:
    return int(db_.execute(text("select count(*) from audit_event where action = :a"), {"a": action}).scalar_one())


# ---- received not invoiced: the arithmetic -------------------------------------------------------


def ev(day: int, qty: str, value: str) -> rec.Event:
    return rec.Event(DAY + timedelta(days=day), D(qty), D(value))


def test_given_a_line_received_and_invoiced_exactly_when_reconciled_then_nothing_is_open() -> None:
    p = rec.position([ev(0, "10", "1000.00")], [ev(2, "10", "1000.00")], DAY + timedelta(days=5))

    assert (p.not_invoiced, p.not_received, p.since, p.age_days) == (D(0), D(0), None, None)
    assert (p.received_qty, p.invoiced_qty) == (D("10"), D("10"))


def test_given_an_invoice_a_paisa_short_or_over_when_reconciled_then_the_paisa_is_open_on_the_right_side() -> None:
    on = DAY + timedelta(days=5)
    short = rec.position([ev(0, "10", "1000.00")], [ev(1, "10", "999.99")], on)
    over = rec.position([ev(0, "10", "1000.00")], [ev(1, "10", "1000.01")], on)

    assert (short.not_invoiced, short.not_received, short.since, short.age_days) == (D("0.01"), D(0), DAY, 5)
    assert (over.not_invoiced, over.not_received, over.since) == (D(0), D("0.01"), DAY + timedelta(days=1))


def test_given_two_part_receipts_and_one_invoice_when_reconciled_then_the_open_value_is_aged_from_the_second() -> None:
    receipts = [ev(0, "4", "400.00"), ev(10, "6", "600.00")]

    p = rec.position(receipts, [ev(3, "4", "400.00")], DAY + timedelta(days=12))

    assert (p.not_invoiced, p.since, p.age_days) == (D("600.00"), DAY + timedelta(days=10), 2)


def test_given_events_after_the_date_when_reconciled_then_they_do_not_count_yet() -> None:
    receipts = [ev(0, "4", "400.00"), ev(10, "6", "600.00")]
    invoices = [ev(3, "4", "400.00"), ev(11, "6", "600.00")]

    assert rec.position(receipts, invoices, DAY + timedelta(days=9)).not_invoiced == D(0)
    assert rec.position(receipts, invoices, DAY + timedelta(days=10)).not_invoiced == D("600.00")  # received that day
    assert rec.position(receipts, invoices, DAY + timedelta(days=11)).not_invoiced == D(0)


def test_given_a_credit_note_on_the_line_when_reconciled_then_it_takes_value_off_what_was_invoiced() -> None:
    p = rec.position(
        [ev(0, "10", "1000.00")], [ev(1, "12", "1200.00"), ev(2, "-2", "-200.00")], DAY + timedelta(days=3)
    )

    assert (p.invoiced_qty, p.invoiced_value, p.not_received, p.not_invoiced) == (D("10"), D("1000.00"), D(0), D(0))


# ---- the MSMED Act deadline ---------------------------------------------------------------------


def test_given_a_written_agreement_of_45_days_when_checked_then_day_45_is_not_late_and_day_46_is() -> None:
    deadline = pay_rules.msme_deadline(DAY, True, 45, pay_rules.MsmeLimits())

    assert deadline == DAY + timedelta(days=45)
    assert pay_rules.days_late(deadline, DAY + timedelta(days=45)) == 0
    assert pay_rules.days_late(deadline, DAY + timedelta(days=46)) == 1
    assert pay_rules.days_late(deadline, DAY + timedelta(days=44)) == -1


def test_given_no_agreement_or_more_than_the_limit_when_checked_then_15_days_or_the_limit_apply() -> None:
    limits = pay_rules.MsmeLimits()
    assert pay_rules.msme_deadline(DAY, False, 60, limits) == DAY + timedelta(days=15)
    assert pay_rules.msme_deadline(DAY, True, 60, limits) == DAY + timedelta(days=45)  # never more than 45
    assert pay_rules.msme_deadline(DAY, True, 30, limits) == DAY + timedelta(days=30)
    assert pay_rules.msme_deadline(DAY, True, None, limits) == DAY + timedelta(days=45)
    tighter = pay_rules.MsmeLimits(max_days=40, no_agreement_days=10)  # the limits are the company's data
    assert pay_rules.msme_deadline(DAY, False, None, tighter) == DAY + timedelta(days=10)
    assert pay_rules.msme_deadline(DAY, True, 45, tighter) == DAY + timedelta(days=40)


# ---- a supplier's statement: the matching -------------------------------------------------------


def _theirs(row: int, kind: str, no: str, amount: str, day: int = 0, ref: str = "") -> rec.Theirs:
    return rec.Theirs(row, DAY + timedelta(days=day), kind, no, D(amount), ref)


def _ours(ref: str, kind: str, no: str, amount: str, day: int = 0, **kw: object) -> rec.Ours:
    return rec.Ours(ref, kind, no, DAY + timedelta(days=day), D(amount), **kw)  # type: ignore[arg-type]


def test_given_a_statement_when_matched_then_each_line_is_matched_differs_or_one_sided_with_its_reason() -> None:
    theirs = [
        _theirs(2, "invoice", "inv/001", "1180.00"),  # same number written differently
        _theirs(3, "invoice", "INV-002", "500.00"),
        _theirs(4, "invoice", "INV-009", "300.00"),
        _theirs(5, "credit_note", "CN-1", "100.00"),
        _theirs(6, "payment", "NEFT", "1180.00", day=3, ref="UTR777"),
    ]
    ours = [
        _ours("PI-1", "invoice", "INV 001", "1180.00"),
        _ours("PI-2", "invoice", "INV-002", "500.01"),
        _ours("PI-3", "invoice", "INV-003", "250.00"),
        _ours("PAY-1", "payment", "PAY-1", "1180.00", day=3, utr="UTR777"),
    ]

    got = {(r.row, r.our_ref): r for r in rec.match_statement(theirs, ours)}

    assert got[(2, "PI-1")].outcome == "matched"
    assert got[(3, "PI-2")].outcome == "amount_differs" and got[(3, "PI-2")].difference == D("-0.01")
    assert "500.00" in got[(3, "PI-2")].reason and "500.01" in got[(3, "PI-2")].reason
    assert got[(4, None)].outcome == "only_theirs" and "INV-009" in got[(4, None)].reason
    assert got[(5, None)].outcome == "only_theirs" and "credit note" in got[(5, None)].reason
    assert got[(6, "PAY-1")].outcome == "matched" and "reference" in got[(6, "PAY-1")].reason
    assert got[(None, "PI-3")].outcome == "only_ours" and "not on their statement" in got[(None, "PI-3")].reason
    assert len(got) == 6


def test_given_a_line_twice_a_cancelled_invoice_or_a_payment_without_reference_when_matched_then_each_is_explained() -> (
    None
):
    theirs = [
        _theirs(2, "invoice", "A-1", "100.00"),
        _theirs(3, "invoice", "A-1", "100.00"),
        _theirs(4, "invoice", "A-2", "50.00"),
        _theirs(5, "payment", "CHQ", "70.00", day=5),  # no reference: three days from ours, same amount
        _theirs(6, "payment", "CHQ", "70.00", day=9),
    ]
    ours = [
        _ours("PI-1", "invoice", "A-1", "100.00"),
        _ours("PI-2", "invoice", "A-2", "50.00", absent="was cancelled"),
        _ours("PAY-1", "payment", "PAY-1", "70.00", day=2),
    ]

    got = rec.match_statement(theirs, ours, window_days=3)

    assert [r.outcome for r in got] == ["matched", "only_theirs", "only_theirs", "matched", "only_theirs"]
    assert "twice" in got[1].reason and "cancelled" in got[2].reason
    assert got[3].our_ref == "PAY-1" and "same amount within 3 days" in got[3].reason
    assert "no payment of ours" in got[4].reason  # day 9 is seven days from ours


def test_bank_suggestions_are_the_same_amount_named_first_then_nearest_in_date() -> None:
    c = [
        rec.Candidate("PAY-3", None, D("100.00"), DAY + timedelta(days=9)),
        rec.Candidate("PAY-2", "REF-B", D("100.00"), DAY + timedelta(days=1)),
        rec.Candidate("PAY-1", "REF-A", D("100.00"), DAY),
        rec.Candidate("PAY-4", None, D("100.01"), DAY),
    ]

    assert rec.suggest(D("100.00"), "NEFT REF-B", DAY, c) == ["PAY-2", "PAY-1", "PAY-3"]
    assert rec.suggest(D("100.00"), "NEFT", DAY, c, limit=1) == ["PAY-1"]
    assert rec.suggest(D("99.99"), "PAY-1", DAY, c) == []


# ---- received not invoiced on the books ---------------------------------------------------------


def test_given_goods_received_with_rejections_and_a_service_confirmed_when_listed_then_only_accepted_value_counts(
    ledger: Session, outbox: Outbox
) -> None:  # noqa: F811
    po_no, _ = _sent(ledger, qty="10", price="100")
    _receive(ledger, po_no, "10", rejected="2", reason="damaged")
    before = reconciliation.received_vs_invoiced(ledger, TODAY - timedelta(days=1))

    on = reconciliation.received_vs_invoiced(ledger, TODAY)

    assert before["lines"] == []
    (line,) = on["lines"]
    assert (line["received_qty"], line["not_invoiced"], line["since"], line["age_days"]) == (
        D("8"),
        D("800.00"),
        TODAY,
        0,
    )
    assert on["suppliers"][0]["not_invoiced"] == D("800.00") and on["not_received"] == D(0)

    ledger.execute(
        text(
            "insert into service_entry (no, po_no, po_line_no, basis, value, qty_accepted, amount_accepted, accepted_by, "
            "accepted_on, posted_by) values ('SRN-1', :p, 1, 'amount', 150, 1.5, 150.00, 'U1', :d, '1')"
        ),
        {"p": po_no, "d": TODAY},
    )
    ledger.commit()
    assert reconciliation.received_vs_invoiced(ledger, TODAY)["not_invoiced"] == D("950.00")

    _invoice(ledger, po_no, "8", "100", number="INV-8")
    after = reconciliation.received_vs_invoiced(ledger, TODAY)
    assert after["not_invoiced"] == D("150.00") and after["not_received"] == D(0)


def test_given_an_invoice_for_more_than_was_received_when_listed_then_it_is_invoiced_not_received(
    ledger: Session, outbox: Outbox
) -> None:  # noqa: F811
    po_no, _ = _goods(ledger, qty="10", price="100", received="4")
    _invoice(ledger, po_no, "10", "100", number="INV-10")  # blocked for quantity, still on the books

    got = reconciliation.received_vs_invoiced(ledger, TODAY)

    (line,) = got["lines"]
    assert (line["not_received"], line["not_invoiced"], line["invoiced_qty"]) == (D("600.00"), D(0), D("10"))
    assert reconciliation.received_vs_invoiced(ledger, TODAY, supplier="S2")["lines"] == []


# ---- the order trace ------------------------------------------------------------------------------


def _production_order(db_: Session, request_no: str) -> tuple[str, str]:
    """A production order whose material request bought its shortfall through the purchase request given."""
    imp = db_.execute(
        text(
            "insert into demand_import (source, files, loaded, already_loaded, rejected) values ('t', '{}', 1, 0, 0) "
            "returning id"
        )
    ).scalar_one()
    po_id = db_.execute(
        text(
            "insert into production_order (order_no, product_code, qty, need_by, location_code, import_id) "
            "values ('MO-77', 'PUMP', 5, :d, 'WH', :i) returning id"
        ),
        {"d": TODAY, "i": imp},
    ).scalar_one()
    req_id = db_.execute(
        text(
            "insert into material_requirement (source_kind, source_doc_no, line_key, production_order_id, item_code, qty, "
            "unit, base_qty, need_by, location_code, import_id) values ('production_order', 'MO-77', 'A', :p, 'A', 10, "
            "'NOS', 10, :d, 'WH', :i) returning id"
        ),
        {"p": po_id, "d": TODAY, "i": imp},
    ).scalar_one()
    db_.execute(
        text(
            "insert into material_request (no, production_order_id, order_no, product_code, location_code, need_by) "
            "values ('MR-77', :p, 'MO-77', 'PUMP', 'WH', :d)"
        ),
        {"p": po_id, "d": TODAY},
    )
    line_id = db_.execute(
        text(
            "insert into material_request_line (mr_no, line_no, requirement_id, item_code, location_code, uom, need_qty, "
            "need_by) values ('MR-77', 1, :r, 'A', 'WH', 'NOS', 10, :d) returning id"
        ),
        {"r": req_id, "d": TODAY},
    ).scalar_one()
    db_.execute(
        text("insert into material_request_buy (mr_line_id, request_no, request_line_no, qty) values (:l, :r, 1, 10)"),
        {"l": line_id, "r": request_no},
    )
    db_.commit()
    return "MO-77", "MR-77"


def test_given_a_paid_order_when_traced_from_any_document_then_the_whole_chain_comes_back_the_same(
    ledger: Session, outbox: Outbox
) -> None:  # noqa: F811
    finance, owner, _ = _people(ledger)
    po_no, inv_no = _two_suppliers(ledger)
    run = _paid_run(ledger, finance, owner, outbox)
    bank_statement.import_statement(
        ledger, _statement_for(ledger, run), finance, today=date(2026, 11, 13), tenant=ledger.info["slug"]
    )
    request_no = ledger.execute(text("select request_no from po_line where po_no = :p"), {"p": po_no}).scalar_one()
    mo, mr = _production_order(ledger, request_no)
    gr_no = ledger.execute(text("select no from goods_receipt")).scalar_one()
    pay_no = ledger.execute(text("select no from payment")).scalar_one()

    chains = {no: trace.chain(ledger, no) for no in (mo, mr, request_no, po_no, gr_no, inv_no, pay_no)}

    first = chains[mo]
    assert first is not None
    stages = {s["stage"]: [i["no"] for i in s["items"]] for s in first["stages"]}
    assert stages == {
        "production_order": [mo], "material_request": [mr], "request": [request_no], "po": [po_no],
        "goods_receipt": [gr_no], "invoice": [inv_no], "payment": [pay_no],
    }  # fmt: skip
    for no, got in chains.items():
        assert got is not None and got["kind"] == trace.kind_of(ledger, no)
        assert [(s["stage"], [i["no"] for i in s["items"]]) for s in got["stages"]] == [
            (s["stage"], [i["no"] for i in s["items"]]) for s in first["stages"]
        ], no
    by_stage = {s["stage"]: s["items"][0] for s in first["stages"]}
    assert by_stage["invoice"]["amount"] == D("1180.00") and by_stage["invoice"]["status"] == "paid"
    assert by_stage["goods_receipt"]["qty"] == D("10") and by_stage["goods_receipt"]["amount"] == D("1000.00")
    assert by_stage["payment"]["who"] and by_stage["payment"]["status"] == "paid"
    assert by_stage["po"]["amount"] is not None and by_stage["request"]["date"] is not None
    assert (by_stage["po"]["to_receive"], by_stage["po"]["to_invoice"]) == (D(0), D(0))  # nothing open on it
    assert trace.chain(ledger, "NOPE-1") is None


# ---- a supplier's statement on the books ----------------------------------------------------------


def _statement(rows: list[str]) -> bytes:
    return ("date,type,document_no,amount,reference\n" + "\n".join(rows) + "\n").encode()


def test_given_a_supplier_statement_when_imported_then_each_outcome_is_kept_and_a_second_import_adds_nothing(
    ledger: Session, outbox: Outbox
) -> None:  # noqa: F811
    _, _, ap_user = _people(ledger)
    po1, logins = _goods(ledger, qty="10", price="100")
    _invoice(ledger, po1, "10", "100", number="S1-1")
    po2, _ = _goods(ledger, qty="5", price="40", logins=logins)
    _invoice(ledger, po2, "5", "40", number="S1-2")  # 236.00 on our books
    data = _statement(
        [
            f"{TODAY},invoice,s1/1,1180.00,",
            f"{TODAY},invoice,S1-2,240.00,",
            f"{TODAY},Credit note,CN-5,50.00,",
            f"{TODAY},payment,NEFT,999.00,UTR-NONE",
            "someday,invoice,S1-7,10.00,",
        ]
    )

    got = vendor_statement.import_statement(ledger, "S1", data, "s1.csv", ap_user)

    outcomes = [(r["doc_no"], r["outcome"]) for r in got["results"]]
    assert outcomes == [
        ("s1/1", "matched"), ("S1-2", "amount_differs"), ("CN-5", "only_theirs"), ("NEFT", "only_theirs"),
    ]  # fmt: skip
    differs = got["results"][1]
    assert (differs["their_amount"], differs["our_amount"], differs["difference"]) == (
        D("240.00"),
        D("236.00"),
        D("4.00"),
    )
    assert got["unreadable"] == [{"row": 6, "why": "the date cannot be read"}]
    assert (got["lines"], got["matched"], got["amount_differs"], got["only_theirs"], got["only_ours"]) == (
        4,
        1,
        1,
        2,
        0,
    )
    assert (got["period_from"], got["period_to"]) == (TODAY, TODAY)
    assert _audit(ledger, "vendor_statement.import") == 1

    again = vendor_statement.import_statement(ledger, "S1", data, "s1.csv", ap_user)

    assert again["already_imported"] and again["id"] == got["id"]
    assert ledger.execute(text("select count(*) from vendor_statement_line")).scalar_one() == 4
    assert _audit(ledger, "vendor_statement.import") == 1

    wider = vendor_statement.import_statement(
        ledger, "S1", _statement([f"{TODAY},invoice,S1-1,1180.00,"]), "s1b.csv", ap_user,
        period_from=TODAY - timedelta(days=30), period_to=TODAY,
    )  # fmt: skip
    assert [(r["our_ref"] is not None, r["outcome"]) for r in wider["results"]] == [
        (True, "matched"),
        (True, "only_ours"),
    ]
    assert "not on their statement" in wider["results"][1]["reason"]

    earlier = vendor_statement.import_statement(
        ledger, "S1", _statement([f"{TODAY},invoice,S1-2,236.00,"]), "s1c.csv", ap_user,
        period_from=TODAY - timedelta(days=30), period_to=TODAY - timedelta(days=1),
    )  # fmt: skip
    assert [(r["outcome"], r["our_ref"]) for r in earlier["results"]] == [("only_theirs", None)]
    assert "after the statement" in earlier["results"][0]["reason"]  # our books as they stood at its end


def test_a_statement_in_excel_reads_like_the_csv_and_a_bad_file_is_refused() -> None:
    book = openpyxl.Workbook()
    sheet = book.active
    sheet.append(["Date", "Type", "Document No", "Amount", "Reference"])
    sheet.append([TODAY, "Invoice", "S1-1", 1180, None])
    sheet.append([TODAY, "Receipt", "PAY-2627-000001", "1,180.00", "UTR1"])
    buf = io.BytesIO()
    book.save(buf)

    lines, bad = vendor_statement.parse(buf.getvalue(), "s.xlsx")

    assert bad == []
    assert [(t.doc_type, t.doc_no, t.amount, t.on) for t in lines] == [
        ("invoice", "S1-1", D("1180"), TODAY), ("payment", "PAY-2627-000001", D("1180.00"), TODAY),
    ]  # fmt: skip
    with pytest.raises(InvoiceError, match="template"):
        vendor_statement.parse(b"when,what\n1,2\n", "s.csv")
    with pytest.raises(InvoiceError, match=r"\.csv or \.xlsx"):
        vendor_statement.parse(b"x", "s.pdf")


# ---- bank lines a person matches ---------------------------------------------------------------------


HEAD = "date,utr,narration,debit,credit\n"


def test_given_a_bank_line_without_a_reference_when_a_person_confirms_it_then_the_run_is_paid_and_audited(
    ledger: Session, outbox: Outbox
) -> None:  # noqa: F811
    finance, owner, _ = _people(ledger)
    _two_suppliers(ledger)
    _paid_run(ledger, finance, owner, outbox)
    pay_no = ledger.execute(text("select no from payment")).scalar_one()
    csv = HEAD + "2026-11-05,UTRX1,NEFT ALPHA TRADERS,1180.00,\n"

    first = bank_statement.import_statement(ledger, csv, finance, today=TODAY)
    bank_statement.import_statement(ledger, csv, finance, today=TODAY)  # the same statement again: kept once

    assert first["settled"] == [] and first["for_a_person"][0]["why"] == "no payment has this reference"
    (line,) = bank_statement.open_lines(ledger)
    assert line["suggestions"] == [pay_no] and line["amount"] == D("1180.00")
    with pytest.raises(InvoiceError, match="segregation of duties"):
        bank_statement.confirm(ledger, line["id"], pay_no, owner, today=TODAY)  # the run's approver
    ledger.rollback()

    done = bank_statement.confirm(ledger, line["id"], pay_no, finance, today=TODAY)

    assert done == {"line": line["id"], "payment": pay_no, "run_paid": True}
    assert ledger.execute(text("select p.status, p.utr, p.value_date from payment p join payment_run r on r.no = p.run_no")).one() == (
        "settled", "UTRX1", date(2026, 11, 5),
    )  # fmt: skip
    assert ledger.execute(text("select status, payment_no from bank_statement_line")).one() == ("matched", pay_no)
    assert _audit(ledger, "bank_statement.confirm") == 1
    assert bank_statement.open_lines(ledger) == []
    with pytest.raises(InvoiceError, match="already"):
        bank_statement.confirm(ledger, line["id"], pay_no, finance, today=TODAY)


def test_a_confirmation_with_another_amount_or_an_unknown_payment_is_refused(ledger: Session, outbox: Outbox) -> None:  # noqa: F811
    finance, owner, _ = _people(ledger)
    _two_suppliers(ledger)
    _paid_run(ledger, finance, owner, outbox)
    pay_no = ledger.execute(text("select no from payment")).scalar_one()
    bank_statement.import_statement(ledger, HEAD + "2026-11-05,U9,SOMETHING,1179.99,\n", finance, today=TODAY)
    (line,) = bank_statement.open_lines(ledger)

    assert line["suggestions"] == []
    with pytest.raises(InvoiceError, match="amounts differ"):
        bank_statement.confirm(ledger, line["id"], pay_no, finance, today=TODAY)
    ledger.rollback()
    with pytest.raises(InvoiceError, match="does not exist"):
        bank_statement.confirm(ledger, line["id"], "PAY-NOPE", finance, today=TODAY)
    ledger.rollback()
    assert _audit(ledger, "bank_statement.confirm") == 0


def test_a_run_is_settled_by_payments_found_on_two_statements(ledger: Session, outbox: Outbox) -> None:  # noqa: F811
    finance, owner, _ = _people(ledger)
    _two_suppliers(ledger)
    run = _paid_run(ledger, finance, owner, outbox)
    pay_no = ledger.execute(text("select no from payment where run_no = :r"), {"r": run}).scalar_one()
    ledger.execute(
        text(
            "insert into payment (no, run_no, type, supplier_code, amount, mode, reference, status) "
            "select 'PAY-2627-000099', run_no, type, supplier_code, 500, mode, 'REF-99', 'in_file' from payment where no = :n"
        ),
        {"n": pay_no},
    )
    ledger.commit()

    one = bank_statement.import_statement(ledger, HEAD + f"2026-11-04,U1,{pay_no},1180.00,\n", finance, today=TODAY)
    two = bank_statement.import_statement(ledger, HEAD + "2026-11-05,U2,REF-99,500.00,\n", finance, today=TODAY)

    assert [s["payment"] for s in one["settled"]] == [pay_no]
    assert [s["payment"] for s in two["settled"]] == ["PAY-2627-000099"]
    assert ledger.execute(text("select status from payment_run")).scalar_one() == "paid"
    assert ledger.execute(text("select value_date from payment where no = :n"), {"n": pay_no}).scalar_one() == date(
        2026, 11, 4
    )


# ---- small suppliers' deadlines -------------------------------------------------------------------


def test_given_a_small_supplier_invoice_when_listed_then_day_45_is_due_and_day_46_is_one_day_late(
    ledger: Session, outbox: Outbox
) -> None:  # noqa: F811
    _small(ledger, agreed=45)
    po_no, _ = _goods(ledger, qty="10", price="100")
    inv = _invoice(ledger, po_no, number="S1-45")

    on_45 = reconciliation.msme_due(ledger, TODAY + timedelta(days=45), 0)
    on_46 = reconciliation.msme_due(ledger, TODAY + timedelta(days=46), 0)

    assert [(r["invoice"], r["days_late"], r["days_left"], r["limit_days"]) for r in on_45] == [(inv.no, 0, 0, 45)]
    assert [(r["invoice"], r["days_late"]) for r in on_46] == [(inv.no, 1)]
    assert reconciliation.msme_due(ledger, TODAY + timedelta(days=44), 0) == []  # one day left, not within 0 days
    assert [r["days_left"] for r in reconciliation.msme_due(ledger, TODAY + timedelta(days=44), 1)] == [1]


def test_given_no_written_agreement_when_listed_then_15_days_apply_and_the_setting_changes_them(
    ledger: Session, outbox: Outbox
) -> None:  # noqa: F811
    _small(ledger)
    ledger.execute(text("update supplier set msme_agreement = false where code = 'S1'"))
    ledger.commit()
    po_no, _ = _goods(ledger, qty="10", price="100")
    inv = _invoice(ledger, po_no, number="S1-15")

    assert inv.status == "approved"
    assert ledger.execute(
        text("select due_date from invoice where no = :n"), {"n": inv.no}
    ).scalar_one() == TODAY + timedelta(days=15)
    (row,) = reconciliation.msme_due(ledger, TODAY + timedelta(days=16), 0)
    assert (row["deadline"], row["days_late"], row["agreement"]) == (TODAY + timedelta(days=15), 1, False)

    ledger.execute(text("insert into org_setting (key, value) values ('msme_no_agreement_days', '10')"))
    ledger.commit()
    assert payments.msme_limits(ledger) == pay_rules.MsmeLimits(45, 10)
    (row,) = reconciliation.msme_due(ledger, TODAY + timedelta(days=16), 0)
    assert (row["deadline"], row["days_late"]) == (TODAY + timedelta(days=10), 6)
    ledger.execute(text("update supplier set msme_category = 'medium' where code = 'S1'"))
    ledger.commit()
    assert reconciliation.msme_due(ledger, TODAY + timedelta(days=16), 0) == []  # medium enterprises are outside it


# ---- the API: who may see and do what, and one company cannot see another ---------------------------------


def test_the_reconciliation_routes_answer_by_role(ledger: Session, outbox: Outbox) -> None:  # noqa: F811
    slug, sfx = ledger.info["slug"], ledger.info["sfx"]
    finance, owner, ap = _people(ledger)
    requester = users.create_user(slug, f"req{sfx}@x.example", "pw-pw-pw-1", "A Requester", "Requester")
    po_no, _ = _goods(ledger, qty="10", price="100")
    h = {
        "ap": _headers(slug, ap, "AP"), "fin": _headers(slug, finance, "Finance"),
        "req": _headers(slug, requester, "Requester"),
    }  # fmt: skip

    grni = client.get("/api/reconciliation/received-not-invoiced", headers=h["ap"])
    assert grni.status_code == 200 and grni.json()["not_invoiced"] == "1000.00"
    assert client.get("/api/reconciliation/received-not-invoiced", headers=h["req"]).status_code == 403
    assert client.get("/api/reconciliation/received-not-invoiced").status_code == 401
    assert client.get("/api/reconciliation/msme?within=-1", headers=h["fin"]).status_code == 422
    assert client.get("/api/reconciliation/msme", headers=h["fin"]).json()["within"] == 7
    traced = client.get(f"/api/trace/{po_no}", headers=h["fin"]).json()
    assert traced["kind"] == "po"
    order = next(s for s in traced["stages"] if s["stage"] == "po")["items"][0]
    assert (D(order["to_receive"]), D(order["to_invoice"])) == (D(0), D(10)) and "received, not invoiced" in order[
        "detail"
    ]
    assert client.get("/api/trace/NOPE-9", headers=h["fin"]).status_code == 404
    assert client.get(f"/api/trace/{po_no}", headers=h["req"]).status_code == 403
    exported = client.get("/api/export/received-not-invoiced", headers=h["ap"])
    assert exported.status_code == 200 and po_no in exported.text and "1000.00" in exported.text

    upload = {"file": ("s.csv", _statement([f"{TODAY},invoice,X-1,10.00,"]), "text/csv")}
    assert (
        client.post("/api/reconciliation/vendor-statements?supplier=S1", files=upload, headers=h["fin"]).status_code
        == 403
    )
    made = client.post("/api/reconciliation/vendor-statements?supplier=S1", files=upload, headers=h["ap"])
    assert made.status_code == 200 and made.json()["results"][0]["outcome"] == "only_theirs"
    sid = made.json()["id"]
    assert client.get(f"/api/reconciliation/vendor-statements/{sid}", headers=h["fin"]).json()["only_theirs"] == 1
    assert (
        client.post("/api/reconciliation/vendor-statements?supplier=NOPE", files=upload, headers=h["ap"]).status_code
        == 404
    )
    assert "document_no" in client.get("/api/reconciliation/vendor-statements/template", headers=h["ap"]).text
    assert client.get("/api/reconciliation/bank-lines", headers=h["fin"]).json() == []
    assert (
        client.post("/api/reconciliation/bank-lines/1/confirm", json={"payment": "P"}, headers=h["ap"]).status_code
        == 403
    )
    assert (
        client.post("/api/reconciliation/bank-lines/999/confirm", json={"payment": "P"}, headers=h["fin"]).status_code
        == 404
    )

    other = "r" + uuid.uuid4().hex[:8]
    assert tenants.create_tenant(other, load_reference=False).ok
    try:
        their_ap = users.create_user(other, f"ap{sfx}@other.example", "pw-pw-pw-1", "Their AP", "AP")
        theirs = _headers(other, their_ap, "AP")
        assert client.get(f"/api/reconciliation/vendor-statements/{sid}", headers=theirs).status_code == 404
        assert client.get("/api/reconciliation/vendor-statements", headers=theirs).json() == []
        assert client.get("/api/reconciliation/received-not-invoiced", headers=theirs).json()["lines"] == []
        assert client.get(f"/api/trace/{po_no}", headers=theirs).status_code == 404
    finally:
        routing.clear_engine_cache()
