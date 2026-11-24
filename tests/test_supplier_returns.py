"""Returns to a supplier and recovery claims (tasks 4.3 and D-21, 9.5-9.6): dispatch, outcome, replacement and claim."""

from decimal import Decimal

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session
from test_orders import shop  # noqa: F401 - fixtures
from test_receiving import _on_hand, _receive, _sent, store  # noqa: F401 - fixtures
from test_sourcing import Outbox, db, outbox  # noqa: F401 - fixtures

from p2p.receiving import goods, quality, returns

D = Decimal
TODAY = __import__("test_receiving").TODAY


def _blocked(store: Session) -> tuple[str, dict[str, int], int]:
    """R4's start: 10 arrive, the inspection rejects 2 into a blocked hold."""
    store.execute(text("update item set inspection_required = true where code = 'A'"))
    store.execute(
        text(
            "insert into supplier_contact (supplier_code, name, email, is_primary) "
            "values ('S1', 'Order Desk', 'orders@supplier.example', true)"
        )
    )
    store.execute(
        text(
            "insert into gst_registration (gstin, state_code, is_default) values ('29AAAAA0000A1Z5', '29', true) "
            "on conflict do nothing"
        )
    )
    store.commit()
    po_no, logins = _sent(store)
    done = _receive(store, po_no, "10")
    goods.inspect(store, done.no, [goods.Decision(1, D("8"), D("2"), "Cracked seals")], logins["cfo"], as_of=TODAY)
    hold = store.execute(text("select id from stock_hold where quality = 'blocked' and closed_at is null")).scalar_one()
    return po_no, logins, int(hold)


def _return(store: Session, hold_id: int, qty: str, *, gr_no: str | None = None, line: int | None = None) -> str:
    made = returns.create(
        store,
        store.info["slug"],
        1,
        "Cracked seals go back",
        [returns.ReturnLineIn(hold_id, D(qty), gr_no=gr_no, po_line_no=line)],
        as_of=TODAY,
    )
    return made.no


def _invoice(store: Session, po_no: str) -> None:
    """A booked invoice for the whole order line, as the match would leave it (the claim reads the booked quantity)."""
    store.execute(
        text(
            "insert into invoice (no, supplier_code, supplier_invoice_no, dedupe_key, invoice_date, fy, received_on, "
            "taxable, total, status, entered_by) values ('INV-T-1', 'S1', 'SI-1', 'k1', :d, '2026-27', :d, 1000, "
            "1180, 'approved', 'U4')"
        ),
        {"d": TODAY},
    )
    store.execute(
        text("insert into invoice_line (invoice_no, line_no, po_no, po_line_no, qty, unit_price, taxable, gst_rate) "
             "values ('INV-T-1', 1, :p, 1, 10, 100, 1000, 18)"),
        {"p": po_no},
    )  # fmt: skip
    store.execute(text("update po_line set invoiced_qty = 10 where po_no = :p"), {"p": po_no})
    store.commit()


def test_given_blocked_stock_when_the_return_is_dispatched_then_it_leaves_and_the_hold_closes(
    store: Session, outbox: Outbox
) -> None:  # noqa: F811
    po_no, logins, hold = _blocked(store)
    no = _return(store, hold, "2")

    done = returns.dispatch(store, store.info["slug"], no, 1, "RDC-11", "MH 14 XX 0000", as_of=TODAY)

    assert done.claims == []
    store.expire_all()
    assert _on_hand(store) == D("8")
    hold_row = store.execute(text("select left_qty, closed_by_doc from stock_hold where id = :i"), {"i": hold}).one()
    assert (D(hold_row[0]), hold_row[1]) == (D("0"), no)
    movement = store.execute(text("select type, quality, qty from stock_movement where ref_doc = :n"), {"n": no}).one()
    assert (movement[0], movement[1], D(movement[2])) == ("return", "blocked", D("2"))
    line = store.execute(
        text("select counted, outcome from supplier_return_line where return_no = :n"), {"n": no}
    ).one()
    assert (line[0], line[1]) == (False, None)


def test_given_a_dispatched_return_when_the_buyer_says_replace_then_the_replacement_is_received_normally(
    store: Session, outbox: Outbox
) -> None:  # noqa: F811
    po_no, logins, hold = _blocked(store)
    no = _return(store, hold, "2")
    returns.dispatch(store, store.info["slug"], no, 1, "RDC-11", "MH 14 XX 0000", as_of=TODAY)

    claims = returns.outcome(store, store.info["slug"], no, logins["procurement"], {1: "replace"}, as_of=TODAY)

    assert claims == []
    assert (
        store.execute(text("select outcome from supplier_return_line where return_no = :n"), {"n": no}).scalar_one()
        == "replace"
    )
    replacement = _receive(store, po_no, "2")
    assert replacement.pending_inspection == [1]
    goods.inspect(store, replacement.no, [goods.Decision(1, D("2"), D("0"))], logins["cfo"], as_of=TODAY)
    assert goods.refresh_order(store, po_no) == "received"
    store.expire_all()
    assert D(store.execute(text("select received_qty from po_line where po_no = :p"), {"p": po_no}).scalar_one()) == D(
        "10"
    )


def test_given_a_counted_return_with_no_replacement_and_an_invoice_beyond_the_goods_then_a_claim_is_raised(
    store: Session, outbox: Outbox
) -> None:  # noqa: F811
    po_no, logins, hold = _blocked(store)
    _invoice(store, po_no)
    # the replacement arrives and is accepted, then two of the usable goods are held and sent back counted
    _receive(store, po_no, "2")
    replacement = store.execute(
        text(
            "select no from goods_receipt where po_no = :p and inspection_status = 'pending' order by no desc limit 1"
        ),
        {"p": po_no},
    ).scalar_one()
    goods.inspect(store, str(replacement), [goods.Decision(1, D("2"), D("0"))], logins["cfo"], as_of=TODAY)
    gr = store.execute(
        text("select gr_no from gr_line where po_no = :p order by gr_no limit 1"), {"p": po_no}
    ).scalar_one()
    held = quality.hold(store, store.info["slug"], 1, "A", "WH-BLR", D("2"), "blocked", "Rusty")
    no = _return(store, held.hold_id, "2", gr_no=gr, line=1)
    returns.dispatch(store, store.info["slug"], no, 1, "RDC-12", "MH 14 XX 0000", as_of=TODAY)

    claims = returns.outcome(store, store.info["slug"], no, logins["procurement"], {1: "no_replacement"}, as_of=TODAY)

    assert len(claims) == 1
    claim = store.execute(text("select * from recovery_claim where no = :n"), {"n": claims[0]}).mappings().one()
    assert (D(claim["qty"]), D(claim["taxable"]), claim["status"]) == (D("2"), D("200.00"), "open")
    assert D(claim["total"]) == D("236.00")  # 200 + 18% IGST
    assert (D(claim["cgst"]), D(claim["sgst"]), D(claim["igst"])) == (D("0.00"), D("0.00"), D("36.00"))
    assert list(claim["invoices"]) == ["INV-T-1"] and claim["debit_note_no"] is None
    store.expire_all()
    assert D(store.execute(text("select received_qty from po_line where po_no = :p"), {"p": po_no}).scalar_one()) == D(
        "8"
    )
    assert (
        store.execute(
            text("select count(*) from supplier_return_line where return_no = :n and claim_no is not null"), {"n": no}
        ).scalar_one()
        == 1
    )


def test_a_counted_return_with_no_replacement_raises_nothing_when_the_invoice_is_not_beyond_the_goods(
    store: Session, outbox: Outbox
) -> None:  # noqa: F811
    po_no, logins, hold = _blocked(store)  # 8 received, the invoice is for 8 only
    _receive(store, po_no, "2")
    gr = store.execute(
        text("select gr_no from gr_line where po_no = :p order by gr_no limit 1"), {"p": po_no}
    ).scalar_one()
    held = quality.hold(store, store.info["slug"], 1, "A", "WH-BLR", D("2"), "blocked", "Rusty")
    no = _return(store, held.hold_id, "2", gr_no=gr, line=1)
    returns.dispatch(store, store.info["slug"], no, 1, "RDC-13", "MH 14 XX 0000", as_of=TODAY)

    assert returns.outcome(store, store.info["slug"], no, logins["procurement"], {1: "no_replacement"},
                           as_of=TODAY) == []  # fmt: skip
    assert store.execute(text("select count(*) from recovery_claim")).scalar_one() == 0


def test_a_manual_hold_without_its_receipt_cannot_be_returned(store: Session, outbox: Outbox) -> None:  # noqa: F811
    po_no, logins = _sent(store)
    _receive(store, po_no, "10")
    held = quality.hold(store, store.info["slug"], 1, "A", "WH-BLR", D("1"), "blocked", "Dented")

    with pytest.raises(returns.ReturnError, match="say which receipt the goods came from"):
        _return(store, held.hold_id, "1")

    assert held.hold_id


def test_a_return_of_stock_with_no_receipt_may_name_only_its_supplier(store: Session, outbox: Outbox) -> None:  # noqa: F811
    store.execute(
        text(
            "insert into stock_movement (id, item_code, location_code, date, type, qty, ref_doc, source, quality) "
            "values ('SM-OLD-1', 'A', 'WH-BLR', :d, 'receipt', 5, 'opening', 'import', 'blocked')"
        ),
        {"d": TODAY},
    )
    store.execute(
        text(
            "insert into stock_hold (item_code, location_code, quality, qty, left_qty, reason, source, held_by) "
            "values ('A', 'WH-BLR', 'blocked', 5, 5, 'Opening stock found bad', 'import', 'import')"
        )
    )
    store.commit()
    hold = store.execute(text("select id from stock_hold where source = 'import'")).scalar_one()

    no = returns.create(
        store, store.info["slug"], 1, "Old stock goes back",
        [returns.ReturnLineIn(int(hold), D("2"), supplier_code="S1")], as_of=TODAY,
    )  # fmt: skip
    line = store.execute(
        text("select counted, po_no from supplier_return_line where return_no = :n"), {"n": no.no}
    ).one()
    assert (line[0], line[1]) == (False, None)


def test_a_dispatched_return_is_final_and_a_draft_can_be_cancelled(store: Session, outbox: Outbox) -> None:  # noqa: F811
    _, _, hold = _blocked(store)
    no = _return(store, hold, "1")
    returns.cancel(store, no, 1, "Wrong hold")
    assert (
        store.execute(text("select status from supplier_return where no = :n"), {"n": no}).scalar_one() == "cancelled"
    )

    no2 = _return(store, hold, "1")
    returns.dispatch(store, store.info["slug"], no2, 1, "RDC-14", "MH 14 XX 0000", as_of=TODAY)
    with pytest.raises(returns.ReturnError, match="dispatched return is final"):
        returns.cancel(store, no2, 1, "Too late")


def test_given_blocked_stock_when_it_is_scrapped_then_only_blocked_stock_leaves_and_usable_does_not(
    store: Session, outbox: Outbox
) -> None:  # noqa: F811
    po_no, _, hold = _blocked(store)
    usable = _on_hand(store)
    with pytest.raises(quality.QualityError, match="only blocked stock is scrapped"):
        quality.scrap(store, 1, _excess_hold(store, po_no), D("1"), "x")

    left = quality.scrap(store, 1, hold, D("2"), "Rusted, not returnable", today=TODAY)

    assert left == D("0")
    assert _on_hand(store) == usable  # usable stock is untouched
    row = store.execute(
        text("select quality, left_qty, closed_by_doc from stock_hold where id = :i"), {"i": hold}
    ).one()
    assert (row[0], D(row[1]), row[2]) == ("blocked", D("0"), f"SCRAP-{hold}")
    assert store.execute(text("select type, quality, qty from stock_movement where type = 'scrap'")).one() == (
        "scrap",
        "blocked",
        D("2"),
    )
    assert store.execute(text("select count(*) from audit_event where action = 'stock.scrap'")).scalar_one() == 1


def _excess_hold(store: Session, po_no: str) -> int:
    """A quarantine hold of goods above the order, for the scrap refusal (nothing to scrap in quarantine)."""
    store.execute(
        text(
            "insert into stock_hold (item_code, location_code, quality, qty, left_qty, reason, source, held_by) "
            "values ('A', 'WH-BLR', 'quarantine', 1, 1, 'Above the order', 'excess', 'U02')"
        )
    )
    store.commit()
    return int(store.execute(text("select max(id) from stock_hold")).scalar_one())
