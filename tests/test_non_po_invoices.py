"""Bills without a purchase order (block 10): the rule in force, who authorises, the approval by amount, the budget,
the possible duplicate and the refusals. Each test says what it is given, what it does and what it expects.
"""

from contextlib import contextmanager
from datetime import date
from decimal import Decimal
from typing import Any, Iterator

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session
from test_orders import _logins, shop  # noqa: F401 - fixtures
from test_receiving import store  # noqa: F401 - fixtures
from test_sourcing import PASSWORD, Outbox, db, outbox  # noqa: F401 - fixtures

from p2p.formulas.calendar import pin_today
from p2p.invoices import non_po
from p2p.invoices import service as invoices
from p2p.platform import users

D = Decimal
TODAY = date(2026, 11, 6)
N1_ON = date(2026, 11, 23)
N2_ON = date(2026, 11, 19)
AUTH_ON = date(2026, 11, 25)
AUTH2_ON = date(2026, 11, 27)
APPROVE_ON = date(2026, 11, 30)
BUYER_GSTIN = "27APXCA0000P1ZL"


@contextmanager
def _on(day: date) -> Iterator[None]:
    pin_today(day)
    try:
        yield
    finally:
        pin_today(TODAY)


@pytest.fixture
def bills(shop: Session) -> Session:  # noqa: F811
    """The order world with the two bill categories, a cost centre, its budget, the NPO rules and the people who decide."""
    x = shop.execute
    slug, sfx = shop.info["slug"], shop.info["sfx"]
    x(
        text(
            "insert into gl_account (code, name, type, is_capex, is_tax_account) values "
            "('5460', 'Internet & Telephone', 'expense', false, false), "
            "('5410', 'Professional & Consultancy Fees', 'expense', false, false), "
            "('1430', 'Stores & Spares', 'inventory', false, false)"
        )
    )  # fmt: skip
    x(
        text(
            "insert into item_category (code, name, buyer_user, min_quotes, default_gl, stocked) values "
            "('UTL', 'Utilities and telecom', 'U5', 2, '5460', false), "
            "('PRF', 'Professional fees', 'U5', 2, '5410', false), "
            "('TEL', 'Internet and telephone', 'U5', 2, '5460', false)"
        )
    )  # fmt: skip
    x(
        text(
            "insert into cost_centre (code, name, department_code, location_code, owner_user, active) "
            "values ('CC-ITO', 'IT operations', 'PUR', 'PLT-CKN', 'U2', true)"
        )
    )
    x(text("insert into location (code, name, is_stock_location, gstin, state_code) values ('PLT-CKN', 'Plant, Chakan', true, :g, '27')"), {"g": BUYER_GSTIN})  # fmt: skip
    x(
        text(
            "insert into cost_centre (code, name, department_code, location_code, owner_user, active) "
            "values ('CC-ADM', 'Plant administration', 'MGMT', 'PLT-CKN', 'U4', true)"
        )
    )
    x(
        text(
            "insert into budget (gl_code, cost_centre_code, period, amount, version, policy) "
            "select '5460', 'CC-ADM', p, 20000, 1, 'block' from (select to_char(d, 'YYYY-MM') as p "
            "from generate_series('2026-04-01'::date, '2027-03-01'::date, '1 month') d) m"
        )
    )
    x(
        text(
            "insert into budget (gl_code, cost_centre_code, period, amount, version, policy) "
            "select '5410', 'CC-ADM', p, 40000, 1, 'block' from (select to_char(d, 'YYYY-MM') as p "
            "from generate_series('2026-04-01'::date, '2027-03-01'::date, '1 month') d) m"
        )
    )
    x(
        text(
            "insert into non_po_rule (category_code, version, effective_from, max_value, supporting_doc, note) values "
            "('UTL', 1, '2025-04-01', 50000.00, null, 'Telephone and internet bills'), "
            "('PRF', 1, '2025-04-01', 200000.00, 'Engagement letter', 'Audit and tax fees'), "
            "('TEL', 1, '2025-04-01', 100000.00, null, 'Office internet')"
        )
    )  # fmt: skip
    x(
        text(
            "insert into supplier_category (supplier_code, category_code, approved_from) values "
            "('S1', 'UTL', '2024-01-01'), ('S2', 'PRF', '2024-01-01'), ('S3', 'UTL', '2024-01-01'), "
            "('S4', 'TEL', '2024-01-01')"
        )
    )  # fmt: skip
    x(text("update supplier set status = 'blocked' where code in ('S3', 'S6')"))
    x(
        text(
            "update supplier set state_code = '27', gstin = '27BBBBB0000B1ZT', msme_category = 'micro', "
            "msme_agreement = true, agreed_payment_days = 30, payment_terms_code = 'NET45' where code = 'S2'"
        )
    )
    x(text("update supplier set state_code = '27', gstin = '27CCCCC0000C1ZK' where code = 'S4'"))
    x(
        text(
            "insert into approver_role (role, source, value) values ('finance_manager', 'designation', 'Finance Manager') "
            "on conflict (role) do nothing"
        )
    )
    x(
        text(
            "insert into org_user (code, name, designation, department_code, manager_code, email, active) values "
            "('U6', 'Fin Manager', 'Finance Manager', 'FIN', 'U1', :fm, true), "
            "('U7', 'Ap Clerk', 'AP Executive', 'FIN', 'U2', :ap, true)"
        ),
        {"fm": f"finmgr{sfx}@x.example", "ap": f"ap{sfx}@x.example"},
    )  # fmt: skip
    x(
        text(
            "insert into approval_rule (rule_code, document, min_value, max_value, category_code, department_code, "
            "spend_type, approver_chain, version, effective_from) values "
            "('NPO-01', 'non_po_invoice', 0.00, 50000.00, 'ANY', 'ANY', 'ANY', 'auto', 1, '2025-04-01'), "
            "('NPO-02', 'non_po_invoice', 50000.01, 500000.00, 'ANY', 'ANY', 'ANY', 'finance_manager', 1, '2025-04-01'), "
            "('NPO-03', 'non_po_invoice', 500000.01, 999999999.00, 'ANY', 'ANY', 'ANY', 'director', 1, '2025-04-01')"
        )
    )  # fmt: skip
    x(
        text(
            "insert into number_series (document_type, prefix, scope, format, reset) values "
            "('invoice_booking', 'PI', 'company', '{prefix}-{fy4}-{seq:06d}', 'fy')"
        )
    )
    x(text("insert into tolerance (key, value, category_code) values ('price_pct', '2', 'ANY'), ('price_abs', '500', 'ANY'), ('qty_pct', '0', 'ANY')"))
    x(text("update payment_terms set baseline = 'invoice_date'"))
    shop.commit()
    fm = users.create_user(slug, f"finmgr{sfx}@x.example", PASSWORD, "Fin Manager", "Approver")
    ap = users.create_user(slug, f"ap{sfx}@x.example", PASSWORD, "Ap Clerk", "AP")
    logins = _logins(shop)
    shop.info["bills"] = {
        "ap": ap, "finance": fm, "owner": logins["keeper"], "director": logins["director"],
        "procurement": logins["procurement"], "buyer": logins["buyer"], "stores": logins["stores"],
        "cfo": logins["cfo"],
    }  # fmt: skip
    return shop


def _doc(shop: Session, name: str = "bill.pdf") -> int:
    n = shop.info.get("doc_n", 0) + 1
    shop.info["doc_n"] = n
    key = f"k-{n}-{name}"
    return int(
        shop.execute(
            text(
                "insert into document (doc_type, filename, content_type, size, sha256, storage_key) values "
                "('invoice_evidence', :n, 'application/pdf', 10, :h, :k) returning id"
            ),
            {"n": name, "h": key, "k": key},
        ).scalar_one()
    )


def _enter(
    shop: Session,
    *,
    supplier: str = "S1",
    number: str = "SNL/2627/0458",
    on: date = N1_ON,
    category: str = "UTL",
    taxable: str = "15000.00",
    rate: str = "18",
    cost_centre: str = "CC-ADM",
    gl: str | None = None,
    supporting: bool = False,
    with_copy: bool = True,
    user: int | None = None,
    line_po: bool = False,
    po_no: str | None = None,
    not_duplicate_reason: str | None = None,
    irn: str | None = None,
) -> invoices.Entered:
    docs = [{"id": _doc(shop, f"{number}.pdf"), "kind": "copy"}] if with_copy else []
    if supporting:
        docs.append({"id": _doc(shop, f"{number}-letter.pdf"), "kind": "supporting"})
    return invoices.enter_invoice(
        shop,
        invoices.InvoiceIn(
            supplier_code=supplier,
            supplier_invoice_no=number,
            invoice_date=on,
            kind="non_po",
            category_code=category,
            cost_centre_code=cost_centre,
            gl_code=gl,
            documents=docs,
            not_duplicate_reason=not_duplicate_reason,
            irn=irn,
            lines=[
                invoices.InvLine(
                    po_no="PO-1" if line_po else None,
                    po_line_no=1 if line_po else None,
                    qty=D("1"),
                    unit_price=D(taxable),
                    gst_rate=D(rate),
                    description="Internet lease line" if category == "UTL" else "Professional fees",
                )
            ],
        ),
        user if user is not None else shop.info["bills"]["ap"],
        as_of=on,
    )


def _row(shop: Session, no: str) -> dict[str, Any]:
    shop.expire_all()
    return dict(shop.execute(text("select * from invoice where no = :n"), {"n": no}).mappings().one())


def _authorise(shop: Session, no: str, *, user: int | None = None, on: date | None = None, reason: str | None = None) -> dict[str, Any]:
    return non_po.authorise(
        shop, shop.info["slug"], no, user if user is not None else shop.info["bills"]["owner"],
        over_budget_reason=reason,
    )


def _approve(shop: Session, no: str, *, user: int | None = None, reason: str | None = None) -> dict[str, Any]:
    return non_po.approve(shop, shop.info["slug"], no, user if user is not None else shop.info["bills"]["finance"], over_budget_reason=reason)  # fmt: skip


def test_n1_a_bill_in_the_automatic_band_is_authorised_then_approved_with_recorded_tax_and_due_date(
    bills: Session, outbox: Outbox
) -> None:  # noqa: F811
    with _on(N1_ON):
        done = _enter(bills, taxable="15000.00")

    assert (done.status, done.kind, done.authoriser) == ("awaiting_authorisation", "non_po", "U4")
    row = _row(bills, done.no)
    assert (row["taxable"], row["cgst"], row["sgst"], row["igst"], row["total"]) == (
        D("15000.00"), D("1350.00"), D("1350.00"), D("0.00"), D("17700.00"),
    )  # fmt: skip
    task = bills.execute(text("select kind, title, assignee from task where kind = 'invoice_authorise'")).one()
    assert task[0] == "invoice_authorise" and "Alpha Traders" in task[1] and "17,700.00" in task[1]
    assert bills.execute(
        text("select 1 from email_message where direction = 'out' and subject like 'Bill to authorise%'")
    ).first()

    with _on(AUTH_ON):
        result = _authorise(bills, done.no)

    assert result["status"] == "approved" and result["rule"]["code"] == "NPO-01"
    row = _row(bills, done.no)
    assert (row["status"], row["approved_by"], row["due_date"], row["authorised_by"], row["authorised_on"]) == (
        "approved", str(bills.info["bills"]["owner"]), date(2026, 12, 23), "U4", AUTH_ON,
    )  # fmt: skip
    held = bills.execute(
        text("select part, amount, gl_code, cost_centre_code from budget_use where cost_centre_code = 'CC-ADM'")
    ).all()
    assert [(p, a, g, c) for p, a, g, c in held] == [("order", D("15000.00"), "5460", "CC-ADM")]


def test_n2_a_bill_in_the_finance_band_needs_the_finance_manager_and_uses_the_msme_deadline(
    bills: Session, outbox: Outbox
) -> None:  # noqa: F811
    with _on(AUTH_ON):
        done = _enter(bills, supplier="S2", number="MG/2627/112", on=N2_ON, category="PRF", taxable="75000.00", supporting=True)
        assert (done.status, done.authoriser) == ("awaiting_authorisation", "U4")
    with _on(AUTH2_ON):
        result = _authorise(bills, done.no)

    assert result["status"] == "awaiting_approval"
    assert [(s["step"], s["name"], s["role"]) for s in result["approvers"]] == [(1, "Fin Manager", "finance_manager")]
    assert _row(bills, done.no)["approval_rule_code"] == "NPO-02"
    assert bills.execute(
        text("select 1 from email_message where direction = 'out' and subject like 'Bill to approve%'")
    ).first()

    with _on(APPROVE_ON):
        approved = _approve(bills, done.no)

    assert approved["status"] == "approved"
    row = _row(bills, done.no)
    assert (row["total"], row["due_date"]) == (D("88500.00"), date(2026, 12, 27))
    assert row["approved_by"] == str(bills.info["bills"]["finance"])


def test_n3_each_refusal_says_why_and_writes_nothing(bills: Session, outbox: Outbox) -> None:  # noqa: F811
    before = bills.execute(text("select count(*) from invoice")).scalar_one()

    with pytest.raises(invoices.InvoiceError, match="needs the Engagement letter attached"):
        _enter(bills, supplier="S2", number="MG/1", category="PRF", supporting=False)
    with pytest.raises(invoices.InvoiceError, match="cannot be billed without a purchase order"):
        _enter(bills, category="C1")
    with pytest.raises(invoices.InvoiceError, match="not approved for"):
        _enter(bills, supplier="S2", number="MG/2", category="UTL")
    with pytest.raises(invoices.InvoiceError, match="is blocked"):
        _enter(bills, supplier="S3", number="S3/1")
    with pytest.raises(invoices.InvoiceError, match="GL 1430 cannot take a bill"):
        _enter(bills, number="SNL/9", gl="1430")
    with pytest.raises(invoices.InvoiceInputError, match="cannot name an order line"):
        _enter(bills, number="SNL/10", line_po=True)
    assert bills.execute(text("select count(*) from invoice")).scalar_one() == before


def test_n4_the_enterer_cannot_authorise_and_a_deputy_may(bills: Session, outbox: Outbox) -> None:  # noqa: F811
    # the cost centre owner is the AP clerk: the authoriser falls to the department head, never the enterer
    bills.execute(text("update cost_centre set owner_user = 'U7' where code = 'CC-ADM'"))
    bills.commit()
    owner_login = bills.info["bills"]["ap"]
    with _on(AUTH_ON):
        first = _enter(bills, supplier="S2", number="MG/2627/120", on=N2_ON, category="PRF", taxable="75000.00", supporting=True, user=owner_login)

    assert _row(bills, first.no)["authoriser"] == "U1"
    with pytest.raises(invoices.InvoiceError, match="it is not your turn"):
        _authorise(bills, first.no, user=owner_login)
    with pytest.raises(invoices.InvoiceError, match="it is not your turn"):
        _authorise(bills, first.no, user=bills.info["bills"]["finance"])
    # a deputy in force for the director may authorise
    bills.execute(
        text(
            "insert into delegation (from_user, to_user, start_date, end_date, reason) "
            "values ('U1', 'U5', '2026-11-26', '2026-11-30', 'on leave')"
        )
    )
    bills.commit()
    with _on(AUTH2_ON):
        done = _authorise(bills, first.no, user=bills.info["bills"]["procurement"])
    assert done["status"] == "awaiting_approval"
    assert _row(bills, first.no)["authorised_by"] == "U5"


def test_n5_a_rejection_cancels_the_bill_tells_ap_and_frees_the_number(bills: Session, outbox: Outbox) -> None:  # noqa: F811
    with _on(AUTH_ON):
        done = _enter(bills, supplier="S2", number="MG/2627/112", on=N2_ON, category="PRF", taxable="75000.00", supporting=True)
    with _on(AUTH2_ON):
        _authorise(bills, done.no)

    non_po.reject(bills, bills.info["slug"], done.no, bills.info["bills"]["finance"], "fee not agreed")

    row = _row(bills, done.no)
    assert row["status"] == "cancelled" and row["cancel_reason"] == "Rejected by Fin Manager: fee not agreed"
    assert bills.execute(text("select count(*) from task where status = 'cancelled'")).scalar_one() == 1
    with _on(AUTH_ON):
        again = _enter(bills, supplier="S2", number="MG/2627/112", on=N2_ON, category="PRF", taxable="75000.00", supporting=True)
    assert again.status == "awaiting_authorisation" and again.duplicate_of is None


def test_n6_the_budget_holds_each_bill_and_the_last_decider_needs_a_reason_over_it(bills: Session, outbox: Outbox) -> None:  # noqa: F811
    with _on(AUTH_ON):
        first = _enter(bills, supplier="S2", number="MG/2627/112", on=N2_ON, category="PRF", taxable="75000.00", supporting=True)
    with _on(AUTH2_ON):
        _authorise(bills, first.no)
    with _on(APPROVE_ON):
        _approve(bills, first.no)
    for number, taxable in (("MG/2627/115", "80000.00"), ("MG/2627/116", "85000.00")):
        with _on(AUTH_ON):
            done = _enter(bills, supplier="S2", number=number, on=N2_ON, category="PRF", taxable=taxable, supporting=True)
        with _on(AUTH2_ON):
            _authorise(bills, done.no)
        with _on(APPROVE_ON):
            _approve(bills, done.no)
    with _on(AUTH_ON):
        last = _enter(bills, supplier="S2", number="MG/2627/117", on=N2_ON, category="PRF", taxable="90000.00", supporting=True)
    with _on(AUTH2_ON):
        _authorise(bills, last.no)

    with _on(APPROVE_ON):
        with pytest.raises(invoices.InvoiceError, match=r"over the budget .* by Rs 10,000.00"):
            _approve(bills, last.no)
        approved = _approve(bills, last.no, reason="the audit fee is contracted")
    assert approved["status"] == "approved"
    assert _row(bills, last.no)["over_budget_reason"] == "the audit fee is contracted"
    assert bills.execute(text("select count(*) from audit_event where action = 'invoice.approve_over_budget'")).scalar_one() == 1


def test_b1_b2_the_limit_is_compared_on_the_recorded_total(bills: Session, outbox: Outbox) -> None:  # noqa: F811
    with _on(N1_ON):
        at_limit = _enter(bills, number="SNL/B1", taxable="42372.88")
        row = _row(bills, at_limit.no)
        assert (row["total"], row["cgst"], row["sgst"]) == (D("50000.00"), D("3813.56"), D("3813.56"))
        with pytest.raises(invoices.InvoiceError, match=r"may be at most Rs 50,000.00; this one is Rs 50,001.00"):
            _enter(bills, number="SNL/B2", taxable="42373.73")


def test_a_possible_duplicate_is_a_question_unless_a_reason_is_given(bills: Session, outbox: Outbox) -> None:  # noqa: F811
    with _on(N1_ON):
        first = _enter(bills, number="SNL/2627/0458", on=N1_ON)
    with _on(date(2026, 11, 30)):
        with pytest.raises(invoices.InvoiceError, match="this may be the same bill as"):
            _enter(bills, number="SNL/2627/0461", on=date(2026, 11, 30))
        other = _enter(bills, supplier="S2", number="MG/2627/900", on=date(2026, 11, 30), category="PRF", taxable="15000.00", supporting=True)
    assert other.status == "awaiting_authorisation"
    with _on(AUTH2_ON):
        seen = _enter(bills, number="SNL/2627/0463", on=date(2026, 11, 27), not_duplicate_reason="November's bill, paid by standing order")
    assert _row(bills, seen.no)["suspect_of"] == first.no


def test_a_twin_number_is_a_cancelled_copy_and_never_live_twice(bills: Session, outbox: Outbox) -> None:  # noqa: F811
    with _on(N1_ON):
        first = _enter(bills, number="SNL/2627/0458", on=N1_ON, taxable="15000.00")
        copy = _enter(bills, number="SNL/2627/458", on=N1_ON, taxable="15000.00")
    assert (copy.status, copy.duplicate_of) == ("cancelled", first.no)
    assert "already on file" in _row(bills, copy.no)["block_reason"]
    assert bills.execute(text("select count(*) from invoice where status <> 'cancelled'")).scalar_one() == 1


def test_n7_a_bill_in_another_company_band_is_authorised_by_its_own_cost_centre_and_uses_its_terms(
    bills: Session, outbox: Outbox
) -> None:  # noqa: F811
    with _on(N1_ON):
        done = _enter(bills, supplier="S4", number="NFN/2627/1187", category="TEL", cost_centre="CC-ITO", taxable="25000.00")
    assert (done.status, done.authoriser) == ("awaiting_authorisation", "U2")
    with _on(AUTH_ON):
        approved = _authorise(bills, done.no, user=bills.info["bills"]["cfo"])
    row = _row(bills, done.no)
    assert approved["status"] == "approved" and approved["rule"]["code"] == "NPO-01"
    assert (row["total"], row["due_date"]) == (D("29500.00"), date(2026, 12, 23))
