"""Every version of an order is kept (block 7, R1-R5): a row when the order is made and at each revision, with what the
version said; the supplier's acknowledgement is of one version, and a revision voids it while the older version keeps
its own; the migration keeps the current version of every order already made."""

import importlib.util
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from po_versions_support import audits, order, rows, sent_order, versions
from sqlalchemy import text
from sqlalchemy.orm import Session
from supplier_answer_support import service_code
from test_dispatch import _approved_order
from test_orders import _headers, _logins, shop  # noqa: F401 - fixtures
from test_sourcing import Outbox, db, outbox  # noqa: F401 - fixtures

import p2p
from p2p.main import app
from p2p.orders import approved_versions, changes, confirmation, dispatch, supplier_links
from p2p.platform import sod

D = Decimal
client = TestClient(app)


def test_an_order_is_kept_as_its_version_1_when_it_is_made_with_what_it_says(shop: Session) -> None:  # noqa: F811
    # given an order of 10 bearings from S1 (Maharashtra) to the Bengaluru store at the contract price 100
    po_no = _approved_order(shop, _logins(shop))

    (v1,) = versions(shop, po_no)

    # then version 1 was kept when the order was made, by the ordering service, with no reason
    assert (v1["version"], v1["made_by"], v1["reason"], v1["document_id"], v1["sent_at"]) == (
        1, "service:ordering", None, None, None,
    )  # fmt: skip
    snap = v1["snapshot"]
    assert (snap["supplier_code"], snap["ship_to_location"], snap["buyer_gstin"], snap["payment_terms_code"]) == (
        "S1", "WH-BLR", "29BBBBB0000B1Z5", "NET45",
    )  # fmt: skip
    assert [D(snap[k]) for k in ("taxable", "cgst", "sgst", "igst", "total")] == [
        D("1000"), D("0"), D("0"), D("180"), D("1180"),
    ]  # fmt: skip
    (line,) = snap["lines"]
    record = shop.execute(text("select id, version from price_record where item_code = 'A'")).one()
    assert (line["item_code"], D(line["qty"]), D(line["unit_price"]), D(line["gst_rate"]), line["status"]) == (
        "A", D("10"), D("100"), D("18"), "open",
    )  # fmt: skip
    assert (line["price_record_id"], line["contract_version"], line["need_by"]) == (record[0], 1, "2026-11-20")
    assert snap["charges"] == []


def test_each_revision_is_a_new_row_by_its_maker_with_its_reason_and_the_older_rows_stay(shop: Session) -> None:  # noqa: F811
    logins = _logins(shop)
    po_no = _approved_order(shop, logins)
    shop.rollback()
    first = versions(shop, po_no)[0]

    changes.revise(shop, shop.info["slug"], po_no, logins["buyer"], "Plan cut", [changes.LineChange(1, qty=D("8"))])

    v1, v2 = versions(shop, po_no)
    assert v1 == first  # the earlier version is never rewritten
    assert (v2["version"], v2["made_by"], v2["reason"]) == (2, f"user:{logins['buyer']}", "Plan cut")
    assert D(v2["snapshot"]["lines"][0]["qty"]) == 8 and D(v2["snapshot"]["total"]) == D("944.00")
    # the snapshot is the order as it now stands, read by the one reader approvals use too
    assert v2["snapshot"] == approved_versions.snapshot(shop, po_no).as_json()


def test_an_acknowledgement_is_of_one_version_and_a_revision_voids_it(shop: Session, outbox: Outbox) -> None:  # noqa: F811
    # given an order sent with its link (version 1) and accepted from it
    po_no, logins, token = sent_order(shop, outbox)
    code = service_code(shop, outbox.sent, token)  # D53: the code the contact got
    confirmation.accept_by_link(shop, shop.info["slug"], supplier_links.read(token), None, None, code)
    assert order(shop, po_no)["acknowledged_version"] == 1
    v1 = versions(shop, po_no)[0]
    assert (v1["ack_via"], v1["acknowledged_by"], v1["acknowledged_at"] is not None) == (
        "link", "service:supplier-link:S1", True,
    )  # fmt: skip
    assert audits(shop, "po.acknowledge", po_no)[-1]["version"] == 1

    # when the buyer cuts the quantity (not material: the approval carries)
    done = changes.revise(shop, shop.info["slug"], po_no, logins["buyer"], "Cut", [changes.LineChange(1, qty=D("8"))])

    # then the order waits for the supplier to answer version 2; version 1 keeps its acknowledgement
    assert (done["reapproval"], done["status"]) == (False, "sent")
    po = order(shop, po_no)
    assert (po["acknowledged_version"], po["acknowledged_at"], po["status"]) == (None, None, "sent")
    assert versions(shop, po_no)[0]["ack_via"] == "link" and versions(shop, po_no)[1]["acknowledged_at"] is None

    # and an acknowledgement of version 1 is refused with nothing written
    before = len(audits(shop, "po.acknowledge", po_no))
    with pytest.raises(dispatch.DispatchError) as stale:
        dispatch.acknowledge(shop, po_no, sod.user(logins["buyer"]), version=1, via="app", promised=None, note=None)
    assert str(stale.value) == (
        f"{po_no} has changed since that version: it is now version 2; the supplier answers the new version"
    )
    assert len(audits(shop, "po.acknowledge", po_no)) == before and order(shop, po_no)["status"] == "sent"

    # and the link already used for version 1 says the order changed, and that it was used (R23, P1)
    old = client.get(f"/supplier/{token}")
    assert old.status_code == 409 and "has changed since this link was sent" in old.text and "used already" in old.text

    # and the buyer recording version 2 in the app binds it to version 2
    head = _headers(shop.info["slug"], logins["buyer"], "Buyer")
    got = client.post(f"/api/purchase-orders/{po_no}/acknowledge", json={"version": 2, "note": "phone"}, headers=head)
    assert got.status_code == 200 and got.json()["version"] == 2
    assert order(shop, po_no)["acknowledged_version"] == 2
    assert versions(shop, po_no)[1]["ack_via"] == "app"
    last = audits(shop, "po.acknowledge", po_no)[-1]
    assert {k: last[k] for k in ("status", "warnings", "version", "via", "by")} == {
        "status": "acknowledged", "warnings": [], "version": 2, "via": "app", "by": f"user:{logins['buyer']}",
    }  # fmt: skip
    assert last["lines"] == {"1": "2026-11-20"}  # each line on its date ordered (block 8)


def test_the_app_refuses_an_acknowledgement_of_a_version_not_on_screen_and_records_the_current_one_when_omitted(
    shop: Session,
    outbox: Outbox,  # noqa: F811
) -> None:
    po_no, logins, _ = sent_order(shop, outbox)
    head = _headers(shop.info["slug"], logins["buyer"], "Buyer")

    stale = client.post(f"/api/purchase-orders/{po_no}/acknowledge", json={"version": 3, "note": "x"}, headers=head)
    assert stale.status_code == 409 and "it is now version 1" in stale.json()["detail"]
    assert (
        client.post(
            f"/api/purchase-orders/{po_no}/acknowledge", json={"version": "x", "note": "x"}, headers=head
        ).status_code
        == 422
    )

    omitted = client.post(f"/api/purchase-orders/{po_no}/acknowledge", json={"note": "phone"}, headers=head)
    assert omitted.status_code == 200 and omitted.json()["version"] == 1
    assert order(shop, po_no)["acknowledged_version"] == 1


def test_the_migration_keeps_the_current_version_of_every_order_already_made(shop: Session, outbox: Outbox) -> None:  # noqa: F811
    # given an order sent and acknowledged before versions were kept (no row, no acknowledged version)
    po_no, logins, _ = sent_order(shop, outbox)
    dispatch.acknowledge(shop, po_no, sod.user(logins["buyer"]), promised=None, note=None)
    shop.execute(text("delete from po_version"))
    shop.execute(text("update po set acknowledged_version = null"))
    shop.commit()
    path = Path(p2p.__file__).parent / "platform" / "migrations" / "tenant" / "versions" / "0140_po_versions.py"
    spec = importlib.util.spec_from_file_location("po_versions_migration", path)
    assert spec and spec.loader
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)

    for statement in migration.BACKFILL:
        shop.execute(text(statement))
    shop.commit()

    (row,) = versions(shop, po_no)
    po = order(shop, po_no)
    assert (row["version"], row["made_by"], row["sent_to"], row["sent_at"], row["acknowledged_at"]) == (
        1, "backfill", "s1@supplier.example", po["sent_at"], po["acknowledged_at"],
    )  # fmt: skip
    assert row["snapshot"] == approved_versions.snapshot(shop, po_no).as_json()  # the same shape as the service's
    assert po["acknowledged_version"] == 1
    # run again, nothing doubles
    for statement in migration.BACKFILL:
        shop.execute(text(statement))
    shop.commit()
    assert len(rows(shop, "select 1 from po_version")) == 1
