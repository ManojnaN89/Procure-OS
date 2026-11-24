"""GST rate and tax per line (block 11, 3.4): the order's rate and split against what the invoice charges.

Given an order at 5% within Maharashtra and an invoice line, when the line is judged, then the rate always counts and
each tax head must be within `tax_line_abs` (one rupee) of the order's split. The tax head itself (IGST against CGST
and SGST) is block 10's header check; until it lands those cases are marked.
"""

from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session
from test_invoices import _goods, ledger  # noqa: F401 - fixtures
from test_orders import shop  # noqa: F401 - fixtures
from test_receiving import store  # noqa: F401 - fixtures

from p2p.formulas import matching as m
from p2p.invoices import service as invoices

D = Decimal
TODAY = date(2026, 11, 6)
BUYER_MH = "27BBBBB0000B1Z5"


def test_judge_gst_compares_the_rate_and_each_head_with_the_tolerance_inclusive() -> None:
    expected = m.TaxParts(D("675.00"), D("675.00"), D("0.00"))

    assert (
        m.judge_gst(
            D("5"),
            D("5"),
            expected,
            m.TaxParts(D("676.00"), D("675.00"), D("0")),
            D("1.00"),
        )
        == []
    )
    assert m.judge_gst(
        D("5"),
        D("5"),
        expected,
        m.TaxParts(D("676.01"), D("675.00"), D("0")),
        D("1.00"),
    ) == [
        "CGST 676.01 against 675.00 expected (1.01 apart, more than 1.00)"
    ]  # B12: exactly one rupee is within, a paisa more is not
    assert m.judge_gst(D("5"), D("12"), expected, m.TaxParts(D("3"), D("0"), D("0")), D("1.00"))[0].startswith(
        "GST 12% on the invoice, 5% on the order"
    )


def test_a_reverse_charge_line_compares_the_rate_only() -> None:
    expected = m.TaxParts(D("675"), D("675"), D("0"))

    assert (
        m.judge_gst(
            D("5"),
            D("5"),
            expected,
            m.TaxParts(D("0"), D("0"), D("0")),
            D("1.00"),
            compare_amounts=False,
        )
        == []
    )
    assert m.judge_gst(
        D("5"),
        D("12"),
        expected,
        m.TaxParts(D("0"), D("0"), D("0")),
        D("1.00"),
        compare_amounts=False,
    ) == ["GST 12% on the invoice, 5% on the order"]


def _intra(ledger_: Session, po_no: str, rate: str = "5") -> None:
    """The order is a supply within Maharashtra, as block 8's M is."""
    ledger_.execute(
        text("update po set buyer_gstin = :b where no = :p"),
        {"b": BUYER_MH, "p": po_no},
    )
    ledger_.execute(
        text("update po_line set gst_rate = :r where po_no = :p"),
        {"r": D(rate), "p": po_no},
    )
    ledger_.commit()


def _enter(
    ledger_: Session,
    po_no: str,
    qty: str,
    price: str,
    *,
    number: str,
    rate: str,
    cgst: str,
    sgst: str,
    igst: str = "0",
    reverse_charge: bool = False,
) -> invoices.Entered:
    q = D(qty)
    taxable = (q * D(price)).quantize(D("0.01"))
    tax = D(cgst) + D(sgst) + D(igst)
    data = invoices.InvoiceIn(
        supplier_code="S1", supplier_invoice_no=number, invoice_date=TODAY, supplier_gstin="27AAAAA0000A1Z5",
        buyer_gstin=BUYER_MH, reverse_charge=reverse_charge, taxable=taxable, cgst=D(cgst), sgst=D(sgst), igst=D(igst),
        total=(taxable + tax).quantize(D("1")),
        lines=[
            invoices.InvLine(
                po_no, 1, q, D(price), taxable=taxable, gst_rate=D(rate), cgst=D(cgst), sgst=D(sgst), igst=D(igst)
            )
        ],
    )  # fmt: skip
    return invoices.enter_invoice(ledger_, data, 1, as_of=TODAY)


def test_a_wrong_rate_blocks_even_when_its_amounts_add_up(ledger: Session) -> None:  # noqa: F811
    po_no, _ = _goods(ledger, qty="100", price="270")
    _intra(ledger, po_no)

    wrong = _enter(ledger, po_no, "100", "270", number="D1", rate="12", cgst="1620", sgst="1620")

    assert (wrong.status, wrong.reasons) == ("blocked", ["gst_mismatch"])
    row = ledger.execute(
        text("select payment_block, block_reason from invoice where no = :n"),
        {"n": wrong.no},
    ).one()
    assert row[0] == "tax" and "line 1: GST 12% on the invoice, 5% on the order" in row[1]
    assert ledger.execute(
        text("select reasons, rules from match_result where invoice_no = :n"),
        {"n": wrong.no},
    ).one() == (
        ["gst_mismatch"],
        2,
    )


def test_each_tax_head_within_one_rupee_passes_and_a_paisa_more_does_not(
    ledger: Session,
) -> None:  # noqa: F811
    po_no, logins = _goods(ledger, qty="100", price="270")
    _intra(ledger, po_no)
    po_two, _ = _goods(ledger, qty="101", price="270", logins=logins)
    _intra(ledger, po_two)

    exact = _enter(ledger, po_no, "100", "270", number="D3", rate="5", cgst="676.00", sgst="676.00")
    over = _enter(
        ledger,
        po_two,
        "100",
        "270",
        number="D2",
        rate="5",
        cgst="676.01",
        sgst="675.00",
    )

    assert (exact.status, exact.reasons) == ("approved", [])
    assert (over.status, over.reasons) == ("blocked", ["gst_mismatch"])
    assert (
        "CGST 676.01 against 675.00 expected (1.01 apart, more than 1.00)"
        in ledger.execute(text("select block_reason from invoice where no = :n"), {"n": over.no}).scalar_one()
    )


def test_a_reverse_charge_line_checks_the_rate_and_not_the_amounts(
    ledger: Session,
) -> None:  # noqa: F811
    po_no, logins = _goods(ledger, qty="100", price="270")
    _intra(ledger, po_no)
    po_two, _ = _goods(ledger, qty="101", price="270", logins=logins)
    _intra(ledger, po_two)

    good = _enter(
        ledger,
        po_no,
        "100",
        "270",
        number="RC-1",
        rate="5",
        cgst="0",
        sgst="0",
        reverse_charge=True,
    )
    wrong = _enter(
        ledger,
        po_two,
        "100",
        "270",
        number="RC-2",
        rate="12",
        cgst="0",
        sgst="0",
        reverse_charge=True,
    )

    assert good.status == "approved"
    assert (wrong.status, wrong.reasons) == ("blocked", ["gst_mismatch"])


def test_a_composition_supplier_stays_the_header_tax_rule_without_a_line_reason(
    ledger: Session,
) -> None:  # noqa: F811
    po_no, _ = _goods(ledger, qty="10", price="100")
    ledger.execute(text("update supplier set gst_reg_type = 'composition' where code = 'S1'"))
    ledger.commit()

    done = _enter(ledger, po_no, "10", "100", number="C-1", rate="5", cgst="25", sgst="25")

    assert done.reasons == ["tax_not_allowed"] and done.status == "blocked"


@pytest.mark.xfail(
    reason="NEEDS block 10: header_problem's tax_head check (IGST against CGST plus SGST) lands with block 10"
)
def test_igst_on_an_intra_state_supply_is_the_headers_tax_head_and_not_a_line_amount(
    ledger: Session,
) -> None:  # noqa: F811
    po_no, _ = _goods(ledger, qty="100", price="270")
    _intra(ledger, po_no)

    done = _enter(
        ledger,
        po_no,
        "100",
        "270",
        number="D4",
        rate="5",
        cgst="0",
        sgst="0",
        igst="1350",
    )

    assert done.reasons == [m.TAX_HEAD] and done.status == "blocked"
    assert ledger.execute(text("select payment_block from invoice where no = :n"), {"n": done.no}).scalar_one() == "tax"


from test_sourcing import Outbox, db, outbox  # noqa: F401, E402 - fixtures
