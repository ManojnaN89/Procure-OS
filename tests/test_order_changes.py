"""Purchase orders after they are made: grouping by terms, the order document, the budget again at the final amount,
revising (a new version), cancelling, and closing what is still to come."""

from datetime import date
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session
from test_dispatch import _approved_order
from test_orders import TODAY, _headers, _logins, _price, _request, _sent_rfq, _source, _three_quotes, shop  # noqa: F401
from test_receiving import _receive, _sent, store  # noqa: F401 - fixtures
from test_sourcing import Outbox, db, outbox  # noqa: F401 - fixtures

from p2p.main import app
from p2p.orders import changes, dispatch, document, quotes, service, workflow
from p2p.orders import contracts as contract_usage
from p2p.platform import numbering, proposals, sod
from p2p.receiving.stock import stock_levels

D = Decimal
client = TestClient(app)


def _pdf_text(data: bytes) -> str:
    import pypdfium2 as pdfium

    pdf = pdfium.PdfDocument(data)
    try:
        return "\n".join(pdf[i].get_textpage().get_text_range() for i in range(len(pdf)))
    finally:
        pdf.close()


# ---- one order per supplier and set of terms ------------------------------------------------------


def test_lines_of_one_supplier_on_different_terms_make_separate_orders(shop: Session, outbox: Outbox) -> None:  # noqa: F811
    # given line A on the contract (net 45, delivery at destination) and line B awarded to the same supplier on a quote
    # with no terms (the supplier's own net 30)
    request, rfq = _sent_rfq(shop, outbox)
    _three_quotes(shop, rfq)
    quotes.award(shop, rfq, {1: "S1"}, sod.user(7), as_of=TODAY)

    # when the request is ordered
    made = service.order_request(shop, shop.info["slug"], request, as_of=TODAY)

    # then each set of terms keeps its own order, so the contract's terms are not lost
    assert len(made.pos) == 2
    rows = shop.execute(
        text("select po_type, contract_code, payment_terms_code, delivery_terms from po order by no")
    ).all()
    assert sorted(rows, key=lambda r: r[0]) == [
        ("contract_release", "K-ACTIVE", "NET45", "for_destination"),
        ("standard", None, "NET30", None),
    ]


# ---- numbering --------------------------------------------------------------------------------------


def test_order_numbers_run_per_unit_and_restart_with_the_financial_year(shop: Session) -> None:  # noqa: F811
    def next_po(on: date, location: str) -> str:
        number = numbering.next_number(shop, "po", on, location_code=location)
        shop.commit()
        return number

    assert next_po(date(2027, 3, 31), "WH-BLR") == "PO-BLR-2627-00001"  # the last day of the financial year
    assert next_po(date(2027, 3, 31), "WH-BLR") == "PO-BLR-2627-00002"
    assert next_po(date(2027, 3, 31), "WH-PUN") == "PO-PUN-2627-00001"  # another unit has its own run
    assert next_po(date(2027, 4, 1), "WH-BLR") == "PO-BLR-2728-00001"  # the next year starts again at one


# ---- the document -----------------------------------------------------------------------------


def test_the_order_document_has_the_order_its_lines_its_tax_and_its_terms(shop: Session, outbox: Outbox) -> None:  # noqa: F811
    po_no = _approved_order(shop, _logins(shop))

    pdf = document.order_document(shop, po_no)

    assert pdf.startswith(b"%PDF")
    page = _pdf_text(pdf)
    for expected in (
        po_no,
        "Bearing",
        "Rs 1,180.00",
        "Order total",
        "Total before tax",
        "IGST",
        "Payment terms: NET45",
    ):
        assert expected in page, expected
    assert "version" not in page.lower().split("purchase order")[1][:60]  # a first version says nothing about a version


def test_the_terms_come_from_the_companys_template_and_a_broken_one_falls_back(shop: Session) -> None:  # noqa: F811
    po_no = _approved_order(shop, _logins(shop))
    shop.execute(
        text(
            "insert into org_setting (key, value) values ('po_terms_template', 'Pay within {{ payment_terms }} for {{ po_no }}.')"
        )
    )
    shop.commit()
    assert "Pay within NET45 for " + po_no in _pdf_text(document.order_document(shop, po_no))

    for broken in ("{{ unclosed", "{{ ''.__class__.__mro__ }}"):  # a syntax error, and an attempt the sandbox refuses
        shop.execute(text("update org_setting set value = :v where key = 'po_terms_template'"), {"v": broken})
        shop.commit()
        assert "Quote the order number " + po_no in _pdf_text(document.order_document(shop, po_no))


def test_the_document_goes_to_the_supplier_with_the_order_email(shop: Session, outbox: Outbox) -> None:  # noqa: F811
    logins = _logins(shop)
    po_no = _approved_order(shop, logins)
    outbox.sent.clear()

    dispatch.send_po(shop, po_no, sod.user(logins["buyer"]))

    (mail,) = outbox.sent
    ((name, kind, data),) = mail["attachments"]
    assert (name, kind) == (f"{po_no}.pdf", "application/pdf") and data.startswith(b"%PDF")


def test_the_document_can_be_opened_from_the_order_page(shop: Session) -> None:  # noqa: F811
    logins = _logins(shop)
    po_no = _approved_order(shop, logins)
    h = _headers(shop.info["slug"], logins["buyer"], "Buyer")

    got = client.get(f"/api/purchase-orders/{po_no}/pdf", headers=h)

    assert (
        got.status_code == 200 and got.headers["content-type"] == "application/pdf" and got.content.startswith(b"%PDF")
    )
    assert client.get("/api/purchase-orders/NOPE/pdf", headers=h).status_code == 404
    assert client.get(f"/api/purchase-orders/{po_no}/pdf").status_code == 401


# ---- the budget again, at the final amount --------------------------------------------------------


def _budget(db: Session, per_month: str, policy: str) -> None:
    """G1 in CC1 (at the store), from April: eight months have run by the test's date."""
    x = db.execute
    x(text("insert into gl_account (code, name) values ('G1', 'Materials')"))
    x(text("insert into cost_centre (code, name, location_code, active) values ('CC1', 'Production', 'WH-BLR', true)"))
    for month in (4, 5, 6, 7, 8, 9, 10, 11):
        x(
            text(
                "insert into budget (cost_centre_code, gl_code, period, amount, version, policy) "
                "values ('CC1', 'G1', :p, :a, 1, :pol)"
            ),
            {"p": f"2026-{month:02d}", "a": per_month, "pol": policy},
        )
    db.commit()


def _ordered_at_a_higher_price(db: Session) -> tuple[str, service.OrderResult]:
    """A request estimated at 90 a unit (900 for ten) that the contract supplies at 100 (1,000): over the estimate by
    11%, inside the 15% band, so nothing but the budget can stop it."""
    _price(db, "S1", "A", "100")
    request = _request(db, "R-BUD", [("A", "10", None, "C1")])
    db.execute(
        text("update request_line set gl_code = 'G1', cost_centre_code = 'CC1', est_price = 90 where request_no = :r"),
        {"r": request},
    )
    db.commit()
    _source(db, request, dispatch=False)
    return request, service.order_request(db, db.info["slug"], request, as_of=TODAY)


def test_an_order_that_fits_the_budget_at_its_final_price_carries_no_budget_note(shop: Session) -> None:  # noqa: F811
    _budget(shop, "200", "block")  # 8 x 200 = 1,600

    _, made = _ordered_at_a_higher_price(shop)

    assert made.held == [] and made.waiting
    reason = shop.execute(text("select decision_reason from po")).scalar_one()
    assert "budget" not in reason.lower()


def test_a_final_price_over_the_budget_is_only_a_warning_when_the_policy_warns(shop: Session) -> None:  # noqa: F811
    _budget(shop, "120", "warn")  # 960: the request's 900 fits, the order's 1,000 does not

    _, made = _ordered_at_a_higher_price(shop)

    assert made.held == [] and made.waiting  # it goes to a person, with the reason said
    reason = shop.execute(text("select decision_reason from po")).scalar_one()
    assert "Over budget: GL G1 by ₹ 40.00" in reason


def test_a_final_price_over_the_budget_is_held_when_the_policy_blocks_until_a_person_accepts(shop: Session) -> None:  # noqa: F811
    logins = _logins(shop)
    _budget(shop, "120", "block")

    _, made = _ordered_at_a_higher_price(shop)

    (po_no,) = made.held
    po = shop.execute(text("select status, hold_reason, hold_proposal_id, budget_accepted from po")).one()
    assert po[0] == "draft" and "Over budget: GL G1 by ₹ 40.00" in po[1] and po[2] and po[3] is False
    with pytest.raises(Exception, match="on hold"):
        workflow.submit(shop, shop.info["slug"], po_no, sod.user(logins["buyer"]))

    proposals.approve(shop, int(po[2]), logins["procurement"])  # a person accepts it knowingly

    after = shop.execute(text("select status, hold_reason, budget_accepted from po")).one()
    assert after[0] == "submitted" and after[1] is None and after[2] is True  # on to approval, not held again
    assert shop.execute(text("select count(*) from audit_event where action = 'po.budget_hold'")).scalar_one() == 1


# ---- cancel ---------------------------------------------------------------------------------------


def _on_the_way(db: Session) -> Decimal:
    db.execute(text("update po_line set base_qty = qty where base_qty is null"))
    db.commit()
    db.expire_all()
    return next((x.on_order for x in stock_levels(db) if x.item_code == "A"), D(0))


def _watch_item_a(db: Session) -> None:
    db.execute(
        text("insert into stock_opening (item_code, location_code, qty, as_of) values ('A', 'WH-BLR', 5, '2026-01-01')")
    )
    db.execute(
        text(
            "insert into item_location (item_code, location_code, reorder_policy, reorder_point, safety_stock, max_qty) "
            "values ('A', 'WH-BLR', 'minmax', 20, 2, 100)"
        )
    )
    db.commit()


def test_cancelling_a_sent_order_gives_back_the_contract_the_open_supply_and_the_need(
    store: Session, outbox: Outbox
) -> None:  # noqa: F811
    _watch_item_a(store)
    po_no, logins = _sent(store)
    assert contract_usage.usage(store, "K-ACTIVE").used == D("1000.00") and _on_the_way(store) == D(10)
    outbox.sent.clear()

    done = changes.cancel(store, store.info["slug"], po_no, logins["buyer"], "The plant no longer needs it")

    assert done == {"status": "cancelled", "supplier_told": True}
    po = store.execute(text("select status, cancel_reason from po")).one()
    assert po[0] == "cancelled" and "The plant no longer needs it" in po[1]
    assert contract_usage.usage(store, "K-ACTIVE").used == D(0) and _on_the_way(store) == D(0)
    assert store.execute(text("select status from request_line")).scalar_one() == "cancelled"  # the need is dropped
    assert store.execute(text("select status from request")).scalar_one() == "closed"
    (mail,) = [m for m in outbox.sent if "cancelled" in m["subject"]]
    assert mail["to"] == "s1@supplier.example" and "The plant no longer needs it" in mail["body"]
    assert store.execute(text("select count(*) from audit_event where action = 'po.cancel'")).scalar_one() == 1


def test_an_order_cancelled_to_be_ordered_again_frees_its_lines(store: Session, outbox: Outbox) -> None:  # noqa: F811
    po_no, logins = _sent(store)

    changes.cancel(store, store.info["slug"], po_no, logins["buyer"], "Wrong supplier", reorder=True)

    assert store.execute(text("select status from request_line")).scalar_one() == "sourcing"
    assert store.execute(text("select status from request")).scalar_one() == "approved"
    made = service.order_request(store, store.info["slug"], "R-A-10", as_of=TODAY)  # a second order can be made
    assert len(made.pos) == 1 and made.pos != [po_no]


def test_cancelling_an_order_waiting_for_approval_withdraws_its_steps_and_a_hold(shop: Session) -> None:  # noqa: F811
    logins = _logins(shop)
    _price(shop, "S1", "A", "100")
    request = _request(shop, "R-HOLD", [("A", "10", None, "C1")])
    shop.execute(text("update request_line set est_price = 50"))  # 100% above: held for a price variance
    shop.commit()
    _source(shop, request, dispatch=False)
    made = service.order_request(shop, shop.info["slug"], request, as_of=TODAY)
    (po_no,) = made.held
    proposal = shop.execute(text("select hold_proposal_id from po")).scalar_one()

    changes.cancel(shop, shop.info["slug"], po_no, logins["buyer"], "Not needed")

    assert shop.execute(text("select status from proposal where id = :i"), {"i": proposal}).scalar_one() == "expired"


@pytest.mark.parametrize("status", ["received", "closed", "cancelled"])
def test_an_order_past_the_point_of_cancelling_is_refused(store: Session, status: str) -> None:  # noqa: F811
    po_no, logins = _sent(store)
    store.execute(text("update po set status = :s"), {"s": status})
    store.commit()

    with pytest.raises(changes.ChangeError, match=f"is {status}"):
        changes.cancel(store, store.info["slug"], po_no, logins["buyer"], "Why not")


def test_goods_received_or_an_invoice_stop_a_cancellation_and_a_reason_is_always_needed(store: Session) -> None:  # noqa: F811
    po_no, logins = _sent(store)
    with pytest.raises(changes.ChangeError, match="reason is needed"):
        changes.cancel(store, store.info["slug"], po_no, logins["buyer"], "  ")

    _receive(store, po_no, "4")

    with pytest.raises(changes.ChangeError, match="received against .* close what is still to come"):
        changes.cancel(store, store.info["slug"], po_no, logins["buyer"], "Too late")
    assert store.execute(text("select status from po")).scalar_one() in ("sent", "part_received")


# ---- close what is still to come ------------------------------------------------------------------


def test_closing_the_balance_after_a_part_delivery_ends_the_open_supply_and_tells_the_supplier(
    store: Session, outbox: Outbox
) -> None:  # noqa: F811
    _watch_item_a(store)
    store.execute(
        text(
            "insert into approval_rule (rule_code, document, min_value, max_value, category_code, department_code, "
            "spend_type, approver_chain, version, effective_from) values "
            "('SC-1', 'short_close', 0, 25000, 'ANY', 'ANY', 'ANY', 'auto', 1, '2024-11-01')"
        )
    )
    store.commit()
    po_no, logins = _sent(store)
    _receive(store, po_no, "4")
    assert _on_the_way(store) == D(6)
    outbox.sent.clear()

    done = changes.close_balance(store, store.info["slug"], po_no, logins["buyer"], "Supplier cannot make the rest")

    # block 13 R1: below the limit with no flag the buyer's own decision closes it at once
    assert done["status"] == "closed" and done["risk"] == "low" and "balance closed" in outbox.sent[0]["subject"]
    line = store.execute(text("select status, received_qty, short_closed_qty from po_line")).one()
    assert line == ("short_closed", D("4"), D("6"))
    assert _on_the_way(store) == D(0)
    po = store.execute(text("select status, closed_reason from po")).one()
    # block 13 R1.6-R1.7: the order is received, not closed; the reason lives on po_short_close
    assert po == ("received", None)
    assert any("balance closed" in m["subject"] for m in outbox.sent)
    assert store.execute(text("select count(*) from audit_event where action = 'po.close_balance'")).scalar_one() == 1


def test_a_balance_cannot_be_closed_when_nothing_was_received_or_nothing_is_left(store: Session) -> None:  # noqa: F811
    po_no, logins = _sent(store)
    with pytest.raises(changes.ChangeError, match="nothing has been received .* cancel it instead"):
        changes.close_balance(store, store.info["slug"], po_no, logins["buyer"], "Why")

    _receive(store, po_no, "10")  # everything came

    with pytest.raises(changes.ChangeError, match="is received|nothing is still to come"):
        changes.close_balance(store, store.info["slug"], po_no, logins["buyer"], "Why")


# ---- revise: a new version ------------------------------------------------------------------------


def test_a_smaller_quantity_is_a_new_version_with_a_lower_total_and_the_supplier_gets_the_revised_order(
    store: Session, outbox: Outbox
) -> None:  # noqa: F811
    po_no, logins = _sent(store)
    outbox.sent.clear()

    done = changes.revise(
        store, store.info["slug"], po_no, logins["buyer"], "Production plan cut", [changes.LineChange(1, qty=D("8"))]
    )

    # 8 x 100.00 = 800.00 and 18% IGST = 144.00
    assert (done["version"], done["status"], done["total"], done["reapproval"]) == (2, "sent", "944.00", False)
    po = store.execute(text("select version, taxable, igst, total, status from po")).one()
    assert po == (2, D("800.00"), D("144.00"), D("944.00"), "sent")
    assert store.execute(text("select qty, taxable, igst from po_line")).one() == (D("8"), D("800.00"), D("144.00"))
    (mail,) = outbox.sent
    assert "(version 2)" in mail["subject"] and mail["to"] == "s1@supplier.example"
    ((name, _kind, data),) = mail["attachments"]
    assert name == f"{po_no}-v2.pdf" and "version 2" in _pdf_text(data) and "Rs 944.00" in _pdf_text(data)
    history = changes.history(store, po_no)
    assert [(h["version"], h["total_before"], h["total_after"], h["reapproval"]) for h in history] == [
        (2, "1180.00", "944.00", False)
    ]
    assert store.execute(text("select count(*) from audit_event where action = 'po.revise'")).scalar_one() == 1


def test_a_higher_total_goes_through_approval_again_on_the_new_amount(store: Session, outbox: Outbox) -> None:  # noqa: F811
    # block 4: a change is measured from the approved version, so going back up to the approved 10 carries the
    # approval; a total above the approved one (12, the request allowing it) goes for approval again
    po_no, logins = _sent(store)
    store.execute(text("update request_line set qty = 12"))
    store.commit()
    changes.revise(store, store.info["slug"], po_no, logins["buyer"], "Cut", [changes.LineChange(1, qty=D("8"))])
    back = changes.revise(
        store, store.info["slug"], po_no, logins["buyer"], "Back to the need", [changes.LineChange(1, qty=D("10"))]
    )
    assert (back["reapproval"], back["status"]) == (False, "sent")
    outbox.sent.clear()

    done = changes.revise(
        store, store.info["slug"], po_no, logins["buyer"], "More is needed", [changes.LineChange(1, qty=D("12"))]
    )

    assert (done["version"], done["reapproval"], done["status"]) == (4, True, "submitted")
    assert done["material"] == ["line 1: quantity up", "the total went up"]
    po = store.execute(text("select status, approval_round, total, sent_at from po")).one()
    assert po[0] == "submitted" and po[1] == 2 and po[2] == D("1416.00") and po[3] is None
    assert outbox.sent == []  # nothing goes out until it is approved again
    workflow.approve(store, store.info["slug"], po_no, logins["procurement"])
    dispatch.send_po(store, po_no, sod.user(logins["buyer"]))
    assert outbox.sent[-1]["subject"] == f"Purchase order {po_no} (version 4)"


@pytest.mark.parametrize(
    ("to", "material"),
    [(date(2026, 11, 23), False), (date(2026, 11, 24), True), (date(2026, 11, 17), False), (date(2026, 11, 16), True)],
)  # promised 20 Nov; the date tolerance is 3 days either way (Q19)
def test_a_date_within_the_tolerance_is_a_new_version_only_and_beyond_it_goes_for_approval_again(
    store: Session,
    outbox: Outbox,
    to: date,
    material: bool,  # noqa: F811
) -> None:
    po_no, logins = _sent(store)

    done = changes.revise(
        store, store.info["slug"], po_no, logins["buyer"], "Plant shutdown", [changes.LineChange(1, need_by=to)]
    )

    assert (done["version"], done["reapproval"]) == (2, material)
    assert done["status"] == ("submitted" if material else "sent")
    assert store.execute(text("select need_by from po_line")).scalar_one() == to
    (entry,) = changes.history(store, po_no)
    assert entry["changes"] == [{"line": 1, "need_by": {"from": "2026-11-20", "to": to.isoformat()}}]


def test_a_revision_clears_the_suppliers_acknowledgement_and_its_new_link_answers_only_the_new_version(
    store: Session,
    outbox: Outbox,  # noqa: F811
) -> None:
    po_no, logins = _sent(store)
    store.execute(text("update po set status = 'acknowledged', acknowledged_at = now()"))
    store.commit()
    outbox.sent.clear()

    done = changes.revise(
        store, store.info["slug"], po_no, logins["buyer"], "Production plan cut", [changes.LineChange(1, qty=D("8"))]
    )

    assert done["status"] == "sent"  # acknowledged version 1, not version 2
    assert store.execute(text("select acknowledged_at from po")).scalar_one() is None
    (mail,) = outbox.sent
    assert "/supplier/" in mail["body"] and "accept this version" in mail["body"]
    versions = store.execute(text("select po_version from supplier_link where purpose = 'respond' order by id")).all()
    assert [v[0] for v in versions][-1] == 2


@pytest.mark.parametrize(
    ("change", "message"),
    [
        (changes.LineChange(1, qty=D("11")), "more than the request asked for"),
        (changes.LineChange(1, qty=D("0")), "must be more than zero"),
        (changes.LineChange(1, qty=D("3")), "has already been received"),
        (changes.LineChange(9, qty=D("5")), "not an open line"),
        (changes.LineChange(1), "nothing to change"),
    ],
)
def test_a_revision_that_makes_no_sense_is_refused_and_changes_nothing(
    store: Session, change: changes.LineChange, message: str
) -> None:  # noqa: F811
    po_no, logins = _sent(store)
    _receive(store, po_no, "4")
    before = store.execute(text("select version, total from po")).one()

    with pytest.raises(changes.ChangeError, match=message):
        changes.revise(store, store.info["slug"], po_no, logins["buyer"], "Try", [change])

    assert store.execute(text("select version, total from po")).one() == before


def test_a_revision_needs_a_reason_a_change_and_an_order_that_is_not_received(store: Session) -> None:  # noqa: F811
    po_no, logins = _sent(store)
    with pytest.raises(changes.ChangeError, match="reason is needed"):
        changes.revise(store, store.info["slug"], po_no, logins["buyer"], None, [changes.LineChange(1, qty=D("5"))])
    with pytest.raises(changes.ChangeError, match="nothing to change"):
        changes.revise(store, store.info["slug"], po_no, logins["buyer"], "Why", [])
    store.execute(text("update po set status = 'received'"))
    store.commit()
    with pytest.raises(changes.ChangeError, match="is received"):
        changes.revise(store, store.info["slug"], po_no, logins["buyer"], "Why", [changes.LineChange(1, qty=D("5"))])


# ---- the screens' side: permissions and what each person may do ------------------------------------


def test_the_buyer_changes_an_order_over_the_api_and_others_cannot(store: Session, outbox: Outbox) -> None:  # noqa: F811
    po_no, logins = _sent(store)
    slug = store.info["slug"]
    buyer = _headers(slug, logins["buyer"], "Buyer")
    keeper = _headers(slug, logins["keeper"], "Store")
    base = f"/api/purchase-orders/{po_no}"

    controls = client.get(f"{base}/controls", headers=buyer).json()
    assert (controls["version"], controls["can_revise"], controls["can_cancel"], controls["can_close_balance"]) == (
        1, True, True, False,
    )  # fmt: skip
    assert client.get(f"{base}/controls", headers=keeper).json()["can_cancel"] is False

    lines = {"lines": [{"line_no": 1, "qty": "8"}], "reason": "Cut"}
    assert client.post(f"{base}/revise", json=lines, headers=keeper).status_code == 403
    assert client.post(f"{base}/revise", json={"reason": "x"}, headers=buyer).status_code == 422
    assert (
        client.post(
            f"{base}/revise", json={"lines": [{"line_no": 1, "qty": "abc"}], "reason": "x"}, headers=buyer
        ).status_code
        == 422
    )
    assert (
        client.post(
            f"{base}/revise", json={"lines": [{"line_no": 1, "need_by": "soon"}], "reason": "x"}, headers=buyer
        ).status_code
        == 422
    )
    done = client.post(f"{base}/revise", json=lines, headers=buyer)
    assert done.status_code == 200 and done.json()["version"] == 2
    assert client.get(f"{base}/controls", headers=buyer).json()["history"][0]["reason"] == "Cut"

    assert client.post(f"{base}/cancel", json={"reason": ""}, headers=buyer).status_code == 409
    assert client.post(f"{base}/cancel", json={"reason": "Done"}, headers=keeper).status_code == 403
    assert client.post(f"{base}/cancel", json={"reason": "No longer needed"}, headers=buyer).status_code == 200
    assert (
        client.post(f"{base}/cancel", json={"reason": "again"}, headers=buyer).status_code == 409
    )  # already cancelled
    assert client.post("/api/purchase-orders/NOPE/cancel", json={"reason": "x"}, headers=buyer).status_code == 404
    assert client.post(f"{base}/close-balance", json={"reason": "x"}).status_code == 401


def test_the_buyer_closes_the_balance_of_a_part_delivered_order_over_the_api(store: Session, outbox: Outbox) -> None:  # noqa: F811
    store.execute(
        text(
            "insert into approval_rule (rule_code, document, min_value, max_value, category_code, department_code, "
            "spend_type, approver_chain, version, effective_from) values "
            "('SC-1', 'short_close', 0, 25000, 'ANY', 'ANY', 'ANY', 'auto', 1, '2024-11-01')"
        )
    )
    store.commit()
    po_no, logins = _sent(store)
    _receive(store, po_no, "4")
    buyer = _headers(store.info["slug"], logins["buyer"], "Buyer")
    base = f"/api/purchase-orders/{po_no}"

    controls = client.get(f"{base}/controls", headers=buyer).json()
    assert (controls["can_close_balance"], controls["can_cancel"]) == (True, False)
    seen = client.get(f"{base}/close-balance/preview", headers=buyer).json()
    assert seen["risk"] == "low" and seen["value"] == "708.00"  # 6 x 100 + 18%

    done = client.post(f"{base}/close-balance", json={"reason": "Supplier stopped making it"}, headers=buyer)

    assert done.status_code == 200 and done.json()["status"] == "closed"
    assert client.get(f"{base}/controls", headers=buyer).json()["can_close_balance"] is False
