"""A request's life outside approval: withdraw it, cancel it, copy it; and the requester is told how it went."""

from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient
from outbox_support import queued_mails
from sqlalchemy import text
from sqlalchemy.orm import Session
from test_approvals import Logins, _audit, _headers, _queued_for_sourcing, _request, _row, _submit, db, logins  # noqa: F401 - fixtures

from p2p.approvals import workflow
from p2p.formulas.calendar import business_today
from p2p.main import app
from p2p.platform import permissions
from p2p.requests import lifecycle

client = TestClient(app)
KEEPER = "U4"  # a storekeeper with a login that only has the Requester role


@pytest.fixture(autouse=True)
def _request_numbers(db: Session) -> None:
    """A copy gets the next request number, so the company needs its number series."""
    db.execute(
        text(
            "insert into number_series (document_type, prefix, scope, format, reset) values "
            "('request', 'REQ', 'location', '{prefix}-{loc}-{fy4}-{seq:05d}', 'fy')"
        )
    )
    db.commit()


def _mine(db: Session, no: str, total: str = "20000.00") -> str:
    """A draft raised by the keeper; 20,000 goes to the manager (the stores head)."""
    return _request(db, no, total, requester=KEEPER)


def _notices(db: Session, login: int) -> list[str]:
    return [
        r[0] for r in db.execute(text("select title from notification where user_id = :u order by id"), {"u": login})
    ]


# ---- withdraw ---------------------------------------------------------------------------------


def test_the_requester_takes_a_waiting_request_back_and_the_approver_is_told_and_the_task_goes(
    db: Session,
    logins: Logins,  # noqa: F811
) -> None:
    no = _mine(db, "R-W1")
    _submit(db, no)
    task_id = db.execute(text("select task_id from approval_step where request_no = :n"), {"n": no}).scalar_one()
    assert task_id is not None

    status = lifecycle.withdraw(db, db.info["slug"], no, logins.keeper)

    assert status == "draft" and _row(db, no)["status"] == "draft" and _row(db, no)["decided_at"] is None
    assert (
        db.execute(text("select status from approval_step where request_no = :n"), {"n": no}).scalar_one()
        == "cancelled"
    )
    assert db.execute(text("select status from task where id = :i"), {"i": task_id}).scalar_one() == "cancelled"
    assert f"Request {no} was withdrawn" in _notices(db, logins.stores)
    rows = _audit(db, "request.withdraw", no)
    assert len(rows) == 1 and rows[0]["actor_id"] == str(logins.keeper)


def test_a_withdrawn_request_can_be_changed_and_submitted_again_in_a_new_round(db: Session, logins: Logins) -> None:  # noqa: F811
    no = _mine(db, "R-W2")
    _submit(db, no)
    lifecycle.withdraw(db, db.info["slug"], no, logins.keeper)

    again = _submit(db, no)

    assert again.status == "submitted" and _row(db, no)["approval_round"] == 2
    assert (
        db.execute(
            text("select count(*) from approval_step where request_no = :n and status = 'pending'"), {"n": no}
        ).scalar_one()
        == 1
    )


def test_only_a_request_waiting_for_approval_can_be_withdrawn(db: Session, logins: Logins) -> None:  # noqa: F811
    draft = _mine(db, "R-W3")

    with pytest.raises(lifecycle.LifecycleError, match="only a request waiting for approval"):
        lifecycle.withdraw(db, db.info["slug"], draft, logins.keeper)


def test_nobody_but_the_requester_withdraws_or_cancels_a_request(db: Session, logins: Logins) -> None:  # noqa: F811
    no = _mine(db, "R-W4")
    _submit(db, no)

    with pytest.raises(lifecycle.LifecycleError, match="only the person who raised"):
        lifecycle.withdraw(db, db.info["slug"], no, logins.stores)
    with pytest.raises(lifecycle.LifecycleError, match="only the person who raised"):
        lifecycle.cancel(db, db.info["slug"], no, logins.director, "not needed")
    assert _row(db, no)["status"] == "submitted"


# ---- cancel -----------------------------------------------------------------------------------


@pytest.mark.parametrize("before", ["draft", "returned", "submitted"])
def test_a_request_is_cancelled_with_a_reason_and_its_lines_stop_counting(
    db: Session,
    logins: Logins,
    before: str,  # noqa: F811
) -> None:
    no = _mine(db, f"R-C-{before}")
    if before != "draft":
        _submit(db, no)
    if before == "returned":
        workflow.return_for_changes(db, no, logins.stores, "Please add the drawing")

    assert lifecycle.cancel(db, db.info["slug"], no, logins.keeper, "Not needed any more") == "cancelled"

    row = _row(db, no)
    assert row["status"] == "cancelled" and "Not needed any more" in row["decision_reason"]
    assert db.execute(
        text("select distinct status from request_line where request_no = :n"), {"n": no}
    ).scalars().all() == ["cancelled"]
    assert (
        db.execute(
            text("select count(*) from approval_step where request_no = :n and status in ('pending', 'waiting')"),
            {"n": no},
        ).scalar_one()
        == 0
    )
    rows = _audit(db, "request.cancel", no)
    assert len(rows) == 1 and rows[0]["reason"] == "Not needed any more"


def test_a_cancel_needs_a_reason_and_a_cancelled_request_cannot_be_cancelled_twice(db: Session, logins: Logins) -> None:  # noqa: F811
    no = _mine(db, "R-C2")

    with pytest.raises(lifecycle.LifecycleError, match="reason is required"):
        lifecycle.cancel(db, db.info["slug"], no, logins.keeper, "  ")
    lifecycle.cancel(db, db.info["slug"], no, logins.keeper, "Duplicate")
    with pytest.raises(lifecycle.LifecycleError, match="cannot be cancelled"):
        lifecycle.cancel(db, db.info["slug"], no, logins.keeper, "Again")


def test_an_approved_request_is_cancelled_until_the_buyer_has_started_buying_it(db: Session, logins: Logins) -> None:  # noqa: F811
    early = _mine(db, "R-C3", "5000.00")  # within the automatic band: approved at once
    started = _mine(db, "R-C4", "5000.00")
    assert _submit(db, early).status == "approved" and _submit(db, started).status == "approved"
    db.execute(text("update request_line set status = 'sourcing' where request_no = :n"), {"n": started})
    db.commit()

    lifecycle.cancel(db, db.info["slug"], early, logins.keeper, "Changed our mind")
    with pytest.raises(lifecycle.LifecycleError, match="already being bought"):
        lifecycle.cancel(db, db.info["slug"], started, logins.keeper, "Changed our mind")

    assert _row(db, early)["status"] == "cancelled" and _row(db, started)["status"] == "approved"
    assert early in _queued_for_sourcing(
        db
    )  # the queued job finds a cancelled request and does nothing (see sourcing/jobs.py)


def test_a_cancelled_request_frees_its_items_for_the_next_restock_run(db: Session, logins: Logins) -> None:  # noqa: F811
    no = _mine(db, "R-C5")
    lifecycle.cancel(db, db.info["slug"], no, logins.keeper, "Not needed")

    counted = db.execute(
        text(
            "select count(*) from request_line l join request r on r.no = l.request_no "
            "where l.status in ('open', 'sourcing') and r.status in ('draft', 'submitted', 'approved', 'returned')"
        )
    ).scalar_one()

    assert counted == 0


def test_a_restock_request_nobody_raised_is_cancelled_by_a_buyer_only(db: Session, logins: Logins) -> None:  # noqa: F811
    no = _request(db, "R-C6", "1000.00", requester=None)

    with pytest.raises(permissions.PermissionDenied, match="only a buyer"):
        lifecycle.cancel(db, db.info["slug"], no, logins.keeper, "Not needed")


# ---- copy -------------------------------------------------------------------------------------


def test_a_copy_is_a_new_draft_in_the_copiers_name_with_the_same_lines_and_a_new_date(
    db: Session, logins: Logins
) -> None:  # noqa: F811
    old = _mine(db, "R-K1", "20000.00")
    db.execute(
        text(
            "update request_line set qty = 4, est_price = 5000, uom = 'NOS', gl_code = '5100', category_code = 'C1' where request_no = :n"
        ),
        {"n": old},
    )
    db.execute(text("update request set status = 'rejected' where no = :n"), {"n": old})
    db.commit()

    new = lifecycle.copy(db, logins.keeper, old, today=date(2026, 11, 6))

    head = _row(db, new)
    assert (
        new != old
        and head["status"] == "draft"
        and head["requester"] == KEEPER
        and head["justification"] == "Copy of R-K1"
    )
    assert (
        head["need_by"] == date(2026, 11, 13) and str(head["total_value"]) == "20000.00" and head["channel"] == "form"
    )
    line = db.execute(
        text(
            "select qty, est_price, uom, gl_code, category_code, need_by, status from request_line where request_no = :n"
        ),
        {"n": new},
    ).one()
    assert (str(line[0]), str(line[1]), line[2], line[3], line[4], line[5], line[6]) == (
        "4.0000",
        "5000.0000",
        "NOS",
        "5100",
        "C1",
        date(2026, 11, 13),
        "open",
    )
    assert _row(db, old)["status"] == "rejected"  # the old one is untouched
    rows = _audit(db, "request.copy", new)
    assert len(rows) == 1 and rows[0]["after"]["copied_from"] == old


def test_a_copy_skips_cancelled_lines_and_refuses_a_past_date(db: Session, logins: Logins) -> None:  # noqa: F811
    old = _mine(db, "R-K2")
    db.execute(
        text(
            "insert into request_line (request_no, line_no, free_text, qty, status) values (:n, 2, 'Gone', 1, 'cancelled')"
        ),
        {"n": old},
    )
    db.commit()

    new = lifecycle.copy(db, logins.keeper, old, today=date(2026, 11, 6))

    assert db.execute(text("select count(*) from request_line where request_no = :n"), {"n": new}).scalar_one() == 1
    with pytest.raises(lifecycle.LifecycleError, match="in the past"):
        lifecycle.copy(db, logins.keeper, old, date(2026, 11, 1), today=date(2026, 11, 6))
    with pytest.raises(lifecycle.LifecycleError, match="does not exist"):
        lifecycle.copy(db, logins.keeper, "R-NOPE")


def test_a_cancelled_request_is_copied_whole_because_that_is_why_one_copies_it(db: Session, logins: Logins) -> None:  # noqa: F811
    old = _mine(db, "R-K3")
    lifecycle.cancel(db, db.info["slug"], old, logins.keeper, "Wrong date")

    new = lifecycle.copy(db, logins.keeper, old, today=date(2026, 11, 6))

    assert db.execute(
        text("select count(*), min(status) from request_line where request_no = :n"), {"n": new}
    ).one() == (1, "open")


# ---- the requester is told ---------------------------------------------------------------------


def test_the_requester_is_told_when_the_request_is_approved_rejected_or_returned(db: Session, logins: Logins) -> None:  # noqa: F811
    slug = db.info["slug"]
    approved, rejected, returned = _mine(db, "R-N1"), _mine(db, "R-N2"), _mine(db, "R-N3")
    for no in (approved, rejected, returned):
        _submit(db, no)

    workflow.approve(db, slug, approved, logins.stores, "ok")
    workflow.reject(db, rejected, logins.stores, "Too expensive", tenant=slug)
    workflow.return_for_changes(db, returned, logins.stores, "Add the drawing", tenant=slug)

    assert _notices(db, logins.keeper) == [
        "Request R-N1 approved",
        "Request R-N2 rejected",
        "Request R-N3 returned to you for changes",
    ]
    body = db.execute(
        text("select body from notification where user_id = :u and title like '%R-N2%'"), {"u": logins.keeper}
    ).scalar_one()
    assert "Too expensive" in body


def test_a_request_that_approves_itself_tells_the_requester_and_a_restock_request_tells_nobody(
    db: Session, logins: Logins
) -> None:  # noqa: F811
    mine = _mine(db, "R-N4", "5000.00")
    nobody = _request(db, "R-N5", "5000.00", requester=None)

    _submit(db, mine)
    _submit(db, nobody)

    assert _notices(db, logins.keeper) == ["Request R-N4 approved"]
    assert db.execute(text("select count(*) from notification where title like '%R-N5%'")).scalar_one() == 0


def test_every_decision_is_also_queued_as_an_email_to_the_requester(db: Session, logins: Logins) -> None:  # noqa: F811
    no = _mine(db, "R-N6")
    _submit(db, no)

    workflow.reject(db, no, logins.stores, "No budget", tenant=db.info["slug"])

    mails = [m["subject"] for m in queued_mails(db)]
    assert "Request R-N6 rejected" in mails


# ---- the screens ------------------------------------------------------------------------------


def test_the_request_page_offers_withdraw_cancel_and_copy_only_to_those_who_may_and_the_api_does_it(
    db: Session, logins: Logins
) -> None:  # noqa: F811
    slug = db.info["slug"]
    mine, keeper = _mine(db, "R-S1"), _headers(slug, logins.keeper, "Requester")
    stores = _headers(slug, logins.stores, "Approver")
    _submit(db, mine)

    page = client.get(f"/api/requisitions/{mine}", headers=keeper).json()["approval"]
    assert (page["can_withdraw"], page["can_cancel"], page["can_copy"]) == (True, True, True)
    other = client.get(f"/api/requisitions/{mine}", headers=stores).json()["approval"]
    assert (other["can_withdraw"], other["can_cancel"]) == (False, False)
    assert client.post(f"/api/requisitions/{mine}/withdraw", headers=stores).status_code == 409

    assert client.post(f"/api/requisitions/{mine}/withdraw", headers=keeper).json() == {"status": "draft"}
    assert client.post(f"/api/requisitions/{mine}/cancel", json={}, headers=keeper).status_code == 409  # no reason
    assert client.post(f"/api/requisitions/{mine}/cancel", json={"reason": "Not needed"}, headers=keeper).json() == {
        "status": "cancelled"
    }
    copy = client.post(f"/api/requisitions/{mine}/copy", json={}, headers=keeper)
    assert copy.status_code == 200 and copy.json()["id"] != mine
    assert client.get(f"/api/requisitions/{copy.json()['id']}", headers=keeper).json()["status"] == "draft"
    assert client.post(f"/api/requisitions/{mine}/copy", json={"need_by": "soon"}, headers=keeper).status_code == 422
    assert client.post("/api/requisitions/R-NOPE/withdraw", headers=keeper).status_code == 404
    assert client.post(f"/api/requisitions/{mine}/withdraw").status_code == 401
    assert business_today() + timedelta(days=7) == date.fromisoformat(
        client.get(f"/api/requisitions/{copy.json()['id']}", headers=keeper).json()["need_by"]
    )
