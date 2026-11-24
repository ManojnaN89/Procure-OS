"""Price measures (block 13 R6, 13.3, PV1-PV3): PPV at the three recognition points, contract variance, savings, the
standard version in force, and a second run adding nothing."""

from datetime import date
from decimal import Decimal

from sqlalchemy import text
from sqlalchemy.orm import Session
from test_invoices import _goods, _invoice, ledger  # noqa: F401 - fixtures
from test_orders import shop  # noqa: F401 - fixture
from test_receiving import store  # noqa: F401 - fixture
from test_sourcing import db  # noqa: F401 - fixtures

from p2p.formulas import ppv
from p2p.invoices import price_measures

D = Decimal


def test_ppv_contract_variance_and_saving_are_unit_price_differences_times_quantity_to_the_paisa() -> None:
    assert ppv.ppv(D("120"), D("222.20"), D("220.00")) == D("264.00")  # PV1 line 2
    assert ppv.ppv(D("120"), D("905.00"), D("950.00")) == D("-5400.00")  # PV2 against version 2
    assert ppv.contract_variance(D("1"), D("222.20"), D("220.00")) == D("2.20")
    assert ppv.saving(D("120"), D("905.00"), D("920.00")) == D("1800.00")
    assert ppv.true_up(D("2"), D("10.005"), D("10.000")) == D("0.01")  # half up


def test_given_a_standard_and_an_invoice_at_a_higher_price_when_recorded_then_ppv_is_unfavourable(
    ledger: Session,
) -> None:  # noqa: F811
    po_no, _ = _goods(ledger, qty="10", price="100")
    ledger.execute(text("insert into item_standard_price (item_code, price, version, effective_from, status, created_by) "
                        "values ('A', 100, 1, '2000-01-01', 'active', 'test')"))  # fmt: skip
    _invoice(ledger, po_no, "10", "110")
    ledger.commit()

    done = price_measures.record(ledger, date(2026, 12, 31))
    again = price_measures.record(ledger, date(2026, 12, 31))

    assert done["added"]["ppv"] == 1
    row = ledger.execute(text("select amount, unit_price, reference_price, reference from price_measure "
                              "where measure = 'ppv'")).one()  # fmt: skip
    assert (D(row[0]), D(row[1]), D(row[2])) == (D("100.00"), D("110.0000"), D("100.0000"))
    assert row[3] == "standard v1"
    assert again["added"]["ppv"] == 0


def test_given_ppv_recognition_receipt_when_recorded_then_the_po_price_is_used_at_acceptance(
    ledger: Session,
) -> None:  # noqa: F811
    _goods(ledger, qty="10", price="100")
    ledger.execute(text("insert into item_standard_price (item_code, price, version, effective_from, status, created_by) "
                        "values ('A', 90, 1, '2000-01-01', 'active', 'test')"))  # fmt: skip
    ledger.execute(text("insert into org_setting (key, value) values ('ppv_recognition', 'receipt') "
                        "on conflict (key) do update set value = 'receipt'"))  # fmt: skip
    ledger.commit()

    done = price_measures.record(ledger, date(2026, 12, 31))

    assert done["added"]["ppv"] == 1
    row = ledger.execute(text("select part, amount from price_measure where measure = 'ppv'")).one()
    assert (row[0], D(row[1])) == ("receipt", D("100.00"))  # 10 x (100 - 90)


def test_given_a_credit_note_when_recorded_then_it_reverses_its_share_with_a_negative_row(ledger: Session) -> None:
    po_no, _ = _goods(ledger, qty="10", price="100")
    ledger.execute(text("insert into item_standard_price (item_code, price, version, effective_from, status, created_by) "
                        "values ('A', 100, 1, '2000-01-01', 'active', 'test')"))  # fmt: skip
    _invoice(ledger, po_no, "10", "110")
    ledger.execute(
        text(
            "insert into invoice (no, type, supplier_code, supplier_invoice_no, dedupe_key, invoice_date, fy, received_at, "
            "received_on, channel, taxable, cgst, sgst, igst, cess, round_off, total, payment_block, status, tds_amount, "
            "advance_used, advance_taxable_used, discount_taken, reverse_charge) values "
            "('CN-1', 'credit_note', 'S1', 'CN-1', 'CN-1', '2026-11-06', '2026-27', now(), '2026-11-06', 'manual', "
            "-550, 0, 0, -99, 0, 0, -649, 'none', 'approved', 0, 0, 0, 0, false)"
        )
    )
    ledger.execute(
        text("insert into invoice_line (invoice_no, line_no, po_no, po_line_no, qty, unit_price, discount, taxable, "
             "gst_rate, cgst, sgst, igst) values ('CN-1', 1, :p, 1, -5, 110, 0, -550, 18, 0, 0, -99)"), {"p": po_no})  # fmt: skip
    ledger.commit()

    price_measures.record(ledger, date(2026, 12, 31))

    amount = ledger.execute(text("select amount from price_measure where measure = 'ppv' and part = 'invoice' "
                                 "and source_ref = 'CN-1'")).scalar_one()  # fmt: skip
    assert D(amount) == D("-50.00")  # -5 x (110 - 100)


def test_given_no_standard_or_a_quantity_in_another_unit_then_no_row_is_kept(ledger: Session) -> None:
    po_no, _ = _goods(ledger, qty="10", price="100")
    _invoice(ledger, po_no, "10", "100")
    ledger.execute(text("update item set base_uom = 'KGS' where code = 'A'"))
    ledger.commit()

    done = price_measures.record(ledger, date(2026, 12, 31))

    assert done["added"]["ppv"] == 0
    assert ledger.execute(text("select count(*) from price_measure where measure = 'ppv'")).scalar_one() == 0


def test_the_standard_version_in_force_follows_its_dates(ledger: Session) -> None:  # noqa: F811
    from p2p.masters import standard_prices

    ledger.execute(text("insert into item_standard_price (item_code, price, version, effective_from, effective_to, status) "
                        "values ('A', 100, 1, '2000-01-01', '2026-11-30', 'active'), "
                        "('A', 110, 2, '2026-12-01', null, 'active')"))  # fmt: skip
    ledger.commit()

    assert standard_prices.in_force(ledger, "A", date(2026, 11, 30)) == D("100")
    assert standard_prices.in_force(ledger, "A", date(2026, 12, 1)) == D("110")
