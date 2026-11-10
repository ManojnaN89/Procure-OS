"""Advance payments against orders and their adjustment against invoices, early-payment discounts, and telling the people
concerned when an order is paid and closed."""

from collections.abc import Callable
from datetime import date
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session
from test_invoices import TODAY, _goods, _headers, _invoice, _row, ledger  # noqa: F401 - fixtures
from test_orders import shop  # noqa: F401 - fixtures
from test_payments import _people
from test_receiving import _receive, _sent, store  # noqa: F401 - fixtures
from test_sourcing import Outbox, db, outbox  # noqa: F401 - fixtures

from p2p.formulas import payment as rules
from p2p.invoices import payment
from p2p.main import app
from p2p.orders import dispatch
from p2p.platform import notifications, proposals, sod, users

D = Decimal
client = TestClient(app)


# ---- the rules --------------------------------------------------------------------------------


def test_an_advance_is_the_percentage_of_the_order_total_and_of_its_taxable_value() -> None:
    assert rules.advance_amounts(D("1180.00"), D("1000.00"), D("30")) == (D("354.00"), D("300.00"))
    assert rules.advance_amounts(D("1000.01"), D("847.46"), D("30")) == (
        D("300.00"),
        D("254.24"),
    )  # each rounded half up
    assert rules.advance_amounts(D("1180.00"), D("1000.00"), D("100")) == (D("1180.00"), D("1000.00"))


def test_an_invoice_takes_what_is_left_of_an_advance_in_total_and_in_taxable_value() -> None:
    assert rules.advance_use(D("354"), D("300"), D("1180"), D("1000")) == (D("354"), D("300"))
    assert rules.advance_use(D("354"), D("300"), D("118"), D("100")) == (
        D("118"),
        D("100"),
    )  # a small invoice takes less
    assert rules.advance_use(D("0"), D("0"), D("118"), D("100")) == (D("0"), D("0"))


@pytest.mark.parametrize(
    ("paid", "expected"),
    [
        (date(2026, 11, 6), "20.00"),  # the invoice date itself
        (date(2026, 11, 16), "20.00"),  # the tenth day: still earns it
        (date(2026, 11, 17), "0"),  # the eleventh: too late
        (date(2026, 11, 1), "20.00"),  # early is early
    ],
)
def test_the_discount_is_earned_up_to_and_including_the_last_day_of_its_window(paid: date, expected: str) -> None:
    assert rules.early_discount(D("1000"), D("2"), 10, date(2026, 11, 6), paid) == D(expected)


def test_no_discount_without_a_percentage_or_days_and_it_is_rounded_half_up() -> None:
    assert rules.early_discount(D("1000"), D("0"), 10, date(2026, 11, 6), date(2026, 11, 6)) == D(0)
    assert rules.early_discount(D("1000"), D("2"), 0, date(2026, 11, 6), date(2026, 11, 6)) == D(0)
    assert rules.early_discount(D("333.25"), D("1.5"), 15, date(2026, 11, 6), date(2026, 11, 6)) == D("5.00")  # 4.99875
    assert rules.discount_window_end(date(2026, 11, 6), 10) == date(2026, 11, 16)


# ---- a world with advance and discount terms --------------------------------------------------


@pytest.fixture
def terms(ledger: Session) -> Session:  # noqa: F811
    ledger.execute(
        text(
            "insert into payment_terms (code, name, days, baseline, advance_pct, discount_pct, discount_days) values "
            "('ADV30', '30% advance, balance Net 30', 30, 'invoice_date', 30, 0, 0), "
            "('ADV100', '100% advance', 0, 'invoice_date', 100, 0, 0), "
            "('2-10N30', '2% in 10 days else Net 30', 30, 'invoice_date', 0, 2, 10), "
            "('2-10R', '2% in 10 days of receipt', 30, 'receipt_date', 0, 2, 10), "
            "('ADV30D', '30% advance, 2% in 10 days', 30, 'invoice_date', 30, 2, 10)"
        )
    )
    ledger.commit()
    return ledger


def _terms(shop_: Session, code: str) -> None:
    """The supplier's terms, and the terms of the orders made from now on (an order takes them from its contract or quote,
    so the test sets them on the order itself)."""
    shop_.execute(text("update supplier set payment_terms_code = :c where code = 'S1'"), {"c": code})
    shop_.info["terms"] = code
    shop_.commit()


def _sent_on_terms(
    shop_: Session, qty: str = "10", price: str = "100", logins: dict[str, int] | None = None
) -> tuple[str, dict[str, int]]:
    po_no, logins = _sent(shop_, qty=qty, price=price, logins=logins)
    shop_.execute(text("update po set payment_terms_code = :c where no = :n"), {"c": shop_.info["terms"], "n": po_no})
    shop_.commit()
    return po_no, logins


def _acknowledged(
    shop_: Session, qty: str = "10", price: str = "100", logins: dict[str, int] | None = None
) -> tuple[str, dict[str, int]]:
    po_no, logins = _sent_on_terms(shop_, qty=qty, price=price, logins=logins)
    dispatch.acknowledge(shop_, po_no, sod.user(1), promised=None, note=None)
    return po_no, logins


def _pay(shop_: Session, run: str, finance: int, owner: int, paid_on: date, *, tenant: str | None = None) -> list[str]:
    pid = shop_.execute(text("select proposal_id from payment_run where no = :n"), {"n": run}).scalar_one()
    proposals.approve(shop_, pid, owner)
    payment.generate_file(shop_, run, finance)
    return payment.mark_paid(shop_, run, finance, paid_on=paid_on, tenant=tenant)


def _advance_paid(shop_: Session, po_no: str, finance: int, owner: int, on: date = date(2026, 11, 6)) -> str:
    run = payment.propose_run(shop_, on, finance, advances_only=True, po_nos=[po_no])
    _pay(shop_, run, finance, owner, on)
    return run


# ---- advances ---------------------------------------------------------------------------------


def test_an_order_is_advanced_once_the_supplier_has_acknowledged_it_and_only_once(
    terms: Session, outbox: Outbox
) -> None:  # noqa: F811
    _terms(terms, "ADV30")
    finance, owner, _ = _people(terms)
    po_no, _ = _sent_on_terms(terms)
    assert payment.due_advances(terms) == []  # sent, not acknowledged: nothing is advanced yet

    dispatch.acknowledge(terms, po_no, sod.user(1), promised=None, note=None)
    (due,) = payment.due_advances(terms)
    assert (due["no"], due["advance_pct"]) == (po_no, 30)

    run = payment.propose_run(terms, TODAY, finance, advances_only=True)

    pay = terms.execute(
        text("select type, po_no, amount, tds_amount, reference from payment where run_no = :r"), {"r": run}
    ).one()
    assert pay == ("advance", po_no, D("354.00"), D("0.00"), po_no)
    assert terms.execute(text("select gross, taxable, used_gross, paid_on from advance")).one() == (
        D("354.00"),
        D("300.00"),
        D("0.00"),
        None,
    )
    assert (
        terms.execute(text("select count(*) from payment_allocation")).scalar_one() == 0
    )  # an advance settles no invoice
    assert "Pay 1 advance(s), 354.00 in all" in terms.execute(text("select summary from proposal")).scalar_one()
    assert payment.due_advances(terms) == []  # already in a run
    pid = terms.execute(text("select proposal_id from payment_run")).scalar_one()
    proposals.approve(terms, pid, owner)  # nothing is paid until the owner approves
    assert terms.execute(text("select paid_on from advance")).scalar_one() is None


def test_an_order_whose_terms_ask_for_no_advance_is_never_advanced(terms: Session, outbox: Outbox) -> None:  # noqa: F811
    _terms(terms, "2-10N30")
    _acknowledged(terms)

    assert payment.due_advances(terms) == []


def test_an_advance_that_was_paid_is_taken_off_the_invoices_of_the_order(terms: Session, outbox: Outbox) -> None:  # noqa: F811
    _terms(terms, "ADV30")
    finance, owner, _ = _people(terms)
    po_no, _ = _acknowledged(terms)
    _advance_paid(terms, po_no, finance, owner)
    assert terms.execute(text("select paid_on from advance")).scalar_one() == date(2026, 11, 6)
    _receive(terms, po_no, "10")

    first = _invoice(terms, po_no, "6", "100", number="A-1")  # 708 with GST: the advance of 354 is taken off
    second = _invoice(terms, po_no, "4", "100", number="A-2")  # nothing is left of the advance

    row = _row(terms, first.no)
    assert (row["advance_used"], row["advance_taxable_used"], row["total"]) == (D("354.00"), D("300.00"), D("708.00"))
    assert terms.execute(
        text("select amount, taxable from advance_adjustment where invoice_no = :i"), {"i": first.no}
    ).one() == (D("354.00"), D("300.00"))
    assert _row(terms, second.no)["advance_used"] == D("0.00")
    assert terms.execute(text("select used_gross, used_taxable from advance")).one() == (D("354.00"), D("300.00"))
    assert payment.payable(terms, first.no, TODAY).net == D("354.00")  # 708 less the 354 already paid
    assert payment.payable(terms, second.no, TODAY).net == D("472.00")


def test_a_small_invoice_takes_only_what_it_can_and_the_rest_waits_for_the_next(terms: Session, outbox: Outbox) -> None:  # noqa: F811
    _terms(terms, "ADV30")
    finance, owner, _ = _people(terms)
    po_no, _ = _acknowledged(terms)
    _advance_paid(terms, po_no, finance, owner)
    _receive(terms, po_no, "10")

    small = _invoice(terms, po_no, "1", "100", number="A-1")  # 118 in all
    big = _invoice(terms, po_no, "9", "100", number="A-2")  # 1,062: takes the remaining 236

    assert _row(terms, small.no)["advance_used"] == D("118.00")
    assert _row(terms, big.no)["advance_used"] == D("236.00")
    assert terms.execute(text("select sum(amount) from advance_adjustment")).scalar_one() == D("354.00")


def test_an_advance_paid_after_the_invoice_arrived_is_not_taken_off_it(terms: Session, outbox: Outbox) -> None:  # noqa: F811
    _terms(terms, "ADV30")
    finance, owner, _ = _people(terms)
    po_no, _ = _acknowledged(terms)
    _receive(terms, po_no, "10")
    invoice = _invoice(terms, po_no, "10", "100", number="A-1", on=date(2026, 11, 6))  # booked before any advance
    _advance_paid(terms, po_no, finance, owner, on=date(2026, 11, 9))

    assert _row(terms, invoice.no)["advance_used"] == D("0.00")
    assert terms.execute(text("select count(*) from advance_adjustment")).scalar_one() == 0

    # an invoice that was held up and is booked only now (after the advance) still arrived before it: nothing is taken off
    terms.execute(text("delete from tds_entry where invoice_no = :i"), {"i": invoice.no})
    terms.execute(text("update invoice set booked_at = null where no = :i"), {"i": invoice.no})
    payment.book_invoice(terms, invoice.no)
    assert _row(terms, invoice.no)["advance_used"] == D("0.00")
    assert terms.execute(text("select count(*) from advance_adjustment")).scalar_one() == 0


def test_a_run_pays_an_invoice_less_the_advance_already_paid(terms: Session, outbox: Outbox) -> None:  # noqa: F811
    _terms(terms, "ADV30")
    finance, owner, _ = _people(terms)
    po_no, _ = _acknowledged(terms)
    _advance_paid(terms, po_no, finance, owner)
    _receive(terms, po_no, "10")
    invoice = _invoice(terms, po_no, "10", "100", number="A-1")  # 1,180; the advance of 354 is taken off
    terms.execute(text("update invoice set due_date = '2026-11-12'"))
    terms.commit()

    run = payment.propose_run(terms, date(2026, 11, 6), finance, include_advances=False)

    assert terms.execute(
        text("select amount, discount from payment_allocation where invoice_no = :i"), {"i": invoice.no}
    ).one() == (
        D("826.00"),
        D("0.00"),
    )
    assert terms.execute(text("select amount from payment where run_no = :r"), {"r": run}).scalar_one() == D("826.00")
    assert (
        "826.00 in all"
        in terms.execute(text("select summary from proposal order by created_at desc limit 1")).scalar_one()
    )


def test_a_full_advance_leaves_nothing_to_send_the_bank_for_the_invoice_but_settles_it(
    terms: Session, outbox: Outbox
) -> None:  # noqa: F811
    _terms(terms, "ADV100")
    finance, owner, _ = _people(terms)
    po_no, _ = _acknowledged(terms)
    _advance_paid(terms, po_no, finance, owner)
    _receive(terms, po_no, "10")
    invoice = _invoice(terms, po_no, "10", "100", number="A-1")
    terms.execute(text("update invoice set due_date = '2026-11-12'"))
    terms.commit()

    assert payment.payable(terms, invoice.no, date(2026, 11, 6)).net == D("0")
    run = payment.propose_run(terms, date(2026, 11, 6), finance)
    pid = terms.execute(text("select proposal_id from payment_run where no = :n"), {"n": run}).scalar_one()
    proposals.approve(terms, pid, owner)
    file = payment.generate_file(terms, run, finance)

    assert (
        file.strip().split("\n")[0].startswith("payment_no") and len(file.strip().split("\n")) == 1
    )  # no line for a zero payment
    payment.mark_paid(terms, run, finance, paid_on=date(2026, 11, 6))
    assert _row(terms, invoice.no)["status"] == "paid"


def test_tax_at_source_on_an_advance_and_on_the_invoice_together_equal_what_the_invoice_alone_would_have_cost(
    terms: Session, outbox: Outbox
) -> None:  # noqa: F811
    _terms(terms, "ADV30")
    terms.execute(
        text(
            "insert into invoice (no, supplier_code, supplier_invoice_no, dedupe_key, invoice_date, fy, received_on, taxable, total, "
            "status, booked_at) values ('PI-OLD', 'S1', 'OLD-1', 'OLD1', '2026-06-01', '2026-27', '2026-06-01', 4999900, 4999900, 'paid', now())"
        )
    )
    terms.execute(
        text(
            "insert into tds_entry (invoice_no, section, base, rate, amount, booked_on, quarter) values ('PI-OLD', '194Q', 0, 0.1, 0, '2026-06-01', '2026-27 Q1')"
        )
    )
    terms.commit()
    finance, owner, _ = _people(terms)
    po_no, _ = _acknowledged(terms)

    run = payment.propose_run(terms, date(2026, 11, 6), finance, advances_only=True, po_nos=[po_no])
    advance_tax = terms.execute(
        text("select base, amount, booked_on from tds_entry where payment_no is not null")
    ).one()
    assert advance_tax == (D("200.00"), D("0.20"), date(2026, 11, 6))  # 4,999,900 + 300 is 200 over the limit
    assert terms.execute(text("select amount, tds_amount from payment where run_no = :r"), {"r": run}).one() == (
        D("353.80"),
        D("0.20"),
    )
    _pay(terms, run, finance, owner, date(2026, 11, 9))
    assert terms.execute(text("select booked_on, quarter from tds_entry where payment_no is not null")).one() == (
        date(2026, 11, 9),
        "2026-27 Q3",
    )  # booked the day it was paid

    _receive(terms, po_no, "10")
    invoice = _invoice(terms, po_no, "10", "100", number="A-1", on=date(2026, 11, 10))

    # the invoice's taxable value is 1,000 of which the advance covered 300: only 700 is new, all of it above the limit
    assert terms.execute(text("select base, amount from tds_entry where invoice_no = :i"), {"i": invoice.no}).one() == (
        D("700.00"),
        D("0.70"),
    )
    assert _row(terms, invoice.no)["tds_amount"] == D(
        "0.70"
    )  # 0.20 + 0.70 is what 1,000 above the limit costs: 0.90 less the 0.0... as one


def test_services_carry_no_tax_at_source_of_the_goods_kind_and_are_still_paid(terms: Session, outbox: Outbox) -> None:  # noqa: F811
    terms.execute(text("update item set kind = 'service' where code = 'A'"))
    terms.commit()
    po_no, _ = _goods(terms)

    invoice = _invoice(terms, po_no)

    assert terms.execute(text("select count(*) from tds_entry")).scalar_one() == 0
    row = _row(terms, invoice.no)
    assert row["tds_amount"] == D("0.00") and row["status"] == "approved" and row["due_date"] is not None


# ---- early-payment discounts ------------------------------------------------------------------


def test_paying_within_the_window_takes_the_discount_on_the_taxable_value_and_leaves_the_tax(
    terms: Session, outbox: Outbox
) -> None:  # noqa: F811
    _terms(terms, "2-10N30")
    po_no, _ = _goods(terms)
    invoice = _invoice(terms, po_no, "10", "100", number="D-1", on=date(2026, 11, 6))

    inside = payment.payable(terms, invoice.no, date(2026, 11, 16))
    outside = payment.payable(terms, invoice.no, date(2026, 11, 17))

    assert (inside.discount, inside.window_end, inside.net) == (
        D("20.00"),
        date(2026, 11, 16),
        D("1160.00"),
    )  # 1,180 less 2% of 1,000
    assert (outside.discount, outside.net) == (D("0"), D("1180.00"))


def test_the_discount_counts_from_the_acceptance_date_when_the_terms_say_so(terms: Session, outbox: Outbox) -> None:  # noqa: F811
    _terms(terms, "2-10R")
    po_no, _ = _goods(terms)
    terms.execute(text("update goods_receipt set accepted_on = '2026-11-02'"))
    terms.commit()
    invoice = _invoice(terms, po_no, "10", "100", number="D-1", on=date(2026, 11, 6))

    assert payment.payable(terms, invoice.no, date(2026, 11, 12)).discount == D("20.00")  # the 10th day after the 2nd
    assert payment.payable(terms, invoice.no, date(2026, 11, 13)).discount == D("0")


def test_the_company_can_switch_discounts_off(terms: Session, outbox: Outbox) -> None:  # noqa: F811
    _terms(terms, "2-10N30")
    po_no, _ = _goods(terms)
    invoice = _invoice(terms, po_no, "10", "100", number="D-1", on=date(2026, 11, 6))
    terms.execute(text("insert into org_setting (key, value) values ('payment_take_discounts', 'false')"))
    terms.commit()

    assert payment.payable(terms, invoice.no, date(2026, 11, 7)).discount == D("0")


def test_a_run_pays_an_invoice_early_when_the_discount_would_be_lost_by_waiting_for_the_next_run(
    terms: Session, outbox: Outbox
) -> None:  # noqa: F811
    _terms(terms, "2-10N30")
    finance, owner, _ = _people(terms)
    po_no, _ = _goods(terms)
    invoice = _invoice(
        terms, po_no, "10", "100", number="D-1", on=date(2026, 11, 6)
    )  # due 6 Dec, discount until 16 Nov

    assert (
        payment.due_invoices(terms, date(2026, 11, 6), window_days=7) == []
    )  # the window runs to the 16th: no hurry yet
    early = payment.due_invoices(terms, date(2026, 11, 12), window_days=7)  # a run a week on would be too late
    assert [(d["no"], d["discount"]) for d in early] == [(invoice.no, D("20.00"))]

    run = payment.propose_run(terms, date(2026, 11, 12), finance, window_days=7)
    summary = terms.execute(text("select summary from proposal")).scalar_one()
    assert "saving 20.00 in early-payment discount" in summary and "1,160.00 in all" in summary
    assert terms.execute(text("select amount, discount from payment_allocation")).one() == (D("1180.00"), D("20.00"))
    assert terms.execute(text("select amount from payment")).scalar_one() == D("1160.00")
    _pay(terms, run, finance, owner, date(2026, 11, 12))

    assert _row(terms, invoice.no)["discount_taken"] == D("20.00") and _row(terms, invoice.no)["status"] == "paid"
    assert terms.execute(text("select sum(amount) from budget_actual")).scalar_one() == D(
        "980.00"
    )  # 1,000 less the discount earned
    (mail,) = outbox.sent[-1:]
    assert "less early-payment discount Rs 20.00" in mail["body"]


def test_with_discounts_off_the_run_waits_for_the_due_date(terms: Session, outbox: Outbox) -> None:  # noqa: F811
    _terms(terms, "2-10N30")
    po_no, _ = _goods(terms)
    _invoice(terms, po_no, "10", "100", number="D-1", on=date(2026, 11, 6))
    terms.execute(text("insert into org_setting (key, value) values ('payment_take_discounts', 'false')"))
    terms.commit()

    assert payment.due_invoices(terms, date(2026, 11, 12), window_days=7) == []


# ---- telling the people concerned -------------------------------------------------------------


def _paid_order(
    terms: Session,
    requester: str | None,
    buyer: str | None,
    tenant: str | None,
    before_paying: Callable[[], None] | None = None,
) -> str:
    finance, owner, _ = _people(terms)
    po_no, _ = _goods(terms)
    terms.execute(text("update request set requester = :r"), {"r": requester})
    terms.execute(text("update po set buyer = :b"), {"b": buyer})
    _invoice(terms, po_no, "10", "100", number="N-1")
    terms.execute(text("update invoice set due_date = '2026-11-12'"))
    terms.commit()
    run = payment.propose_run(terms, date(2026, 11, 6), finance)
    if before_paying:
        before_paying()
    _pay(terms, run, finance, owner, date(2026, 11, 12), tenant=tenant)
    return po_no


def _told(terms: Session) -> list[tuple[int, str]]:
    return [
        (r[0], r[1])
        for r in terms.execute(
            text("select user_id, link from notification where kind = 'order_paid' order by user_id")
        )
    ]


def test_the_requester_and_the_buyer_are_each_told_once_that_the_order_is_paid_and_closed(
    terms: Session, outbox: Outbox
) -> None:  # noqa: F811
    slug, sfx = terms.info["slug"], terms.info["sfx"]
    po_no = _paid_order(terms, "U4", "U5", slug)  # the keeper asked for it, the procurement head bought it

    told = _told(terms)
    keeper = users.user_id_by_email(slug, f"keeper{sfx}@x.example")
    buyer = users.user_id_by_email(slug, f"pur{sfx}@x.example")
    assert sorted(told) == sorted([(keeper, f"#/purchase-orders/{po_no}"), (buyer, f"#/purchase-orders/{po_no}")])
    title = terms.execute(text("select title from notification where user_id = :u"), {"u": keeper}).scalar_one()
    assert title == f"Order {po_no} is paid and closed"


def test_one_person_who_both_asked_and_bought_is_told_once(terms: Session, outbox: Outbox) -> None:  # noqa: F811
    _paid_order(terms, "U5", "U5", terms.info["slug"])

    assert len(_told(terms)) == 1


def test_nobody_is_told_about_an_order_that_is_not_closed_or_when_the_company_is_not_named(
    terms: Session, outbox: Outbox
) -> None:  # noqa: F811
    _paid_order(terms, "U4", "U5", None)

    assert _told(terms) == []


def test_a_notification_that_fails_never_undoes_the_payment(
    terms: Session, outbox: Outbox, monkeypatch: pytest.MonkeyPatch
) -> None:  # noqa: F811
    def broken(*args: object, **kwargs: object) -> int:
        raise RuntimeError("the notification service is down")

    _paid_order(
        terms,
        "U4",
        "U5",
        terms.info["slug"],
        before_paying=lambda: monkeypatch.setattr(
            notifications, "notify", broken
        ),  # only the payment's own notices fail
    )

    assert terms.execute(text("select status from invoice where type = 'invoice'")).scalar_one() == "paid"
    assert terms.execute(text("select status from payment_run")).scalar_one() == "paid"


# ---- the screens' API -------------------------------------------------------------------------


def test_the_plan_screen_lists_advances_and_discounts_and_the_run_shows_them(terms: Session, outbox: Outbox) -> None:  # noqa: F811
    finance, owner, _ = _people(terms)
    slug = terms.info["slug"]
    h_fin = _headers(slug, finance, "Finance")
    _terms(terms, "ADV30D")
    first, logins = _goods(terms)
    _invoice(terms, first, "10", "100", number="D-1", on=date(2026, 11, 6))
    second, _ = _acknowledged(terms, qty="12", logins=logins)  # S1 now has advance terms: a second order is advanced

    preview = client.get("/api/payment-runs/due?on=2026-11-12&window=7", headers=h_fin).json()

    kinds = [(p["kind"], p["po_no"], p["invoice"], p["net"]) for p in preview]
    assert kinds[0] == ("advance", second, None, "424.80")  # 30% of 1,416
    discounted = next(p for p in preview if p["kind"] == "invoice")
    assert (discounted["discount"], discounted["discount_until"], discounted["net"]) == (
        "20.00",
        "2026-11-16",
        "1160.00",
    )

    skipped = client.post(
        "/api/payment-runs", json={"run_date": "2026-11-12", "window_days": 7, "include_advances": False}, headers=h_fin
    )
    run = skipped.json()["run"]
    page = client.get(f"/api/payment-runs/{run}", headers=h_fin).json()
    assert [p["kind"] for p in page["payments"]] == ["invoice"]
    assert page["payments"][0]["invoices"][0]["discount"] == "20.00"
    with_advance = client.post("/api/payment-runs", json={"run_date": "2026-11-12", "window_days": 7}, headers=h_fin)
    page2 = client.get(f"/api/payment-runs/{with_advance.json()['run']}", headers=h_fin).json()
    assert [(p["kind"], p["po_no"]) for p in page2["payments"]] == [("advance", second)]
    assert page2["payments"][0]["invoices"] == []
    assert owner
