"""GSTR-2B (block 13 R4, D-19, G1): the parser, the compare boundaries and one import that changes nothing."""

import json
from datetime import date
from decimal import Decimal

from sqlalchemy import text
from sqlalchemy.orm import Session
from test_invoices import ledger  # noqa: F401 - fixture
from test_orders import shop  # noqa: F401 - fixture
from test_receiving import store  # noqa: F401 - fixture
from test_sourcing import db  # noqa: F401 - fixture

from p2p.formulas import gstr2b as rules
from p2p.invoices import gstr2b

D = Decimal
OURS = "29BBBBB0000B1Z5"


def doc(number: str = "SWAE/2526/0019", *, gstin: str = "27AAAAA0000A1Z5", taxable: str = "1000.00",
        cgst: str = "90.00", sgst: str = "90.00", igst: str = "0.00", on: date = date(2026, 10, 5)) -> rules.Doc:  # fmt: skip
    return rules.Doc(gstin, "invoice", number, on, D(taxable), D(igst), D(cgst), D(sgst))


def test_given_numbers_written_differently_when_keyed_then_they_are_the_same_document() -> None:
    a, b = doc("SWAE/2526/0019"), doc("swae-2526-0019")
    c = doc("SWAE/2526/0019", on=date(2027, 4, 5))  # another financial year

    assert a.key == b.key
    assert a.key != c.key


def test_given_each_tax_head_one_rupee_apart_when_compared_then_it_matches_and_one_paisa_more_differs() -> None:
    exact = rules.compare([doc()], [doc(cgst="91.00")], tolerance=D("1.00"), period="2026-10")
    over = rules.compare([doc()], [doc(cgst="91.01")], tolerance=D("1.00"), period="2026-10")

    assert exact[0].outcome == "matched"
    assert over[0].outcome == "differs"
    assert over[0].differences == ("cgst: ours ₹ 90.00, 2B ₹ 91.01 (₹ 1.01 more)",)


def test_given_a_document_only_in_2b_or_only_in_our_books_then_each_is_reported_on_its_side() -> None:
    got = rules.compare([doc("OURS/1")], [doc("THEIRS/1")], tolerance=D("1.00"), period="2026-10")

    assert [(r.outcome, r.doc.doc_no) for r in got] == [("only_2b", "THEIRS/1"), ("only_books", "OURS/1")]
    assert got[1].reason == "not in any 2B for 0 months"


def test_given_the_same_pan_under_another_state_gstin_then_it_is_reported_apart() -> None:
    theirs = doc(gstin="24AAAAA0000A1Z8")

    got = rules.compare([doc()], [theirs], tolerance=D("1.00"), period="2026-10")

    assert got[0].outcome == "gstin_differs"


def test_given_the_same_document_twice_in_the_file_when_compared_then_the_second_is_a_duplicate() -> None:
    got = rules.compare([doc()], [doc(), doc()], tolerance=D("1.00"), period="2026-10")

    assert [r.outcome for r in got] == ["matched", "duplicate"]


def test_given_credit_not_available_when_compared_then_the_reason_is_flagged_without_changing_the_outcome() -> None:
    theirs = rules.Doc("27AAAAA0000A1Z5", "invoice", "SWAE/2526/0019", date(2026, 10, 5), D("1000.00"),
                       D(0), D("90.00"), D("90.00"), D(0), itc_available=False, itc_reason="P")  # fmt: skip

    got = rules.compare([doc()], [theirs], tolerance=D("1.00"), period="2026-10")

    assert got[0].outcome == "matched" and got[0].reason == "credit not available: P"


def test_json_numbers_are_read_as_exact_decimals_and_an_amendment_uses_its_new_number() -> None:
    body = {
        "data": {
            "gstin": OURS,
            "rtnprd": "102026",
            "gendt": "14-11-2026",
            "docdata": {
                "b2b": [
                    {
                        "ctin": "27AAAAA0000A1Z5",
                        "inv": [
                            {
                                "inum": "A/1",
                                "dt": "05-10-2026",
                                "txval": 0.1 + 0.2,
                                "cgst": 0.3,
                                "sgst": 0.3,
                                "igst": 0.0,
                                "cess": 0.0,
                                "itcavl": "Y",
                            }
                        ],
                    }
                ],  # fmt: skip
                "b2ba": [
                    {
                        "ctin": "27AAAAA0000A1Z5",
                        "inv": [
                            {
                                "oinum": "A/0",
                                "oidt": "01-10-2026",
                                "inum": "A/2",
                                "dt": "06-10-2026",
                                "txval": "10.00",
                                "cgst": "0.90",
                                "sgst": "0.90",
                                "igst": "0",
                                "cess": "0",
                            }
                        ],
                    }
                ],  # fmt: skip
            },
        }
    }

    parsed = gstr2b.parse_json(json.dumps(body).encode())

    assert parsed.gstin == OURS and parsed.period == "2026-10" and parsed.generated_on == date(2026, 11, 14)
    assert [d.doc_no for d in parsed.docs] == ["A/1", "A/2"]
    # the file's exact digits, no second float rounding
    assert parsed.docs[0].taxable == D("0.30000000000000004")


def test_csv_template_is_read_with_its_columns() -> None:
    data = (
        b"supplier_gstin,type,doc_no,doc_date,taxable,igst,cgst,sgst,cess,reverse_charge,itc_available,reason\n"
        b"27AAAAA0000A1Z5,invoice,C/1,05-10-2026,1000.00,0,90,90,0,false,Y,\n"
    )

    parsed = gstr2b.parse_csv(data)

    assert parsed.docs[0].key[2] == "C1" and parsed.period == "2026-10"


def test_given_a_matching_2b_file_when_imported_then_nothing_about_the_invoice_changes_and_twice_adds_nothing(
    ledger: Session,
) -> None:  # noqa: F811
    ledger.execute(text("insert into gst_registration (gstin, state_code, is_default) values (:g, '29', true)"),
                  {"g": OURS})  # fmt: skip
    ledger.execute(text("insert into tolerance_version (version, effective_from, note) values (1, '2000-01-01', 'x') "
                        "on conflict (version) do nothing"))  # fmt: skip
    ledger.execute(text("insert into tolerance (key, value, category_code, version) "
                        "values ('gstr2b_diff_abs', '1.00', 'ANY', 1) on conflict do nothing"))  # fmt: skip
    ledger.execute(
        text(
            "insert into invoice (no, type, supplier_code, supplier_gstin, buyer_gstin, supplier_invoice_no, dedupe_key, "
            "invoice_date, fy, received_at, received_on, channel, taxable, cgst, sgst, igst, cess, round_off, total, "
            "payment_block, status, tds_amount, advance_used, advance_taxable_used, discount_taken, reverse_charge) "
            "values ('PI-1', 'invoice', 'S1', '27AAAAA0000A1Z5', :g, 'G/1', 'G/1', '2026-10-05', '2026-27', now(), "
            "'2026-10-05', 'manual', 1000, 90, 90, 0, 0, 0, 1180, 'none', 'approved', 0, 0, 0, 0, false)"
        ),
        {"g": OURS},
    )
    ledger.commit()
    body = {"data": {"gstin": OURS, "rtnprd": "102026", "gendt": "14-11-2026", "docdata": {"b2b": [{"ctin": "27AAAAA0000A1Z5",
            "inv": [{"inum": "G/1", "dt": "05-10-2026", "txval": "1000.00", "cgst": "90.00", "sgst": "90.00",
                     "igst": "0", "cess": "0", "itcavl": "Y"}]}]}}}  # fmt: skip
    data = json.dumps(body).encode()

    first = gstr2b.import_file(ledger, data, "g.json", 1, tenant=ledger.info["slug"])
    second = gstr2b.import_file(ledger, data, "g.json", 1, tenant=ledger.info["slug"])

    assert first["outcomes"]["matched"] == 1 and first["already_imported"] is False
    assert second["already_imported"] is True
    assert ledger.execute(text("select status, total from invoice where no = 'PI-1'")).one() == (
        "approved",
        D("1180.00"),
    )
    assert ledger.execute(text("select count(*) from gstr2b_import")).scalar_one() == 1
    assert ledger.execute(text("select count(*) from task where kind = 'gstr2b_differences'")).scalar_one() == 0
