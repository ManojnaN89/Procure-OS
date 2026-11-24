"""Supplier invoices: taking them in, the three-way match, duplicates, credit notes, blocks and who releases them."""

from datetime import date
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session
from test_orders import _headers, shop  # noqa: F401 - fixtures
from test_receiving import _receive, _sent, store  # noqa: F401 - fixtures
from test_sourcing import PASSWORD, Outbox, db, outbox  # noqa: F401 - fixtures

from p2p.formulas import matching as m
from p2p.invoices import service as invoices
from p2p.main import app
from p2p.platform import permissions, proposals, users

D = Decimal
client = TestClient(app)
TODAY = date(2026, 11, 6)
S1_GSTIN, BUYER_GSTIN = "27AAAAA0000A1Z5", "29BBBBB0000B1Z5"


# ---- the rules --------------------------------------------------------------------------------

TOL = m.Tolerance(price_pct=D("2"), price_abs=D("500"), qty_pct=D("0"))


def test_the_price_passes_only_when_both_the_percentage_and_the_rupee_limit_hold() -> None:
    assert m.judge_line(D("1000"), D("1020"), D("10"), D("10"), TOL).result == m.WITHIN  # 2.00% and 200.00 exactly
    assert (
        m.judge_line(D("1000"), D("1020.01"), D("10"), D("10"), TOL).reason == m.PRICE
    )  # one paisa over 2%: the rupee test alone no longer lets it through (B2)
    assert (
        m.judge_line(D("1000"), D("1050"), D("10"), D("10"), TOL).reason == m.PRICE
    )  # exactly 500 on the line, but 5% is over the percentage: both must hold (B1, DF-12)
    assert m.judge_line(D("1000"), D("1040"), D("10"), D("10"), TOL).reason == m.PRICE  # 4% and 400: one test fails
    assert (
        m.judge_line(D("480"), D("483.01"), D("100"), D("100"), m.Tolerance(D("2"), D("300"), D("0"))).reason == m.PRICE
    )  # 3.01 within 2% (9.60), but 301.00 is over the 300 rupee line limit (M's A)
    assert m.judge_line(D("480"), D("483.00"), D("100"), D("100"), TOL).result == m.WITHIN  # exactly 300 (B1)
    cheap = m.judge_line(D("1000"), D("990"), D("10"), D("10"), TOL)
    assert (cheap.result, cheap.price_var, cheap.variance_value) == (m.MATCHED, D("-10"), D("0.00"))  # B3


def test_the_variance_is_half_up_to_paise_and_the_limits_use_the_unrounded_product() -> None:
    tol = m.Tolerance(D("2"), D("300"), D("0"))
    verdict = m.judge_line(D("480"), D("482.995"), D("100"), D("100"), tol)
    assert (verdict.result, verdict.variance_value) == (m.WITHIN, D("299.50"))  # H: 299.50 <= 300
    line = m.judge_line(D("100"), D("100"), D("1000.0005"), D("1000"), m.Tolerance(D("0"), D("0"), D("0")))
    assert line.reason == m.QTY  # B5: a ten-thousandth over accepted is blocked at 0%


def test_a_line_keeps_every_reason_it_has() -> None:
    verdict = m.judge_line(D("100"), D("140"), D("10"), D("6"), m.Tolerance(D("2"), D("50"), D("0")))

    assert verdict.result == m.BLOCKED
    assert verdict.reasons == (m.PRICE, m.QTY) and verdict.reason == m.PRICE  # B11
    assert verdict.variance_value == D("400.00")



def test_the_quantity_may_not_exceed_what_was_accepted_plus_the_quantity_tolerance() -> None:
    assert m.judge_line(D("10"), D("10"), D("5"), D("5"), TOL).result == m.MATCHED
    assert m.judge_line(D("10"), D("10"), D("5.0001"), D("5"), TOL).reason == m.QTY
    wide = m.Tolerance(D("2"), D("500"), D("1"))  # bulk goods: 1% weighbridge variance
    assert m.judge_line(D("10"), D("10"), D("5.05"), D("5"), wide).result == m.MATCHED
    assert m.judge_line(D("10"), D("10"), D("5.0501"), D("5"), wide).reason == m.QTY


def test_the_first_line_that_blocks_decides_and_one_line_within_tolerance_makes_it_so() -> None:
    ok, within = m.LineVerdict(m.MATCHED), m.LineVerdict(m.WITHIN)
    qty, price = m.LineVerdict(m.BLOCKED, m.QTY), m.LineVerdict(m.BLOCKED, m.PRICE)
    assert m.overall([ok, ok]) == (m.MATCHED, None)
    assert m.overall([ok, within]) == (m.WITHIN, None)
    assert m.overall([within, qty, price]) == (m.BLOCKED, m.QTY)


def test_a_category_tolerance_overrides_the_general_one_key_by_key() -> None:
    rows = [
        {"key": "price_pct", "value": "2", "category_code": "ANY"},
        {"key": "price_abs", "value": "500", "category_code": "ANY"},
        {"key": "qty_pct", "value": "0", "category_code": "ANY"},
        {"key": "qty_pct", "value": "1", "category_code": "RAW"},
        {"key": "price_pct", "value": "1", "category_code": "RAW"},
    ]
    assert m.tolerance_for(rows, "RAW") == m.Tolerance(D("1"), D("500"), D("1"))
    assert m.tolerance_for(rows, "MRO") == m.Tolerance(D("2"), D("500"), D("0"))
    assert m.tolerance_for(rows, None) == m.Tolerance(D("2"), D("500"), D("0"))


def test_an_invoice_number_is_compared_without_case_spaces_or_punctuation() -> None:
    assert m.normalize_invoice_no("PIS/2425/0036") == m.normalize_invoice_no(" pis 2425 0036 ") == "PIS24250036"
    assert m.normalize_invoice_no("A-1") != m.normalize_invoice_no("A-2")


def _header(**kw: object) -> m.Header:
    base = {
        "supplier_gst_type": "regular", "e_invoice_applicable": False, "invoice_type": "invoice", "irn": None,
        "tax_charged": D("18"), "invoice_supplier_gstin": "S", "valid_supplier_gstins": frozenset({"S"}),
        "invoice_buyer_gstin": "B", "order_buyer_gstin": "B",
    }  # fmt: skip
    return m.Header(**{**base, **kw})  # type: ignore[arg-type]


def test_a_problem_with_the_invoice_as_a_whole_is_found_before_the_lines() -> None:
    assert m.header_problem(_header()) is None
    assert m.header_problem(_header(supplier_gst_type="composition")) == m.TAX
    assert m.header_problem(_header(supplier_gst_type="composition", tax_charged=D(0))) is None
    assert m.header_problem(_header(supplier_gst_type="unregistered")) == m.TAX
    assert m.header_problem(_header(e_invoice_applicable=True)) == m.IRN
    assert m.header_problem(_header(e_invoice_applicable=True, irn="X" * 64)) is None
    assert m.header_problem(_header(e_invoice_applicable=True, invoice_type="credit_note")) is None
    assert m.header_problem(_header(invoice_buyer_gstin="OTHER")) == m.GSTIN
    assert m.header_problem(_header(invoice_supplier_gstin="OTHER")) == m.GSTIN
    assert m.header_problem(_header(valid_supplier_gstins=frozenset({"S", "S2"}), invoice_supplier_gstin="S2")) is None


def test_the_invoice_must_add_up_within_a_few_paise_on_a_line_and_a_rupee_in_total() -> None:
    line = m.LineSums(D("3"), D("33.33"), D("0"), D("99.99"))
    assert m.check_arithmetic([line], [], D("99.99"), D("18.00"), D("0.01"), D("118.00")).problems == []
    assert m.check_arithmetic(
        [m.LineSums(D("3"), D("33.33"), D("0"), D("100.05"))], [], D("100.05"), D("0"), D("0"), D("100")
    ).problems
    assert m.check_arithmetic(
        [line], [], D("99.99"), D("18.00"), D("0"), D("120.00")
    ).problems  # total off by more than a rupee
    assert (
        m.check_arithmetic([line], [D("10")], D("109.99"), D("0"), D("0"), D("110")).problems == []
    )  # a charge is part of it


# ---- entering invoices ------------------------------------------------------------------------


@pytest.fixture
def ledger(store: Session) -> Session:  # noqa: F811
    """The receiving world with what invoices, payments and bank files need."""
    x = store.execute
    x(text("update payment_terms set baseline = 'invoice_date'"))
    x(
        text(
            "insert into number_series (document_type, prefix, scope, format, reset) values "
            "('invoice_booking', 'PI', 'company', '{prefix}-{fy4}-{seq:06d}', 'fy'), "
            "('payment_run', 'PR', 'company', '{prefix}-{fy4}-{seq:04d}', 'fy'), "
            "('payment', 'PAY', 'company', '{prefix}-{fy4}-{seq:06d}', 'fy')"
        )
    )
    x(
        text(
            "insert into company_bank_account (code, bank_code, account_no, ifsc, purpose) values ('CBA-01', 'HDFC', '1234', 'HDFC0000001', 'payments')"
        )
    )
    x(
        text(
            "insert into tolerance (key, value, category_code) values ('price_pct', '2', 'ANY'), ('price_abs', '500', 'ANY'), ('qty_pct', '0', 'ANY')"
        )
    )
    x(
        text(
            "insert into approval_rule (rule_code, document, min_value, max_value, category_code, department_code, "
            "spend_type, approver_chain, version, effective_from) values ('VAR-01', 'price_variance', 0, 999999999, "
            "'ANY', 'ANY', 'ANY', 'procurement_head', 1, '2024-11-01')"
        )
    )
    for code in ("S1", "S2", "S3"):
        x(
            text(
                "insert into supplier_bank_account (supplier_code, bank_code, account_no, ifsc, verified, verified_on) "
                "values (:c, 'HDFC', :a, 'HDFC0000002', true, '2026-01-01')"
            ),
            {"c": code, "a": f"99{code}"},
        )
    store.commit()
    return store


def _goods(
    shop_: Session, qty: str = "10", price: str = "100", *, received: str | None = None, **kw: object
) -> tuple[str, dict[str, int]]:
    """A sent order for S1 with goods received."""
    po_no, logins = _sent(shop_, qty=qty, price=price, **kw)  # type: ignore[arg-type]
    _receive(shop_, po_no, received or qty)
    return po_no, logins


def _line(po_no: str, qty: str, price: str, *, line: int = 1) -> invoices.InvLine:
    return invoices.InvLine(po_no, line, D(qty), D(price))


def _invoice(
    shop_: Session,
    po_no: str,
    qty: str = "10",
    price: str = "100",
    *,
    number: str = "INV-1",
    user: int = 1,
    on: date = TODAY,
    **kw: object,
) -> invoices.Entered:
    data = invoices.InvoiceIn(
        supplier_code="S1",
        supplier_invoice_no=number,
        invoice_date=on,
        lines=[_line(po_no, qty, price)],
        supplier_gstin=S1_GSTIN,
        buyer_gstin=BUYER_GSTIN,
        **kw,
    )  # type: ignore[arg-type]
    return invoices.enter_invoice(shop_, data, user, as_of=on)


def _row(shop_: Session, no: str) -> dict:
    shop_.expire_all()
    return dict(shop_.execute(text("select * from invoice where no = :n"), {"n": no}).mappings().one())


def test_an_invoice_that_agrees_with_the_order_and_the_receipt_is_approved_and_booked(
    ledger: Session, outbox: Outbox
) -> None:  # noqa: F811
    po_no, _ = _goods(ledger)

    done = _invoice(ledger, po_no)

    assert (done.status, done.result, done.reasons) == ("approved", "matched", [])
    assert done.no == "PI-2627-000001"
    row = _row(ledger, done.no)
    assert (row["taxable"], row["igst"], row["cgst"], row["total"]) == (
        D("1000.00"),
        D("180.00"),
        D("0.00"),
        D("1180.00"),
    )
    assert (row["payment_block"], row["fy"], row["dedupe_key"]) == ("none", "2026-27", "INV1")
    assert row["due_date"] == date(2026, 12, 6) and row["booked_at"] is not None  # net 30 from the invoice date
    assert ledger.execute(text("select invoiced_qty from po_line")).scalar_one() == D("10")
    assert ledger.execute(text("select amount, base from tds_entry")).one() == (D("0.00"), D("0.00"))
    results = ledger.execute(text("select result, reason_codes from match_result")).all()
    assert results == [("matched", None)]
    assert (
        ledger.execute(
            text("select count(*) from audit_event where action in ('invoice.enter', 'invoice.match')")
        ).scalar_one()
        == 2
    )


def test_a_price_a_little_over_is_within_tolerance_and_paid(ledger: Session, outbox: Outbox) -> None:  # noqa: F811
    po_no, _ = _goods(ledger, qty="10", price="100")

    within = _invoice(ledger, po_no, "5", "102", number="A")  # 2% over on 5 of the 10: both limits hold
    over = _invoice(ledger, po_no, "5", "104", number="B")  # 4% over: 20 on the line, but the percentage fails

    assert (within.status, within.result) == ("approved", "within_tolerance")
    assert (over.status, over.result, over.reasons) == ("blocked", "blocked", ["price_variance"])



def test_a_price_over_both_limits_blocks_with_the_reason_and_the_kind_of_block(ledger: Session, outbox: Outbox) -> None:  # noqa: F811
    po_no, _ = _goods(ledger, qty="10", price="1000")

    done = _invoice(ledger, po_no, "10", "1060")  # 60 a unit is 6%, and 600 on the line

    assert (done.status, done.result, done.reasons) == ("blocked", "blocked", ["price_variance"])
    row = _row(ledger, done.no)
    assert (row["payment_block"], row["status"]) == ("match", "blocked") and "Price above the order price" in row[
        "block_reason"
    ]
    assert row["booked_at"] is None and row["due_date"] is None  # nothing is booked until it is approved
    assert ledger.execute(text("select invoiced_qty from po_line")).scalar_one() == D(
        "0"
    )  # a blocked invoice uses no quantity


def test_an_invoice_for_more_than_was_received_is_blocked_until_the_rest_arrives(
    ledger: Session, outbox: Outbox
) -> None:  # noqa: F811
    po_no, _ = _goods(ledger, qty="10", received="6")

    done = _invoice(ledger, po_no, "10", "100")
    assert (done.status, done.reasons) == ("blocked", ["qty_over_received"])
    assert "more than was received" in _row(ledger, done.no)["block_reason"]

    posted = _receive(ledger, po_no, "4")  # the remainder arrives: the waiting invoice is matched again by itself

    assert posted.rematched == [done.no]
    assert _row(ledger, done.no)["status"] == "approved"
    with pytest.raises(invoices.InvoiceError, match="only a blocked invoice"):
        invoices.rematch_invoice(ledger, done.no, 1)  # an approved one is not matched again


def test_what_other_invoices_already_took_is_not_available_again(ledger: Session, outbox: Outbox) -> None:  # noqa: F811
    po_no, _ = _goods(ledger, qty="10")

    first = _invoice(ledger, po_no, "6", "100", number="A")
    second = _invoice(ledger, po_no, "6", "100", number="B")  # 12 against 10 received

    assert first.status == "approved" and (second.status, second.reasons) == ("blocked", ["qty_over_received"])


@pytest.mark.parametrize(
    ("again", "expected_duplicate"),
    [("INV-1", True), ("inv-1", True), (" INV 1 ", True), ("INV/1", True), ("INV-2", False)],
)
def test_the_same_invoice_number_again_is_a_copy_whatever_its_formatting(
    ledger: Session,
    outbox: Outbox,
    again: str,
    expected_duplicate: bool,  # noqa: F811
) -> None:
    po_no, _ = _goods(ledger, qty="10")
    original = _invoice(ledger, po_no, "4", "100", number="INV-1")

    second = _invoice(ledger, po_no, "4", "100", number=again)

    if expected_duplicate:
        assert (second.status, second.duplicate_of, second.reasons) == ("cancelled", original.no, ["duplicate"])
        row = _row(ledger, second.no)
        assert row["payment_block"] == "duplicate" and "already on file" in row["block_reason"]
        assert ledger.execute(text("select invoiced_qty from po_line")).scalar_one() == D(
            "4"
        )  # a copy takes no quantity
        assert (
            ledger.execute(text("select count(*) from audit_event where action = 'invoice.duplicate'")).scalar_one()
            == 1
        )
    else:
        assert second.duplicate_of is None and second.status == "approved"


def test_the_same_number_in_another_financial_year_or_from_another_supplier_is_not_a_copy(
    ledger: Session, outbox: Outbox
) -> None:  # noqa: F811
    po_no, _ = _goods(ledger, qty="10")
    _invoice(ledger, po_no, "2", "100", number="INV-1", on=date(2026, 11, 6))

    next_year = _invoice(ledger, po_no, "2", "100", number="INV-1", on=date(2027, 4, 2))  # the new financial year

    assert next_year.duplicate_of is None and _row(ledger, next_year.no)["fy"] == "2027-28"


def test_a_cancelled_original_does_not_make_its_replacement_a_copy(ledger: Session, outbox: Outbox) -> None:  # noqa: F811
    po_no, _ = _goods(ledger, qty="10", price="1000")
    bad = _invoice(ledger, po_no, "10", "1060", number="INV-1")  # blocked
    invoices.cancel_invoice(ledger, bad.no, "Supplier will send a corrected invoice", 1)

    corrected = _invoice(ledger, po_no, "10", "1000", number="INV-1")

    assert corrected.duplicate_of is None and corrected.status == "approved"


def test_a_credit_note_is_taken_in_against_its_invoice_and_needs_no_match(ledger: Session, outbox: Outbox) -> None:  # noqa: F811
    po_no, _ = _goods(ledger, qty="10")
    original = _invoice(ledger, po_no, "10", "100")

    credit = invoices.enter_invoice(
        ledger,
        invoices.InvoiceIn(
            supplier_code="S1", supplier_invoice_no="CN-1", invoice_date=TODAY, type="credit_note", ref_invoice_no=original.no,
            lines=[_line(po_no, "1", "100")], supplier_gstin=S1_GSTIN, buyer_gstin=BUYER_GSTIN,
        ),
        1, as_of=TODAY,
    )  # fmt: skip

    assert credit.status == "approved" and _row(ledger, credit.no)["type"] == "credit_note"
    assert ledger.execute(text("select invoiced_qty from po_line")).scalar_one() == D(
        "10"
    )  # a credit note changes no quantity
    with pytest.raises(invoices.InvoiceError, match="is not an invoice of S1"):
        invoices.enter_invoice(
            ledger,
            invoices.InvoiceIn(supplier_code="S1", supplier_invoice_no="CN-2", invoice_date=TODAY, type="credit_note", ref_invoice_no="NOPE", lines=[]),
            1, as_of=TODAY,
        )  # fmt: skip


def test_each_problem_with_the_invoice_itself_blocks_it_as_a_tax_block(ledger: Session, outbox: Outbox) -> None:  # noqa: F811
    po_no, _ = _goods(ledger, qty="10")
    ledger.execute(text("update supplier set gst_reg_type = 'composition' where code = 'S1'"))
    ledger.commit()
    composition = _invoice(ledger, po_no, "1", "100", number="C-1", igst=D("18"))
    assert (composition.status, composition.reasons) == ("blocked", ["tax_not_allowed"]) and _row(
        ledger, composition.no
    )["payment_block"] == "tax"

    ledger.execute(text("update supplier set gst_reg_type = 'regular', e_invoice_applicable = true where code = 'S1'"))
    ledger.commit()
    no_irn = _invoice(ledger, po_no, "1", "100", number="C-2")
    assert no_irn.reasons == ["irn_missing"]
    with_irn = _invoice(ledger, po_no, "1", "100", number="C-3", irn="A" * 64)
    assert with_irn.status == "approved"

    wrong = invoices.enter_invoice(
        ledger,
        invoices.InvoiceIn(
            supplier_code="S1", supplier_invoice_no="C-4", invoice_date=TODAY, lines=[_line(po_no, "1", "100")], irn="A" * 64,
            supplier_gstin=S1_GSTIN, buyer_gstin="24ZZZZZ0000Z1Z5",
        ),
        1, as_of=TODAY,
    )  # fmt: skip
    assert wrong.reasons == ["gstin_mismatch"]


def test_an_invoice_that_does_not_add_up_is_blocked(ledger: Session, outbox: Outbox) -> None:  # noqa: F811
    po_no, _ = _goods(ledger, qty="10")

    done = invoices.enter_invoice(
        ledger,
        invoices.InvoiceIn(
            supplier_code="S1", supplier_invoice_no="X-1", invoice_date=TODAY, lines=[_line(po_no, "10", "100")],
            supplier_gstin=S1_GSTIN, buyer_gstin=BUYER_GSTIN, total=D("2000"),
        ),
        1, as_of=TODAY,
    )  # fmt: skip

    assert done.reasons == ["arithmetic_mismatch"] and "invoice total is 2000" in _row(ledger, done.no)["block_reason"]


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"supplier_code": "NOPE"}, "does not exist"),
        ({"supplier_invoice_no": "  "}, "invoice number is needed"),
        ({"lines": []}, "at least one line"),
        ({"type": "receipt"}, "must be invoice"),
    ],
)
def test_an_invoice_that_cannot_be_recorded_is_refused_not_blocked(
    ledger: Session, outbox: Outbox, changes: dict, message: str
) -> None:  # noqa: F811
    po_no, _ = _goods(ledger, qty="10")
    base = {
        "supplier_code": "S1",
        "supplier_invoice_no": "I-1",
        "invoice_date": TODAY,
        "lines": [_line(po_no, "1", "100")],
    }

    with pytest.raises(invoices.InvoiceError, match=message):
        invoices.enter_invoice(ledger, invoices.InvoiceIn(**{**base, **changes}), 1, as_of=TODAY)

    ledger.rollback()
    assert ledger.execute(text("select count(*) from invoice")).scalar_one() == 0


def test_a_line_must_belong_to_an_order_of_that_supplier_that_can_be_invoiced(ledger: Session, outbox: Outbox) -> None:  # noqa: F811
    po_no, _ = _goods(ledger, qty="10")
    for line, message in (
        (invoices.InvLine(po_no, 9, D(1), D(1)), "does not exist"),
        (invoices.InvLine(po_no, 1, D(0), D(1)), "more than zero"),
        (invoices.InvLine(po_no, 1, D(1), D(-1)), "more than zero"),
    ):
        with pytest.raises(invoices.InvoiceError, match=message):
            invoices.enter_invoice(ledger, invoices.InvoiceIn("S1", "I-9", TODAY, [line]), 1, as_of=TODAY)
        ledger.rollback()
    with pytest.raises(invoices.InvoiceError, match="not of S2"):
        invoices.enter_invoice(
            ledger, invoices.InvoiceIn("S2", "I-9", TODAY, [_line(po_no, "1", "100")]), 1, as_of=TODAY
        )
    ledger.rollback()
    ledger.execute(text("update po set status = 'cancelled'"))
    ledger.commit()
    with pytest.raises(invoices.InvoiceError, match="nothing can be invoiced"):
        invoices.enter_invoice(
            ledger, invoices.InvoiceIn("S1", "I-9", TODAY, [_line(po_no, "1", "100")]), 1, as_of=TODAY
        )


# ---- blocks: release and cancel ---------------------------------------------------------------


def test_a_block_is_released_only_by_the_price_rule_person_through_a_proposal(ledger: Session, outbox: Outbox) -> None:  # noqa: F811
    po_no, logins = _goods(ledger, qty="10", price="1000")
    slug, sfx = ledger.info["slug"], ledger.info["sfx"]
    ap = users.create_user(slug, f"ap{sfx}@x.example", PASSWORD, "An AP", "AP")
    done = _invoice(ledger, po_no, "10", "1060", user=ap)
    assert done.status == "blocked"

    with pytest.raises(invoices.InvoiceError, match="reason is needed"):
        invoices.request_release(ledger, done.no, " ", ap)
    pid = invoices.request_release(ledger, done.no, "The supplier agreed the new price in writing", ap)
    with pytest.raises(invoices.InvoiceError, match="already waiting"):
        invoices.request_release(ledger, done.no, "again", ap)
    assert _row(ledger, done.no)["status"] == "blocked"  # asking pays nothing

    proposals.approve(ledger, pid, logins["procurement"])  # the person VAR-01 names

    row = _row(ledger, done.no)
    assert (row["status"], row["payment_block"]) == ("approved", "none") and row["due_date"] is not None
    assert row["variance_accepted"] == D("600.00")
    assert ledger.execute(
        text("select resolved_by, resolution from match_result where resolved_at is not null")
    ).one() == (str(logins["procurement"]), "The supplier agreed the new price in writing")
    assert ledger.execute(
        text("select resolution_kind from match_result where resolved_at is not null")
    ).scalar_one() == "price_accepted"
    assert ledger.execute(text("select invoiced_qty from po_line")).scalar_one() == D(
        "10"
    )  # now it counts against the receipt
    assert (
        ledger.execute(text("select count(*) from audit_event where action = 'invoice.release_block'")).scalar_one()
        == 1
    )
    assert (
        ledger.execute(text("select count(*) from audit_event where action = 'invoice.variance_accepted'")).scalar_one()
        == 1
    )


def test_an_invoice_entered_by_the_rule_person_has_nobody_who_may_accept_its_price(ledger: Session, outbox: Outbox) -> None:  # noqa: F811
    po_no, logins = _goods(ledger, qty="10", price="1000")
    slug, sfx = ledger.info["slug"], ledger.info["sfx"]
    other = users.create_user(slug, f"ap{sfx}@x.example", PASSWORD, "An AP", "AP")
    done = _invoice(ledger, po_no, "10", "1060", user=logins["procurement"])  # entered by the rule's person

    with pytest.raises(invoices.InvoiceError, match="names nobody who may accept this price"):
        invoices.request_release(ledger, done.no, "agreed", other)  # the enterer is never the approver (rule 7)

    assert _row(ledger, done.no)["status"] == "blocked"
    assert ledger.execute(text("select count(*) from proposal")).scalar_one() == 0


def test_the_person_who_asked_cannot_approve_and_someone_without_the_right_cannot_either(
    ledger: Session, outbox: Outbox
) -> None:  # noqa: F811
    po_no, _ = _goods(ledger, qty="10", price="1000")
    slug, sfx = ledger.info["slug"], ledger.info["sfx"]
    ap = users.create_user(slug, f"ap{sfx}@x.example", PASSWORD, "An AP", "AP")
    done = _invoice(ledger, po_no, "10", "1060", user=ap)
    pid = invoices.request_release(ledger, done.no, "agreed", ap)

    with pytest.raises(permissions.PermissionDenied):
        proposals.approve(ledger, pid, ap)  # an AP person may ask but not approve

    assert _row(ledger, done.no)["status"] == "blocked"


def test_a_copy_is_never_paid_and_cannot_be_released(ledger: Session, outbox: Outbox) -> None:  # noqa: F811
    po_no, _ = _goods(ledger, qty="10")
    _invoice(ledger, po_no, "2", "100", number="INV-1")
    copy = _invoice(ledger, po_no, "2", "100", number="INV 1")

    with pytest.raises(invoices.InvoiceError, match="is cancelled, not blocked"):
        invoices.request_release(ledger, copy.no, "pay it", 1)


def test_a_blocked_invoice_can_be_cancelled_for_a_reason_and_an_approved_one_cannot(
    ledger: Session, outbox: Outbox
) -> None:  # noqa: F811
    po_no, _ = _goods(ledger, qty="10", price="1000")
    blocked = _invoice(ledger, po_no, "10", "1060", number="B")
    good = _invoice(ledger, po_no, "10", "1000", number="G")
    with pytest.raises(invoices.InvoiceError, match="reason is needed"):
        invoices.cancel_invoice(ledger, blocked.no, "", 1)

    invoices.cancel_invoice(ledger, blocked.no, "A corrected invoice is coming", 1)

    assert _row(ledger, blocked.no)["status"] == "cancelled"
    with pytest.raises(invoices.InvoiceError, match="only a blocked invoice"):
        invoices.cancel_invoice(ledger, good.no, "oops", 1)
    assert ledger.execute(text("select count(*) from audit_event where action = 'invoice.cancel'")).scalar_one() == 1


# ---- the screens' API -------------------------------------------------------------------------


def test_ap_enters_an_invoice_over_the_api_and_sees_it_matched_and_the_others_cannot(
    ledger: Session, outbox: Outbox
) -> None:  # noqa: F811
    po_no, logins = _goods(ledger, qty="10")
    slug, sfx = ledger.info["slug"], ledger.info["sfx"]
    ap = users.create_user(slug, f"ap{sfx}@x.example", PASSWORD, "An AP", "AP")
    h_ap, h_buyer = _headers(slug, ap, "AP"), _headers(slug, logins["buyer"], "Buyer")
    body = {
        "supplier_code": "S1", "number": "API-1", "date": "2026-11-06", "supplier_gstin": S1_GSTIN, "buyer_gstin": BUYER_GSTIN,
        "lines": [{"po_no": po_no, "po_line_no": 1, "qty": "10", "unit_price": "100"}],
    }  # fmt: skip

    ready = client.get("/api/invoicing/ready", headers=h_ap).json()
    assert [(r["po_no"], r["available"], r["unit_price"]) for r in ready] == [(po_no, "10", "100.0000")]
    assert client.post("/api/invoices", json=body).status_code == 401
    assert client.post("/api/invoices", json=body, headers=h_buyer).status_code == 403

    r = client.post("/api/invoices", json=body, headers=h_ap)

    assert r.status_code == 200 and (r.json()["status"], r.json()["result"]) == ("approved", "matched")
    no = r.json()["invoice"]
    detail = client.get(f"/api/invoices/{no}", headers=h_ap).json()
    assert (detail["total"], detail["status"], detail["due_date"], detail["can_enter"], detail["can_act"]) == (
        "1180.00",
        "approved",
        "2026-12-06",
        True,
        False,
    )
    assert detail["lines"][0]["taxable"] == "1000.00" and detail["match"][0]["result"] == "matched"
    assert [i["id"] for i in client.get("/api/invoices", headers=h_ap).json()] == [no]
    assert client.get("/api/invoicing/ready", headers=h_ap).json() == []  # everything received is now invoiced
    again = client.post("/api/invoices", json={**body, "number": "api 1"}, headers=h_ap)
    assert again.json()["duplicate_of"] == no and again.json()["status"] == "cancelled"
    assert client.get("/api/invoices/NOPE", headers=h_ap).status_code == 404


@pytest.mark.parametrize(
    "body",
    [
        {"supplier_code": "S1"},
        {"supplier_code": "S1", "number": "X", "date": "not a date", "lines": []},
        {
            "supplier_code": "S1",
            "number": "X",
            "date": "2026-11-06",
            "lines": [{"po_no": "P", "po_line_no": 1, "qty": "NaN", "unit_price": "1"}],
        },
        {"supplier_code": "S1", "number": "X", "date": "2026-11-06", "lines": "no"},
    ],
)
def test_the_api_refuses_what_is_not_an_invoice(ledger: Session, outbox: Outbox, body: dict) -> None:  # noqa: F811
    slug, sfx = ledger.info["slug"], ledger.info["sfx"]
    ap = users.create_user(slug, f"ap{sfx}@x.example", PASSWORD, "An AP", "AP")

    assert client.post("/api/invoices", json=body, headers=_headers(slug, ap, "AP")).status_code == 422


def test_ap_works_a_blocked_invoice_over_the_api_and_the_price_rule_person_approves_the_release(
    ledger: Session, outbox: Outbox
) -> None:  # noqa: F811
    po_no, logins = _goods(ledger, qty="10", price="1000")
    slug, sfx = ledger.info["slug"], ledger.info["sfx"]
    ap = users.create_user(slug, f"ap{sfx}@x.example", PASSWORD, "An AP", "AP")
    boss = logins["procurement"]
    h_ap, h_boss = _headers(slug, ap, "AP"), _headers(slug, boss, "Approver")
    body = {
        "supplier_code": "S1", "number": "API-2", "date": "2026-11-06", "supplier_gstin": S1_GSTIN, "buyer_gstin": BUYER_GSTIN,
        "lines": [{"po_no": po_no, "po_line_no": 1, "qty": "10", "unit_price": "1060"}],
    }  # fmt: skip
    no = client.post("/api/invoices", json=body, headers=h_ap).json()["invoice"]
    detail = client.get(f"/api/invoices/{no}", headers=h_ap).json()
    assert (detail["status"], detail["block"], detail["can_act"]) == ("blocked", "match", True) and "price" in detail[
        "reason"
    ].lower()
    assert (
        client.get(f"/api/invoices/{no}", headers=h_boss).json()["can_act"] is False
    )  # finance approves, AP works the block

    assert client.post(f"/api/invoices/{no}/release", json={"reason": " "}, headers=h_ap).status_code == 409
    asked = client.post(f"/api/invoices/{no}/release", json={"reason": "agreed in writing"}, headers=h_ap)
    assert asked.status_code == 200
    assert (
        client.get(f"/api/invoices/{no}", headers=h_ap).json()["pending_release"]["id"] == asked.json()["proposal_id"]
    )
    assert client.post(f"/api/invoices/{no}/cancel", json={"reason": "x"}, headers=h_boss).status_code == 403

    proposals.approve(ledger, asked.json()["proposal_id"], boss)

    assert client.get(f"/api/invoices/{no}", headers=h_ap).json()["status"] == "approved"
    assert [i["id"] for i in client.get("/api/invoices?status=blocked", headers=h_ap).json()] == []
