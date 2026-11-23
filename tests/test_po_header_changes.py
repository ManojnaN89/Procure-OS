"""Changing an order's header (block 7, R11-R22): the delivery place (GST worked out again, approved again), the payment
terms (approved again; fixed once money moves), the delivery terms (a minor change: the approval carries, the supplier
answers the new version), the reviser as a maker, a touchless order a person changed, and every refusal."""

from datetime import date
from decimal import Decimal

from fastapi.testclient import TestClient
from po_versions_support import audits, buyer, order, revise, rows, second_place, sent_order, versions
from sqlalchemy import text
from sqlalchemy.orm import Session
from test_dispatch import _approved_order
from test_order_changes import _pdf_text
from test_orders import _headers, _logins, shop  # noqa: F401 - fixtures
from test_po_tiers import _gate, _order, tiers  # noqa: F401 - fixtures
from test_receiving import _receive, store  # noqa: F401 - fixtures
from test_sourcing import Outbox, _price, _request, _source, db, outbox  # noqa: F401 - fixtures
from test_supplier_confirmation import _token

from p2p.main import app
from p2p.orders import changes, confirmation, dispatch, service, supplier_links, workflow
from p2p.orders import versions as order_versions
from p2p.platform import notifications, sod

D = Decimal
client = TestClient(app)


def _lines(db: Session, po_no: str) -> list[tuple[Decimal, Decimal, Decimal, Decimal]]:
    return [
        (r["taxable"], r["cgst"], r["sgst"], r["igst"])
        for r in rows(db, "select * from po_line where po_no = :n order by line_no", n=po_no)
    ]


def _totals(db: Session, po_no: str) -> tuple[Decimal, Decimal, Decimal, Decimal, Decimal]:
    po = order(db, po_no)
    return po["taxable"], po["cgst"], po["sgst"], po["igst"], po["total"]


# ---- the delivery place ----------------------------------------------------------------------------------------


def test_moving_the_delivery_place_into_the_suppliers_state_splits_the_gst_and_is_approved_again(
    shop: Session,
    outbox: Outbox,  # noqa: F811
) -> None:
    # given an acknowledged order from S1 (Maharashtra) to the Bengaluru store: IGST 18% on 1,000.00
    second_place(shop)
    po_no, logins, token = sent_order(shop, outbox)
    dispatch.acknowledge(shop, po_no, sod.user(logins["buyer"]), promised=None, note=None)
    assert _totals(shop, po_no) == (D("1000.00"), D("0"), D("0"), D("180.00"), D("1180.00"))

    # when the buyer moves the delivery to the Pune store, in the supplier's state
    done = revise(shop, logins, po_no, {"header": {"ship_to_location": "WH-PUN"}, "version": 1})

    # then CGST and SGST replace IGST, the order carries the Pune GSTIN, and it goes for approval again
    assert done.status_code == 200, done.text
    body = done.json()
    assert (body["status"], body["reapproval"], body["material"]) == ("submitted", True, ["delivery location changed"])
    assert body["header_changes"] == [
        {"header": "ship_to_location", "from": "WH-BLR", "to": "WH-PUN", "gst": "CGST and SGST"}
    ]
    po = order(shop, po_no)
    assert (po["ship_to_location"], po["buyer_gstin"], po["acknowledged_version"], po["approved_version"]) == (
        "WH-PUN", "27BBBBB0000B2Z5", None, 1,
    )  # fmt: skip
    assert _totals(shop, po_no) == (D("1000.00"), D("90.00"), D("90.00"), D("0.00"), D("1180.00"))
    assert _lines(shop, po_no) == [(D("1000.00"), D("90.00"), D("90.00"), D("0.00"))]
    head = workflow.org_code_of(shop, logins["procurement"])
    assert rows(shop, "select approver, version from po_approval_step where status = 'pending'") == [
        {"approver": head, "version": 2}
    ]
    said = client.get(f"/api/purchase-orders/{po_no}/controls", headers=buyer(shop, logins)).json()["history"][0]
    assert said["said"] == ["delivery place WH-BLR to WH-PUN (GST now CGST and SGST)"]
    # the old link answers nothing now, and says the order changed
    assert "has changed since this link was sent" in client.get(f"/supplier/{token}").text

    # and once approved and sent, the document shows the new place and the split
    workflow.approve(shop, shop.info["slug"], po_no, logins["procurement"], version=2)
    dispatch.send_po(shop, po_no, sod.user(logins["buyer"]), tenant=shop.info["slug"])
    page = _pdf_text(order_versions.stored_pdf(shop, shop.info["slug"], po_no, 2) or b"")
    assert "Pune store (WH-PUN)" in page and "CGST" in page and "IGST" not in page

    # and back the other way: IGST once more
    back = revise(shop, logins, po_no, {"header": {"ship_to_location": "WH-BLR"}})
    assert back.json()["header_changes"][0]["gst"] == "IGST"
    assert _totals(shop, po_no) == (D("1000.00"), D("0.00"), D("0.00"), D("180.00"), D("1180.00"))


def test_cgst_and_sgst_are_each_rounded_while_igst_is_rounded_once_and_charges_follow_the_place(
    shop: Session,  # noqa: F811
) -> None:
    # given one bearing at 100.05 (IGST 18% = 18.009, so 18.01) with freight of 50.05 on the order
    second_place(shop)
    logins = _logins(shop)
    _price(shop, "S1", "A", "100.05")
    request = _request(shop, "R-ODD", [("A", "1", None, "C1")])
    _source(shop, request, dispatch=False)
    (po_no,) = service.order_request(shop, shop.info["slug"], request, as_of=date(2026, 11, 6)).waiting
    shop.execute(
        text(
            "insert into po_charge (po_no, type, amount, gst_rate, cgst, sgst, igst) "
            "values (:n, 'freight', 50.05, 18, 0, 0, 9.01)"
        ),
        {"n": po_no},
    )
    shop.execute(text("update po set taxable = 150.10, igst = 27.02, total = 177.12 where no = :n"), {"n": po_no})
    shop.commit()

    changes.revise(
        shop, shop.info["slug"], po_no, logins["buyer"], "Deliver to Pune", [],
        header=changes.HeaderChange(ship_to_location="WH-PUN"),
    )  # fmt: skip

    # then each half is 9.0045 -> 9.00 on the line and 4.5045 -> 4.50 on the freight: 0.02 less than IGST in all
    assert _lines(shop, po_no) == [(D("100.05"), D("9.00"), D("9.00"), D("0.00"))]
    assert rows(shop, "select cgst, sgst, igst from po_charge") == [
        {"cgst": D("4.50"), "sgst": D("4.50"), "igst": D("0.00")}
    ]
    assert _totals(shop, po_no) == (D("150.10"), D("13.50"), D("13.50"), D("0.00"), D("177.10"))
    (charge,) = versions(shop, po_no)[1]["snapshot"]["charges"]
    assert (charge["type"], D(charge["cgst"]), D(charge["sgst"]), D(charge["igst"])) == (
        "freight", D("4.50"), D("4.50"), D("0"),
    )  # fmt: skip


def test_the_delivery_place_is_fixed_once_goods_arrive_or_the_supplier_records_a_dispatch(
    store: Session,
    outbox: Outbox,  # noqa: F811
) -> None:
    second_place(store)
    po_no, logins, _ = sent_order(store, outbox)
    store.execute(
        text(
            "insert into dispatch_note (po_no, shipped_on, source, recorded_by) values (:n, '2026-11-10', 'link', 'S1')"
        ),
        {"n": po_no},
    )
    store.commit()
    shipped = revise(store, logins, po_no, {"header": {"ship_to_location": "WH-PUN"}})
    assert (shipped.status_code, shipped.json()["detail"]) == (
        409, "the supplier has recorded a dispatch to Bengaluru store: agree the new place with it first",
    )  # fmt: skip

    store.execute(text("delete from dispatch_note"))
    store.commit()
    _receive(store, po_no, "5")
    received = revise(store, logins, po_no, {"header": {"ship_to_location": "WH-PUN"}})
    assert (received.status_code, received.json()["detail"]) == (
        409, "goods have been received at Bengaluru store: the delivery place cannot change",
    )  # fmt: skip
    assert len(versions(store, po_no)) == 1 and audits(store, "po.revise", po_no) == []


def test_a_contract_price_for_one_region_refuses_a_place_outside_it(shop: Session, outbox: Outbox) -> None:  # noqa: F811
    second_place(shop)
    po_no, logins, _ = sent_order(shop, outbox)
    shop.execute(text("insert into region (code, name) values ('KA', 'Karnataka stores')"))
    shop.execute(text("update location set region_code = 'KA' where code = 'WH-BLR'"))
    shop.execute(text("update price_record set region_code = 'KA' where item_code = 'A'"))
    shop.commit()

    got = revise(shop, logins, po_no, {"header": {"ship_to_location": "WH-PUN"}})

    assert (got.status_code, got.json()["detail"]) == (409, "line 1: K-ACTIVE applies in Karnataka stores only")


# ---- payment terms, the maker, touchless ----------------------------------------------------------------------


def test_payment_terms_with_an_advance_are_approved_again_by_someone_who_did_not_make_the_change(
    tiers: Session,
    outbox: Outbox,  # noqa: F811
) -> None:
    # given a touchless order on the contract (38,350.00, approved by rule PO-01 and sent by the system)
    logins = _logins(tiers)
    _gate(tiers)
    tiers.execute(
        text("insert into payment_terms (code, name, days, advance_pct) values ('ADV30', 'Advance 30', 30, 30)")
    )
    tiers.commit()
    po = _order(tiers, "R-P5", "RM-01", "50", "650")
    assert (order(tiers, po)["touchless"], order(tiers, po)["status"]) == (True, "sent")

    # when the procurement head, working as a buyer too, changes the payment terms to 30% in advance
    done = changes.revise(
        tiers, tiers.info["slug"], po, logins["procurement"], "Supplier needs an advance", [],
        header=changes.HeaderChange(payment_terms_code="ADV30"),
    )  # fmt: skip

    # then it is material, no longer touchless (the revision and the advance both fail), and the band of the
    # procurement head goes up to the director: the person who changed it never approves it (C1)
    assert (done["material"], done["status"], done["touchless_lost"]) == (["payment terms changed"], "submitted", True)
    row = order(tiers, po)
    failed = sorted(c["test"] for c in row["touchless_checks"] if not c["passed"])
    assert row["touchless"] is False and failed == ["touchless.advance", "touchless.revision"]
    director = workflow.org_code_of(tiers, logins["director"])
    assert rows(tiers, "select approver, version from po_approval_step where status = 'pending'") == [
        {"approver": director, "version": 2}
    ]
    assert len(audits(tiers, "po.touchless_lost", po)) == 1


def test_payment_terms_are_fixed_once_an_invoice_or_an_advance_exists(shop: Session, outbox: Outbox) -> None:  # noqa: F811
    po_no, logins, _ = sent_order(shop, outbox)
    shop.execute(
        text(
            "insert into invoice (no, supplier_code, supplier_invoice_no, dedupe_key, invoice_date, fy, received_on, "
            "taxable, total, status) values ('PI-1', 'S1', 'S1-1', 'S11', '2026-11-10', '2026-27', '2026-11-10', 100, "
            "118, 'received')"
        )
    )
    shop.execute(
        text(
            "insert into invoice_line (invoice_no, line_no, po_no, po_line_no, qty, unit_price, taxable, gst_rate) "
            "values ('PI-1', 1, :n, 1, 1, 100, 100, 18)"
        ),
        {"n": po_no},
    )
    shop.commit()
    invoiced = revise(shop, logins, po_no, {"header": {"payment_terms_code": "NET30"}})
    assert (invoiced.status_code, invoiced.json()["detail"]) == (
        409, f"an invoice has been entered against {po_no}: its payment terms are fixed",
    )  # fmt: skip

    shop.execute(text("update invoice set status = 'cancelled'"))  # a cancelled invoice fixes nothing
    shop.execute(
        text(
            "insert into payment_run (no, run_date, proposed_by, status, total) values ('RUN-1', '2026-11-06', 'U9', "
            "'paid', 100)"
        )
    )
    shop.execute(
        text(
            "insert into payment (no, run_no, type, supplier_code, po_no, amount, tds_amount, msme_interest, mode) "
            "values ('PAY-1', 'RUN-1', 'advance', 'S1', :n, 100, 0, 0, 'neft')"
        ),
        {"n": po_no},
    )
    shop.execute(
        text("insert into advance (payment_no, po_no, gross, taxable) values ('PAY-1', :n, 100, 100)"), {"n": po_no}
    )
    shop.commit()
    advanced = revise(shop, logins, po_no, {"header": {"payment_terms_code": "NET30"}})
    assert (advanced.status_code, advanced.json()["detail"]) == (
        409, f"an advance has been paid against {po_no}: its payment terms are fixed",
    )  # fmt: skip


def test_delivery_terms_are_a_minor_change_the_approval_carries_and_the_supplier_answers_the_new_version(
    shop: Session,
    outbox: Outbox,  # noqa: F811
) -> None:
    po_no, logins, _ = sent_order(shop, outbox)
    dispatch.acknowledge(shop, po_no, sod.user(logins["buyer"]), promised=None, note=None)
    outbox.sent.clear()

    done = revise(shop, logins, po_no, {"header": {"delivery_terms": "  ex_works  "}, "version": 1})

    body = done.json()
    assert (body["reapproval"], body["approval_carried"], body["status"], body["material"]) == (False, True, "sent", [])
    po = order(shop, po_no)
    assert (po["delivery_terms"], po["approved_version"], po["acknowledged_version"]) == ("ex_works", 1, None)
    (said,) = client.get(f"/api/purchase-orders/{po_no}/controls", headers=buyer(shop, logins)).json()["history"]
    assert said["outcome"] == "approval of version 1 carries: nothing material changed"
    assert said["acknowledged"].startswith("Version 1 acknowledged in the app on ")
    assert said["acknowledged"].endswith("; version 2 not yet acknowledged")
    assert rows(shop, "select count(*) as n from po_approval_step where version = 2") == [{"n": 0}]
    (mail,) = outbox.sent
    (link,) = rows(shop, "select po_version from supplier_link order by id desc limit 1")
    assert link["po_version"] == 2 and "/supplier/" in mail["body"]
    page = _pdf_text(order_versions.stored_pdf(shop, shop.info["slug"], po_no, 2) or b"")
    assert "Delivery terms: ex_works" in page
    # the supplier accepts version 2 from the new link
    confirmation.accept_by_link(shop, shop.info["slug"], supplier_links.read(_token(mail["body"])), None, None)
    assert order(shop, po_no)["acknowledged_version"] == 2


def test_a_touchless_order_changed_by_a_person_is_no_longer_touchless_but_keeps_its_rule_approval(
    tiers: Session,
    outbox: Outbox,  # noqa: F811
) -> None:
    logins = _logins(tiers)
    _gate(tiers)
    po = _order(tiers, "R-P6", "RM-01", "50", "650")
    tiers.execute(  # the buyer as a person of the organisation, so the note names them
        text("insert into org_user (code, name, email) values ('U-BUY', 'A Buyer', :e)"),
        {"e": notifications.email_of(logins["buyer"])},
    )
    tiers.commit()

    done = changes.revise(
        tiers, tiers.info["slug"], po, logins["buyer"], "Collect it ourselves", [],
        header=changes.HeaderChange(delivery_terms="ex_works"),
    )  # fmt: skip

    assert (done["reapproval"], done["touchless_lost"], done["status"]) == (False, True, "sent")
    row = order(tiers, po)
    assert row["touchless"] is False and row["touchless_note"] == (
        "Changed by A Buyer after it was issued on 06 Nov 2026: "  # the business day it went out, not the clock
        "no longer touchless (Rev s5). The approval by rule PO-01 of version 1 carries: nothing material changed."
    )
    assert rows(tiers, "select version, approved_by from po_approved_version") == [
        {"version": 1, "approved_by": "rule:PO-01"}
    ]
    assert rows(tiers, "select count(*) as n from po_approval_step") == [{"n": 0}]
    assert audits(tiers, "po.touchless_lost", po) == [{"version": 2}]


def test_a_supplier_date_change_a_buyer_accepts_also_ends_touchless(tiers: Session, outbox: Outbox) -> None:  # noqa: F811
    logins = _logins(tiers)
    _gate(tiers)
    po = _order(tiers, "R-P6c", "RM-01", "50", "650")
    later = date(2026, 11, 22)  # 2 days after the need-by of 20 Nov: within the tolerance, not material
    (change,) = confirmation.propose(
        tiers, tiers.info["slug"], po, [confirmation.Proposed("date", 1, later.isoformat())],
        source="link", reason="Furnace repair", actor=confirmation.supplier_actor("S1"),
    )  # fmt: skip

    confirmation.decide(tiers, tiers.info["slug"], change, logins["buyer"], accept=True)

    row = order(tiers, po)
    assert (row["touchless"], row["version"], row["status"]) == (False, 2, "acknowledged")
    assert row["acknowledged_version"] == 2 and versions(tiers, po)[1]["ack_via"] == "app"


# ---- refusals ----------------------------------------------------------------------------------------------------


def test_every_refusal_says_why_and_writes_nothing(shop: Session, outbox: Outbox) -> None:  # noqa: F811
    po_no, logins, _ = sent_order(shop, outbox)
    revise(shop, logins, po_no, {"lines": [{"line_no": 1, "qty": "8"}]})  # now version 2
    shop.execute(text("update po set delivery_terms = 'for_destination'"))
    shop.commit()
    kept = (
        len(versions(shop, po_no)),
        len(audits(shop, "po.revise", po_no)),
        len(rows(shop, "select 1 from supplier_link")),
    )

    refused = [
        ({"supplier_code": "S2"}, 422, "the supplier cannot be changed on an order: cancel it and order the lines again"),
        ({"header": {"supplier_code": "S2"}}, 422, "the supplier cannot be changed on an order: cancel it and order the lines again"),
        ({"lines": [{"line_no": 1, "item_code": "B"}]}, 422, "the item cannot be changed on an order: cancel it and order the lines again"),
        ({"lines": [{"line_no": 1, "unit_price": "90"}]}, 422, "a price is not typed: it comes from the contract or the award"),
        ({"header": {"delivery_terms": "for_destination"}}, 422, "delivery_terms is already for_destination"),
        ({"header": {"delivery_terms": "x" * 201}}, 422, "delivery terms must be 1 to 200 characters"),
        ({"header": {"delivery_terms": "   "}}, 422, "delivery terms must be 1 to 200 characters"),
        ({"header": {"ship_to_location": "WH-BLR"}}, 422, "ship_to_location is already WH-BLR"),
        ({"header": {"ship_to_location": "NOWHERE"}}, 422, "NOWHERE is not one of the company's delivery places"),
        ({"header": {"payment_terms_code": "NET45"}}, 422, "payment_terms_code is already NET45"),
        ({"header": {"payment_terms_code": "NET99"}}, 422, "NET99 is not one of the company's payment terms"),
        ({"header": {"tax": "0"}}, 422, "tax cannot be changed on an order"),
        ({"header": {"delivery_terms": "ex_works"}, "version": 1}, 409, f"{po_no} has changed since you opened it: it is now version 2"),
        ({}, 422, "lines is required (a list of {line_no, qty, need_by or reprice}) unless header is given"),
    ]  # fmt: skip
    for body, status, detail in refused:
        got = revise(shop, logins, po_no, body)
        assert (got.status_code, got.json()["detail"]) == (status, detail), body
    shop.execute(text("update po_line set source = 'award', source_ref = 'Q-1'"))
    shop.commit()
    award = revise(shop, logins, po_no, {"lines": [{"line_no": 1, "reprice": True}]})
    assert (award.status_code, award.json()["detail"]) == (409, "line 1 is not bought on a contract")
    requester = _headers(shop.info["slug"], logins["keeper"], "Requester")
    forbidden = client.post(
        f"/api/purchase-orders/{po_no}/revise",
        json={"reason": "x", "header": {"delivery_terms": "y"}},
        headers=requester,
    )
    assert forbidden.status_code == 403
    after = (
        len(versions(shop, po_no)),
        len(audits(shop, "po.revise", po_no)),
        len(rows(shop, "select 1 from supplier_link")),
    )
    assert after == kept and order(shop, po_no)["version"] == 2


def test_the_controls_give_the_places_and_payment_terms_to_choose_from(shop: Session) -> None:  # noqa: F811
    second_place(shop)
    logins = _logins(shop)
    po_no = _approved_order(shop, logins)
    c = client.get(f"/api/purchase-orders/{po_no}/controls", headers=buyer(shop, logins)).json()
    assert c["can_change_header"] is True
    assert {"code": "WH-PUN", "name": "Pune store", "state": "27"} in c["options"]["locations"]
    assert [t["code"] for t in c["options"]["payment_terms"]] == ["NET30", "NET45"]
    keeper = _headers(shop.info["slug"], logins["keeper"], "Requester")
    assert client.get(f"/api/purchase-orders/{po_no}/controls", headers=keeper).json()["can_change_header"] is False
