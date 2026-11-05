import uuid
from collections.abc import Iterator
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

from p2p.formulas.calendar import IST
from p2p.platform import permissions, proposals, routing, tenants, users
from p2p.platform.proposals import ProposalError
from p2p.platform.sod import Actor

NOW = datetime(2026, 11, 5, 12, 0, tzinfo=IST)
EXECUTED: list[dict[str, Any]] = []


@proposals.executor("test.release_payment")
def _release(db: Session, payload: dict[str, Any], approver: Actor) -> dict[str, Any]:
    EXECUTED.append({"payload": payload, "by": approver.id})
    return {"released": payload["invoice"]}


@proposals.executor("test.explode")
def _explode(db: Session, payload: dict[str, Any], approver: Actor) -> dict[str, Any]:
    raise RuntimeError("bank said no")


class Env:
    def __init__(self) -> None:
        self.slug = "t" + uuid.uuid4().hex[:8]
        tenants.create_tenant(self.slug, load_reference=False)
        self.db = Session(routing.engine_for(self.slug))
        self.buyer = self.user("Buyer")
        self.finance = self.user("Finance")
        users.set_limit(self.slug, self.finance, "payment_run", Decimal("1000000"))

    def user(self, role: str) -> int:
        return users.create_user(
            self.slug, f"{role}.{uuid.uuid4().hex[:6]}@example.com", "correct horse battery", role, role
        )

    def propose(self, **kw: Any) -> int:
        base: dict[str, Any] = dict(
            action="test.release_payment",
            summary="Release payment for INV-1",
            payload={"invoice": "INV-1", "amount": "50000"},
            required_permission="payment.approve",
            document="payment_run",
            value=Decimal("50000"),
        )
        return proposals.propose(self.db, kw.pop("by", Actor("user", str(self.buyer))), **{**base, **kw})

    def status(self, pid: int) -> tuple[str, str | None]:
        self.db.expire_all()
        row = self.db.execute(text("select status, execution from proposal where id = :i"), {"i": pid}).one()
        return row[0], row[1]


@pytest.fixture
def env(control_db: str) -> Iterator[Env]:
    EXECUTED.clear()
    e = Env()
    yield e
    e.db.close()
    routing.clear_engine_cache()


def test_nothing_runs_until_an_allowed_person_approves(env: Env) -> None:
    pid = env.propose()
    assert EXECUTED == [] and env.status(pid) == ("pending", None)

    ref = proposals.approve(env.db, pid, env.finance, note="checked against the invoice")

    assert ref.status == "approved" and env.status(pid) == ("approved", "ok")
    assert EXECUTED == [{"payload": {"invoice": "INV-1", "amount": "50000"}, "by": str(env.finance)}]


def test_the_audit_log_shows_who_decided_and_why(env: Env) -> None:
    pid = env.propose()
    proposals.approve(env.db, pid, env.finance, note="checked against the invoice")

    rows = env.db.execute(
        text("select action, actor_type, actor_id, reason from audit_event where entity = 'proposal' order by id")
    ).all()
    assert [tuple(r) for r in rows] == [
        ("proposal.create", "user", str(env.buyer), None),
        ("proposal.approved", "user", str(env.finance), "checked against the invoice"),
    ]


def test_a_person_without_the_permission_cannot_approve(env: Env) -> None:
    pid = env.propose()
    with pytest.raises(permissions.PermissionDenied, match="role"):
        proposals.approve(env.db, pid, env.buyer)
    assert env.status(pid) == ("pending", None) and EXECUTED == []


def test_the_proposer_cannot_approve_their_own_proposal(env: Env) -> None:
    both = env.user("Finance")
    users.set_limit(env.slug, both, "payment_run", Decimal("1000000"))
    pid = env.propose(by=Actor("user", str(both)))

    with pytest.raises(permissions.PermissionDenied, match="requester"):
        proposals.approve(env.db, pid, both)
    assert EXECUTED == []


def test_an_agent_acting_for_a_person_cannot_get_that_person_to_approve_it(env: Env) -> None:
    pid = env.propose(by=Actor("agent", "payments", acts_as=f"user:{env.finance}"))
    with pytest.raises(permissions.PermissionDenied, match="requester"):
        proposals.approve(env.db, pid, env.finance)


def test_the_approver_must_have_a_limit_that_covers_the_value(env: Env) -> None:
    pid = env.propose(value=Decimal("5000000"))
    with pytest.raises(permissions.PermissionDenied, match="limit"):
        proposals.approve(env.db, pid, env.finance)
    assert EXECUTED == []


def test_an_approver_can_edit_the_payload_and_the_edited_version_runs(env: Env) -> None:
    pid = env.propose()
    ref = proposals.approve(
        env.db, pid, env.finance, edited_payload={"invoice": "INV-1", "amount": "45000"}, note="short-paid"
    )

    assert ref.status == "edited" and env.status(pid) == ("edited", "ok")
    assert EXECUTED[0]["payload"]["amount"] == "45000"
    audit_row = env.db.execute(
        text("select before->>'amount', after->'payload'->>'amount' from audit_event where action = 'proposal.edited'")
    ).one()
    assert tuple(audit_row) == ("50000", "45000")


def test_a_proposal_can_be_rejected_only_with_a_reason_and_never_runs(env: Env) -> None:
    pid = env.propose()
    with pytest.raises(ProposalError, match="reason"):
        proposals.reject(env.db, pid, env.finance, " ")
    proposals.reject(env.db, pid, env.finance, "wrong bank account")

    assert env.status(pid) == ("rejected", None) and EXECUTED == []
    with pytest.raises(ProposalError, match="already rejected"):
        proposals.approve(env.db, pid, env.finance)


def test_a_proposal_cannot_be_decided_twice(env: Env) -> None:
    pid = env.propose()
    proposals.approve(env.db, pid, env.finance)
    with pytest.raises(ProposalError, match="already approved"):
        proposals.approve(env.db, pid, env.finance)
    assert len(EXECUTED) == 1


def test_an_expired_proposal_is_refused(env: Env) -> None:
    pid = env.propose(expires_at=NOW + timedelta(hours=1))
    with pytest.raises(ProposalError, match="expired"):
        proposals.approve(env.db, pid, env.finance, now=NOW + timedelta(hours=2))
    assert env.status(pid)[0] == "expired" and EXECUTED == []

    other = env.propose(expires_at=NOW)
    assert proposals.expire_due(env.db, now=NOW + timedelta(minutes=1)) == 1
    assert env.status(other)[0] == "expired"


def test_a_failing_action_is_recorded_as_failed_not_lost(env: Env) -> None:
    pid = env.propose(action="test.explode")
    proposals.approve(env.db, pid, env.finance)

    assert env.status(pid) == ("approved", "failed")
    result = env.db.execute(text("select result->>'error' from proposal where id = :i"), {"i": pid}).scalar_one()
    assert "bank said no" in result


def test_an_action_with_no_executor_cannot_be_approved(env: Env) -> None:
    pid = env.propose(action="test.unknown")
    with pytest.raises(ProposalError, match="no executor"):
        proposals.approve(env.db, pid, env.finance)
    assert env.status(pid) == ("pending", None)
