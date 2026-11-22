"""The recommendation record, revalidation before a person's confirmation, and tracing (tasks 11a.2, 11a.3, 11a.4)."""

import uuid
from collections.abc import Iterator
from typing import Any

import pytest
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from p2p.agents import recommendations, trace
from p2p.agents.runtime import AgentRuntime, Decision, Message
from p2p.agents.tools import Tool, ToolRegistry
from p2p.ai_steps.adapters import RawResponse
from p2p.ai_steps.gateway import Gateway
from p2p.platform import permissions, proposals, routing, tenants, users
from p2p.platform.sod import Actor

STOCK: dict[str, str] = {"RM-01": "10"}


class TopUp(BaseModel):
    item: str
    qty: int


def top_up(db: Session, p: TopUp, actor: Actor) -> dict[str, Any]:
    return {"topped_up": p.item, "qty": p.qty}


def registry() -> ToolRegistry:
    r = ToolRegistry()
    r.register(
        Tool(
            "top_up_item", "Top up an item", TopUp, top_up, writes=True, decision_type="stock.top_up",
            required_permission="payment.approve", summary=lambda p: f"Top up {p.item}",  # type: ignore[attr-defined]
            business_key=lambda p: str(p.item),  # type: ignore[attr-defined]
            basis=lambda db, key: {"item": key, "on_hand": STOCK[key]},
        )
    )  # fmt: skip
    return r


def scripted(*decisions: Decision) -> Any:
    queue = list(decisions)

    def decide(messages: list[Message], specs: list[dict[str, Any]]) -> Decision:
        return queue.pop(0)

    return decide


class Env:
    def __init__(self) -> None:
        self.slug = "t" + uuid.uuid4().hex[:8]
        tenants.create_tenant(self.slug, load_reference=False)
        self.db = Session(routing.engine_for(self.slug))
        self.maker = users.create_user(
            self.slug, f"m{uuid.uuid4().hex[:6]}@example.com", "correct horse battery", "Mo", "Finance"
        )
        self.checker = users.create_user(
            self.slug, f"c{uuid.uuid4().hex[:6]}@example.com", "correct horse battery", "Cy", "Finance"
        )

    def propose(self, user_id: int | None = None) -> tuple[int, int]:
        """A run directed by `user_id` that asks to top up RM-01: (recommendation, proposal)."""
        rt = AgentRuntime(
            self.slug, self.db, registry(), scripted(Decision(tool="top_up_item", args={"item": "RM-01", "qty": 5})),
            agent="planner", capability="planning", user_id=user_id,
        )  # fmt: skip
        out = rt.start("top up RM-01", thread_id="t-" + uuid.uuid4().hex[:8])
        assert out.status == "waiting" and out.proposal_id is not None
        rec = self.db.execute(text("select id from recommendation where proposal_id = :p"), {"p": out.proposal_id})
        return int(rec.scalar_one()), out.proposal_id


@pytest.fixture
def env(control_db: str) -> Iterator[Env]:
    STOCK["RM-01"] = "10"
    e = Env()
    yield e
    e.db.close()
    routing.clear_engine_cache()


def _rec(env: Env, rec_id: int) -> dict[str, Any]:
    env.db.rollback()
    return dict(env.db.execute(text("select * from recommendation where id = :i"), {"i": rec_id}).mappings().one())


def test_a_write_an_agent_asks_for_is_recorded_with_its_evidence_figures_and_run(env: Env) -> None:
    rec_id, pid = env.propose(env.maker)
    rec = _rec(env, rec_id)

    assert (rec["capability"], rec["kind"], rec["business_key"], rec["status"]) == (
        "planning", "tool.top_up_item", "RM-01", "proposed",
    )  # fmt: skip
    assert rec["engine_figures"] == {"item": "RM-01", "on_hand": "10"} and rec["evidence"]
    assert rec["run_id"] is not None and rec["directed_by"] == env.maker and rec["proposal_id"] == pid
    assert env.db.execute(
        text("select payload->>'_recommendation_id' from proposal where id = :p"), {"p": pid}
    ).scalar() == str(rec_id)


def test_the_evidence_cannot_be_changed_once_written(env: Env) -> None:
    rec_id, _ = env.propose()
    with pytest.raises(DBAPIError, match="cannot be changed"):
        env.db.execute(text("update recommendation set evidence = '[]' where id = :i"), {"i": rec_id})
    env.db.rollback()


def test_a_recommendation_needs_its_words_evidence_and_figures(env: Env) -> None:
    common: dict[str, Any] = {
        "capability": "sourcing", "kind": "rfq.draft", "business_key": "RFQ-1", "summary": "Ask three suppliers",
        "evidence": [{"source": "tool", "ref": "x", "value": 1}], "engine_figures": {"total": "100"},
        "actor": Actor("agent", "sourcing"),
    }  # fmt: skip
    for missing in ("summary", "evidence", "engine_figures"):
        with pytest.raises(ValueError):
            recommendations.record(
                env.db, **{**common, missing: "" if missing == "summary" else type(common[missing])()}
            )
    first = recommendations.record(env.db, **common)
    second = recommendations.record(env.db, **common)
    env.db.commit()
    assert _rec(env, first)["status"] == "superseded" and _rec(env, first)["superseded_by"] == second


def test_approving_on_the_same_figures_runs_and_records_the_confirmer(env: Env) -> None:
    rec_id, pid = env.propose(env.maker)
    proposals.approve(env.db, pid, env.checker)

    rec = _rec(env, rec_id)
    assert (rec["status"], rec["decided_by"]) == ("accepted", env.checker) and rec["decided_at"] is not None


def test_whoever_directed_the_agent_cannot_confirm_it_but_may_turn_it_down(env: Env) -> None:
    rec_id, pid = env.propose(env.maker)
    with pytest.raises(permissions.PermissionDenied):
        proposals.approve(env.db, pid, env.maker)
    assert _rec(env, rec_id)["status"] == "proposed"
    proposals.reject(env.db, pid, env.maker, "not needed")
    assert _rec(env, rec_id)["status"] == "rejected"


def test_changed_figures_stop_the_approval_withdraw_the_proposal_and_ask_for_a_fresh_one(env: Env) -> None:
    rec_id, pid = env.propose(env.maker)
    STOCK["RM-01"] = "7"  # the stock moved after the agent recommended

    with pytest.raises(proposals.ProposalError, match="figures changed"):
        proposals.approve(env.db, pid, env.checker)

    rec = _rec(env, rec_id)
    assert rec["status"] == "stale" and "changed" in rec["stale_reason"]
    assert env.db.execute(text("select status from proposal where id = :p"), {"p": pid}).scalar() == "withdrawn"
    assert env.db.execute(text("select count(*) from event where type = 'tool.top_up_item.refresh'")).scalar() == 1


# ---- tracing ----------------------------------------------------------------------------------------------------


class Model:
    def chat(self, model: str, messages: Any, **kw: Any) -> RawResponse:
        return RawResponse('{"final": "done"}', 10, 3, "stop")


def test_model_calls_in_a_run_carry_the_run_capability_and_person(env: Env) -> None:
    from p2p.agents.decider import GatewayDecider

    gw = Gateway({"m": Model()}, {"default": [("m", "one")], "agent": [("m", "one")]})
    rt = AgentRuntime(
        env.slug, env.db, registry(), GatewayDecider(gw, "agent", env.db), agent="assistant", user_id=env.maker,
    )  # fmt: skip
    out = rt.start("hello", thread_id="t-" + uuid.uuid4().hex[:8])

    run = env.db.execute(
        text("select capability, started_by, user_id from agent_run where id = :i"), {"i": out.run_id}
    ).one()
    assert tuple(run) == ("assistant", f"user:{env.maker}", env.maker)
    calls = env.db.execute(text("select run_id, capability, user_id from model_call")).all()
    assert calls and all(tuple(c) == (out.run_id, "assistant", env.maker) for c in calls)


def test_a_nested_span_adds_to_the_outer_one_and_restores_it() -> None:
    with trace.span(run_id=1, capability="sourcing"):
        with trace.span(entity="rfq", entity_id="RFQ-1"):
            assert trace.current() == trace.Trace(1, "sourcing", None, None, "rfq", "RFQ-1")
        assert trace.current() == trace.Trace(1, "sourcing")
    assert trace.current() == trace.Trace()
