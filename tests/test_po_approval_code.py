"""The one-time code to approve a purchase order above Rs 5,00,000 (task 1.2, D-07): needed above the limit (its total
with GST), for approving only, emailed to the person whose turn it is, bound to the version, the round and the step, used
up with the approval; rejecting and approving by the rule need none."""

import uuid
from decimal import Decimal
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session
from test_orders import _headers, _logins, shop  # noqa: F401 - fixtures
from test_po_tiers import _gate, _order, tiers  # noqa: F401 - fixtures
from test_sourcing import Outbox, db, outbox  # noqa: F401 - fixtures

from p2p.main import app
from p2p.orders import changes, workflow
from p2p.platform import mail, routing, tenants

D = Decimal
client = TestClient(app)


@pytest.fixture
def codes(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """The code emails (sent straight by SMTP, never queued)."""
    sent: list[dict[str, Any]] = []

    def fake(to: str, subject: str, body: str, **kw: Any) -> str:
        sent.append({"to": to, "subject": subject, "body": body})
        return "id"

    monkeypatch.setattr(mail, "send_email", fake)
    return sent


def _code(sent: list[dict[str, Any]]) -> str:
    return sent[-1]["body"].split("code is ")[1][:6]


def _big(db: Session, no: str = "R-C1", qty: str = "700") -> str:
    """700 castings at 650 = 4,55,000.00 + IGST 18% 81,900.00 = 5,36,900.00: the director's band, above the limit."""
    _gate(db, "propose")
    return _order(db, no, "RM-01", qty, "650")


def _approve(db: Session, po: str, user_id: int, **body: Any) -> Any:
    return client.post(
        f"/api/purchase-orders/{po}/approve", json=body, headers=_headers(db.info["slug"], user_id, "Approver")
    )


def _ask(db: Session, po: str, user_id: int) -> Any:
    return client.post(
        f"/api/purchase-orders/{po}/approval-code", json={}, headers=_headers(db.info["slug"], user_id, "Approver")
    )


def test_an_order_above_the_limit_needs_the_approvers_code_and_it_works_once(
    tiers: Session,  # noqa: F811
    outbox: Outbox,  # noqa: F811
    codes: list[dict[str, Any]],
) -> None:
    logins = _logins(tiers)
    po = _big(tiers)
    tiers.rollback()
    assert tiers.execute(text("select total, rule_code from po where no = :n"), {"n": po}).one() == (
        D("536900.00"),
        "PO-03",
    )
    page = client.get(
        f"/api/purchase-orders/{po}", headers=_headers(tiers.info["slug"], logins["director"], "Approver")
    ).json()
    assert page["approval"]["needs_code"] is True and page["approval"]["code_above"] == "500000.00"

    missing = _approve(tiers, po, logins["director"], version=1)
    assert missing.status_code == 409 and "needs a one-time code" in missing.json()["detail"]
    asked = _ask(tiers, po, logins["director"])
    assert asked.status_code == 200 and asked.json() == {"sent_to": codes[-1]["to"], "expires_minutes": 10}
    assert codes[-1]["subject"] == f"Your code to approve purchase order {po}"
    assert _ask(tiers, po, logins["director"]).status_code == 409  # again within 30 seconds
    code = _code(codes)
    wrong = "000000" if code != "000000" else "111111"
    bad = _approve(tiers, po, logins["director"], version=1, code=wrong)
    assert bad.status_code == 409 and "4 tries left" in bad.json()["detail"]
    tiers.rollback()
    assert (
        tiers.execute(text("select status from po_approval_step where po_no = :n"), {"n": po}).scalar_one() == "pending"
    )  # nothing approved

    done = _approve(tiers, po, logins["director"], version=1, code=code)

    assert done.status_code == 200 and done.json() == {"status": "approved"}
    tiers.rollback()
    after = tiers.execute(
        text("select after from audit_event where action = 'po.approve' and entity_id = :n"), {"n": po}
    ).scalar_one()
    assert after["with_code"] is True and after["version"] == 1
    used = tiers.execute(
        text("select purpose, ref, status from one_time_code where ref like :r"), {"r": f"{po}:%"}
    ).one()
    assert used == ("po_approval", f"{po}:1:1:1", "used")


def test_a_code_for_one_version_does_not_approve_the_next(
    tiers: Session,  # noqa: F811
    outbox: Outbox,  # noqa: F811
    codes: list[dict[str, Any]],
) -> None:
    logins = _logins(tiers)
    po = _big(tiers, "R-C2", "720")
    assert _ask(tiers, po, logins["director"]).status_code == 200
    old = _code(codes)
    # the buyer cuts it to 700 while it waits: version 2, still the director's band
    changes.revise(tiers, tiers.info["slug"], po, logins["buyer"], "Fewer", [changes.LineChange(1, qty=D("700"))])

    stale = _approve(tiers, po, logins["director"], version=2, code=old)

    assert stale.status_code == 409 and "ask for one" in stale.json()["detail"]
    assert _ask(tiers, po, logins["director"]).status_code == 200
    assert _approve(tiers, po, logins["director"], version=2, code=_code(codes)).status_code == 200


def test_rejecting_needs_no_code_and_an_order_at_the_limit_needs_none(
    tiers: Session,  # noqa: F811
    outbox: Outbox,  # noqa: F811
    codes: list[dict[str, Any]],
) -> None:
    logins = _logins(tiers)
    po = _big(tiers)
    head = _headers(tiers.info["slug"], logins["director"], "Approver")

    rejected = client.post(f"/api/purchase-orders/{po}/reject", json={"reason": "Too many", "version": 1}, headers=head)

    assert rejected.status_code == 200 and rejected.json() == {"status": "cancelled"}
    # an order of 1,28,148.00 in the procurement head's band: no code, and none can be asked for
    small = _order(tiers, "R-C3", "RM-01", "167", "650")  # 1,08,550.00 + 19,539.00 = 1,28,089.00
    tiers.rollback()
    assert workflow.needs_code(tiers, {"total": D("500000.00")}) is False
    assert workflow.needs_code(tiers, {"total": D("500000.01")}) is True
    refused = _ask(tiers, small, logins["procurement"])
    assert refused.status_code == 409 and refused.json()["detail"] == (
        f"{small} needs no code: it is not above ₹ 5,00,000.00"
    )
    assert codes == []
    assert _approve(tiers, small, logins["procurement"]).json() == {"status": "approved"}


def test_only_the_person_whose_turn_it_is_gets_a_code_and_a_deputy_gets_their_own(
    tiers: Session,  # noqa: F811
    outbox: Outbox,  # noqa: F811
    codes: list[dict[str, Any]],
) -> None:
    logins = _logins(tiers)
    po = _big(tiers)
    for other in (logins["procurement"], logins["cfo"]):
        assert _ask(tiers, po, other).status_code == 409  # not their turn
    assert codes == []
    assert _ask(tiers, po, logins["director"]).status_code == 200
    director_code = _code(codes)
    # the CFO stands in for the director on the open step (as a deputy does); the deputy needs a code of their own
    director = workflow.org_code_of(tiers, logins["director"])
    cfo = workflow.org_code_of(tiers, logins["cfo"])
    tiers.execute(text("update po_approval_step set delegate = :c where approver = :d"), {"c": cfo, "d": director})
    tiers.commit()

    with_theirs = _approve(tiers, po, logins["cfo"], version=1, code=director_code)

    assert with_theirs.status_code == 409 and "ask for one" in with_theirs.json()["detail"]
    assert _ask(tiers, po, logins["cfo"]).status_code == 200 and codes[-1]["to"] != codes[0]["to"]
    assert _approve(tiers, po, logins["cfo"], version=1, code=_code(codes)).status_code == 200


def test_a_touchless_order_is_approved_by_the_rule_with_no_code(tiers: Session, outbox: Outbox) -> None:  # noqa: F811
    _gate(tiers)
    po = _order(tiers, "R-C4", "RM-01", "50", "650")
    tiers.rollback()
    assert tiers.execute(text("select status from po where no = :n"), {"n": po}).scalar_one() == "sent"
    assert tiers.execute(text("select count(*) from one_time_code")).scalar_one() == 0


def test_another_company_cannot_ask_a_code_for_this_companys_order(
    tiers: Session,  # noqa: F811
    outbox: Outbox,  # noqa: F811
    codes: list[dict[str, Any]],
) -> None:
    logins = _logins(tiers)
    po = _big(tiers)
    other = "b" + uuid.uuid4().hex[:8]
    assert tenants.create_tenant(other, load_reference=False).ok
    try:
        asked = client.post(
            f"/api/purchase-orders/{po}/approval-code",
            json={},
            headers=_headers(other, logins["director"], "Approver"),
        )
        assert asked.status_code in (401, 403, 404, 409) and codes == []
    finally:
        routing.clear_engine_cache()
        tenants.drop_tenant(other)
