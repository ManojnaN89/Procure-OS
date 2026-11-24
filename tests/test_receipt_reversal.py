"""Receipt reversals (task 3.6, 9.7): the whole receipt, its window, its refusals and what it gives back."""

import threading
from datetime import timedelta
from decimal import Decimal

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session
from test_orders import shop  # noqa: F401 - fixtures
from test_receiving import _line, _on_hand, _po_status, _receive, _sent, store  # noqa: F401 - fixtures
from test_sourcing import Outbox, db, outbox  # noqa: F401 - fixtures

from p2p.platform import permissions, proposals, routing, users
from p2p.receiving import reversal

D = Decimal
TODAY = __import__("test_receiving").TODAY


def _received(store: Session, qty: str = "10") -> tuple[str, str, dict[str, int]]:
    po_no, logins = _sent(store, qty=qty)
    done = _receive(store, po_no, qty)
    return po_no, done.no, logins


def _login(store: Session, code: str) -> int:
    email = store.execute(text("select email from org_user where code = :c"), {"c": code}).scalar_one()
    found = users.user_id_by_email(store.info["slug"], str(email))
    assert found is not None
    return found


def test_given_a_receipt_on_the_posting_day_when_the_store_reverses_it_then_stock_and_the_order_go_back(
    store: Session, outbox: Outbox
) -> None:  # noqa: F811
    po_no, gr, logins = _received(store)

    done = reversal.reverse_receipt(store, store.info["slug"], gr, 1, "Posted on the wrong order", as_of=TODAY)

    assert done == {"status": "reversed"}
    assert _on_hand(store) == D("0") and _line(store) == (D("0"), "open")
    assert _po_status(store) == "sent"
    assert store.execute(text("select status, reversed_on, reversal_reason from goods_receipt")).one() == (
        "reversed",
        TODAY,
        "Posted on the wrong order",
    )
    budget = [
        tuple(r)
        for r in store.execute(text("select source, period, amount from budget_actual order by id"))
    ]
    assert [row[0] for row in budget] == ["receipt", "receipt_reversal"]
    assert budget[0][1] == "2026-11" and D(budget[0][2]) == D("1000.00")
    assert D(budget[1][2]) == D("-1000.00")
    assert store.execute(text("select count(*) from audit_event where action = 'grn.reverse'")).scalar_one() == 1


def test_given_a_receipt_inside_the_window_when_the_order_acknowledged_its_version_then_it_goes_back_to_acknowledged(
    store: Session, outbox: Outbox
) -> None:  # noqa: F811
    po_no, logins = _sent(store)
    store.execute(text("update po set acknowledged_version = version, status = 'acknowledged' where no = :p"), {"p": po_no})
    store.commit()
    gr = _receive(store, po_no, "4").no

    reversal.reverse_receipt(store, store.info["slug"], gr, 1, "Wrong item", as_of=TODAY)

    assert _po_status(store) == "acknowledged"


def test_given_an_invoiced_receipt_when_it_is_reversed_then_the_invoice_is_not_left_unsupported(
    store: Session, outbox: Outbox
) -> None:  # noqa: F811
    po_no, gr, _ = _received(store)
    store.execute(text("update po_line set invoiced_qty = 10 where po_no = :p"), {"p": po_no})
    store.commit()

    with pytest.raises(reversal.ReversalError, match=r"line 1: 10 is invoiced .*reversing would leave 0 received"):
        reversal.reverse_receipt(store, store.info["slug"], gr, 1, "Undo", as_of=TODAY)

    assert store.execute(text("select status from goods_receipt where no = :n"), {"n": gr}).scalar_one() == "posted"


def test_given_goods_already_issued_when_the_receipt_is_reversed_then_the_stock_is_not_there(
    store: Session, outbox: Outbox
) -> None:  # noqa: F811
    po_no, gr, _ = _received(store)
    store.execute(
        text(
            "insert into stock_movement (id, item_code, location_code, date, type, qty, ref_doc, source) "
            "values ('SM-ISS-1', 'A', 'WH-BLR', :d, 'issue', 9, 'to the line', 'platform')"
        ),
        {"d": TODAY},
    )
    store.commit()

    with pytest.raises(reversal.ReversalError, match="A: only 1 usable is at Bengaluru store, so 10 cannot be taken back"):
        reversal.reverse_receipt(store, store.info["slug"], gr, 1, "Undo", as_of=TODAY)


def test_given_an_erp_or_cutover_receipt_when_it_is_reversed_then_it_is_refused(
    store: Session, outbox: Outbox
) -> None:  # noqa: F811
    po_no, gr, _ = _received(store)
    store.execute(text("update goods_receipt set source = 'erp' where no = :n"), {"n": gr})
    store.commit()

    with pytest.raises(reversal.ReversalError, match="came from the ERP: reverse it there"):
        reversal.reverse_receipt(store, store.info["slug"], gr, 1, "Undo", as_of=TODAY)


def test_given_a_receipt_past_the_window_when_the_store_asks_then_a_purchase_manager_approves(
    store: Session, outbox: Outbox
) -> None:  # noqa: F811
    po_no, gr, logins = _received(store)
    posted = reversal._posted_on(  # noqa: SLF001 - the day the window counts from
        dict(store.execute(text("select * from goods_receipt where no = :n"), {"n": gr}).mappings().one())
    )
    late = posted + timedelta(days=10)

    asked = reversal.reverse_receipt(store, store.info["slug"], gr, 1, "Found later", as_of=late)

    assert "proposal_id" in asked
    slug, sfx = store.info["slug"], store.info["sfx"]
    storekeeper = users.create_user(slug, f"store{sfx}@x.example", "correct horse battery", "A Store", "Store")
    with pytest.raises(permissions.PermissionDenied):
        proposals.approve(store, int(asked["proposal_id"]), storekeeper)  # a store person
    proposals.approve(store, int(asked["proposal_id"]), logins["procurement"])

    store.expire_all()
    assert store.execute(text("select status from goods_receipt where no = :n"), {"n": gr}).scalar_one() == "reversed"


def test_two_reversals_of_one_receipt_at_the_same_moment_leave_one_winner(
    store: Session, outbox: Outbox
) -> None:  # noqa: F811
    po_no, gr, logins = _received(store)
    slug = store.info["slug"]
    barrier = threading.Barrier(2)
    outcomes: list[str] = []

    def worker(user_id: int) -> None:
        with Session(routing.engine_for(slug)) as mine:
            barrier.wait()
            try:
                reversal.reverse_receipt(mine, slug, gr, user_id, "Twice at once", as_of=TODAY)
                outcomes.append("reversed")
            except reversal.ReversalError:
                mine.rollback()
                outcomes.append("refused")

    threads = [threading.Thread(target=worker, args=(1,)), threading.Thread(target=worker, args=(logins["cfo"],))]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert sorted(outcomes) == ["refused", "reversed"]
    assert store.execute(text("select count(*) from audit_event where action = 'grn.reverse'")).scalar_one() == 1
