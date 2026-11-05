import uuid
from decimal import Decimal
from typing import Annotated

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from p2p.platform import permissions, routing, sod, tenants, users
from p2p.platform.permissions import PermissionDenied
from p2p.platform.tokens import create_access_token

PASSWORD = "correct horse battery"

app = FastAPI()


@app.get("/payments/release")
def release(
    ctx: Annotated[routing.TenantContext, Depends(permissions.require("payment.approve"))],
) -> dict[str, int]:
    return {"user": ctx.user_id}


def _tenant() -> str:
    slug = "t" + uuid.uuid4().hex[:8]
    tenants.create_tenant(slug)
    return slug


def _user(slug: str, role: str) -> int:
    return users.create_user(slug, f"{uuid.uuid4().hex[:8]}@example.com", PASSWORD, role, role)


def _db(slug: str) -> Session:
    return Session(routing.engine_for(slug))


# ---- roles and scope -------------------------------------------------------------------------


def test_each_role_can_do_its_own_work_and_not_another_roles(control_db: str) -> None:
    slug = _tenant()
    requester, buyer, store, ap, finance, admin = (
        _user(slug, r) for r in ("Requester", "Buyer", "Store", "AP", "Finance", "Admin")
    )
    with _db(slug) as db:
        assert permissions.can(db, requester, "request.create")
        assert not permissions.can(db, requester, "request.approve")
        assert permissions.can(db, buyer, "po.create") and not permissions.can(db, buyer, "payment.approve")
        assert permissions.can(db, store, "grn.create") and not permissions.can(db, store, "invoice.book")
        assert permissions.can(db, ap, "invoice.book") and not permissions.can(db, ap, "payment.approve")
        assert permissions.can(db, finance, "payment.approve") and not permissions.can(
            db, finance, "po.create"
        )
        assert permissions.can(db, admin, "admin.users") and not permissions.can(db, admin, "request.approve")


def test_a_role_limited_to_one_location_only_works_there(control_db: str) -> None:
    slug = _tenant()
    uid = _user(slug, "Requester")
    users.grant_role(slug, uid, "Store", location="PLT-VAD")
    with _db(slug) as db:
        assert permissions.can(db, uid, "grn.create", location="PLT-VAD")
        assert not permissions.can(db, uid, "grn.create", location="WH-BLR")
        assert not permissions.can(db, uid, "grn.create")  # a scoped role needs the location stated


def test_a_screen_can_be_gated_by_permission(control_db: str) -> None:
    slug = _tenant()
    finance, buyer = _user(slug, "Finance"), _user(slug, "Buyer")
    client = TestClient(app)

    ok = client.get(
        "/payments/release",
        headers={"Authorization": f"Bearer {create_access_token(finance, slug, 'Finance')}"},
    )
    no = client.get(
        "/payments/release", headers={"Authorization": f"Bearer {create_access_token(buyer, slug, 'Buyer')}"}
    )

    assert ok.status_code == 200
    assert no.status_code == 403


# ---- approval limits (FR42) ------------------------------------------------------------------


def test_a_user_may_approve_up_to_their_limit_and_not_above(control_db: str) -> None:
    slug = _tenant()
    uid = _user(slug, "Approver")
    users.set_limit(slug, uid, "po", Decimal("100000"))
    with _db(slug) as db:
        assert permissions.within_limit(db, uid, "po", Decimal("100000"))
        assert not permissions.within_limit(db, uid, "po", Decimal("100000.01"))


def test_no_limit_set_means_no_approval(control_db: str) -> None:
    slug = _tenant()
    uid = _user(slug, "Approver")
    with _db(slug) as db:
        assert not permissions.within_limit(db, uid, "po", Decimal("1"))


def test_the_most_specific_limit_wins(control_db: str) -> None:
    slug = _tenant()
    uid = _user(slug, "Approver")
    users.set_limit(slug, uid, "po", Decimal("500000"))
    users.set_limit(slug, uid, "po", Decimal("50000"), category="CAP")
    users.set_limit(slug, uid, "po", Decimal("2000000"), category="RAW", location="PLT-VAD")
    with _db(slug) as db:
        assert permissions.approval_limit(db, uid, "po") == Decimal("500000")
        assert permissions.approval_limit(db, uid, "po", category="CAP") == Decimal("50000")
        assert permissions.approval_limit(db, uid, "po", category="RAW", location="PLT-VAD") == Decimal(
            "2000000"
        )
        assert permissions.approval_limit(db, uid, "po", category="RAW", location="WH-BLR") == Decimal(
            "500000"
        )
        assert permissions.approval_limit(db, uid, "payment_run") is None


# ---- segregation of duties (FR32) ------------------------------------------------------------


def test_a_user_cannot_approve_what_they_requested(control_db: str) -> None:
    slug = _tenant()
    uid = _user(slug, "Approver")
    users.set_limit(slug, uid, "request", Decimal("1000000"))
    with _db(slug) as db, pytest.raises(PermissionDenied, match="requester"):
        permissions.authorize_approval(
            db,
            uid,
            action="request.approve",
            document="request",
            value=Decimal("5000"),
            duty="approver",
            case_actors={"requester": sod.user(uid)},
        )


def test_an_approval_passes_when_role_limit_and_duties_are_all_fine(control_db: str) -> None:
    slug = _tenant()
    requester, approver = _user(slug, "Requester"), _user(slug, "Approver")
    users.set_limit(slug, approver, "request", Decimal("1000000"))
    with _db(slug) as db:
        permissions.authorize_approval(
            db,
            approver,
            action="request.approve",
            document="request",
            value=Decimal("5000"),
            duty="approver",
            case_actors={"requester": sod.user(requester)},
        )


def test_approval_is_refused_for_a_missing_role_or_a_value_above_the_limit(control_db: str) -> None:
    slug = _tenant()
    buyer, approver = _user(slug, "Buyer"), _user(slug, "Approver")
    users.set_limit(slug, buyer, "request", Decimal("1000000"))
    users.set_limit(slug, approver, "request", Decimal("1000"))
    with _db(slug) as db:
        kwargs = dict(action="request.approve", document="request", duty="approver", case_actors={})
        with pytest.raises(PermissionDenied, match="role"):
            permissions.authorize_approval(db, buyer, value=Decimal("10"), **kwargs)  # type: ignore[arg-type]
        with pytest.raises(PermissionDenied, match="limit"):
            permissions.authorize_approval(db, approver, value=Decimal("1001"), **kwargs)  # type: ignore[arg-type]


def test_an_agent_counts_as_the_person_it_acts_for() -> None:
    requester = sod.user(7)
    agent_for_requester = sod.Actor("agent", "intake", acts_as="user:7")

    with pytest.raises(sod.SegregationError):
        sod.check_segregation({"requester": requester}, "approver", agent_for_requester)


def test_the_five_duties_must_all_be_different_people() -> None:
    case = {
        "requester": sod.user(1),
        "approver": sod.user(2),
        "receiver": sod.user(3),
        "invoice_approver": sod.user(4),
    }

    sod.check_segregation(case, "payer", sod.user(5))  # a fifth person: fine
    for held_by in (1, 2, 3, 4):
        with pytest.raises(sod.SegregationError):
            sod.check_segregation(case, "payer", sod.user(held_by))


def test_the_same_person_may_repeat_their_own_duty() -> None:
    sod.check_segregation(
        {"approver": sod.user(2)}, "approver", sod.user(2)
    )  # a second approval step by the same person is not a conflict
    with pytest.raises(ValueError, match="unknown duty"):
        sod.check_segregation({}, "janitor", sod.user(1))
