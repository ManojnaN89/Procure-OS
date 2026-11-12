import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session

from p2p.agents import autonomy
from p2p.agents.decider import GatewayDecider
from p2p.agents.runtime import AgentRuntime, Decision, Message
from p2p.agents.tools import Tool, ToolRegistry
from p2p.ai_steps.adapters import RawResponse
from p2p.ai_steps.gateway import Gateway
from p2p.platform import proposals, routing, tenants, users
from p2p.platform.proposals import ProposalError
from p2p.platform.sod import Actor

EXECUTED: list[dict[str, Any]] = []
ADMIN = Actor("user", "1")


class Lookup(BaseModel):
    invoice: str


class Release(BaseModel):
    invoice: str
    amount: Decimal


def lookup(db: Session, p: Lookup, actor: Actor) -> dict[str, Any]:
    return {"invoice": p.invoice, "amount": "50000", "status": "approved"}


def release(db: Session, p: Release, actor: Actor) -> dict[str, Any]:
    EXECUTED.append({"invoice": p.invoice, "amount": str(p.amount), "by": f"{actor.kind}:{actor.id}"})
    return {"released": p.invoice}


def registry() -> ToolRegistry:
    r = ToolRegistry()
    r.register(Tool("lookup_invoice", "Read an invoice", Lookup, lookup))
    r.register(
        Tool(
            "release_payment",
            "Release a payment for an invoice",
            Release,
            release,
            writes=True,
            decision_type="payment_release",
            required_permission="payment.approve",
            document="payment_run",
            value_of=lambda p: p.amount,  # type: ignore[attr-defined]
            summary=lambda p: f"Release payment for {p.invoice}",  # type: ignore[attr-defined]
        )
    )
    return r


def scripted(*decisions: Decision) -> Any:
    queue = list(decisions)

    def decide(messages: list[Message], specs: list[dict[str, Any]]) -> Decision:
        return queue.pop(0)

    return decide


CALL_LOOKUP = Decision(tool="lookup_invoice", args={"invoice": "INV-9"})
CALL_RELEASE = Decision(tool="release_payment", args={"invoice": "INV-9", "amount": "50000"}, confidence=0.95)


class Env:
    def __init__(self) -> None:
        self.slug = "t" + uuid.uuid4().hex[:8]
        tenants.create_tenant(self.slug, load_reference=False)
        self.db = Session(routing.engine_for(self.slug))
        self.finance = users.create_user(
            self.slug, f"f{uuid.uuid4().hex[:6]}@example.com", "correct horse battery", "Fin", "Finance"
        )
        users.set_limit(self.slug, self.finance, "payment_run", Decimal("1000000"))

    def runtime(self, decide: Any, db: Session | None = None, **kw: Any) -> AgentRuntime:
        return AgentRuntime(self.slug, db or self.db, registry(), decide, agent="payments", **kw)

    def steps(self, thread: str) -> list[str]:
        rows = self.db.execute(
            text(
                "select s.kind from agent_step s join agent_run r on r.id = s.run_id where r.thread_id = :t order by s.id"
            ),
            {"t": thread},
        )
        return [r[0] for r in rows]

    def run_row(self, thread: str) -> tuple[str, str | None]:
        row = self.db.execute(text("select status, result from agent_run where thread_id = :t"), {"t": thread}).one()
        return row[0], row[1]


@pytest.fixture
def env(control_db: str) -> Iterator[Env]:
    EXECUTED.clear()
    e = Env()
    yield e
    e.db.close()
    routing.clear_engine_cache()


def thread() -> str:
    return "run-" + uuid.uuid4().hex[:8]


# ---- the loop --------------------------------------------------------------------------------


def test_an_agent_reads_with_a_tool_and_then_answers(env: Env) -> None:
    t = thread()
    out = env.runtime(scripted(CALL_LOOKUP, Decision(final="INV-9 is approved for 50000."))).start(
        "Status of INV-9?", t
    )

    assert (out.status, out.result) == ("done", "INV-9 is approved for 50000.")
    assert env.steps(t) == ["model", "tool", "model"]
    assert env.run_row(t) == ("done", "INV-9 is approved for 50000.")


def test_an_unknown_tool_or_bad_arguments_go_back_to_the_model_which_then_carries_on(env: Env) -> None:
    t = thread()
    out = env.runtime(
        scripted(
            Decision(tool="delete_everything"),
            Decision(tool="lookup_invoice", args={"wrong": 1}),
            Decision(final="Could not do it."),
        )
    ).start("x", t)
    assert out.status == "done" and env.steps(t).count("error") == 2


def test_an_agent_that_never_finishes_is_stopped(env: Env) -> None:
    out = env.runtime(scripted(*[CALL_LOOKUP] * 10), max_steps=3).start("loop", thread())
    assert out.status == "done" and "too many steps" in (out.result or "")


# ---- pausing for a person and resuming after a restart ---------------------------------------


def test_a_write_pauses_the_agent_and_nothing_runs_until_a_person_approves(env: Env) -> None:
    t = thread()
    out = env.runtime(scripted(CALL_LOOKUP, CALL_RELEASE)).start("Pay INV-9", t)

    assert out.status == "waiting" and out.proposal_id is not None
    assert EXECUTED == []
    assert env.run_row(t)[0] == "waiting"
    status = env.db.execute(
        text("select status, proposed_by_type, summary from proposal where id = :i"), {"i": out.proposal_id}
    ).one()
    assert tuple(status) == ("pending", "agent", "Release payment for INV-9")


def test_the_run_resumes_from_the_same_point_after_a_restart_and_the_person_not_the_agent_acts(env: Env) -> None:
    t = thread()
    first = env.runtime(scripted(CALL_LOOKUP, CALL_RELEASE)).start("Pay INV-9", t)
    assert first.status == "waiting"

    # the application restarts: nothing is kept in memory, a new session and a new runtime take over
    env.db.close()
    routing.clear_engine_cache()
    fresh_db = Session(routing.engine_for(env.slug))
    proposals.approve(fresh_db, first.proposal_id or 0, env.finance, note="matches the invoice")
    out = env.runtime(scripted(Decision(final="Payment for INV-9 was released.")), db=fresh_db).resume(
        t, first.proposal_id or 0
    )

    assert (out.status, out.result) == ("done", "Payment for INV-9 was released.")
    assert EXECUTED == [
        {"invoice": "INV-9", "amount": "50000", "by": f"user:{env.finance}"}
    ]  # run once, by the approver
    fresh_db.close()
    env.db = Session(routing.engine_for(env.slug))
    assert env.steps(t) == ["model", "tool", "model", "gate", "wait", "resume", "model"]


def test_a_rejected_action_is_never_run_and_the_agent_is_told(env: Env) -> None:
    t = thread()
    first = env.runtime(scripted(CALL_RELEASE)).start("Pay INV-9", t)
    proposals.reject(env.db, first.proposal_id or 0, env.finance, "wrong bank account")
    seen: list[list[Message]] = []

    def decide(messages: list[Message], specs: list[dict[str, Any]]) -> Decision:
        seen.append(messages)
        return Decision(final="Not paid: a person rejected it.")

    out = env.runtime(decide).resume(t, first.proposal_id or 0)

    assert out.status == "done" and EXECUTED == []
    assert "rejected this action: wrong bank account" in seen[0][-1]["content"]


def test_a_run_cannot_resume_before_the_person_has_decided(env: Env) -> None:
    t = thread()
    first = env.runtime(scripted(CALL_RELEASE)).start("Pay", t)
    with pytest.raises(ProposalError, match="not been decided"):
        env.runtime(scripted(Decision(final="x"))).resume(t, first.proposal_id or 0)


# ---- the autonomy gate (FR37) ----------------------------------------------------------------


def auto(env: Env, *, cap: str | None = "100000") -> None:
    autonomy.configure(env.db, ADMIN, "payment_release", level="auto", value_cap=Decimal(cap) if cap else None)


def test_within_the_limits_the_agent_acts_on_its_own_and_it_is_recorded(env: Env) -> None:
    auto(env)
    t = thread()
    out = env.runtime(scripted(CALL_RELEASE, Decision(final="Done."))).start("Pay INV-9", t)

    assert out.status == "done"
    assert EXECUTED == [{"invoice": "INV-9", "amount": "50000", "by": "agent:payments"}]
    assert env.db.execute(text("select count(*) from autonomy_outcome where auto")).scalar_one() == 1
    assert env.db.execute(text("select count(*) from audit_event where action = 'agent.auto_action'")).scalar_one() == 1


@pytest.mark.parametrize(
    ("setup", "decision", "why"),
    [
        (
            "cap",
            Decision(tool="release_payment", args={"invoice": "I", "amount": "150000"}, confidence=0.99),
            "above the automatic limit of ₹ ",
        ),
        (
            "cap",
            Decision(tool="release_payment", args={"invoice": "I", "amount": "5000"}, confidence=0.5),
            "confidence",
        ),
        ("off", CALL_RELEASE, "a person confirms it first"),
    ],
)
def test_outside_the_limits_the_action_becomes_a_proposal(env: Env, setup: str, decision: Decision, why: str) -> None:
    if setup == "cap":
        auto(env)
    out = env.runtime(scripted(decision)).start("Pay", thread())

    assert out.status == "waiting" and EXECUTED == []
    gate = env.db.execute(text("select detail->>'reason' from agent_step where kind = 'gate'")).scalar_one()
    assert why in gate


def test_the_kill_switch_stops_automatic_action_at_once(env: Env) -> None:
    auto(env)
    assert env.runtime(scripted(CALL_RELEASE, Decision(final="ok"))).start("Pay", thread()).status == "done"
    assert len(EXECUTED) == 1

    autonomy.set_kill_switch(env.db, ADMIN, True, "incident")
    out = env.runtime(scripted(CALL_RELEASE)).start("Pay again", thread())

    assert out.status == "waiting" and len(EXECUTED) == 1
    assert (
        "kill switch"
        in env.db.execute(
            text("select detail->>'reason' from agent_step where kind = 'gate' order by id desc limit 1")
        ).scalar_one()
    )


def test_a_poor_track_record_takes_autonomy_away(env: Env) -> None:
    autonomy.configure(
        env.db, ADMIN, "payment_release", level="auto", value_cap=Decimal("100000"), max_wrong_rate=Decimal("0.10")
    )
    now = datetime.now(UTC)
    ids = [
        autonomy.record_outcome(env.db, "payment_release", auto=True, now=now - timedelta(days=2)) for _ in range(10)
    ]
    assert autonomy.gate(env.db, "payment_release", value=Decimal(1), confidence=0.99).allowed

    autonomy.mark_wrong(env.db, ids[0], ADMIN, "paid twice")  # 1 of 10 = 10%: still within the limit
    assert autonomy.gate(env.db, "payment_release", value=Decimal(1), confidence=0.99).allowed
    autonomy.mark_wrong(env.db, ids[1], ADMIN, "wrong supplier")  # 2 of 10 = 20%: over the limit
    verdict = autonomy.gate(env.db, "payment_release", value=Decimal(1), confidence=0.99)
    assert not verdict.allowed and "2 of 10" in verdict.reason


def test_old_mistakes_fall_out_of_the_30_day_window(env: Env) -> None:
    autonomy.configure(env.db, ADMIN, "x", level="auto", max_wrong_rate=Decimal("0.01"))
    old = autonomy.record_outcome(env.db, "x", auto=True, now=datetime.now(UTC) - timedelta(days=45))
    autonomy.mark_wrong(env.db, old, ADMIN, "long ago")
    assert autonomy.gate(env.db, "x", value=None, confidence=0.99).allowed


# ---- the model-backed decider ----------------------------------------------------------------


class FakeProvider:
    def chat(self, model: str, messages: Any, *, temperature: float, max_tokens: int, json_output: bool) -> RawResponse:
        self.last = messages
        return RawResponse('{"tool": "lookup_invoice", "args": {"invoice": "INV-9"}, "confidence": 0.9}', 10, 5, "stop")


def test_the_model_backed_decider_turns_a_json_answer_into_a_decision(env: Env) -> None:
    provider = FakeProvider()
    decider = GatewayDecider(Gateway({"fake": provider}, {"agent": [("fake", "m")]}))
    decision = decider([{"role": "user", "content": "status of INV-9?"}], registry().specs())

    assert decision.tool == "lookup_invoice" and decision.args == {"invoice": "INV-9"}
    system = " ".join(m["content"] for m in provider.last)
    assert "lookup_invoice" in system and "release_payment" in system
