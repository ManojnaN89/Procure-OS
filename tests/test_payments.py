"""Payment: due dates and the small-supplier rule, tax at source, what is payable, payment runs and their approval, the bank
file, recording payment, and closing the case."""

from datetime import date, timedelta
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session
from test_invoices import S1_GSTIN, TODAY, _goods, _headers, _invoice, _row, ledger  # noqa: F401 - fixtures
from test_orders import shop  # noqa: F401 - fixtures
from test_receiving import store  # noqa: F401 - fixtures
from test_sourcing import PASSWORD, Outbox, db, outbox  # noqa: F401 - fixtures

from p2p.formulas import payment as rules
from p2p.formulas.calendar import business_today
from p2p.invoices import payment
from p2p.invoices.service import InvoiceError
from p2p.main import app
from p2p.platform import permissions, proposals, users

D = Decimal
client = TestClient(app)


# ---- due dates --------------------------------------------------------------------------------


def _due(**kw: object) -> date:
    base = {
        "baseline": "invoice_date", "days": 30, "invoice_date": date(2026, 11, 6), "accepted_on": date(2026, 11, 10),
        "msme_category": None, "msme_agreement": False, "agreed_days": None,
    }  # fmt: skip
    return rules.due_date(**{**base, **kw})  # type: ignore[arg-type]


def test_the_due_date_counts_the_terms_from_the_invoice_date_or_from_acceptance() -> None:
    assert _due() == date(2026, 12, 6)  # 30 days from the invoice date
    assert _due(baseline="acceptance_date") == date(2026, 12, 10)
    assert _due(baseline="receipt_date", days=0) == date(2026, 11, 10)
    assert _due(msme_category="medium", msme_agreement=False) == date(
        2026, 12, 6
    )  # medium suppliers are outside the rule


@pytest.mark.parametrize(
    ("agreed", "expected"),
    [(30, date(2026, 12, 6)), (20, date(2026, 11, 30)), (45, date(2026, 12, 6)), (90, date(2026, 12, 6))],
)
def test_a_small_supplier_with_a_written_agreement_is_paid_by_the_earlier_of_the_terms_and_the_agreed_days(
    agreed: int, expected: date
) -> None:
    # accepted on 10 Nov; the terms say 6 Dec (30 days from the invoice); the agreed days count from acceptance, never above 45
    assert _due(msme_category="small", msme_agreement=True, agreed_days=agreed) == expected


def test_a_small_supplier_without_a_written_agreement_must_be_paid_within_fifteen_days_of_acceptance() -> None:
    assert _due(msme_category="micro", msme_agreement=False) == date(2026, 11, 25)
    assert _due(msme_category="micro", msme_agreement=False, accepted_on=date(2026, 11, 10)) == date(2026, 11, 25)


# ---- tax at source and interest ---------------------------------------------------------------


@pytest.mark.parametrize(
    ("before", "taxable", "pan_bad", "base", "amount"),
    [
        ("0", "5000000", False, "0", "0.00"),  # exactly 50 lakh: nothing above it
        ("0", "5000001", False, "1", "0.00"),  # a rupee above: base 1, tax under a paisa
        ("4999000", "2000000", False, "1999000", "1999.00"),  # crossing within one invoice
        ("5000000", "100000", False, "100000", "100.00"),  # already past: the whole invoice
        ("6000000", "100000", True, "100000", "5000.00"),  # no valid PAN: 5%
        ("0", "100000", False, "0", "0.00"),
    ],
)
def test_tax_on_goods_applies_to_the_part_of_the_year_above_fifty_lakh(
    before: str, taxable: str, pan_bad: bool, base: str, amount: str
) -> None:
    got = rules.tds_on_goods(D(before), D(taxable), date(2026, 11, 6), pan_inoperative=pan_bad)
    assert (got.base, got.amount) == (D(base), D(amount))


def test_the_tax_section_is_quoted_in_the_new_act_form_from_april_2026() -> None:
    assert rules.tds_on_goods(D(0), D(1), date(2026, 3, 31), pan_inoperative=False).section == "194Q"
    assert rules.tds_on_goods(D(0), D(1), date(2026, 4, 1), pan_inoperative=False).section == "393(1) Sl.8(ii) [194Q]"
    assert (
        rules.quarter_label(date(2026, 4, 1)) == "2026-27 Q1" and rules.quarter_label(date(2027, 3, 31)) == "2026-27 Q4"
    )
    assert rules.quarter_label(date(2026, 10, 31)) == "2026-27 Q3"


RATES = [(date(2025, 4, 9), D("6.25")), (date(2025, 6, 6), D("5.75")), (date(2026, 10, 7), D("5.75"))]


def test_the_bank_rate_is_the_one_in_force_on_the_due_date() -> None:
    assert rules.bank_rate(date(2025, 6, 5), RATES) == D("6.25")
    assert rules.bank_rate(date(2025, 6, 6), RATES) == D("5.75")
    assert rules.bank_rate(date(2020, 1, 1), RATES) == D("6.25")  # before the first row: the first row


def test_interest_to_a_small_supplier_is_three_times_the_bank_rate_compounded_monthly_and_nothing_if_paid_on_time() -> (
    None
):
    assert rules.msme_interest(D("100000"), date(2026, 11, 6), date(2026, 11, 6), RATES) == D("0")
    one_month = rules.msme_interest(D("100000"), date(2026, 1, 1), date(2026, 1, 31), RATES)  # 30 days late
    assert D("1400") < one_month < D("1500")  # 5.75% x 3 = 17.25% a year, about 1.43% for a month
    assert rules.msme_interest(D("100000"), date(2026, 1, 1), date(2026, 3, 2), RATES) > one_month * 2


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


# ---- booking, what is payable -----------------------------------------------------------------


def test_a_booked_invoice_gets_the_small_supplier_due_date(ledger: Session, outbox: Outbox) -> None:  # noqa: F811
    _small(ledger, agreed=20)
    po_no, _ = _goods(ledger)

    done = _invoice(ledger, po_no)

    assert _row(ledger, done.no)["due_date"] == date(2026, 11, 26)  # accepted on the 6th: 20 days, sooner than net 30


def test_tax_at_source_is_held_back_only_on_the_part_of_the_year_above_fifty_lakh(
    ledger: Session, outbox: Outbox
) -> None:  # noqa: F811
    ledger.execute(
        text(
            "insert into invoice (no, supplier_code, supplier_invoice_no, dedupe_key, invoice_date, fy, received_on, taxable, total, "
            "status, booked_at) values ('PI-OLD', 'S1', 'OLD-1', 'OLD1', '2026-06-01', '2026-27', '2026-06-01', 4999900, 4999900, 'paid', now())"
        )
    )
    ledger.execute(
        text(
            "insert into tds_entry (invoice_no, section, base, rate, amount, booked_on, quarter) values ('PI-OLD', '194Q', 0, 0.1, 0, '2026-06-01', '2026-27 Q1')"
        )
    )
    ledger.commit()
    po_no, _ = _goods(ledger, qty="10", price="100")  # 1,000 taxable: 4,999,900 + 1,000 is 900 over

    done = _invoice(ledger, po_no)

    assert ledger.execute(text("select base, rate, amount, quarter from tds_entry where invoice_no = :n"), {"n": done.no}).one() == (
        D("900.00"), D("0.100"), D("0.90"), "2026-27 Q3",
    )  # fmt: skip
    assert _row(ledger, done.no)["tds_amount"] == D("0.90")


def test_what_is_payable_is_the_total_less_tax_and_credit_notes_and_interest_is_added_when_late(
    ledger: Session, outbox: Outbox
) -> None:  # noqa: F811
    _small(ledger, category="micro")
    ledger.execute(text("insert into ref_rbi_bank_rate (effective_from, rate_pct) values ('2025-01-01', 6.5)"))
    ledger.commit()
    po_no, _ = _goods(ledger, qty="10")
    inv = _invoice(ledger, po_no)
    credit = invoices_credit(ledger, inv.no, po_no, "1", "100")

    on_time = payment.payable(ledger, inv.no, date(2026, 12, 6))
    late = payment.payable(ledger, inv.no, date(2027, 1, 5))

    assert (on_time.total, on_time.tds, on_time.credit, on_time.net, on_time.interest) == (
        D("1180.00"),
        D("0.00"),
        D("118.00"),
        D("1062.00"),
        D("0"),
    )
    assert on_time.credit_notes == [credit]
    assert late.interest > 0 and late.net == D("1062.00")


def invoices_credit(shop_: Session, ref: str, po_no: str, qty: str, price: str) -> str:
    from p2p.invoices import service as invoices

    done = invoices.enter_invoice(
        shop_,
        invoices.InvoiceIn(
            supplier_code="S1", supplier_invoice_no="CN-9", invoice_date=TODAY, type="credit_note", ref_invoice_no=ref,
            lines=[invoices.InvLine(po_no, 1, D(qty), D(price))], supplier_gstin=S1_GSTIN, buyer_gstin="29BBBBB0000B1Z5",
        ),
        1, as_of=TODAY,
    )  # fmt: skip
    return done.no


# ---- payment runs -----------------------------------------------------------------------------


def _people(shop_: Session) -> tuple[int, int, int]:
    slug, sfx = shop_.info["slug"], shop_.info["sfx"]
    finance = users.create_user(slug, f"fin{sfx}@x.example", PASSWORD, "A Finance", "Finance")
    owner = users.create_user(slug, f"own{sfx}@x.example", PASSWORD, "An Owner", "Approver")
    ap = users.create_user(slug, f"ap{sfx}@x.example", PASSWORD, "An AP", "AP")
    return finance, owner, ap


def _two_suppliers(ledger: Session) -> tuple[str, str, dict[str, int]]:
    """Two approved invoices: one of a small supplier (S1) and one of S2 for an order given to it."""
    _small(ledger)
    first, logins = _goods(ledger, qty="10", price="100")
    inv1 = _invoice(ledger, first, "10", "100", number="S1-1")
    return first, inv1.no, logins


def test_a_run_pays_what_is_due_in_the_window_small_suppliers_first_and_goes_for_approval(
    ledger: Session, outbox: Outbox
) -> None:  # noqa: F811
    finance, _, _ = _people(ledger)
    po_no, inv_no, _ = _two_suppliers(ledger)
    ledger.execute(text("update invoice set due_date = '2026-11-12' where no = :n"), {"n": inv_no})
    ledger.commit()

    assert payment.due_invoices(ledger, date(2026, 11, 1), window_days=7) == []  # due on the 12th: outside the window
    due = payment.due_invoices(ledger, date(2026, 11, 6), window_days=7)
    assert [d["no"] for d in due] == [inv_no]

    run = payment.propose_run(ledger, date(2026, 11, 6), finance, window_days=7)

    assert run == "PR-2627-0001"
    row = ledger.execute(text("select status, total, proposed_by, proposal_id from payment_run")).one()
    assert (row[0], row[1], row[2]) == ("proposed", D("1180.00"), str(finance))
    pay = ledger.execute(text("select no, supplier_code, amount, tds_amount, mode from payment")).one()
    assert pay == ("PAY-2627-000001", "S1", D("1180.00"), D("0.00"), "neft")
    assert ledger.execute(text("select invoice_no, amount from payment_allocation")).one() == (inv_no, D("1180.00"))
    assert (
        "Pay 1 invoice(s) to 1 supplier(s), 1,180.00 in all"
        in ledger.execute(text("select summary from proposal where id = :p"), {"p": row[3]}).scalar_one()
    )
    with pytest.raises(InvoiceError, match="no approved invoice is due"):
        payment.propose_run(ledger, date(2026, 11, 6), finance, window_days=7)  # already in a run
    assert (
        ledger.execute(text("select count(*) from audit_event where action = 'payment_run.propose'")).scalar_one() == 1
    )
    assert po_no


def test_a_blocked_or_unapproved_invoice_is_never_in_a_run(ledger: Session, outbox: Outbox) -> None:  # noqa: F811
    finance, _, _ = _people(ledger)
    po_no, _ = _goods(ledger, qty="10", price="1000")
    blocked = _invoice(ledger, po_no, "10", "1060", number="B")
    assert blocked.status == "blocked"
    ledger.execute(text("update invoice set due_date = '2026-11-01'"))
    ledger.commit()

    with pytest.raises(InvoiceError, match="no approved invoice is due"):
        payment.propose_run(ledger, date(2026, 11, 6), finance)


def test_small_suppliers_come_first_and_a_credit_note_is_netted_in_the_same_payment(
    ledger: Session, outbox: Outbox
) -> None:  # noqa: F811
    finance, _, _ = _people(ledger)
    first, inv1, logins = _two_suppliers(ledger)
    second, _ = _goods(
        ledger, qty="12", price="100", logins=logins
    )  # another order, given to S2 (not a small supplier)
    ledger.execute(text("update po set supplier_code = 'S2' where no = :p"), {"p": second})
    ledger.commit()
    from p2p.invoices import service as invoices

    inv2 = invoices.enter_invoice(
        ledger,
        invoices.InvoiceIn("S2", "S2-1", TODAY, [invoices.InvLine(second, 1, D(10), D(100))], supplier_gstin=None, buyer_gstin="29BBBBB0000B1Z5"),
        1, as_of=TODAY,
    )  # fmt: skip
    ledger.execute(text("update supplier set gstin = null where code = 'S2'"))
    ledger.commit()
    credit = invoices_credit(ledger, inv1, first, "1", "100")
    ledger.execute(text("update invoice set due_date = '2026-11-20' where type = 'invoice'"))
    ledger.commit()

    order = [d["supplier_code"] for d in payment.due_invoices(ledger, date(2026, 11, 20), window_days=0)]
    assert order[0] == "S1"  # the small supplier is paid first
    assert inv2.no  # the second supplier's invoice is on file too

    run = payment.propose_run(ledger, date(2026, 11, 20), finance, window_days=0)

    s1 = ledger.execute(
        text("select no, amount from payment where run_no = :r and supplier_code = 'S1'"), {"r": run}
    ).one()
    assert s1[1] == D("1062.00")  # 1,180 less the 118 credit note
    allocations = ledger.execute(
        text("select invoice_no, amount from payment_allocation where payment_no = :p order by amount"), {"p": s1[0]}
    ).all()
    assert allocations == [(credit, D("-118.00")), (inv1, D("1180.00"))]


def test_the_owner_approves_the_run_and_the_person_who_proposed_it_cannot(ledger: Session, outbox: Outbox) -> None:  # noqa: F811
    finance, owner, ap = _people(ledger)
    _, inv_no, _ = _two_suppliers(ledger)
    ledger.execute(text("update invoice set due_date = '2026-11-12' where no = :n"), {"n": inv_no})
    ledger.commit()
    run = payment.propose_run(ledger, date(2026, 11, 6), finance)
    pid = ledger.execute(text("select proposal_id from payment_run where no = :n"), {"n": run}).scalar_one()

    with pytest.raises(permissions.PermissionDenied):
        proposals.approve(ledger, pid, finance)  # the proposer may not approve their own run
    with pytest.raises(permissions.PermissionDenied):
        proposals.approve(ledger, pid, ap)  # AP has no right to approve payments
    assert ledger.execute(text("select status from payment_run")).scalar_one() == "proposed"

    proposals.approve(ledger, pid, owner)

    row = ledger.execute(text("select status, approved_by, approved_at is not null from payment_run")).one()
    assert row == ("approved", str(owner), True)
    assert (
        ledger.execute(text("select count(*) from audit_event where action = 'payment_run.approve'")).scalar_one() == 1
    )


def test_the_bank_file_is_made_only_for_an_approved_run_and_says_exactly_what_was_approved(
    ledger: Session, outbox: Outbox
) -> None:  # noqa: F811
    finance, owner, _ = _people(ledger)
    _, inv_no, _ = _two_suppliers(ledger)
    ledger.execute(text("update invoice set due_date = '2026-11-12' where no = :n"), {"n": inv_no})
    ledger.commit()
    run = payment.propose_run(ledger, date(2026, 11, 6), finance)
    with pytest.raises(InvoiceError, match="only for an approved run"):
        payment.generate_file(ledger, run, finance)
    proposals.approve(ledger, ledger.execute(text("select proposal_id from payment_run")).scalar_one(), owner)

    file = payment.generate_file(ledger, run, finance)

    lines = file.strip().split("\n")
    assert lines[0] == "payment_no,beneficiary,account_no,ifsc,amount,mode,reference"
    assert lines[1] == "PAY-2627-000001,Alpha Traders,99S1,HDFC0000002,1180.00,NEFT,PI-2627-000001"
    assert len(lines) == 2 and ledger.execute(text("select status, file_text from payment_run")).one() == (
        "file_generated",
        file,
    )
    with pytest.raises(InvoiceError, match="only for an approved run"):
        payment.generate_file(ledger, run, finance)  # not twice


def test_a_supplier_without_a_verified_bank_account_cannot_be_put_in_a_file(ledger: Session, outbox: Outbox) -> None:  # noqa: F811
    finance, owner, _ = _people(ledger)
    _, inv_no, _ = _two_suppliers(ledger)
    ledger.execute(text("update invoice set due_date = '2026-11-12' where no = :n"), {"n": inv_no})
    ledger.execute(text("update supplier_bank_account set verified = false where supplier_code = 'S1'"))
    ledger.commit()
    run = payment.propose_run(ledger, date(2026, 11, 6), finance)
    proposals.approve(ledger, ledger.execute(text("select proposal_id from payment_run")).scalar_one(), owner)

    with pytest.raises(InvoiceError, match="no verified bank account"):
        payment.generate_file(ledger, run, finance)

    ledger.rollback()
    assert ledger.execute(text("select status from payment_run")).scalar_one() == "approved"


def _paid_run(ledger: Session, finance: int, owner: int, due: str = "2026-11-12") -> str:
    ledger.execute(text("update invoice set due_date = :d where type = 'invoice'"), {"d": due})
    ledger.commit()
    run = payment.propose_run(ledger, date(2026, 11, 6), finance)
    proposals.approve(
        ledger,
        ledger.execute(text("select proposal_id from payment_run where no = :n"), {"n": run}).scalar_one(),
        owner,
    )
    payment.generate_file(ledger, run, finance)
    return run


def test_when_the_bank_has_paid_the_invoices_are_settled_the_case_is_closed_and_the_supplier_is_told(
    ledger: Session, outbox: Outbox
) -> None:  # noqa: F811
    finance, owner, _ = _people(ledger)
    po_no, inv_no, _ = _two_suppliers(ledger)
    run = _paid_run(ledger, finance, owner)
    with pytest.raises(InvoiceError, match="does not exist"):
        payment.mark_paid(ledger, "PR-0000", finance, paid_on=TODAY)
    outbox.sent.clear()

    closed = payment.mark_paid(ledger, run, finance, paid_on=date(2026, 11, 12))

    assert closed == [po_no]
    assert (_row(ledger, inv_no)["status"], _row(ledger, inv_no)["paid_on"]) == ("paid", date(2026, 11, 12))
    assert ledger.execute(text("select status from payment_run")).scalar_one() == "paid"
    utr, paid_on = ledger.execute(text("select utr, paid_on from payment")).one()
    assert utr.startswith("UTR261112") and paid_on == date(2026, 11, 12)
    assert ledger.execute(text("select status from po_line")).scalar_one() == "closed"
    assert ledger.execute(text("select status from po")).scalar_one() == "closed"
    assert ledger.execute(text("select gl_code is null, amount, period from budget_actual")).one()[1:] == (
        D("1000.00"),
        "2026-11",
    )
    (mail,) = outbox.sent
    assert mail["to"] == "s1@supplier.example" and mail["subject"].startswith("Payment advice PAY-")
    assert (
        "Rs 1,180.00" in mail["body"] and utr in mail["body"] and "INV-" not in mail["body"] or "S1-1" in mail["body"]
    )
    assert ledger.execute(text("select remittance_sent_at is not null from payment")).scalar_one() is True
    with pytest.raises(InvoiceError, match="only a run whose bank file was made"):
        payment.mark_paid(ledger, run, finance, paid_on=date(2026, 11, 13))  # not twice
    assert (
        ledger.execute(
            text("select count(*) from audit_event where action in ('payment_run.paid', 'po.close')")
        ).scalar_one()
        == 2
    )


def test_an_order_with_goods_still_to_come_is_not_closed_when_what_was_invoiced_is_paid(
    ledger: Session, outbox: Outbox
) -> None:  # noqa: F811
    finance, owner, _ = _people(ledger)
    po_no, _ = _goods(ledger, qty="10", received="6")
    _invoice(ledger, po_no, "6", "100", number="P-1")
    run = _paid_run(ledger, finance, owner)

    closed = payment.mark_paid(ledger, run, finance, paid_on=date(2026, 11, 12))

    assert closed == []
    assert ledger.execute(text("select status from po_line")).scalar_one() == "part_received"
    assert ledger.execute(text("select status from po")).scalar_one() == "part_received"


def test_a_failed_remittance_email_does_not_undo_the_payment(ledger: Session, outbox: Outbox) -> None:  # noqa: F811
    finance, owner, _ = _people(ledger)
    _, inv_no, _ = _two_suppliers(ledger)
    run = _paid_run(ledger, finance, owner)
    outbox.refuse = {"s1@supplier.example"}

    payment.mark_paid(ledger, run, finance, paid_on=date(2026, 11, 12))

    assert _row(ledger, inv_no)["status"] == "paid"
    assert ledger.execute(text("select remittance_sent_at from payment")).scalar_one() is None


# ---- the screens' API -------------------------------------------------------------------------


def test_finance_plans_the_owner_approves_and_finance_pays_over_the_api(ledger: Session, outbox: Outbox) -> None:  # noqa: F811
    finance, owner, ap = _people(ledger)
    _, inv_no, _ = _two_suppliers(ledger)
    ledger.execute(text("update invoice set due_date = '2026-11-12' where no = :n"), {"n": inv_no})
    ledger.commit()
    slug = ledger.info["slug"]
    h_fin, h_ap = _headers(slug, finance, "Finance"), _headers(slug, ap, "AP")

    preview = client.get("/api/payment-runs/due?on=2026-11-06&window=7", headers=h_fin).json()
    assert [(p["invoice"], p["msme"], p["net"]) for p in preview] == [(inv_no, True, "1180.00")]
    assert client.post("/api/payment-runs", json={"run_date": "2026-11-06"}, headers=h_ap).status_code == 403
    assert client.post("/api/payment-runs", json={"run_date": "2026-11-06"}).status_code == 401
    assert client.post("/api/payment-runs", json={"run_date": "soon"}, headers=h_fin).status_code == 422

    planned = client.post("/api/payment-runs", json={"run_date": "2026-11-06", "window_days": 7}, headers=h_fin)
    assert planned.status_code == 200
    run = planned.json()["run"]
    assert (
        client.post("/api/payment-runs", json={"run_date": "2026-11-06"}, headers=h_fin).status_code == 409
    )  # nothing left due
    page = client.get(f"/api/payment-runs/{run}", headers=h_fin).json()
    assert (page["status"], page["total"], page["can_file"], page["can_mark_paid"]) == (
        "proposed",
        "1180.00",
        False,
        False,
    )
    assert page["payments"][0]["invoices"][0]["id"] == inv_no
    assert client.post(f"/api/payment-runs/{run}/file", headers=h_fin).status_code == 409  # not approved yet

    proposals.approve(ledger, page["approval"]["id"], owner)

    assert client.get(f"/api/payment-runs/{run}", headers=h_fin).json()["can_file"] is True
    assert client.get(f"/api/payment-runs/{run}", headers=h_ap).json()["can_file"] is False
    made = client.post(f"/api/payment-runs/{run}/file", headers=h_fin)
    assert made.status_code == 200 and "PAY-2627-000001" in made.json()["file"]
    assert "PAY-2627-000001" in client.get(f"/api/payment-runs/{run}/file", headers=h_ap).json()["file"]
    tomorrow = (business_today() + timedelta(days=1)).isoformat()
    future = client.post(f"/api/payment-runs/{run}/paid", json={"paid_on": tomorrow}, headers=h_fin)
    assert future.status_code == 422  # the bank cannot have paid on a day that has not come
    today = business_today().isoformat()
    paid = client.post(f"/api/payment-runs/{run}/paid", json={"paid_on": today}, headers=h_fin)
    assert paid.status_code == 200 and paid.json()["status"] == "paid" and len(paid.json()["closed_orders"]) == 1
    assert client.get(f"/api/payment-runs/{run}", headers=h_fin).json()["payments"][0]["paid_on"] == today
    assert [r["id"] for r in client.get("/api/payment-runs", headers=h_fin).json()] == [run]
    assert client.get("/api/payment-runs/NOPE", headers=h_fin).status_code == 404
    assert client.post("/api/payment-runs/NOPE/paid", json={}, headers=h_fin).status_code == 404
