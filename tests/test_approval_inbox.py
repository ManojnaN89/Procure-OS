"""The approver's inbox: approve with a lower quantity, approve several at once, approve from an emailed link, and the
requester's reminder to whoever has the request."""

import time
from decimal import Decimal
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session
from test_approvals import PASSWORD, TODAY, Logins, _audit, _request, _row, _submit, db, logins  # noqa: F401 - fixtures
from test_sourcing import _headers

from p2p.approvals import email_links, workflow
from p2p.main import app
from p2p.platform import mail, permissions, tenants, users

D = Decimal
client = TestClient(app)


def _big(db: Session, no: str, *, requester: str | None = None) -> str:
    """A request for 10 pieces at Rs 2,000 each: it is over the automatic limit, so the stores head decides."""
    _request(db, no, "20000.00", requester=requester)
    db.execute(
        text(
            "update request_line set qty = 10, base_qty = 10, est_price = 2000, free_text = 'Gasket' where request_no = :n"
        ),
        {"n": no},
    )
    db.commit()
    assert _submit(db, no).status == "submitted"
    return no


def _qty(db: Session, no: str) -> tuple[Decimal, Decimal, Decimal]:
    return tuple(  # type: ignore[return-value]
        db.execute(
            text(
                "select l.qty, l.base_qty, r.total_value from request_line l join request r on r.no = l.request_no where r.no = :n"
            ),
            {"n": no},
        ).one()
    )


# ---- a lower quantity -------------------------------------------------------------------------


def test_an_approver_can_approve_less_and_the_value_and_the_audit_follow(db: Session, logins: Logins) -> None:  # noqa: F811
    no = _big(db, "R-Q1")

    status = workflow.approve(db, db.info["slug"], no, logins.stores, "We hold stock for half", quantities={1: D("6")})

    assert status == "approved"
    assert _qty(db, no) == (D("6.0000"), D("6.0000"), D("12000.00"))  # 6 x 2,000, to the paisa
    (edit,) = _audit(db, "request.quantity_edit", no)
    assert edit["reason"] == "We hold stock for half"
    assert edit["after"]["lines"] == [{"line": 1, "from": "10.0000", "to": "6.0000"}]
    assert (edit["before"]["total_value"], edit["after"]["total_value"]) == ("20000.00", "12000.00")


@pytest.mark.parametrize(
    ("quantities", "reason", "message"),
    [
        ({1: D("6")}, None, "a reason is needed"),
        ({1: D("11")}, "more", "lower a quantity, not raise"),
        ({1: D("0")}, "none", "more than zero"),
        ({9: D("1")}, "no such line", "has no line 9"),
    ],
)
def test_a_quantity_that_is_raised_zeroed_unknown_or_unexplained_changes_nothing(
    db: Session,
    logins: Logins,
    quantities: dict,
    reason: str | None,
    message: str,  # noqa: F811
) -> None:
    no = _big(db, "R-Q2")

    with pytest.raises(workflow.ApprovalError, match=message):
        workflow.approve(db, db.info["slug"], no, logins.stores, reason, quantities=quantities)

    db.rollback()
    assert _qty(db, no) == (D("10.0000"), D("10.0000"), D("20000.00"))
    assert _row(db, no)["status"] == "submitted"  # still waiting for the same person
    assert _audit(db, "request.quantity_edit", no) == []


def test_the_same_quantity_is_not_a_change(db: Session, logins: Logins) -> None:  # noqa: F811
    no = _big(db, "R-Q3")

    workflow.approve(db, db.info["slug"], no, logins.stores, "as asked", quantities={1: D("10")})

    assert _audit(db, "request.quantity_edit", no) == []


# ---- several at once --------------------------------------------------------------------------


def test_bulk_approve_decides_each_request_on_its_own_and_reports_what_it_left(db: Session, logins: Logins) -> None:  # noqa: F811
    first, second = _big(db, "R-B1"), _big(db, "R-B2")
    done = workflow.bulk_approve(db, db.info["slug"], [first, second, "R-NOPE"], logins.stores, "ok")
    assert {no: r.get("status") for no, r in done.items() if "status" in r} == {first: "approved", second: "approved"}
    assert "does not exist" in done["R-NOPE"]["error"]

    third = _big(db, "R-B3")
    other = workflow.bulk_approve(db, db.info["slug"], [third], logins.director, None)  # not the director's turn
    assert "not your turn" in other[third]["error"]
    assert _row(db, third)["status"] == "submitted"


def test_the_screens_approve_with_a_quantity_and_in_bulk_over_the_api(db: Session, logins: Logins) -> None:  # noqa: F811
    slug = db.info["slug"]
    h = _headers(slug, logins.stores, "Approver")
    one, two = _big(db, "R-A1"), _big(db, "R-A2")

    r = client.post(f"/api/requisitions/{one}/approve", json={"comment": "less", "quantities": {"1": "4"}}, headers=h)
    assert r.status_code == 200 and r.json() == {"status": "approved"}
    assert (
        client.post(f"/api/requisitions/{two}/approve", json={"quantities": {"1": "abc"}}, headers=h).status_code == 422
    )
    assert (
        client.post(f"/api/requisitions/{two}/approve", json={"quantities": {"1": "3"}}, headers=h).status_code == 409
    )  # no reason

    three = _big(db, "R-A3")
    done = client.post("/api/requisitions/bulk-approve", json={"requests": [two, three, one]}, headers=h)
    assert done.status_code == 200
    assert set(done.json()["approved"]) == {two, three}
    assert "R-A1" in done.json()["refused"]  # already approved: not waiting for anyone
    assert client.post("/api/requisitions/bulk-approve", json={"requests": []}, headers=h).status_code == 422
    keeper = _headers(slug, logins.keeper, "Requester")
    assert client.post("/api/requisitions/bulk-approve", json={"requests": [two]}, headers=keeper).status_code == 403


# ---- an emailed link --------------------------------------------------------------------------


@pytest.fixture
def codes(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """The one-time codes emailed to approvers: every decision from a link needs one (D-07)."""
    sent: list[str] = []

    def fake(to: str, subject: str, body: str, **kw: Any) -> str:
        sent.append(body.split("code is ")[1][:6])
        return "id"

    monkeypatch.setattr(mail, "send_email", fake)
    return sent


def _code(token: str, codes: list[str]) -> str:
    client.post(f"/approve/{token}/code")
    return codes[-1]


def _link_for(db: Session, no: str, user_id: int) -> str:
    req = _row(db, no)
    step = db.execute(
        text("select step from approval_step where request_no = :n and round = :r and status = 'pending'"),
        {"n": no, "r": req["approval_round"]},
    ).scalar_one()
    return email_links.make(db.info["slug"], no, int(req["approval_round"]), int(step), user_id)


def test_the_email_to_the_approver_carries_a_link_that_is_not_kept_in_the_app(db: Session, logins: Logins) -> None:  # noqa: F811
    no = _big(db, "R-E1")

    with tenants.control_engine().connect() as c:
        bodies = [
            r[0]
            for r in c.execute(
                text("select payload->>'body' from job where tenant_slug = :t and kind = 'send_email'"),
                {"t": db.info["slug"]},
            )
        ]
    assert any(f"Approve request {no}" in (b or "") or "/approve/" in (b or "") for b in bodies)
    link = next(b for b in bodies if "/approve/" in (b or ""))
    token = link.split("/approve/")[1].split()[0]
    assert email_links.read(token).request_no == no
    assert db.execute(text("select count(*) from notification where body like '%/approve/%'")).scalar_one() == 0


def test_a_link_that_was_altered_or_has_expired_is_not_valid(db: Session, logins: Logins) -> None:  # noqa: F811
    no = _big(db, "R-E2")
    token = _link_for(db, no, logins.stores)

    assert email_links.read(token).request_no == no
    body, signature = token.split(".")
    for bad in (f"{body}.{signature[:-2]}AA", f"{body[:-2]}AA.{signature}", "garbage", "", f"{body}."):
        with pytest.raises(email_links.LinkError, match="not valid"):
            email_links.read(bad)
    with pytest.raises(email_links.LinkError, match="expired"):
        email_links.read(token, now=time.time() + 8 * 86400)


def test_opening_the_page_changes_nothing_and_the_button_decides_once(
    db: Session,
    logins: Logins,  # noqa: F811
    codes: list[str],
) -> None:
    no = _big(db, "R-E3")
    token = _link_for(db, no, logins.stores)

    page = client.get(f"/approve/{token}")
    assert page.status_code == 200 and "Gasket" in page.text and "Approve" in page.text
    assert page.headers["cache-control"] == "no-store"
    assert _row(db, no)["status"] == "submitted"  # a mail scanner opening the link decides nothing

    code = _code(token, codes)
    done = client.post(f"/approve/{token}", data={"action": "approve", "reason": "fine", "code": code})
    assert done.status_code == 200 and "Approved" in done.text
    db.rollback()
    assert _row(db, no)["status"] == "approved"
    (row,) = _audit(db, "request.approve", no)
    assert row["after"]["via"] == "emailed link" and row["reason"] == "fine"

    again = client.post(f"/approve/{token}", data={"action": "approve", "code": code})
    assert again.status_code == 409 and "no longer waiting" in again.text  # it works once
    assert client.get(f"/approve/{token}").status_code == 409


def test_a_link_is_for_one_person_and_the_same_rules_apply_as_on_the_screen(
    db: Session,
    logins: Logins,  # noqa: F811
    codes: list[str],
) -> None:
    no = _big(db, "R-E4")

    director = client.post(f"/approve/{_link_for(db, no, logins.director)}", data={"action": "approve"})
    assert director.status_code == 409  # the stores head's turn, not the director's
    stores = _link_for(db, no, logins.stores)
    code = _code(stores, codes)
    no_reason = client.post(f"/approve/{stores}", data={"action": "reject", "code": code})
    assert no_reason.status_code == 409 and "reason" in no_reason.text
    assert _row(db, no)["status"] == "submitted"

    rejected = client.post(f"/approve/{stores}", data={"action": "reject", "reason": "not needed", "code": code})
    assert rejected.status_code == 200 and "Rejected" in rejected.text
    db.rollback()
    assert _row(db, no)["status"] == "rejected"
    (row,) = _audit(db, "request.reject", no)
    assert row["after"]["via"] == "emailed link"
    odd_link = _link_for(db, _big(db, "R-E5"), logins.stores)
    odd = client.post(f"/approve/{odd_link}", data={"action": "shrug", "code": _code(odd_link, codes)})
    assert odd.status_code == 409


def test_wrong_links_are_counted_and_too_many_are_refused(db: Session, logins: Logins) -> None:  # noqa: F811
    from p2p.platform import ratelimit

    ratelimit.reset()
    for _ in range(30):
        assert client.get("/approve/not-a-link").status_code == 400
    assert client.get("/approve/not-a-link").status_code == 429
    ratelimit.reset()


# ---- a reminder -------------------------------------------------------------------------------


def test_the_requester_can_remind_the_approver_but_not_again_within_hours(db: Session, logins: Logins) -> None:  # noqa: F811
    no = _big(db, "R-M1", requester="U4")

    name = workflow.remind_approver(db, db.info["slug"], no, logins.keeper)

    assert name == "Stores Prakash"
    assert (
        db.execute(
            text("select count(*) from notification where user_id = :u and title like :t"),
            {"u": logins.stores, "t": f"Reminder: request {no}%"},
        ).scalar_one()
        == 1
    )
    (row,) = _audit(db, "request.remind", no)
    assert row["after"]["approver"] == "U3"
    with pytest.raises(workflow.ApprovalError, match="reminded in the last"):
        workflow.remind_approver(db, db.info["slug"], no, logins.keeper)


def test_only_the_requester_can_remind_and_only_while_it_waits(db: Session, logins: Logins) -> None:  # noqa: F811
    no = _big(db, "R-M2", requester="U4")
    stranger = users.create_user(db.info["slug"], f"o{db.info['sfx']}@x.example", PASSWORD, "Other", "Requester")

    with pytest.raises(permissions.PermissionDenied):
        workflow.remind_approver(db, db.info["slug"], no, stranger)  # not the requester, no view-all right
    workflow.approve(db, db.info["slug"], no, logins.stores, None)
    with pytest.raises(workflow.ApprovalError, match="nobody is deciding"):
        workflow.remind_approver(db, db.info["slug"], no, logins.keeper)


def test_the_page_offers_the_reminder_over_the_api(db: Session, logins: Logins) -> None:  # noqa: F811
    slug = db.info["slug"]
    no = _big(db, "R-M3", requester="U4")

    ok = client.post(f"/api/requisitions/{no}/remind-approver", headers=_headers(slug, logins.keeper, "Requester"))
    assert ok.status_code == 200 and ok.json() == {"reminded": "Stores Prakash"}
    again = client.post(f"/api/requisitions/{no}/remind-approver", headers=_headers(slug, logins.keeper, "Requester"))
    assert again.status_code == 409
    stranger = users.create_user(slug, f"o{db.info['sfx']}@x.example", PASSWORD, "Other", "Requester")
    other = client.post(f"/api/requisitions/{no}/remind-approver", headers=_headers(slug, stranger, "Requester"))
    assert other.status_code == 403
    assert client.post(f"/api/requisitions/{no}/remind-approver").status_code == 401
    assert TODAY  # (the pinned day the fixtures use)


# ---- escalation ---------------------------------------------------------------------------------


def test_an_approval_that_waits_past_its_time_goes_to_the_managers_who_can_then_decide_it(
    db: Session,
    logins: Logins,  # noqa: F811
) -> None:
    from datetime import timedelta

    from p2p.platform import worker

    no = _big(db, "R-X1")
    task = db.execute(
        text("select id, due_at, escalation_chain from task where payload->>'request_no' = :n"), {"n": no}
    ).one()
    assert list(task.escalation_chain) == [logins.cfo]  # the stores head's own manager, from the organisation chart
    assert workflow.can_act(db, no, logins.cfo) is False

    worker.run_once(now=task.due_at + timedelta(minutes=1))
    db.rollback()

    assert db.execute(text("select assignee from task where id = :i"), {"i": task.id}).scalar_one() == logins.cfo
    assert db.execute(text("select delegate from approval_step where request_no = :n"), {"n": no}).scalar_one() == "U2"
    assert workflow.can_act(db, no, logins.cfo) is True
    assert workflow.can_act(db, no, logins.stores) is True  # the approver can still decide it
    (esc,) = _audit(db, "request.escalate", no)
    assert esc["after"] == {"step_approver": "U3", "may_also_decide": "U2"}

    assert workflow.approve(db, db.info["slug"], no, logins.cfo, "covering for Prakash") == "approved"
    (done,) = _audit(db, "request.approve", no)
    assert done["after"]["approver"] == "U2" and done["after"]["on_behalf_of"] == "U3"


def test_the_person_who_asked_is_never_the_one_it_escalates_to(db: Session, logins: Logins) -> None:  # noqa: F811
    no = _big(db, "R-X2", requester="U2")  # the stores head's manager asked for it

    (chain,) = db.execute(text("select escalation_chain from task where payload->>'request_no' = :n"), {"n": no}).one()

    assert list(chain) == []
