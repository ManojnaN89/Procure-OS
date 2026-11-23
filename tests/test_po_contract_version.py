"""An order keeps the contract price version it was bought at (block 7, R15, R16, R26-R28, D48): activating a later
version changes no existing order line; a buyer may move a contract line to the price in force (a rise is approved
again, a fall is not); a quantity break is the highest the quantity reaches; a supplier's accepted price is no longer
the contract's; new orders use the version in force on the day they are made."""

from datetime import date
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from po_versions_support import buyer, order, revise, rows, sent_order, versions
from sqlalchemy import text
from sqlalchemy.orm import Session
from test_order_changes import _pdf_text
from test_orders import _logins, shop  # noqa: F401 - fixtures
from test_sourcing import Outbox, _price, _request, _source, db, outbox  # noqa: F401 - fixtures

from p2p.formulas.calendar import pin_today
from p2p.main import app
from p2p.masters import documents_and_contracts as contracts
from p2p.orders import changes, confirmation, dispatch, document, service, workflow
from p2p.platform import sod
from p2p.sourcing import service as sourcing

D = Decimal
client = TestClient(app)
NEW_YEAR = date(2027, 1, 4)  # the first Monday on version 2


def _with_version_2(db: Session, logins: dict[str, int], prices: list[tuple[str, str]]) -> None:
    """Version 2 of K-ACTIVE from 1 Jan 2027 at the given (min quantity, price) breaks, submitted by the buyer and
    activated by the procurement head (D48: never by its maker). The contract runs to March 2027."""
    db.execute(text("update contract set valid_to = '2027-03-31' where code = 'K-ACTIVE'"))
    for min_qty, price in prices:
        db.execute(
            text(
                "insert into price_record (supplier_code, item_code, contract_code, uom, price, min_qty, valid_from, "
                "lead_days, version, status) values ('S1', 'A', 'K-ACTIVE', 'NOS', :p, :m, '2027-01-01', 10, 2, 'draft')"
            ),
            {"p": price, "m": min_qty},
        )
    db.commit()
    contracts.submit_version(db, "K-ACTIVE", logins["buyer"])
    contracts.activate_version(db, "K-ACTIVE", logins["procurement"])


def _order_on_v1(db: Session, outbox: Outbox, qty: str = "10") -> tuple[str, dict[str, int]]:
    """An order of item A on K-ACTIVE version 1 at 100.00 (a price with no end date), approved and sent."""
    db.execute(text("update contract set valid_to = '2027-03-31' where code = 'K-ACTIVE'"))
    db.commit()
    _price(db, "S1", "A", "100", valid_to=None)
    request = _request(db, f"R-A{qty}", [("A", qty, None, "C1")])
    _source(db, request, dispatch=False)
    logins = _logins(db)
    (po_no,) = service.order_request(db, db.info["slug"], request, as_of=date(2026, 11, 6)).waiting
    workflow.approve(db, db.info["slug"], po_no, logins["procurement"])
    dispatch.send_po(db, po_no, sod.user(logins["buyer"]), tenant=db.info["slug"])
    return po_no, logins


def _line(db: Session, po_no: str) -> dict:
    (line,) = rows(db, "select * from po_line where po_no = :n", n=po_no)
    return line


def _record(db: Session, version: int, min_qty: int = 1) -> int:
    (row,) = rows(
        db, "select id from price_record where item_code = 'A' and version = :v and min_qty = :m", v=version, m=min_qty
    )
    return int(row["id"])


def test_activating_a_later_contract_version_changes_no_existing_order_line(shop: Session, outbox: Outbox) -> None:  # noqa: F811
    po_no, logins = _order_on_v1(shop, outbox)
    before = _line(shop, po_no)

    _with_version_2(shop, logins, [("1", "104.00")])

    after = _line(shop, po_no)
    assert (after["unit_price"], after["price_record_id"], after["taxable"]) == (
        before["unit_price"], _record(shop, 1), before["taxable"],
    )  # fmt: skip
    assert rows(shop, "select valid_to from price_record where id = :i", i=_record(shop, 1)) == [
        {"valid_to": date(2026, 12, 31)}  # version 1 ends the day before version 2 starts
    ]
    assert order(shop, po_no)["version"] == 1
    # the order page and the document name the version the line was bought at
    shown = client.get(f"/api/purchase-orders/{po_no}", headers=buyer(shop, logins)).json()["lines"][0]["contract"]
    assert shown == {
        "code": "K-ACTIVE", "version": 1, "price_record_id": _record(shop, 1), "valid_from": "2026-01-01",
        "valid_to": "2026-12-31",
    }  # fmt: skip
    assert "Contract K-ACTIVE version 1" in _pdf_text(document.order_document(shop, po_no))


def test_re_pricing_moves_a_contract_line_to_the_version_in_force_and_a_rise_is_approved_again(
    shop: Session,
    outbox: Outbox,  # noqa: F811
) -> None:
    po_no, logins = _order_on_v1(shop, outbox)
    _with_version_2(shop, logins, [("1", "104.00")])

    # before version 2 starts, version 1 is still the price in force
    early = revise(shop, logins, po_no, {"lines": [{"line_no": 1, "reprice": True}]})
    assert (early.status_code, early.json()["detail"]) == (
        409, "line 1 is already at the contract price in force (K-ACTIVE version 1)",
    )  # fmt: skip

    pin_today(NEW_YEAR)
    done = revise(shop, logins, po_no, {"lines": [{"line_no": 1, "reprice": True}], "version": 1})

    body = done.json()
    assert (body["status"], body["material"]) == ("submitted", ["line 1: price up", "the total went up"])
    line = _line(shop, po_no)
    assert (line["unit_price"], line["price_record_id"], line["taxable"], line["igst"]) == (
        D("104.0000"), _record(shop, 2), D("1040.00"), D("187.20"),
    )  # fmt: skip
    assert order(shop, po_no)["total"] == D("1227.20")
    head = workflow.org_code_of(shop, logins["procurement"])
    assert rows(shop, "select approver, version from po_approval_step where status = 'pending'") == [
        {"approver": head, "version": 2}
    ]
    history = client.get(f"/api/purchase-orders/{po_no}/controls", headers=buyer(shop, logins)).json()["history"]
    assert history[0]["said"] == ["line 1: price ₹ 100.00 to ₹ 104.00", "line 1: contract price version 1 to 2"]
    compared = client.get(f"/api/purchase-orders/{po_no}/versions/compare?from=1&to=2", headers=buyer(shop, logins))
    fields = {f["field"]: (f["before"], f["after"]) for f in compared.json()["lines"][0]["fields"]}
    assert (
        fields["contract_version"] == (1, 2) and D(fields["unit_price"][0]) == 100 and D(fields["unit_price"][1]) == 104
    )
    assert versions(shop, po_no)[0]["snapshot"]["lines"][0]["contract_version"] == 1  # version 1 keeps its price


def test_a_price_break_is_the_highest_the_quantity_reaches_and_a_fall_carries_the_approval(
    shop: Session,
    outbox: Outbox,  # noqa: F811
) -> None:
    po_no, logins = _order_on_v1(shop, outbox)
    _with_version_2(shop, logins, [("1", "104.00"), ("10", "98.00"), ("11", "90.00")])
    pin_today(NEW_YEAR)

    done = changes.revise(
        shop, shop.info["slug"], po_no, logins["buyer"], "New year price", [changes.LineChange(1, reprice=True)]
    )

    # 10 reaches the break at 10, not the one at 11: 98.00, lower, so the approval carries
    assert (done["reapproval"], done["approval_carried"], done["status"]) == (False, True, "sent")
    line = _line(shop, po_no)
    assert (line["unit_price"], line["price_record_id"]) == (D("98.0000"), _record(shop, 2, 10))


def test_no_price_in_force_is_refused_and_writes_nothing(shop: Session, outbox: Outbox) -> None:  # noqa: F811
    po_no, logins = _order_on_v1(shop, outbox)
    shop.execute(text("update price_record set valid_to = '2026-12-31' where item_code = 'A'"))
    shop.commit()
    pin_today(NEW_YEAR)

    got = revise(shop, logins, po_no, {"lines": [{"line_no": 1, "reprice": True}]})

    assert (got.status_code, got.json()["detail"]) == (
        409, "no price of K-ACTIVE is in force for A at Bengaluru store on 04 Jan 2027",
    )  # fmt: skip
    assert len(versions(shop, po_no)) == 1


def test_an_accepted_supplier_price_on_a_contract_line_is_no_longer_the_contracts(
    shop: Session,
    outbox: Outbox,  # noqa: F811
) -> None:
    po_no, logins, _ = sent_order(shop, outbox)
    record = _line(shop, po_no)["price_record_id"]
    (change,) = confirmation.propose(
        shop, shop.info["slug"], po_no, [confirmation.Proposed("price", 1, "101")],
        source="link", reason="Copper up", actor=confirmation.supplier_actor("S1"),
    )  # fmt: skip

    confirmation.decide(shop, shop.info["slug"], change, logins["buyer"], accept=True)

    assert _line(shop, po_no)["unit_price"] == D("101") and _line(shop, po_no)["price_record_id"] is None
    assert versions(shop, po_no)[0]["snapshot"]["lines"][0]["price_record_id"] == record  # the old version keeps it
    shown = client.get(f"/api/purchase-orders/{po_no}", headers=buyer(shop, logins)).json()["lines"][0]
    assert shown["contract"] is None and shown["source"] == "contract"
    said = client.get(f"/api/purchase-orders/{po_no}/controls", headers=buyer(shop, logins)).json()["history"][0]
    assert "line 1: no longer at the contract price (version 1)" in said["said"]


@pytest.mark.parametrize(("day", "price", "version"), [(date(2026, 12, 31), "100.0000", 1), (NEW_YEAR, "104.0000", 2)])
def test_a_new_order_uses_the_version_in_force_on_the_day_it_is_made(
    shop: Session,
    outbox: Outbox,
    day: date,
    price: str,
    version: int,  # noqa: F811
) -> None:
    logins = _logins(shop)
    shop.execute(text("update contract set valid_to = '2027-03-31' where code = 'K-ACTIVE'"))
    shop.commit()
    _price(shop, "S1", "A", "100", valid_to=None)
    _with_version_2(shop, logins, [("1", "104.00")])
    pin_today(day)
    request = _request(shop, "R-NEW", [("A", "10", None, "C1")])
    sourcing.source_request(shop, shop.info["slug"], request, as_of=day, dispatch=False)
    (po_no,) = service.order_request(shop, shop.info["slug"], request, as_of=day).pos

    line = _line(shop, po_no)
    assert (line["unit_price"], line["price_record_id"]) == (D(price), _record(shop, version))
    assert versions(shop, po_no)[0]["snapshot"]["lines"][0]["contract_version"] == version
