"""The exceptions screen: everything stuck that needs a person, with the reason, the owner, the next step and the age in
working days; each person sees what their roles may see."""

import uuid
from datetime import date, timedelta
from decimal import Decimal
from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session
from test_invoices import _goods, _invoice, ledger  # noqa: F401 - fixtures
from test_orders import _headers, shop  # noqa: F401 - fixtures
from test_receiving import _sent, store  # noqa: F401 - fixtures
from test_service_entry import _service_po, _unordered, svc  # noqa: F401 - fixtures
from test_sourcing import Outbox, db, outbox  # noqa: F401 - fixtures

from p2p.formulas.calendar import IST, BusinessCalendar
from p2p.invoices import service as invoices
from p2p.main import app
from p2p.platform import exceptions, proposals, routing, tenants, users
from p2p.platform.sod import user

D = Decimal
client = TestClient(app)
PASSWORD = "correct horse battery staple"  # noqa: S105 - test only
WEEKDAYS = BusinessCalendar.of()
FRI, MON, TUE = date(2026, 11, 20), date(2026, 11, 23), date(2026, 11, 24)


def _ap(shop_: Session) -> int:
    return users.create_user(shop_.info["slug"], f"ap{shop_.info['sfx']}@x.example", PASSWORD, "Ap Asha", "AP")


def _rows(shop_: Session, login: int, today: date = TUE, cal: BusinessCalendar = WEEKDAYS, **kw: Any) -> list[dict]:
    shop_.expire_all()
    return exceptions.for_user(shop_, login, today, cal, **kw)


def _one(shop_: Session, login: int, kind: str, **kw: Any) -> dict:
    (row,) = [r for r in _rows(shop_, login, **kw) if r["kind"] == kind]
    return row


# ---- invoices ---------------------------------------------------------------------------------


def test_given_a_price_block_when_ap_looks_then_it_shows_why_who_and_what_next_and_a_requester_does_not_see_it(
    svc: Session, outbox: Outbox
) -> None:  # noqa: F811
    po_no, logins = _goods(svc, qty="10", price="1000")
    held = _invoice(svc, po_no, "10", "1060", user=logins["buyer"])
    ap = _ap(svc)

    row = _one(svc, ap, "invoice")

    assert (row["ref"], row["link"], row["owner"], row["mine"]) == (held.no, f"#/invoices/{held.no}", "AP", True)
    assert row["reason"].startswith("Price above the order price")
    assert row["next_step"].startswith("Ask the supplier for a credit note")
    assert row["amount"] == str(svc.execute(text("select total from invoice")).scalar_one())
    assert [r for r in _rows(svc, logins["keeper"]) if r["kind"] == "invoice"] == []  # no invoice permission


def test_given_a_release_asked_for_when_it_waits_then_finance_owns_it_but_not_who_entered_it_and_once_released_it_drops_off(
    svc: Session, outbox: Outbox
) -> None:  # noqa: F811
    po_no, logins = _goods(svc, qty="10", price="1000")
    ap = _ap(svc)
    held = _invoice(svc, po_no, "10", "1060", user=ap)
    pid = invoices.request_release(svc, held.no, "agreed with the supplier", ap)

    row = _one(svc, logins["cfo"], "invoice")
    assert row["owner"].startswith("Finance or an approver") and row["mine"] is True
    assert row["next_step"] == "Approve or reject the release on Approvals"
    assert _one(svc, ap, "invoice")["mine"] is False  # the person who entered it may not release it
    assert [r for r in _rows(svc, logins["cfo"]) if r["kind"] == "proposal"] == []  # shown once, on the invoice

    proposals.approve(svc, pid, logins["cfo"])

    assert [r for r in _rows(svc, ap) if r["kind"] == "invoice"] == []


def test_given_an_unconfirmed_service_invoiced_then_the_requester_owns_the_block_even_without_invoice_rights(
    svc: Session, outbox: Outbox
) -> None:  # noqa: F811
    po_no, logins = _service_po(svc)
    _invoice(svc, po_no, user=logins["buyer"])

    row = _one(svc, logins["keeper"], "invoice")

    assert row["reason"].startswith("Service not confirmed")
    assert (row["owner"], row["mine"]) == ("Requester: Keeper Mahesh", True)
    assert row["next_step"].startswith("Confirm the service")


# ---- services past their date, and the age in working days -----------------------------------


def test_a_service_appears_only_after_its_due_date_and_ages_in_working_days_across_a_weekend_and_a_holiday(
    svc: Session, outbox: Outbox
) -> None:  # noqa: F811
    po_no, logins = _service_po(svc)
    svc.execute(text("update po_line set need_by = :d"), {"d": FRI})
    svc.commit()
    keeper = logins["keeper"]

    assert [r for r in _rows(svc, keeper, today=FRI) if r["kind"] == "service"] == []  # due today: not late yet
    saturday = _one(svc, keeper, "service", today=FRI + timedelta(days=1))
    assert (saturday["ref"], saturday["age_days"], saturday["since"]) == (po_no, 0, FRI.isoformat())
    assert saturday["owner"] == "Requester: Keeper Mahesh" and saturday["mine"] is True
    assert saturday["amount"] == "1000.00" and "was due by 20 Nov 2026" in saturday["reason"]
    assert _one(svc, keeper, "service", today=MON)["age_days"] == 1  # Saturday and Sunday do not count
    assert _one(svc, keeper, "service", today=TUE)["age_days"] == 2
    holiday = BusinessCalendar.of([MON])
    assert _one(svc, keeper, "service", today=TUE, cal=holiday)["age_days"] == 1  # nor does a holiday
    assert _one(svc, logins["buyer"], "service")["mine"] is False  # the buyer sees it, the requester acts
    assert [r for r in _rows(svc, logins["stores"]) if r["kind"] == "service"] == []  # neither asked nor buys


# ---- goods received without an order ----------------------------------------------------------


def test_given_goods_without_an_order_then_the_purchase_manager_owns_them_then_the_buyer_once_approved(
    svc: Session, outbox: Outbox
) -> None:  # noqa: F811
    _, logins = _sent(svc)
    no = _unordered(svc, logins["stores"])

    row = _one(svc, logins["procurement"], "unordered")
    assert (row["ref"], row["owner"], row["mine"]) == (no, "Purchase manager", True)
    assert row["reason"].startswith("Goods received without an order from Alpha Traders (Bearing)")
    assert row["next_step"].startswith("Approve the goods")
    assert _one(svc, logins["stores"], "unordered")["mine"] is False  # recorded them: may not approve them
    assert [r for r in _rows(svc, logins["keeper"]) if r["kind"] == "unordered"] == []
    assert [r for r in _rows(svc, logins["procurement"]) if r["kind"] == "proposal"] == []  # shown once

    pid = svc.execute(text("select proposal_id from unordered_receipt where no = :n"), {"n": no}).scalar_one()
    proposals.approve(svc, pid, logins["procurement"])

    after = _one(svc, logins["buyer"], "unordered", mine=True)
    assert after["owner"].startswith("Buyer") and after["next_step"].startswith("Raise the order after the fact")


# ---- proposals, approvals past their reminder, suppliers to chase -----------------------------


def test_given_a_proposal_waiting_then_the_roles_that_may_decide_own_it_and_the_proposer_never(
    svc: Session, outbox: Outbox
) -> None:  # noqa: F811
    _, logins = _sent(svc)
    pid = proposals.propose(
        svc, user(logins["director"]), action="po.accept_variance", summary="Accept a price 3% above the contract",
        payload={}, required_permission="po.approve", value=D("1234.50"),
    )  # fmt: skip
    svc.commit()

    row = _one(svc, logins["cfo"], "proposal")
    assert (row["ref"], row["reason"], row["amount"], row["mine"]) == (
        f"Proposal {pid}",
        "Accept a price 3% above the contract",
        "1234.50",
        True,
    )
    assert "Approver" in row["owner"] and row["next_step"] == "Approve or reject it on Approvals"
    assert _one(svc, logins["director"], "proposal")["mine"] is False


def test_given_an_approval_reminded_and_still_waiting_then_the_approver_owns_it(svc: Session, outbox: Outbox) -> None:  # noqa: F811
    _, logins = _sent(svc)
    opened = svc.execute(text("select now()")).scalar_one()
    svc.execute(
        text(
            "insert into request (no, requester, ship_to_location, type, channel, status, total_value, approval_round) "
            "values ('R-LATE', 'U4', 'WH-BLR', 'goods', 'form', 'submitted', 5000, 1)"
        )
    )
    svc.execute(
        text(
            "insert into approval_step (request_no, round, step, role, approver, status, opened_at, reminded_at) "
            "values ('R-LATE', 1, 1, 'manager', 'U3', 'pending', :o, :o), ('R-LATE', 1, 2, 'director', 'U1', 'waiting', null, null)"
        ),
        {"o": opened},
    )
    svc.commit()

    row = _one(svc, logins["stores"], "approval")

    assert (row["ref"], row["owner"], row["mine"], row["amount"]) == (
        "R-LATE",
        "Approver: Stores Prakash",
        True,
        "5000.00",
    )
    assert row["since"] == opened.astimezone(IST).date().isoformat()
    assert (
        _one(svc, logins["director"], "approval")["mine"] is False
    )  # an approver sees it; the next step is not yet his


def test_given_an_order_the_supplier_has_not_acknowledged_then_the_buyer_is_to_chase_it(
    svc: Session, outbox: Outbox
) -> None:  # noqa: F811
    po_no, logins = _sent(svc)
    sent = svc.execute(text("select sent_at from po")).scalar_one().astimezone(IST).date()

    row = _one(svc, logins["buyer"], "supplier", today=sent + timedelta(days=14))

    assert (row["ref"], row["owner"], row["mine"]) == (po_no, "Buyer", True)
    assert row["next_step"] == "Ask the supplier to acknowledge the order" and row["since"] == sent.isoformat()


def test_rows_come_oldest_first_and_filter_by_kind_and_by_mine(svc: Session, outbox: Outbox) -> None:  # noqa: F811
    po_no, logins = _service_po(svc)
    svc.execute(text("update po_line set need_by = '2026-11-02'"))  # before the goods below came in (6 Nov)
    svc.commit()
    _unordered(svc, logins["stores"])

    everything = _rows(svc, logins["buyer"])
    assert [r["kind"] for r in everything if r["kind"] in ("service", "unordered")] == ["service", "unordered"]
    ages = [r["age_days"] for r in everything]
    assert ages == sorted(ages, reverse=True) and ages[0] == 16
    assert {r["kind"] for r in _rows(svc, logins["buyer"], kind="unordered")} == {"unordered"}
    assert all(r["mine"] for r in _rows(svc, logins["keeper"], mine=True))
    assert [r["ref"] for r in _rows(svc, logins["keeper"], mine=True)] == [po_no]


# ---- the API, and one company never sees another's --------------------------------------------


def test_the_api_lists_a_persons_rows_and_a_company_never_sees_anothers(svc: Session, outbox: Outbox) -> None:  # noqa: F811
    _, logins = _sent(svc)
    _unordered(svc, logins["stores"])
    other = "b" + uuid.uuid4().hex[:8]
    assert tenants.create_tenant(other, load_reference=False).ok
    try:
        here = _headers(svc.info["slug"], logins["procurement"], "Approver")
        data = client.get("/api/exceptions?kind=unordered&mine=true", headers=here).json()
        assert [r["owner"] for r in data["rows"]] == ["Purchase manager"]
        assert {"key": "invoice", "title": "Invoices blocked"} in data["kinds"]
        there = _headers(other, logins["procurement"], "Approver")
        assert client.get("/api/exceptions", headers=there).json()["rows"] == []
        assert client.get("/api/exceptions?kind=nonsense", headers=here).status_code == 422
        assert client.get("/api/exceptions").status_code == 401
    finally:
        routing.clear_engine_cache()
