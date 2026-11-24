"""Releasing a block and accepting a price variance (block 11, 11.5; AT-07).

A price variance is a proposal for the person the price variance rule names, never the enterer, the asker, a requester,
a receiver or an order maker. The executor re-checks under the line lock, so a release whose goods were taken
meanwhile fails with the reason and the invoice stays blocked.
"""

from datetime import date, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session
from test_invoices import _goods, _invoice, ledger  # noqa: F401 - fixtures
from test_orders import _headers, shop  # noqa: F401 - fixtures
from test_receiving import store  # noqa: F401 - fixtures
from test_sourcing import PASSWORD, Outbox, db, outbox  # noqa: F401 - fixtures

from p2p.invoices import service as invoices
from p2p.platform import permissions, proposals, users

D = Decimal
TODAY = date(2026, 11, 6)


def _ap(world: Session) -> int:
    return users.create_user(
        world.info["slug"],
        f"ap{world.info['sfx']}@x.example",
        PASSWORD,
        "Ap Asha",
        "AP",
    )


def test_the_guard_refuses_every_person_who_may_not_accept_a_price_and_leaves_the_proposal_waiting(
    ledger: Session, outbox: Outbox
) -> None:  # noqa: F811
    po_no, logins = _goods(ledger, qty="10", price="1000")
    ledger.execute(text("update request set requester = 'U4', on_behalf_of = null"))
    ap = _ap(ledger)
    held = _invoice(ledger, po_no, "10", "1060", number="P-1", user=ap)
    pid = invoices.request_release(ledger, held.no, "agreed in writing", ap)

    for login in (
        logins["cfo"],
        logins["keeper"],
        logins["stores"],
    ):  # not named; requester; receiver
        with pytest.raises(permissions.PermissionDenied):
            proposals.approve(ledger, pid, login)
        assert (
            ledger.execute(
                text("select status from invoice where no = :n"), {"n": held.no}
            ).scalar_one()
            == "blocked"
        )
        assert (
            ledger.execute(
                text("select status from proposal where id = :i"), {"i": pid}
            ).scalar_one()
            == "pending"
        )


def test_an_order_maker_cannot_accept_the_price_of_their_own_order(
    ledger: Session, outbox: Outbox
) -> None:  # noqa: F811
    po_no, logins = _goods(ledger, qty="10", price="1000")
    ledger.execute(
        text("update po set buyer = 'U2' where no = :p"), {"p": po_no}
    )  # the CFO made it
    ap = _ap(ledger)
    held = _invoice(ledger, po_no, "10", "1060", number="P-2", user=ap)
    pid = invoices.request_release(ledger, held.no, "agreed", ap)

    with pytest.raises(permissions.PermissionDenied, match="made the order"):
        proposals.approve(ledger, pid, logins["cfo"])


def test_the_named_approvers_deputy_may_decide(ledger: Session, outbox: Outbox) -> None:  # noqa: F811
    po_no, logins = _goods(ledger, qty="10", price="1000")
    ledger.execute(
        text(
            "insert into delegation (from_user, to_user, start_date, end_date, reason) "
            "values ('U5', 'U2', :a, :b, 'leave')"
        ),
        {"a": TODAY - timedelta(days=1), "b": TODAY + timedelta(days=1)},
    )
    ledger.commit()
    ap = _ap(ledger)
    held = _invoice(ledger, po_no, "10", "1060", number="P-3", user=ap)
    pid = invoices.request_release(ledger, held.no, "agreed", ap)

    proposals.approve(ledger, pid, logins["cfo"])

    assert (
        ledger.execute(
            text("select status from invoice where no = :n"), {"n": held.no}
        ).scalar_one()
        == "approved"
    )


def test_a_release_whose_goods_were_taken_meanwhile_fails_and_the_invoice_stays_blocked(
    ledger: Session, outbox: Outbox
) -> None:  # noqa: F811
    po_no, logins = _goods(ledger, qty="10", price="1000")
    ap = _ap(ledger)
    first = _invoice(ledger, po_no, "10", "1060", number="P-4", user=ap)
    second = _invoice(ledger, po_no, "10", "1060", number="P-5", user=ap)
    one = invoices.request_release(ledger, first.no, "agreed", ap)
    two = invoices.request_release(ledger, second.no, "agreed too", ap)
    proposals.approve(ledger, one, logins["procurement"])  # books all 10 accepted

    proposals.approve(ledger, two, logins["procurement"])

    ledger.expire_all()
    assert (
        ledger.execute(
            text("select status from invoice where no = :n"), {"n": second.no}
        ).scalar_one()
        == "blocked"
    )
    row = ledger.execute(
        text("select execution, result from proposal where id = :i"), {"i": two}
    ).one()
    assert row[0] == "failed"
    assert (
        f"{second.no} can no longer be released: line 1 (order line 1): 10 more than can be invoiced"
        in row[1]["error"]
    )
    assert "match it again" in row[1]["error"]


def test_the_asker_is_told_the_outcome(ledger: Session, outbox: Outbox) -> None:  # noqa: F811
    po_no, logins = _goods(ledger, qty="10", price="1000")
    ap = _ap(ledger)
    held = _invoice(ledger, po_no, "10", "1060", number="P-6", user=ap)
    pid = invoices.request_release(ledger, held.no, "agreed", ap)
    proposals.approve(ledger, pid, logins["procurement"])

    told = ledger.execute(
        text(
            "select title from notification where user_id = :u and kind = 'invoice_release' order by id desc limit 1"
        ),
        {"u": ap},
    ).scalar_one()
    assert told == f"{held.no} was released by Procure Vikram"


def test_a_second_release_for_one_invoice_is_refused(
    ledger: Session, outbox: Outbox
) -> None:  # noqa: F811
    po_no, _ = _goods(ledger, qty="10", price="1000")
    ap = _ap(ledger)
    held = _invoice(ledger, po_no, "10", "1060", number="P-7", user=ap)
    invoices.request_release(ledger, held.no, "agreed", ap)

    with pytest.raises(invoices.InvoiceError, match="already waiting"):
        invoices.request_release(ledger, held.no, "again", ap)


def test_the_api_reports_the_variance_and_the_named_approver(
    ledger: Session, outbox: Outbox
) -> None:  # noqa: F811
    from fastapi.testclient import TestClient

    from p2p.main import app

    client = TestClient(app)
    po_no, _ = _goods(ledger, qty="10", price="1000")
    ap = _ap(ledger)
    held = _invoice(ledger, po_no, "10", "1060", number="P-8", user=ap)
    h_ap = _headers(ledger.info["slug"], ap, "AP")

    detail = client.get(f"/api/invoices/{held.no}", headers=h_ap).json()

    assert detail["release"]["allowed"] is True
    assert (detail["release"]["kind"], detail["release"]["variance"]) == (
        "price",
        "600.00",
    )
    assert detail["release"]["approver_name"] == "Procure Vikram"
    assert (
        detail["match"][0]["variance_value"] == "600.00"
        and detail["match"][0]["rules"] == 2
    )
    asked = client.post(
        f"/api/invoices/{held.no}/release", json={"reason": "agreed"}, headers=h_ap
    ).json()
    assert (asked["kind"], asked["variance"], asked["approver"]) == (
        "price",
        "600.00",
        "U5",
    )
