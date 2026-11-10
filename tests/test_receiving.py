"""Goods receipt: tolerance, rejection, inspection, stock, the order's progress, and who may receive."""

import threading
from datetime import date
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session
from test_orders import _headers, _logins, _price, _request, _source, shop  # noqa: F401 - fixtures
from test_sourcing import PASSWORD, Outbox, db, outbox  # noqa: F401 - fixtures

from p2p.formulas import receiving as rules
from p2p.main import app
from p2p.orders import dispatch, service, workflow
from p2p.platform import routing, users
from p2p.receiving import goods
from p2p.receiving.goods import Decision, Header, LineIn, ReceiptError

D = Decimal
client = TestClient(app)
TODAY = date(2026, 11, 6)


# ---- the arithmetic ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("ordered", "pct", "expected"),
    [
        ("10", "5", "10.5000"),
        ("10", "0", "10.0000"),
        ("4", "10", "4.4000"),
        ("3", "2.5", "3.0750"),
        ("0.3333", "5", "0.3500"),
    ],
)
def test_the_most_that_may_be_received_is_the_order_plus_the_accepted_over_delivery(
    ordered: str, pct: str, expected: str
) -> None:
    assert rules.max_receivable(D(ordered), D(pct)) == D(expected)


def test_what_is_left_to_receive_never_goes_below_zero_and_rejected_goods_do_not_count() -> None:
    assert rules.room_left(D("10"), D("0"), D("5")) == D("10.5")
    assert rules.room_left(D("10"), D("10.5"), D("5")) == D("0")
    assert rules.room_left(D("10"), D("12"), D("5")) == D("0")


def test_received_goods_are_accepted_except_what_is_rejected() -> None:
    assert rules.split(D("10"), None) == rules.SplitResult(D("10"), D("0"))
    assert rules.split(D("10"), D("10")) == rules.SplitResult(D("0"), D("10"))
    for received, rejected in (("0", None), ("-1", None), ("10", "-1"), ("10", "10.0001")):
        with pytest.raises(ValueError, match="must be"):
            rules.split(D(received), None if rejected is None else D(rejected))


def test_a_line_and_its_order_take_their_status_from_what_was_accepted() -> None:
    assert [rules.line_status(D("10"), D(x)) for x in ("0", "9.9999", "10", "10.5")] == [
        "open",
        "part_received",
        "received",
        "received",
    ]
    assert rules.order_status(["open", "open"]) is None
    assert rules.order_status(["received", "open"]) == "part_received"
    assert rules.order_status(["received", "received", "cancelled"]) == "received"
    assert rules.order_status(["part_received"]) == "part_received"


def test_the_stock_cost_is_the_price_of_a_purchase_unit_divided_by_the_stock_units_in_it() -> None:
    assert rules.stock_unit_cost(D("120"), D("10"), D("120")) == D("10.0000")  # 10 dozen = 120 pieces
    assert rules.stock_unit_cost(D("100"), D("10"), None) == D("100.0000")
    assert rules.stock_unit_cost(D("100"), D("3"), D("7")) == D("42.8571")


# ---- a store and a sent order ------------------------------------------------------------------


@pytest.fixture
def store(shop: Session) -> Session:  # noqa: F811
    """The sourcing world with the receipt number series and the company's over-delivery tolerances."""
    shop.execute(
        text(
            "insert into number_series (document_type, prefix, scope, format, reset) "
            "values ('goods_receipt', 'GRN', 'location', '{prefix}-{loc}-{fy4}-{seq:05d}', 'fy')"
        )
    )
    shop.execute(
        text(
            "insert into tolerance (key, value, category_code) values "
            "('receipt_over_pct', '5.00', 'ANY'), ('receipt_over_pct', '10.00', 'C2')"
        )
    )
    shop.commit()
    return shop


def _sent(
    shop: Session,
    item: str = "A",
    qty: str = "10",
    price: str = "100",
    cat: str = "C1",
    logins: dict[str, int] | None = None,
) -> tuple[str, dict[str, int]]:
    logins = logins or _logins(shop)
    _price(shop, "S1", item, price)
    request = _request(shop, f"R-{item}-{qty}", [(item, qty, None, cat)])
    _source(shop, request, dispatch=False)
    made = service.order_request(shop, shop.info["slug"], request, as_of=TODAY)
    (po_no,) = made.waiting or made.pos
    if made.waiting:
        workflow.approve(shop, shop.info["slug"], po_no, logins["procurement"])
    dispatch.send_po(shop, po_no)
    return po_no, logins


def _receive(
    shop: Session,
    po_no: str,
    qty: str,
    rejected: str | None = None,
    reason: str | None = None,
    line: int = 1,
    user: int = 1,
) -> goods.Posted:
    return goods.post_receipt(
        shop,
        po_no,
        [LineIn(line, D(qty), None if rejected is None else D(rejected), reason)],
        Header(challan="DC/1", invoice_ref="INV-9"),
        user,
        as_of=TODAY,
    )


def _on_hand(shop: Session, item: str = "A") -> Decimal:
    shop.expire_all()
    return D(
        shop.execute(
            text("select coalesce(sum(on_hand), 0) from stock_level where item_code = :i"), {"i": item}
        ).scalar_one()
    )


def _line(shop: Session) -> tuple[Decimal, str]:
    shop.expire_all()
    row = shop.execute(text("select received_qty, status from po_line where line_no = 1")).one()
    return D(row[0]), row[1]


def _po_status(shop: Session) -> str:
    shop.expire_all()
    return str(shop.execute(text("select status from po")).scalar_one())


def test_a_full_receipt_closes_the_line_the_order_and_puts_the_goods_into_stock(store: Session, outbox: Outbox) -> None:  # noqa: F811
    po_no, _ = _sent(store)

    done = _receive(store, po_no, "10")

    assert done.no == "GRN-BLR-2627-00001" and done.po_status == "received"
    assert _line(store) == (D("10"), "received") and _po_status(store) == "received"
    assert _on_hand(store) == D("10")
    move = store.execute(
        text("select type, qty, unit_cost, ref_doc, source, location_code, date from stock_movement")
    ).one()
    assert move == ("receipt", D("10"), D("100"), done.no, "platform", "WH-BLR", TODAY)
    gr = store.execute(
        text(
            "select po_no, delivery_challan_no, supplier_invoice_ref, inspection_status, accepted_on, location_code from goods_receipt"
        )
    ).one()
    assert gr == (po_no, "DC/1", "INV-9", "not_required", TODAY, "WH-BLR")
    assert store.execute(text("select qty_received, qty_accepted, qty_rejected from gr_line")).one() == (
        D("10"),
        D("10"),
        D("0"),
    )
    assert store.execute(text("select count(*) from audit_event where action = 'grn.post'")).scalar_one() == 1


def test_a_part_receipt_leaves_the_order_open_for_the_rest(store: Session, outbox: Outbox) -> None:  # noqa: F811
    po_no, _ = _sent(store)

    first = _receive(store, po_no, "6")
    assert (first.po_status, _line(store), _on_hand(store)) == ("part_received", (D("6"), "part_received"), D("6"))
    second = _receive(store, po_no, "4")

    assert (second.no, second.po_status) == ("GRN-BLR-2627-00002", "received")
    assert _line(store) == (D("10"), "received") and _on_hand(store) == D("10")
    with pytest.raises(ReceiptError, match="is received"):
        _receive(store, po_no, "1")  # nothing more is expected against a complete order


def test_over_delivery_is_accepted_up_to_the_tolerance_of_the_category_and_not_a_unit_more(
    store: Session, outbox: Outbox
) -> None:  # noqa: F811
    po_no, _ = _sent(store)  # item A, category C1: the ANY tolerance of 5%

    with pytest.raises(ReceiptError, match="more than the 10.5000 that may still be received"):
        _receive(store, po_no, "10.5001")
    assert _on_hand(store) == D("0")
    _receive(store, po_no, "10.5")  # exactly the limit

    assert _line(store) == (D("10.5"), "received")


def test_a_category_with_its_own_tolerance_uses_it(store: Session, outbox: Outbox) -> None:  # noqa: F811
    store.execute(text("update item set category_code = 'C2' where code = 'D'"))
    store.commit()
    po_no, _ = _sent(store, item="D", qty="4", price="40", cat="C2")  # packaging: 10%

    with pytest.raises(ReceiptError, match="4.4000"):
        _receive(store, po_no, "4.4001")
    _receive(store, po_no, "4.4")


def test_what_is_received_in_parts_counts_together_against_the_tolerance(store: Session, outbox: Outbox) -> None:  # noqa: F811
    po_no, logins = _sent(store)
    _receive(store, po_no, "6")
    _receive(store, po_no, "4")  # the line is received; a further receipt is refused whatever the tolerance
    assert _line(store)[1] == "received"

    po2, _ = _sent(store, qty="20", logins=logins)
    _receive(store, po2, "12", line=1)
    with pytest.raises(ReceiptError, match="9.0000 that may still be received"):
        _receive(store, po2, "9.5")  # 12 + 9.5 = 21.5 > 21
    _receive(store, po2, "9")


def test_rejected_goods_need_a_reason_stay_out_of_stock_and_leave_the_line_open_for_a_replacement(
    store: Session, outbox: Outbox
) -> None:  # noqa: F811
    po_no, _ = _sent(store)
    with pytest.raises(ReceiptError, match="reason is needed"):
        _receive(store, po_no, "10", rejected="4")

    done = _receive(store, po_no, "10", rejected="4", reason="Cracked housings")

    assert done.po_status == "part_received" and done.accepted == {1: D("6")}
    assert _line(store) == (D("6"), "part_received") and _on_hand(store) == D("6")
    assert store.execute(text("select qty_accepted, qty_rejected, reject_reason from gr_line")).one() == (
        D("6"),
        D("4"),
        "Cracked housings",
    )
    # the replacement for the four rejected fits, because rejected goods do not use up the order
    again = _receive(store, po_no, "4")
    assert again.po_status == "received" and _line(store) == (D("10"), "received") and _on_hand(store) == D("10")


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"qty": "0"}, "must be more than zero"),
        ({"qty": "-1"}, "must be more than zero"),
        ({"qty": "5", "rejected": "6", "reason": "x"}, "between zero and the quantity received"),
        ({"qty": "5", "rejected": "-1", "reason": "x"}, "between zero and the quantity received"),
        ({"qty": "5", "line": 9}, "line 9 is not open"),
    ],
)
def test_a_receipt_that_makes_no_sense_is_refused_and_changes_nothing(
    store: Session, outbox: Outbox, kwargs: dict, message: str
) -> None:  # noqa: F811
    po_no, _ = _sent(store)

    with pytest.raises(ReceiptError, match=message):
        _receive(store, po_no, **kwargs)

    store.rollback()
    assert store.execute(text("select count(*) from goods_receipt")).scalar_one() == 0
    assert _on_hand(store) == D("0") and _line(store) == (D("0"), "open")


def test_a_line_twice_or_nothing_at_all_is_refused(store: Session, outbox: Outbox) -> None:  # noqa: F811
    po_no, _ = _sent(store)
    for lines, message in (([], "at least one line"), ([LineIn(1, D("1")), LineIn(1, D("2"))], "appears twice")):
        with pytest.raises(ReceiptError, match=message):
            goods.post_receipt(store, po_no, lines, Header(), 1, as_of=TODAY)


@pytest.mark.parametrize("status", ["draft", "submitted", "approved", "cancelled", "received", "closed"])
def test_goods_are_received_only_against_an_order_that_was_sent(store: Session, outbox: Outbox, status: str) -> None:  # noqa: F811
    po_no, _ = _sent(store)
    store.execute(text("update po set status = :s"), {"s": status})
    store.commit()

    with pytest.raises(ReceiptError, match=f"is {status}"):
        _receive(store, po_no, "1")


def test_goods_may_also_arrive_before_the_supplier_acknowledges_and_after(store: Session, outbox: Outbox) -> None:  # noqa: F811
    po_no, _ = _sent(store, qty="10")
    _receive(store, po_no, "2")  # sent, not acknowledged
    store.execute(text("update po set status = 'acknowledged'"))
    store.commit()
    assert _receive(store, po_no, "2").po_status == "part_received"


def test_an_item_that_is_not_stocked_counts_against_the_order_but_not_into_stock(
    store: Session, outbox: Outbox
) -> None:  # noqa: F811
    store.execute(text("update item set stocked = false where code = 'A'"))
    store.commit()
    po_no, _ = _sent(store)

    _receive(store, po_no, "10")

    assert _line(store) == (D("10"), "received")
    assert store.execute(text("select count(*) from stock_movement")).scalar_one() == 0


def test_stock_is_posted_in_stock_units_at_the_cost_of_one_stock_unit(store: Session, outbox: Outbox) -> None:  # noqa: F811
    po_no, _ = _sent(store, qty="10", price="120")  # bought by the dozen
    store.execute(text("update po_line set base_qty = 120 where po_no = :p"), {"p": po_no})
    store.commit()

    _receive(store, po_no, "5")

    move = store.execute(text("select qty, unit_cost from stock_movement")).one()
    assert move == (D("60"), D("10"))  # 5 dozen are 60 pieces at 10 each


def test_an_item_that_needs_inspection_waits_and_nothing_counts_until_it_is_inspected(
    store: Session, outbox: Outbox
) -> None:  # noqa: F811
    store.execute(text("update item set inspection_required = true where code = 'A'"))
    store.commit()
    po_no, _ = _sent(store)

    done = _receive(store, po_no, "10")

    assert done.pending_inspection == [1] and done.po_status == "sent"
    assert (_line(store), _on_hand(store)) == ((D("0"), "open"), D("0"))
    assert store.execute(text("select inspection_status, accepted_on from goods_receipt")).one() == ("pending", None)
    # a second delivery cannot use up more than the order allows while the first one waits
    with pytest.raises(ReceiptError, match="0.5000 that may still be received"):
        _receive(store, po_no, "1")
    with pytest.raises(ReceiptError, match="decides what is rejected"):
        _receive(store, po_no, "0.5", rejected="0.1", reason="x")

    with pytest.raises(ReceiptError, match="add up to the 10.0000 received"):
        goods.inspect(store, done.no, [Decision(1, D("9"), D("0"))], 1, as_of=TODAY)
    with pytest.raises(ReceiptError, match="reason is needed"):
        goods.inspect(store, done.no, [Decision(1, D("8"), D("2"))], 1, as_of=TODAY)
    with pytest.raises(ReceiptError, match="decide every line"):
        goods.inspect(store, done.no, [], 1, as_of=TODAY)
    checked = goods.inspect(store, done.no, [Decision(1, D("8"), D("2"), "Out of specification")], 1, as_of=TODAY)

    assert checked.po_status == "part_received" and (_line(store), _on_hand(store)) == (
        (D("8"), "part_received"),
        D("8"),
    )
    assert store.execute(text("select inspection_status, accepted_on from goods_receipt")).one() == ("failed", TODAY)
    assert store.execute(text("select qty_accepted, qty_rejected, reject_reason from gr_line")).one() == (
        D("8"),
        D("2"),
        "Out of specification",
    )
    with pytest.raises(ReceiptError, match="not waiting for inspection"):
        goods.inspect(store, done.no, [Decision(1, D("8"), D("2"), "again")], 1, as_of=TODAY)
    assert store.execute(text("select count(*) from audit_event where action = 'grn.inspect'")).scalar_one() == 1


def test_an_inspection_that_rejects_nothing_passes(store: Session, outbox: Outbox) -> None:  # noqa: F811
    store.execute(text("update item set inspection_required = true where code = 'A'"))
    store.commit()
    po_no, _ = _sent(store)
    done = _receive(store, po_no, "10")

    goods.inspect(store, done.no, [Decision(1, D("10"), D("0"))], 1, as_of=TODAY)

    assert store.execute(text("select inspection_status from goods_receipt")).scalar_one() == "passed"
    assert _po_status(store) == "received"


# ---- who may receive ---------------------------------------------------------------------------


def test_the_buyer_an_approver_or_the_requester_cannot_receive_the_goods(store: Session, outbox: Outbox) -> None:  # noqa: F811
    po_no, logins = _sent(store)
    sfx = store.info["sfx"]
    store.execute(text("update request set requester = 'U4'"))
    store.execute(text("update po set buyer = 'U3'"))
    store.commit()
    # U5 approved the order, U4 is the requester, U3 the buyer; the others hold none of those duties
    cases = {
        "an approver": (logins["procurement"], "an approver of the order"),
        "the buyer": (logins["stores"], "the buyer who ordered"),
        "the requester": (logins["keeper"], "the person who asked"),
    }
    for user_id, text_ in cases.values():
        with pytest.raises(ReceiptError, match=f"segregation of duties: {text_}"):
            _receive(store, po_no, "1", user=user_id)
    assert logins["director"] and logins["cfo"]  # logins of people who hold no duty on this order
    store.rollback()
    assert store.execute(text("select count(*) from goods_receipt")).scalar_one() == 0
    assert sfx


def test_someone_with_no_duty_on_the_order_receives_it(store: Session, outbox: Outbox) -> None:  # noqa: F811
    po_no, logins = _sent(store)
    store.execute(text("update request set requester = 'U4'"))
    store.execute(text("update po set buyer = 'U3'"))
    store.commit()

    assert _receive(store, po_no, "1", user=logins["cfo"]).no.startswith("GRN-")


# ---- the screens -------------------------------------------------------------------------------


def test_the_api_lets_a_store_person_receive_and_nobody_else(store: Session, outbox: Outbox) -> None:  # noqa: F811
    po_no, logins = _sent(store)
    slug, sfx = store.info["slug"], store.info["sfx"]
    keeper = users.create_user(slug, f"store{sfx}@x.example", PASSWORD, "A Store", "Store")
    h_store, h_buyer = _headers(slug, keeper, "Store"), _headers(slug, logins["buyer"], "Buyer")
    body = {"challan": "DC/77", "vehicle": "KA01AB1234", "lines": [{"line_no": 1, "qty_received": "6"}]}

    assert client.post(f"/api/purchase-orders/{po_no}/receive", json=body).status_code == 401
    assert client.post(f"/api/purchase-orders/{po_no}/receive", json=body, headers=h_buyer).status_code == 403
    page = client.get(f"/api/purchase-orders/{po_no}", headers=h_store).json()
    assert page["can_receive"] is True
    assert client.get(f"/api/purchase-orders/{po_no}", headers=h_buyer).json()["can_receive"] is False

    waiting = client.get("/api/receiving/waiting", headers=h_store).json()
    assert [(w["id"], w["lines"][0]["ordered"], w["lines"][0]["received"]) for w in waiting] == [(po_no, "10", "0")]

    done = client.post(f"/api/purchase-orders/{po_no}/receive", json=body, headers=h_store)
    assert done.status_code == 200 and done.json()["receipt"] == "GRN-BLR-2627-00001"
    assert done.json()["po_status"] == "part_received" and done.json()["accepted"] == {"1": "6"}

    detail = client.get("/api/receipts/GRN-BLR-2627-00001", headers=h_store).json()
    assert (detail["challan"], detail["vehicle"], detail["inspection"]) == ("DC/77", "KA01AB1234", "not_required")
    assert detail["lines"] == [
        {
            "line_no": 1,
            "name": "Bearing",
            "item_code": "A",
            "uom": "NOS",
            "received": "6",
            "accepted": "6",
            "rejected": "0",
            "reason": None,
        }
    ]
    assert [r["id"] for r in client.get("/api/receipts", headers=h_store).json()] == ["GRN-BLR-2627-00001"]
    page = client.get(f"/api/purchase-orders/{po_no}", headers=h_store).json()
    assert page["lines"][0]["received_qty"] == "6" and page["receipts"][0]["id"] == "GRN-BLR-2627-00001"
    assert client.get("/api/receipts/NOPE", headers=h_store).status_code == 404
    assert client.get("/api/receipts").status_code == 401


@pytest.mark.parametrize(
    ("body", "code"),
    [
        ({"lines": []}, 422),
        ({}, 422),
        ({"lines": [{"line_no": 1, "qty_received": "abc"}]}, 422),
        ({"lines": [{"line_no": 1, "qty_received": "NaN"}]}, 422),
        ({"lines": [{"line_no": 1, "qty_received": "Infinity"}]}, 422),
        ({"lines": [{"qty_received": "1"}]}, 422),
        ({"lines": [{"line_no": 1, "qty_received": "11"}]}, 409),
        ({"lines": [{"line_no": 1, "qty_received": "5", "qty_rejected": "1"}]}, 409),
    ],
)
def test_the_api_refuses_what_is_not_a_receipt(store: Session, outbox: Outbox, body: dict, code: int) -> None:  # noqa: F811
    po_no, _ = _sent(store)
    slug, sfx = store.info["slug"], store.info["sfx"]
    keeper = users.create_user(slug, f"store{sfx}@x.example", PASSWORD, "A Store", "Store")

    r = client.post(f"/api/purchase-orders/{po_no}/receive", json=body, headers=_headers(slug, keeper, "Store"))

    assert r.status_code == code
    assert (
        client.post(
            "/api/purchase-orders/NOPE/receive",
            json={"lines": [{"line_no": 1, "qty_received": "1"}]},
            headers=_headers(slug, keeper, "Store"),
        ).status_code
        == 404
    )


def test_the_api_inspection_and_the_separation_of_duties_answer_with_the_right_codes(
    store: Session, outbox: Outbox
) -> None:  # noqa: F811
    store.execute(text("update item set inspection_required = true where code = 'A'"))
    store.commit()
    po_no, logins = _sent(store)
    store.execute(text("update po set buyer = 'U3'"))
    store.commit()
    slug, sfx = store.info["slug"], store.info["sfx"]
    keeper = users.create_user(slug, f"store{sfx}@x.example", PASSWORD, "A Store", "Store")
    h = _headers(slug, keeper, "Store")
    # a store login that is the buyer in the organisation (the stores head's own address) is refused
    conflicted = _headers(slug, logins["stores"], "Store")
    line = {"lines": [{"line_no": 1, "qty_received": "10"}]}
    assert client.post(f"/api/purchase-orders/{po_no}/receive", json=line, headers=conflicted).status_code == 403

    gr = client.post(f"/api/purchase-orders/{po_no}/receive", json=line, headers=h).json()["receipt"]
    assert client.get(f"/api/receipts/{gr}", headers=h).json()["can_inspect"] is True
    assert (
        client.post(
            f"/api/receipts/{gr}/inspect",
            json={"lines": [{"line_no": 1, "qty_accepted": "9", "qty_rejected": "0"}]},
            headers=h,
        ).status_code
        == 409
    )
    assert client.post(f"/api/receipts/{gr}/inspect", json={"lines": [{"line_no": 1}]}, headers=h).status_code == 422
    assert client.post(f"/api/receipts/{gr}/inspect", json={"lines": []}, headers=h).status_code == 422
    ok = client.post(
        f"/api/receipts/{gr}/inspect",
        json={"lines": [{"line_no": 1, "qty_accepted": "10", "qty_rejected": "0"}]},
        headers=h,
    )
    assert ok.status_code == 200 and ok.json()["po_status"] == "received"
    assert client.get(f"/api/receipts/{gr}", headers=h).json()["can_inspect"] is False
    assert (
        client.post(
            "/api/receipts/NOPE/inspect",
            json={"lines": [{"line_no": 1, "qty_accepted": "1", "qty_rejected": "0"}]},
            headers=h,
        ).status_code
        == 404
    )


def test_two_receipts_at_the_same_moment_cannot_together_take_in_more_than_the_order_allows(
    store: Session, outbox: Outbox
) -> None:  # noqa: F811
    po_no, _ = _sent(store)
    slug = store.info["slug"]
    outcomes: list[str] = []
    barrier = threading.Barrier(2)

    def worker() -> None:
        with Session(routing.engine_for(slug)) as mine:
            barrier.wait()
            try:
                _receive(mine, po_no, "7")  # two of these would be 14 against a limit of 10.5
                outcomes.append("ok")
            except ReceiptError:
                mine.rollback()
                outcomes.append("refused")

    threads = [threading.Thread(target=worker) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert sorted(outcomes) == ["ok", "refused"]
    assert _line(store)[0] == D("7") and store.execute(text("select count(*) from goods_receipt")).scalar_one() == 1
