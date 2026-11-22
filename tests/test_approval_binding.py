"""An order's approval is bound to the version the approver saw (task 0.2, DF-07, DF-08, Q19).

Each step is for one version; an approver who saw an older version is refused; the approved version and what it said
are kept; a revision is measured from the approved version, so small changes add up; a material change is approved
again, any other carries the approval; a supplier's price is judged from the first approved price; a revised order is
never touchless; sending refuses a version changed materially after approval.
"""

import importlib.util
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session
from test_orders import _headers, _logins, shop  # noqa: F401 - fixtures
from test_po_tiers import _gate, _order, tiers  # noqa: F401 - fixtures
from test_receiving import _sent, store  # noqa: F401 - fixtures
from test_sourcing import TODAY, Outbox, _price, _request, _source, db, outbox  # noqa: F401 - fixtures
from test_supplier_confirmation import _price_rule, _token

import p2p
from p2p.main import app
from p2p.orders import approved_versions, changes, dispatch, service, workflow
from p2p.platform import sod

D = Decimal
client = TestClient(app)
APPROVED_DAY = date(2026, 11, 20)  # the need-by of the test world's order line


def _waiting_order(db: Session, no: str = "R-V1", qty: str = "10") -> tuple[str, dict[str, int]]:
    """An order of item A from S1 at 100 (IGST 18%), waiting for the procurement head (never approved)."""
    logins = _logins(db)
    _price(db, "S1", "A", "100")
    request = _request(db, no, [("A", qty, None, "C1")])
    _source(db, request, dispatch=False)
    (po_no,) = service.order_request(db, db.info["slug"], request, as_of=TODAY).waiting
    return po_no, logins


def _steps(db: Session, po_no: str) -> list[tuple[int, int, int | None, str]]:
    db.rollback()
    return [
        (r[0], r[1], r[2], r[3])
        for r in db.execute(
            text("select round, step, version, status from po_approval_step where po_no = :n order by round, step"),
            {"n": po_no},
        )
    ]


def _audit(db: Session, action: str, po_no: str) -> list[dict[str, Any]]:
    db.rollback()
    return [
        dict(r)
        for r in db.execute(
            text("select after from audit_event where action = :a and entity_id = :n order by id"),
            {"a": action, "n": po_no},
        ).mappings()
    ]


def _revise(db: Session, po_no: str, user_id: int, **change: Any) -> dict[str, Any]:
    return changes.revise(db, db.info["slug"], po_no, user_id, "Plan changed", [changes.LineChange(1, **change)])


def _day(plus: int) -> date:
    return date.fromordinal(APPROVED_DAY.toordinal() + plus)


# ---- V1: approval of the version seen ----------------------------------------------------------------------------


def test_an_approver_who_saw_an_older_version_is_refused_and_the_version_approved_is_kept(shop: Session) -> None:  # noqa: F811
    # given an order waiting at version 1, revised by the buyer while it waits (10 -> 8)
    po_no, logins = _waiting_order(shop)
    assert _steps(shop, po_no) == [(1, 1, 1, "pending")]
    _revise(shop, po_no, logins["buyer"], qty=D("8"))
    assert _steps(shop, po_no) == [(1, 1, 1, "cancelled"), (2, 1, 2, "pending")]
    # never approved: nothing to measure from, so no baseline and no material reasons (approval restarts anyway)
    assert shop.execute(text("select baseline_version, material, reapproval from po_revision")).one() == (
        None,
        [],
        True,
    )
    approver = _headers(shop.info["slug"], logins["procurement"], "Approver")

    # when the approver approves version 1, then version 2
    for seen in (1, 3):
        stale = client.post(f"/api/purchase-orders/{po_no}/approve", json={"version": seen}, headers=approver)
        assert stale.status_code == 409
        assert stale.json()["detail"] == (
            f"{po_no} has changed since you opened it: it is now version 2. Open it again to see the change"
        )
    assert _audit(shop, "po.approve", po_no) == [] and _steps(shop, po_no)[-1] == (2, 1, 2, "pending")
    stale_reject = client.post(
        f"/api/purchase-orders/{po_no}/reject", json={"version": 1, "reason": "no"}, headers=approver
    )
    assert stale_reject.status_code == 409 and "now version 2" in stale_reject.json()["detail"]
    done = client.post(f"/api/purchase-orders/{po_no}/approve", json={"version": 2}, headers=approver)

    # then version 2 is approved and recorded with what it said
    assert done.status_code == 200 and done.json() == {"status": "approved"}
    shop.rollback()
    assert shop.execute(text("select status, version, approved_version from po")).one() == ("approved", 2, 2)
    row = shop.execute(text("select * from po_approved_version")).mappings().one()
    head = workflow.org_code_of(shop, logins["procurement"])
    assert (row["version"], row["total"], row["band_max"], row["approved_by"], row["rule_code"]) == (
        2, D("944.00"), D("500000.00"), head, "PO-01",
    )  # fmt: skip
    (line,) = row["snapshot"]["lines"]
    assert (line["line_no"], line["item_code"], D(line["qty"]), D(line["unit_price"]), line["need_by"]) == (
        1, "A", D("8"), D("100"), "2026-11-20",
    )  # fmt: skip
    (approve,) = [a["after"] for a in _audit(shop, "po.approve", po_no)]
    assert (approve["version"], approve["with_code"]) == (2, False)
    assert [a["after"]["version"] for a in _audit(shop, "po.approved", po_no)] == [2]


def test_an_approval_with_no_version_given_approves_the_current_one_and_records_it(shop: Session) -> None:  # noqa: F811
    po_no, logins = _waiting_order(shop)

    assert workflow.approve(shop, shop.info["slug"], po_no, logins["procurement"]) == "approved"

    shop.rollback()
    assert shop.execute(text("select approved_version from po")).scalar_one() == 1
    assert shop.execute(text("select version from po_approved_version")).scalar_one() == 1


def test_the_order_page_says_which_version_is_waiting_and_which_was_approved(shop: Session) -> None:  # noqa: F811
    po_no, logins = _waiting_order(shop)
    page = client.get(
        f"/api/purchase-orders/{po_no}", headers=_headers(shop.info["slug"], logins["procurement"], "Approver")
    ).json()
    assert (page["version"], page["approved_version"]) == (1, None)
    approval = page["approval"]
    assert (approval["version"], approval["needs_code"], approval["code_above"]) == (1, False, "500000.00")


# ---- V2, V2b, V3: material changes against the approved version ---------------------------------------------------


def test_a_date_moved_beyond_the_tolerance_after_approval_is_approved_again_and_acknowledged_again(
    store: Session,  # noqa: F811
    outbox: Outbox,  # noqa: F811
) -> None:
    po_no, logins = _sent(store)
    store.execute(text("update po set status = 'acknowledged', acknowledged_at = now()"))
    store.commit()

    done = _revise(store, po_no, logins["buyer"], need_by=_day(5))

    assert (done["status"], done["reapproval"], done["approval_carried"]) == ("submitted", True, False)
    assert done["material"] == ["line 1: date moved 5 days from the approved 20 Nov 2026, more than the 3 accepted"]
    store.rollback()
    po = store.execute(text("select version, approved_version, acknowledged_at, total from po")).one()
    assert po == (2, 1, None, D("1180.00"))  # the old approval stays recorded until the new one
    rev = store.execute(text("select reapproval, baseline_version, material from po_revision")).one()
    assert rev == (True, 1, done["material"])
    assert _steps(store, po_no)[-1] == (2, 1, 2, "pending")
    (entry,) = changes.history(store, po_no)
    assert (entry["material"], entry["baseline_version"], entry["approval_carried"]) == (done["material"], 1, False)
    # once approved again and sent, the supplier answers version 2
    workflow.approve(store, store.info["slug"], po_no, logins["procurement"], version=2)
    dispatch.send_po(store, po_no, sod.user(logins["buyer"]), tenant=store.info["slug"])
    store.rollback()
    assert store.execute(text("select approved_version from po")).scalar_one() == 2
    assert [r[0] for r in store.execute(text("select version from po_approved_version order by version"))] == [1, 2]


def test_a_change_that_is_not_material_carries_the_approval_and_asks_only_a_new_acknowledgement(
    store: Session,  # noqa: F811
    outbox: Outbox,  # noqa: F811
) -> None:
    po_no, logins = _sent(store)
    store.execute(text("update po set status = 'acknowledged', acknowledged_at = now()"))
    store.commit()

    smaller = _revise(store, po_no, logins["buyer"], qty=D("8"))
    later = _revise(store, po_no, logins["buyer"], need_by=_day(3))  # 3 days from the approved date: within

    for done in (smaller, later):
        assert (done["status"], done["reapproval"], done["approval_carried"], done["material"]) == (
            "sent", False, True, [],
        )  # fmt: skip
    store.rollback()
    assert store.execute(text("select version, approved_version, acknowledged_at from po")).one() == (3, 1, None)
    assert [(h["version"], h["baseline_version"], h["approval_carried"]) for h in changes.history(store, po_no)] == [
        (3, 1, True), (2, 1, True),
    ]  # fmt: skip
    assert len(_steps(store, po_no)) == 1  # no new round


def test_two_small_date_slips_add_up_and_the_second_is_approved_again(store: Session, outbox: Outbox) -> None:  # noqa: F811
    po_no, logins = _sent(store)

    first = _revise(store, po_no, logins["buyer"], need_by=_day(2))
    second = _revise(store, po_no, logins["buyer"], need_by=_day(4))

    assert (first["reapproval"], first["status"]) == (False, "sent")
    assert (second["reapproval"], second["status"]) == (True, "submitted")
    assert second["material"] == [
        "line 1: date moved 4 days from the approved 20 Nov 2026, more than the 3 accepted"
    ]  # the old rule compared with the version before: 2 days, not material


def test_a_quantity_back_up_to_the_approved_one_carries_the_approval(store: Session, outbox: Outbox) -> None:  # noqa: F811
    po_no, logins = _sent(store)
    _revise(store, po_no, logins["buyer"], qty=D("8"))

    back = _revise(store, po_no, logins["buyer"], qty=D("10"))

    assert (back["reapproval"], back["status"], back["material"]) == (False, "sent", [])
    assert back["total"] == "1180.00"


# ---- V4: price rises add up from the first approved price (DF-08) -------------------------------------------------


def _ask_price(db: Session, token: str, price: str) -> tuple[int, bool, str]:
    client.post(f"/supplier/{token}", data={"action": "change", "price_1": price})
    db.rollback()
    row = db.execute(
        text("select id, within_tolerance, base_value from po_change where status = 'proposed' order by id desc")
    ).first()
    assert row is not None
    return int(row[0]), bool(row[1]), str(row[2])


def test_price_rises_are_judged_from_the_first_approved_price_and_the_variance_counts_the_whole_rise(
    shop: Session,  # noqa: F811
    outbox: Outbox,  # noqa: F811
) -> None:
    from test_supplier_confirmation import _sent as sent_with_link

    _price_rule(shop)
    po_no, logins, token = sent_with_link(shop, outbox)
    slug = shop.info["slug"]
    buyer, manager = _headers(slug, logins["buyer"], "Buyer"), _headers(slug, logins["procurement"], "Approver")

    # a rise of 1.5% from 100: the buyer's to accept; the order is approved again at 101.50 and sent
    change, within, base = _ask_price(shop, token, "101.50")
    assert (within, base) == (True, "100")
    assert (
        client.post(f"/api/supplier-changes/{change}/decide", json={"accept": True}, headers=buyer).status_code == 200
    )
    assert client.post(f"/api/purchase-orders/{po_no}/approve", json={"version": 2}, headers=manager).status_code == 200
    outbox.sent.clear()
    dispatch.send_po(shop, po_no, sod.user(logins["buyer"]), tenant=slug)
    token = _token(outbox.sent[-1]["body"])

    # 102.50 is 0.99% above the current 101.50 but 2.5% above the first approved 100: not the buyer's
    change, within, base = _ask_price(shop, token, "102.50")
    assert (within, base) == (False, "100")
    shown = client.get(f"/api/purchase-orders/{po_no}/supplier-answer", headers=buyer).json()["changes"][0]
    assert (shown["base_value"], shown["rise_pct"]) == ("100", "2.50")
    referred = client.post(f"/api/supplier-changes/{change}/decide", json={"accept": True}, headers=buyer)
    assert referred.json()["status"] == "waiting_approval"
    shop.rollback()
    assert shop.execute(text("select value from proposal where action = 'po.supplier_price'")).scalar_one() == D(
        "25.00"
    )  # (102.50 - 100) x 10, not (102.50 - 101.50) x 10
    done = client.post(f"/api/supplier-changes/{change}/decide", json={"accept": True}, headers=manager)

    # the purchase manager's acceptance is not making: the order comes back to them, version 3
    assert done.status_code == 200
    head = workflow.org_code_of(shop, logins["procurement"])
    shop.rollback()
    assert shop.execute(text("select version, status from po")).one() == (3, "submitted")
    assert shop.execute(text("select approver, version from po_approval_step where status = 'pending'")).one() == (
        head,
        3,
    )
    assert approved_versions.base_price(shop, po_no, 1) == D("100.0000")


# ---- V5: a revised order is never touchless --------------------------------------------------------------------


def test_a_touchless_order_keeps_its_rule_approval_and_once_changed_materially_a_person_approves_it(
    tiers: Session,  # noqa: F811
    outbox: Outbox,  # noqa: F811
) -> None:
    logins = _logins(tiers)
    _gate(tiers)
    po = _order(tiers, "R-T1", "RM-01", "50", "650")  # 38,350.00: touchless, sent by the rule
    tiers.rollback()
    first = tiers.execute(text("select version, approved_by, rule_code, band_max from po_approved_version")).one()
    assert first == (1, "rule:PO-01", "PO-01", D("100000.00"))
    assert approved_versions.base_price(tiers, po, 1) == D("650.0000")  # the contract price
    need_by = tiers.execute(text("select need_by from po_line where po_no = :n"), {"n": po}).scalar_one()

    later = date.fromordinal(need_by.toordinal() + 5)

    done = changes.revise(
        tiers, tiers.info["slug"], po, logins["buyer"], "Later", [changes.LineChange(1, need_by=later)]
    )

    assert (done["reapproval"], done["status"]) == (True, "submitted")
    tiers.rollback()
    row = tiers.execute(text("select touchless, touchless_checks from po where no = :n"), {"n": po}).one()
    revision = [c for c in row[1] if c["test"] == "touchless.revision"]
    assert row[0] is False and revision == [
        {
            "test": "touchless.revision",
            "passed": False,
            "detail": "the order was changed after approval: a person approves it",
        }
    ]
    head = workflow.org_code_of(tiers, logins["procurement"])
    assert tiers.execute(text("select approver, version from po_approval_step where status = 'pending'")).one() == (
        head, 2,
    )  # fmt: skip


# ---- the send guard and the migration's backfill ---------------------------------------------------------------


def test_a_version_changed_materially_after_approval_is_never_sent(store: Session) -> None:  # noqa: F811
    po_no, logins = _sent(store)
    store.execute(text("update po set status = 'approved', version = 2"))
    store.execute(
        text(
            "insert into po_revision (po_no, version, changed_by, reason, changes, total_before, total_after, reapproval) "
            "values (:n, 2, 'x', 'forced', '[]', 1180, 1180, true)"
        ),
        {"n": po_no},
    )
    store.commit()

    with pytest.raises(dispatch.DispatchError, match="version 2 changed materially after approval"):
        dispatch.send_po(store, po_no, sod.user(logins["buyer"]))


def test_the_migration_gives_an_order_approved_before_it_a_snapshot_and_its_version(store: Session) -> None:  # noqa: F811
    po_no, _ = _sent(store)
    store.execute(text("delete from po_approved_version"))
    store.execute(text("update po set approved_version = null"))
    store.execute(text("update po_approval_step set version = null"))
    store.commit()
    path = Path(p2p.__file__).parent / "platform" / "migrations" / "tenant" / "versions" / "0110_approval_binding.py"
    spec = importlib.util.spec_from_file_location("approval_binding_migration", path)
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)

    for statement in migration.BACKFILL:
        store.execute(text(statement))
    store.commit()

    assert store.execute(text("select approved_version from po")).scalar_one() == 1
    assert store.execute(text("select version from po_approval_step")).scalar_one() == 1
    row = store.execute(text("select version, approved_by, band_max, total, snapshot from po_approved_version")).one()
    assert row[:4] == (1, "backfill", D("500000.00"), D("1180.00"))
    assert D(row[4]["lines"][0]["qty"]) == 10 and row[4]["supplier_code"] == "S1"


def test_an_order_approved_with_no_snapshot_is_measured_from_the_version_it_is_now(
    store: Session, outbox: Outbox
) -> None:  # noqa: F811
    # given an approved order whose snapshot was never kept (approved before block 4, or written outside the workflow)
    po_no, logins = _sent(store)
    store.execute(text("delete from po_approved_version"))
    store.execute(text("update po set approved_version = null"))
    store.commit()

    done = _revise(store, po_no, logins["buyer"], need_by=_day(4))

    assert (done["reapproval"], done["status"]) == (True, "submitted")
    assert done["material"] == ["line 1: date moved 4 days from the approved 20 Nov 2026, more than the 3 accepted"]
