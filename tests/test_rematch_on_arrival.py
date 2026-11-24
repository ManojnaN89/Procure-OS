"""A quantity or service hold is matched again when the goods arrive (block 11, 3.5).

The receipt that raises the accepted quantity calls `invoices.rematch.after_accepted` after it commits: invoices held
for quantity or an unconfirmed service on those lines are matched again, oldest received first, and only what was
accepted can pass. A price block is never cleared by an arrival.
"""

from datetime import date
from decimal import Decimal

from sqlalchemy import text
from sqlalchemy.orm import Session
from test_invoices import _goods, _invoice, ledger  # noqa: F401 - fixtures
from test_orders import shop  # noqa: F401 - fixtures
from test_receiving import _receive, _sent, store  # noqa: F401 - fixtures
from test_service_entry import svc  # noqa: F401 - fixtures
from test_sourcing import Outbox  # noqa: F401 - fixtures

from p2p.formulas import matching as m
from p2p.invoices import service as invoices
from p2p.receiving import goods

D = Decimal
TODAY = date(2026, 11, 6)


def _row(world: Session, no: str) -> dict:
    world.expire_all()
    return dict(
        world.execute(text("select * from invoice where no = :n"), {"n": no})
        .mappings()
        .one()
    )


def test_given_an_invoice_held_for_quantity_when_the_rest_is_received_then_it_is_matched_again(
    ledger: Session, outbox: Outbox
) -> None:  # noqa: F811
    po_no, _ = _goods(ledger, qty="200", price="100", received="120")
    taken = _invoice(ledger, po_no, "10", "100", number="B")
    held = _invoice(
        ledger, po_no, "190", "100", number="E"
    )  # available 120 - 10 = 110, so 80 over

    assert taken.status == "approved"
    assert (held.status, held.reasons) == ("blocked", ["qty_over_received"])

    posted = _receive(ledger, po_no, "80")  # R2: available 200 - 10 = 190

    assert posted.rematched == [held.no]
    assert _row(ledger, held.no)["status"] == "approved"
    assert (
        "goods accepted on"
        in ledger.execute(
            text(
                "select reason from audit_event where action = 'invoice.match' and entity_id = :n order by id desc limit 1"
            ),
            {"n": held.no},
        ).scalar_one()
    )


def test_a_second_receipt_approves_an_invoice_only_up_to_what_is_accepted(
    ledger: Session, outbox: Outbox
) -> None:  # noqa: F811
    po_no, _ = _goods(ledger, qty="1000", price="100", received="400")
    big = _invoice(ledger, po_no, "1000", "100", number="I1")  # 600 over
    small = _invoice(ledger, po_no, "400", "100", number="I2")

    assert (big.status, big.reasons) == ("blocked", ["qty_over_received"])
    assert small.status == "approved"

    posted = _receive(ledger, po_no, "600")

    assert posted.rematched == []  # available 1000 - 400 = 600, still short of 1000
    row = _row(ledger, big.no)
    assert row["status"] == "blocked" and "400" in row["block_reason"]
    assert ledger.execute(
        text("select qty_var from match_result where invoice_no = :n"), {"n": big.no}
    ).scalar_one() == D("400")


def test_goods_waiting_for_inspection_are_matched_again_when_the_inspection_accepts_them(
    ledger: Session, outbox: Outbox
) -> None:  # noqa: F811
    ledger.execute(text("update item set inspection_required = true where code = 'A'"))
    ledger.commit()
    po_no, _ = _sent(ledger, qty="10", price="100")
    _receive(
        ledger, po_no, "10"
    )  # the receipt waits for inspection: nothing is accepted yet
    held = _invoice(ledger, po_no, "10", "100", number="INSP")

    assert held.reasons == ["qty_over_received"]

    posted = goods.inspect(
        ledger, ledger.execute(text("select no from goods_receipt")).scalar_one(),
        [goods.Decision(1, D("10"), D("0"))], 1, as_of=TODAY,
    )  # fmt: skip

    assert posted.rematched == [held.no]
    assert _row(ledger, held.no)["status"] == "approved"


def test_the_oldest_invoice_waiting_is_matched_first(
    ledger: Session, outbox: Outbox
) -> None:  # noqa: F811
    po_no, _ = _goods(ledger, qty="10", price="100", received="5")
    old = _invoice(ledger, po_no, "8", "100", number="OLD")
    new = _invoice(ledger, po_no, "8", "100", number="NEW")

    posted = _receive(ledger, po_no, "5")

    assert posted.rematched == [old.no]
    assert _row(ledger, old.no)["status"] == "approved"
    assert _row(ledger, new.no)["status"] == "blocked"


def test_a_price_block_is_not_cleared_by_goods_arriving(
    ledger: Session, outbox: Outbox
) -> None:  # noqa: F811
    po_no, _ = _goods(ledger, qty="10", price="1000", received="5")
    held = _invoice(
        ledger, po_no, "10", "1060", number="P1"
    )  # price and quantity both out

    posted = _receive(ledger, po_no, "5")

    assert posted.rematched == []  # the quantity clears, the price does not
    row = _row(ledger, held.no)
    assert row["status"] == "blocked" and row["payment_block"] == "match"
    reasons = ledger.execute(
        text("select reasons from match_result where invoice_no = :n"), {"n": held.no}
    ).scalar_one()
    assert reasons == [m.PRICE]


def test_a_service_hold_is_matched_again_by_the_requesters_confirmation(
    svc: Session, outbox: Outbox
) -> None:  # noqa: F811
    from test_service_entry import _confirm, _service_po

    po_no, logins = _service_po(svc)
    held = _invoice(svc, po_no, user=logins["buyer"])
    assert held.reasons == [m.SERVICE]

    done = _confirm(svc, po_no, logins["keeper"], "pct", "100")

    assert done.rematched == [held.no]
    assert _row(svc, held.no)["status"] == "approved"
    assert (
        svc.execute(
            text("select count(*) from match_result where invoice_no = :n"),
            {"n": held.no},
        ).scalar_one()
        >= 1
    )


from test_sourcing import Outbox, db, outbox  # noqa: F401, E402 - fixtures
