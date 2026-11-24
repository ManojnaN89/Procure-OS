"""No over-invoicing and no double release (block 11, 3.3; AT-05, DF-11).

Two matches for the last goods run one after the other on the order line's lock, so exactly one is approved. Two
price-blocked invoices for the same goods cannot both be released: the second release's re-check fails and leaves the
invoice blocked. A match racing a receipt reversal is block 9's test and is marked until that branch lands.
"""

import threading
import uuid
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session
from test_invoices import _goods, _invoice, ledger  # noqa: F401 - fixtures
from test_orders import shop  # noqa: F401 - fixtures
from test_receiving import _receive, store  # noqa: F401 - fixtures
from test_sourcing import PASSWORD, Outbox  # noqa: F401 - fixtures

from p2p.invoices import service as invoices
from p2p.platform import proposals, routing, tenants, users

D = Decimal
TODAY = date(2026, 11, 6)
S1_GSTIN, BUYER_GSTIN = "27AAAAA0000A1Z5", "29BBBBB0000B1Z5"


def _ap(world: Session) -> int:
    return users.create_user(
        world.info["slug"],
        f"ap{world.info['sfx']}@x.example",
        PASSWORD,
        "Ap Asha",
        "AP",
    )


def _invoice_data(po_no: str, number: str, qty: str, price: str) -> invoices.InvoiceIn:
    return invoices.InvoiceIn(
        supplier_code="S1", supplier_invoice_no=number, invoice_date=TODAY,
        lines=[invoices.InvLine(po_no, 1, D(qty), D(price))], supplier_gstin=S1_GSTIN, buyer_gstin=BUYER_GSTIN,
    )  # fmt: skip


def _entered_in_another_session(
    slug: str,
    po_no: str,
    number: str,
    qty: str,
    price: str,
    ap: int,
    gate: threading.Barrier,
) -> str:
    with Session(routing.engine_for(slug)) as db:
        gate.wait(timeout=30)
        return invoices.enter_invoice(db, _invoice_data(po_no, number, qty, price), ap, as_of=TODAY).status


def _thread_entry(
    results: list[str],
    slug: str,
    po_no: str,
    number: str,
    ap: int,
    gate: threading.Barrier,
) -> None:
    results.append(_entered_in_another_session(slug, po_no, number, "6", "100", ap, gate))


def test_two_invoices_for_the_last_goods_at_once_approve_exactly_one_and_never_exceed_what_was_accepted(
    ledger: Session, outbox: Outbox
) -> None:  # noqa: F811
    po_no, _ = _goods(ledger, qty="10", price="100")
    slug, ap = ledger.info["slug"], _ap(ledger)
    ledger.commit()

    for attempt in range(3):  # repeated on fresh order lines, as the eval does
        gate = threading.Barrier(2)
        results: list[str] = []
        threads = [
            threading.Thread(
                target=_thread_entry,
                args=(results, slug, po_no, f"T-{attempt * 2 + k}", ap, gate),
            )
            for k in range(2)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=60)

        assert sorted(results) == ["approved", "blocked"], results
        booked = ledger.execute(
            text(
                "select coalesce(sum(l.qty), 0) from invoice_line l join invoice i on i.no = l.invoice_no "
                "where l.po_no = :p and i.status in ('approved', 'part_paid', 'paid')"
            ),
            {"p": po_no},
        ).scalar_one()
        assert booked <= D("10")
        # give the next attempt room again
        if attempt < 2:
            ledger.execute(
                text("update invoice set status = 'cancelled' where status in ('approved', 'part_paid', 'paid')")
            )
            ledger.commit()


def test_two_price_blocked_invoices_for_the_same_goods_cannot_both_be_released(ledger: Session, outbox: Outbox) -> None:  # noqa: F811
    po_no, logins = _goods(ledger, qty="10", price="1000")
    ap = _ap(ledger)
    first = _invoice(ledger, po_no, "10", "1060", number="A", user=ap)
    second = _invoice(ledger, po_no, "10", "1060", number="B", user=ap)
    assert (first.status, second.status) == ("blocked", "blocked")
    one = invoices.request_release(ledger, first.no, "the price was agreed", ap)
    two = invoices.request_release(ledger, second.no, "the price was agreed too", ap)

    proposals.approve(ledger, one, logins["procurement"])

    ledger.expire_all()
    assert ledger.execute(text("select status from invoice where no = :n"), {"n": first.no}).scalar_one() == "approved"
    proposals.approve(ledger, two, logins["procurement"])  # the re-check fails and is recorded on the proposal
    ledger.expire_all()
    assert ledger.execute(text("select status from invoice where no = :n"), {"n": second.no}).scalar_one() == "blocked"
    row = ledger.execute(text("select execution, result from proposal where id = :i"), {"i": two}).one()
    assert row[0] == "failed" and "more than can be invoiced" in row[1]["error"]
    assert ledger.execute(text("select invoiced_qty from po_line")).scalar_one() == D("10")


def test_the_invoiced_quantity_never_exceeds_accepted_after_each_step_of_a_partial_flow(
    ledger: Session, outbox: Outbox
) -> None:  # noqa: F811
    po_no, _ = _goods(ledger, qty="200", price="100", received="120")
    earlier = _invoice(ledger, po_no, "10", "100", number="EARLY")
    blocked = _invoice(ledger, po_no, "190", "100", number="LATE")

    def invariant() -> None:
        ledger.expire_all()
        row = ledger.execute(
            text("select received_qty, invoiced_qty from po_line where po_no = :p"),
            {"p": po_no},
        ).one()
        assert row[1] <= row[0], row

    assert earlier.status == "approved" and blocked.status == "blocked"
    invariant()
    posted = _receive(ledger, po_no, "80")
    assert posted.rematched == [blocked.no]
    invariant()
    assert (
        ledger.execute(text("select status from invoice where no = :n"), {"n": blocked.no}).scalar_one() == "approved"
    )


@pytest.mark.xfail(
    reason="NEEDS block 9: receipt reversal (receiving/goods.reverse) lands with block 9",
    strict=True,
)
def test_a_match_and_a_receipt_reversal_on_one_line_run_one_after_the_other() -> None:
    """The reversal locks the same order line; the match then sees the lowered accepted quantity (H-2)."""
    pytest.xfail("NEEDS block 9: receipt reversal")


def test_two_companies_never_see_each_others_invoices(ledger: Session, outbox: Outbox) -> None:  # noqa: F811
    other = "c" + uuid.uuid4().hex[:8]
    assert tenants.create_tenant(other, load_reference=False).ok
    try:
        with Session(routing.engine_for(other)) as db:
            assert db.execute(text("select count(*) from invoice")).scalar_one() == 0
    finally:
        tenants.drop_tenant(other)


from test_sourcing import Outbox, db, outbox  # noqa: F401, E402 - fixtures
