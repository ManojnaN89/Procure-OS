"""Service entry (the requester confirms a service), the two-way match of a service invoice, goods received without an
order, and serial numbers for serialised items."""

import time
import uuid
from datetime import date
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session
from test_invoices import _invoice, ledger  # noqa: F401 - fixtures
from test_orders import _headers, shop  # noqa: F401 - fixtures
from test_receiving import _on_hand, _sent, store  # noqa: F401 - fixtures
from test_sourcing import Outbox, db, outbox  # noqa: F401 - fixtures

from p2p.approvals.email_links import LinkError
from p2p.formulas import matching
from p2p.formulas import receiving as rules
from p2p.invoices import service as invoices
from p2p.main import app
from p2p.orders import dispatch, workflow
from p2p.platform import permissions, proposals, routing, tenants
from p2p.receiving import goods, service_links, services, unordered
from p2p.receiving.services import Confirmation, ServiceEntryError

D = Decimal
client = TestClient(app)
TODAY = date(2026, 11, 6)
SERIES = "'{prefix}-{loc}-{fy4}-{seq:05d}'"


# ---- the arithmetic ---------------------------------------------------------------------------


def test_given_a_service_line_when_it_is_confirmed_by_percent_quantity_or_amount_then_each_counts_in_the_lines_unit() -> (
    None
):
    assert rules.confirm_service("pct", D("100"), D("10"), D("100"), D("10")) == rules.ServiceConfirmed(
        D("10"), D("1000.00")
    )
    assert rules.confirm_service("pct", D("40"), D("10"), D("100"), D("10")) == rules.ServiceConfirmed(
        D("4"), D("400.00")
    )
    assert rules.confirm_service("qty", D("2.5"), D("10"), D("100"), D("10")).amount == D("250.00")
    lump = rules.confirm_service("amount", D("42500"), D("1"), D("85000"), D("1"))
    assert lump == rules.ServiceConfirmed(D("0.5000"), D("42500.00"))


@pytest.mark.parametrize(
    ("basis", "value", "room", "ok"),
    [
        ("pct", "100", "10", True),  # exactly the order
        ("pct", "100.01", "10", False),  # one hundredth of a percent more, no tolerance
        ("pct", "100.01", "10.5", True),  # within a 5% over-delivery tolerance
        ("amount", "85000.00", "1", True),
        ("amount", "85000.01", "1", False),  # one paisa more than the lump sum
        ("qty", "0", "10", False),
        ("cash", "1", "10", False),
    ],
)
def test_the_edges_of_a_service_confirmation(basis: str, value: str, room: str, ok: bool) -> None:
    price, ordered = (D("85000"), D("1")) if basis == "amount" else (D("100"), D("10"))
    if ok:
        rules.confirm_service(basis, D(value), ordered, price, D(room))
    else:
        with pytest.raises(ValueError, match="more than|must be"):
            rules.confirm_service(basis, D(value), ordered, price, D(room))


def test_a_serialised_item_needs_one_new_serial_per_whole_unit() -> None:
    assert rules.check_serials([" sn-1 ", "SN-2"], D("2")) == ["SN-1", "SN-2"]
    for serials, units, why in (
        (["A"], D("2"), "2 serial number"),
        (["A", "a"], D("2"), "given twice"),
        (["A"], D("1.5"), "whole units"),
    ):
        with pytest.raises(ValueError, match=why):
            rules.check_serials(serials, units)


def test_a_service_line_invoiced_beyond_what_was_confirmed_is_held_as_not_confirmed() -> None:
    tol = matching.Tolerance()
    assert matching.judge_line(D("100"), D("100"), D("1"), D("0"), tol, service=True).reason == matching.SERVICE
    assert matching.judge_line(D("100"), D("100"), D("1"), D("0"), tol).reason == matching.QTY
    assert matching.judge_line(D("100"), D("100"), D("1"), D("1"), tol, service=True).result == matching.MATCHED


# ---- a sent order for a service ---------------------------------------------------------------


@pytest.fixture
def svc(ledger: Session) -> Session:  # noqa: F811
    """The invoice world with the service entry, unordered receipt and request number series, and no over-delivery."""
    for doc, prefix in (("service_receipt", "SRN"), ("unordered_receipt", "URN"), ("request", "REQ")):
        ledger.execute(
            text(
                "insert into number_series (document_type, prefix, scope, format, reset) "
                f"values ('{doc}', '{prefix}', 'location', {SERIES}, 'fy')"
            )
        )
    ledger.execute(text("update tolerance set value = '0' where key = 'receipt_over_pct'"))
    ledger.commit()
    return ledger


def _service_po(
    shop_: Session, requester: str = "U4", qty: str = "10", price: str = "100"
) -> tuple[str, dict[str, int]]:
    """A sent order whose one line is a service, asked for by `requester` (U4 is the storekeeper's login)."""
    shop_.execute(text("update item set kind = 'service', stocked = false where code = 'A'"))
    shop_.commit()
    po_no, logins = _sent(shop_, qty=qty, price=price)
    shop_.execute(text("update request set requester = :r"), {"r": requester})
    shop_.commit()
    return po_no, logins


def _confirm(shop_: Session, po_no: str, user: int, basis: str = "pct", value: str = "100") -> services.Confirmed:
    return services.confirm(shop_, Confirmation(po_no, 1, basis, D(value)), user, as_of=TODAY)


def _line(shop_: Session) -> tuple[Decimal, str]:
    shop_.expire_all()
    row = shop_.execute(text("select received_qty, status from po_line where line_no = 1")).one()
    return D(row[0]), row[1]


def test_given_a_sent_service_order_when_the_requester_confirms_all_of_it_then_it_counts_as_received_and_is_audited(
    svc: Session, outbox: Outbox
) -> None:  # noqa: F811
    po_no, logins = _service_po(svc)

    done = _confirm(svc, po_no, logins["keeper"])

    assert (done.no, done.qty, done.amount, done.po_status) == ("SRN-BLR-2627-00001", D("10"), D("1000.00"), "received")
    assert _line(svc) == (D("10"), "received")
    entry = svc.execute(text("select basis, value, accepted_by, via, accepted_on from service_entry")).one()
    assert entry == ("pct", D("100"), "U4", "app", TODAY)
    assert svc.execute(text("select count(*) from stock_movement")).scalar_one() == 0  # a service is never stock
    audit = svc.execute(text("select actor_id, entity_id from audit_event where action = 'service.confirm'")).one()
    assert audit == (str(logins["keeper"]), done.no)


def test_given_no_over_delivery_tolerance_when_100_01_percent_is_confirmed_then_it_is_refused_and_with_5_percent_allowed(
    svc: Session, outbox: Outbox
) -> None:  # noqa: F811
    po_no, logins = _service_po(svc)

    with pytest.raises(ServiceEntryError, match="more than the 10.0000"):
        _confirm(svc, po_no, logins["keeper"], value="100.01")
    svc.rollback()
    svc.execute(text("update tolerance set value = '5' where key = 'receipt_over_pct'"))
    svc.commit()

    assert _confirm(svc, po_no, logins["keeper"], value="100.01").qty == D("10.0010")


def test_given_a_service_when_it_is_confirmed_in_parts_then_each_part_counts_until_the_order_is_full(
    svc: Session, outbox: Outbox
) -> None:  # noqa: F811
    po_no, logins = _service_po(svc)

    first = _confirm(svc, po_no, logins["keeper"], "pct", "40")
    assert (first.qty, first.po_status) == (D("4"), "part_received")
    with pytest.raises(ServiceEntryError, match="more than the 6"):
        _confirm(svc, po_no, logins["keeper"], "qty", "6.0001")
    svc.rollback()
    second = _confirm(svc, po_no, logins["keeper"], "amount", "600")

    assert (second.qty, second.po_status) == (D("6"), "received")
    assert svc.execute(text("select count(*) from service_entry")).scalar_one() == 2


def test_only_the_person_who_asked_confirms_and_never_the_buyer_or_on_goods(svc: Session, outbox: Outbox) -> None:  # noqa: F811
    po_no, logins = _service_po(svc)

    with pytest.raises(ServiceEntryError, match="only the person who asked"):
        _confirm(svc, po_no, logins["stores"])
    svc.rollback()
    with pytest.raises(goods.ReceiptError, match="is a service"):  # the store does not receive a service
        goods.post_receipt(svc, po_no, [goods.LineIn(1, D("10"))], goods.Header(), logins["stores"], as_of=TODAY)
    svc.rollback()
    svc.execute(text("update po set buyer = 'U4'"))  # the requester also bought it
    svc.commit()
    with pytest.raises(ServiceEntryError, match="segregation of duties: the buyer"):
        _confirm(svc, po_no, logins["keeper"])


def test_given_goods_when_the_requester_tries_to_confirm_them_as_a_service_then_it_is_refused(
    svc: Session, outbox: Outbox
) -> None:  # noqa: F811
    po_no, logins = _sent(svc)
    svc.execute(text("update request set requester = 'U4'"))
    svc.commit()

    with pytest.raises(ServiceEntryError, match="is goods"):
        _confirm(svc, po_no, logins["keeper"])


# ---- the two-way match ------------------------------------------------------------------------


def test_given_an_unconfirmed_service_when_it_is_invoiced_then_it_is_held_and_released_by_the_confirmation(
    svc: Session, outbox: Outbox
) -> None:  # noqa: F811
    po_no, logins = _service_po(svc)

    held = _invoice(svc, po_no, user=logins["buyer"])

    assert (held.status, held.reasons) == ("blocked", [matching.SERVICE])
    row = svc.execute(text("select payment_block, block_reason from invoice where no = :n"), {"n": held.no}).one()
    assert row[0] == "match" and row[1].startswith("Service not confirmed")

    done = _confirm(svc, po_no, logins["keeper"])

    assert done.rematched == [held.no]
    svc.expire_all()
    assert svc.execute(text("select status from invoice where no = :n"), {"n": held.no}).scalar_one() == "approved"


def test_given_a_sent_service_nobody_confirmed_yet_then_its_invoice_can_be_entered_on_the_screen(
    svc: Session, outbox: Outbox
) -> None:  # noqa: F811
    po_no, logins = _service_po(svc)  # found by the Brightwave walk-through (E46): the line was missing from the form

    ready = client.get("/api/invoicing/ready", headers=_headers(svc.info["slug"], logins["buyer"], "Buyer"))

    assert ready.status_code == 200, ready.text
    assert [(r["po_no"], r["line_no"], r["available"]) for r in ready.json()] == [(po_no, 1, "10")]
    held = _invoice(svc, po_no, user=logins["buyer"])  # entered from that line, it is held until confirmed
    assert held.reasons == [matching.SERVICE]


def test_given_the_requester_confirmed_a_service_when_they_approve_its_blocked_invoice_then_segregation_refuses_it(
    svc: Session, outbox: Outbox
) -> None:  # noqa: F811
    po_no, logins = _service_po(svc, requester="U2")  # the CFO asked for it; she may release blocked invoices
    _confirm(svc, po_no, logins["cfo"], "pct", "50")  # confirming her own service is her duty, and allowed
    held = _invoice(svc, po_no, user=logins["buyer"])  # all ten invoiced, five confirmed
    assert held.reasons == [matching.SERVICE]
    pid = invoices.request_release(svc, held.no, "paid in advance by agreement", logins["buyer"])

    proposals.approve(svc, pid, logins["cfo"])

    svc.expire_all()
    run = svc.execute(text("select execution, result from proposal where id = :i"), {"i": pid}).one()
    assert run[0] == "failed" and "segregation of duties" in run[1]["error"]
    assert svc.execute(text("select status from invoice where no = :n"), {"n": held.no}).scalar_one() == "blocked"


# ---- the emailed link -------------------------------------------------------------------------


def test_given_a_confirmation_link_when_it_is_used_then_it_works_once_and_an_expired_or_foreign_one_never(
    svc: Session, outbox: Outbox
) -> None:  # noqa: F811
    po_no, logins = _service_po(svc)
    slug = svc.info["slug"]
    token = service_links.make(slug, po_no, 1, logins["keeper"], 0)

    assert client.get(f"/confirm-service/{token}").status_code == 200  # opening it changes nothing
    assert svc.execute(text("select count(*) from service_entry")).scalar_one() == 0
    r = client.post(f"/confirm-service/{token}", data={"basis": "pct", "value": "30"})
    assert r.status_code == 200 and "SRN-BLR-2627-00001" in r.text
    again = client.post(f"/confirm-service/{token}", data={"basis": "pct", "value": "30"})
    assert again.status_code == 409 and "has been used" in again.text
    svc.expire_all()
    assert svc.execute(text("select qty_accepted, via from service_entry")).all() == [(D("3"), "emailed link")]

    old = service_links.make(slug, po_no, 1, logins["keeper"], 1, now=time.time() - 8 * 86400)
    with pytest.raises(LinkError, match="expired"):
        service_links.read(old)
    assert client.get(f"/confirm-service/{old}").status_code == 400
    from p2p.approvals import email_links  # an approval link is not a confirmation link

    with pytest.raises(LinkError, match="not valid"):
        service_links.read(email_links.make(slug, po_no, 1, 1, logins["keeper"]))
    for_someone_else = service_links.make(slug, po_no, 1, logins["stores"], 1)
    assert client.post(f"/confirm-service/{for_someone_else}", data={"basis": "pct", "value": "10"}).status_code == 409


def test_at_the_end_of_a_period_each_requester_gets_one_email_with_a_link_per_service(
    svc: Session, outbox: Outbox
) -> None:  # noqa: F811
    po_no, logins = _service_po(svc)

    told = service_links.ask_requesters(svc, svc.info["slug"])

    assert told == [("U4", 1)]
    assert (
        svc.execute(text("select count(*) from audit_event where action = 'service.confirm_email'")).scalar_one() == 1
    )


# ---- serial numbers ---------------------------------------------------------------------------


def test_given_a_serialised_item_when_a_serial_is_received_twice_then_the_second_receipt_is_refused(
    svc: Session, outbox: Outbox
) -> None:  # noqa: F811
    svc.execute(text("update item set serialised = true where code = 'A'"))
    svc.commit()
    po_no, logins = _sent(svc, qty="4")

    def receive(*serials: str) -> goods.Posted:
        line = goods.LineIn(1, D(len(serials)), serials=list(serials))
        return goods.post_receipt(svc, po_no, [line], goods.Header(), logins["stores"], as_of=TODAY)

    first = receive("LT-1", "LT-2")
    with pytest.raises(goods.ReceiptError, match="already received for A: LT-2"):
        receive("lt-2", "LT-3")
    svc.rollback()
    with pytest.raises(goods.ReceiptError, match="1 serial number"):
        goods.post_receipt(svc, po_no, [goods.LineIn(1, D("1"))], goods.Header(), logins["stores"], as_of=TODAY)
    svc.rollback()

    assert svc.execute(text("select serial, gr_no from serial_number order by serial")).all() == [
        ("LT-1", first.no),
        ("LT-2", first.no),
    ]


# ---- goods received without an order ----------------------------------------------------------


def _unordered(shop_: Session, user: int, qty: str = "3") -> str:
    data = unordered.UnorderedIn(
        "S1", "A", D(qty), "WH-BLR", "Supplier sent spares the plant had asked for by phone", "DC/77"
    )
    return unordered.record(shop_, data, user, as_of=TODAY)


def _state(shop_: Session, no: str) -> str:
    shop_.expire_all()
    return next(r["status"] for r in unordered.listing(shop_) if r["no"] == no)


def test_given_goods_without_an_order_when_the_store_records_them_then_they_wait_outside_stock_for_the_purchase_manager(
    svc: Session, outbox: Outbox
) -> None:  # noqa: F811
    _, logins = _sent(svc)  # the logins and an order to compare with
    no = _unordered(svc, logins["stores"])

    assert _state(svc, no) == "pending" and _on_hand(svc) == D("0")
    pid = svc.execute(text("select proposal_id from unordered_receipt where no = :n"), {"n": no}).scalar_one()
    assert svc.execute(text("select action, status from proposal where id = :i"), {"i": pid}).one() == (
        "receipt.unordered",
        "pending",
    )
    with pytest.raises(permissions.PermissionDenied):  # the person who took them in cannot approve them
        proposals.approve(svc, pid, logins["stores"])
    with pytest.raises(permissions.PermissionDenied):  # nor someone without the purchase manager's right
        proposals.approve(svc, pid, logins["keeper"])
    with pytest.raises(unordered.UnorderedError, match="once the purchase manager has approved"):
        unordered.raise_order(svc, svc.info["slug"], no, logins["buyer"], D("100"), as_of=TODAY)


def test_given_an_approved_unordered_receipt_when_the_buyer_orders_it_then_the_store_receives_it_on_that_order(
    svc: Session, outbox: Outbox
) -> None:  # noqa: F811
    slug = svc.info["slug"]
    _, logins = _sent(svc)
    no = _unordered(svc, logins["stores"])
    pid = svc.execute(text("select proposal_id from unordered_receipt where no = :n"), {"n": no}).scalar_one()
    proposals.approve(svc, pid, logins["procurement"])
    assert _state(svc, no) == "approved"

    po_no = unordered.raise_order(svc, slug, no, logins["buyer"], D("100"), as_of=TODAY)
    with pytest.raises(unordered.UnorderedError, match="approved and sent"):
        unordered.receive_on_order(svc, slug, no, logins["stores"], as_of=TODAY)
    svc.rollback()
    if svc.execute(text("select status from po where no = :p"), {"p": po_no}).scalar_one() == "submitted":
        workflow.approve(svc, slug, po_no, logins["procurement"])
    dispatch.send_po(svc, po_no)
    gr_no = unordered.receive_on_order(svc, slug, no, logins["stores"], as_of=TODAY)

    assert _state(svc, no) == "received" and _on_hand(svc) == D("3")  # the three now enter stock
    req = svc.execute(
        text("select r.channel, r.source_ref from po join request r on r.no = po.request_no where po.no = :p"),
        {"p": po_no},
    ).one()
    assert req == ("unordered_receipt", no)
    assert svc.execute(text("select po_no from goods_receipt where no = :g"), {"g": gr_no}).scalar_one() == po_no
    actions = {
        r[0]
        for r in svc.execute(
            text("select action from audit_event where entity = 'unordered_receipt' and entity_id = :n"), {"n": no}
        )
    }
    assert actions == {"unordered.record", "unordered.approve", "unordered.order", "unordered.receive"}


def test_given_an_unordered_receipt_when_the_purchase_manager_rejects_it_then_the_goods_go_back_and_never_into_stock(
    svc: Session, outbox: Outbox
) -> None:  # noqa: F811
    slug = svc.info["slug"]
    _, logins = _sent(svc)
    no = _unordered(svc, logins["stores"])

    unordered.reject(svc, no, logins["procurement"], "Nobody asked for these")

    assert _state(svc, no) == "rejected"
    unordered.mark_returned(svc, slug, no, logins["stores"], "RTN/5", "MH 12 XX 0000", as_of=TODAY)
    assert _state(svc, no) == "returned" and _on_hand(svc) == D("0")
    with pytest.raises(unordered.UnorderedError, match="only rejected goods"):
        unordered.mark_returned(svc, slug, no, logins["stores"], None, None, as_of=TODAY)
    assert [r["no"] for r in unordered.listing(svc, open_only=True)] == []


def test_a_company_sees_only_its_own_service_lines_and_unordered_receipts(svc: Session, outbox: Outbox) -> None:  # noqa: F811
    po_no, logins = _service_po(svc)
    _unordered(svc, logins["stores"])
    other = "b" + uuid.uuid4().hex[:8]
    assert tenants.create_tenant(other, load_reference=False).ok
    try:
        mine = _headers(svc.info["slug"], logins["stores"], "Approver")
        theirs = _headers(other, logins["stores"], "Approver")
        assert len(client.get("/api/unordered-receipts", headers=mine).json()) == 1
        assert client.get("/api/unordered-receipts", headers=theirs).json() == []
        keeper_there = _headers(other, logins["keeper"], "Requester")
        assert client.get("/api/services/to-confirm", headers=keeper_there).json()["lines"] == []
        r = client.post(
            "/api/services/confirm",
            headers=keeper_there,
            json={"po_no": po_no, "line_no": 1, "basis": "pct", "value": "100"},
        )
        assert r.status_code == 404
        here = client.get("/api/services/to-confirm", headers=_headers(svc.info["slug"], logins["keeper"], "Requester"))
        assert [ln["po_no"] for ln in here.json()["lines"]] == [po_no]
    finally:
        routing.clear_engine_cache()


# ---- block 9: a service entry can be reversed --------------------------------------------------------


def test_given_a_confirmed_service_when_its_confirmer_reverses_it_then_the_line_goes_back_and_no_budget_moves(
    svc: Session, outbox: Outbox
) -> None:  # noqa: F811
    po_no, logins = _service_po(svc)
    done = _confirm(svc, po_no, logins["keeper"])
    assert _line(svc) == (D("10"), "received")

    from p2p.receiving import reversal

    got = reversal.reverse_service_entry(
        svc, svc.info["slug"], done.no, logins["keeper"], "Done for another site", as_of=TODAY
    )

    assert got == {"status": "reversed"}
    assert _line(svc) == (D("0"), "open")
    assert svc.execute(text("select status from po where no = :p"), {"p": po_no}).scalar_one() == "sent"
    assert svc.execute(text("select status, reversal_reason from service_entry")).one() == (
        "reversed",
        "Done for another site",
    )
    assert svc.execute(text("select count(*) from audit_event where action = 'service.reverse'")).scalar_one() == 1
    assert svc.execute(text("select count(*) from budget_actual")).scalar_one() == 0


def test_another_person_cannot_reverse_the_services_confirmation(svc: Session, outbox: Outbox) -> None:  # noqa: F811
    po_no, logins = _service_po(svc)
    done = _confirm(svc, po_no, logins["keeper"])

    from p2p.receiving import reversal

    with pytest.raises(reversal.ReversalError, match="only the person who confirmed the service"):
        reversal.reverse_service_entry(svc, svc.info["slug"], done.no, logins["cfo"], "Not mine", as_of=TODAY)


def test_a_service_already_invoiced_is_not_reversed(svc: Session, outbox: Outbox) -> None:  # noqa: F811
    po_no, logins = _service_po(svc)
    done = _confirm(svc, po_no, logins["keeper"])
    svc.execute(text("update po_line set invoiced_qty = 10 where po_no = :p"), {"p": po_no})
    svc.commit()

    from p2p.receiving import reversal

    with pytest.raises(reversal.ReversalError, match="is invoiced"):
        reversal.reverse_service_entry(svc, svc.info["slug"], done.no, logins["keeper"], "Undo", as_of=TODAY)
