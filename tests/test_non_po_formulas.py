"""The plain rules of a bill without an order: its limit, supplier, cost centre, GL and evidence, the possible duplicate
window, the FR33 key and the tax head. No database: each test says what it is given, what it does and what it expects.
"""

from datetime import date
from decimal import Decimal

from p2p.formulas import matching as m
from p2p.formulas import non_po as n

D = Decimal
UTL = n.Rule(1, "UTL", "Utilities and telecom", 1, D("50000.00"), None, "5460", False)


def _problems(**kw: object) -> list[n.Problem]:
    base: dict[str, object] = {
        "rule": UTL,
        "category_name": "Utilities and telecom",
        "total": D("17700.00"),
        "supplier_name": "Sahyadri Netlink Pvt Ltd",
        "supplier_status": "active",
        "supplier_approved": True,
        "cost_centre_ok": True,
        "gl_code": "5460",
        "gl_ok": True,
        "has_copy": True,
        "has_supporting": True,
    }
    return n.rule_problems(**{**base, **kw})  # type: ignore[arg-type]


def test_a_bill_at_its_limit_is_allowed_and_one_paisa_over_is_refused() -> None:
    assert _problems(total=D("50000.00")) == []
    over = _problems(total=D("50000.01"))
    assert [p.code for p in over] == ["limit"] and over[0].status == 409
    assert "may be at most Rs 50,000.00" in over[0].message and "Rs 50,000.01" in over[0].message


def test_only_an_active_supplier_approved_for_the_category_may_bill() -> None:
    assert [p.code for p in _problems(supplier_status="blocked")] == ["supplier_status"]
    assert [p.code for p in _problems(supplier_approved=False)] == ["supplier_category"]
    assert "is blocked" in _problems(supplier_status="blocked")[0].message


def test_the_cost_centre_and_an_expense_gl_are_needed() -> None:
    assert [p.code for p in _problems(cost_centre_ok=False)] == ["cost_centre"]
    assert [p.code for p in _problems(gl_ok=False, gl_code="1430")] == ["gl"]
    assert _problems(gl_ok=False, gl_code="1430")[0].message == "GL 1430 cannot take a bill without an order"


def test_the_bill_copy_always_and_the_named_supporting_document_when_a_rule_names_one() -> None:
    assert [p.code for p in _problems(has_copy=False)] == ["copy"]
    assert _problems(has_copy=False)[0].status == 422
    prf = n.Rule(2, "PRF", "Professional fees", 1, D("200000.00"), "Engagement letter", "5410", False)
    missing = _problems(rule=prf, has_supporting=False)
    assert [p.code for p in missing] == ["supporting_doc"]
    assert missing[0].message == "a professional fees bill needs the Engagement letter attached"
    assert missing[0].status == 422


def test_a_category_without_a_rule_and_a_stocked_category_are_refused_in_words() -> None:
    assert (
        n.rule_problems(
            rule=None,
            category_name="Raw materials",
            total=D("1"),
            supplier_name="S",
            supplier_status="active",
            supplier_approved=True,
            cost_centre_ok=True,
            gl_code="1410",
            gl_ok=True,
            has_copy=True,
            has_supporting=True,
        )[0].message
        == "Raw materials cannot be billed without a purchase order"
    )
    stocked = n.Rule(3, "RAW", "Raw materials", 1, D("1000.00"), None, "1410", True)
    assert _problems(rule=stocked)[0].message == "Raw materials cannot be billed without a purchase order"


def _possible(day: date, number: str = "SNL/2627/0458") -> n.Possible:
    return n.Possible("PI-2627-000001", number, day, D("17700.00"))


def test_the_possible_duplicate_window_includes_both_ends_and_both_directions() -> None:
    on = date(2026, 11, 23)

    assert n.possible_duplicate([_possible(date(2026, 11, 30))], on, 7) is not None  # 7 days after
    assert n.possible_duplicate([_possible(date(2026, 11, 16))], on, 7) is not None  # 7 days before
    assert n.possible_duplicate([_possible(date(2026, 12, 1))], on, 7) is None  # 8 days after
    assert n.possible_duplicate([_possible(date(2026, 11, 15))], on, 7) is None  # 8 days before
    assert n.possible_duplicate([_possible(date(2026, 11, 6))], on, 7) is None  # 17 days away


def test_the_fr33_key_drops_leading_zeros_in_each_run_but_a_run_of_zeros_keeps_one() -> None:
    assert m.normalize_invoice_no("PIS/2425/0036") == "PIS242536"
    assert m.normalize_invoice_no("pis 2425 36") == "PIS242536"
    assert m.normalize_invoice_no("INV-001") == m.normalize_invoice_no("inv 1") == "INV1"
    assert m.normalize_invoice_no("INV-000") == "INV0"
    assert m.normalize_invoice_no("0") == "0"
    assert m.normalize_invoice_no("A-0B-007") == "A0B7"


def _header(**kw: object) -> m.Header:
    base: dict[str, object] = {
        "supplier_gst_type": "regular",
        "e_invoice_applicable": False,
        "invoice_type": "invoice",
        "irn": None,
        "tax_charged": D("18"),
        "invoice_supplier_gstin": "27AAAAA0000A1Z2",
        "valid_supplier_gstins": frozenset({"27AAAAA0000A1Z2"}),
        "invoice_buyer_gstin": "27AAAAA0000A1Z2",
        "order_buyer_gstin": "27AAAAA0000A1Z2",
        "supplier_state": "27",
        "place_of_supply": "27",
        "cgst": D("9"),
        "sgst": D("9"),
        "igst": D("0"),
    }
    return m.Header(**{**base, **kw})  # type: ignore[arg-type]


def test_igst_within_a_state_and_cgst_sgst_across_states_are_refused_in_words() -> None:
    igst = _header(cgst=D("0"), sgst=D("0"), igst=D("18"))
    assert m.header_problem(igst) == m.TAX_HEAD
    assert m.header_words(igst, {"27": "Maharashtra"}) == (
        "IGST was charged on a supply within Maharashtra (27): CGST and SGST are due"
    )
    across = _header(
        invoice_buyer_gstin="29BBBBB0000B1ZP",
        order_buyer_gstin="29BBBBB0000B1ZP",
        place_of_supply="29",
        igst=D("0"),
        cgst=D("9"),
        sgst=D("9"),
    )
    assert m.header_problem(across) == m.TAX_HEAD
    assert m.header_words(across, {"27": "Maharashtra", "29": "Karnataka"}) == (
        "CGST and SGST were charged on a supply from Maharashtra (27) to Karnataka (29): IGST is due"
    )
    zero = _header(tax_charged=D("0"), cgst=D("0"), sgst=D("0"), igst=D("0"))
    assert m.header_problem(zero) is None


def test_a_bills_buyer_gstin_must_be_the_cost_centres_location() -> None:
    wrong = _header(invoice_buyer_gstin="29BBBBB0000B1ZP", order_buyer_gstin="27AAAAA0000A1Z2")
    assert m.header_problem(wrong) == m.GSTIN
