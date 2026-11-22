"""Agent runtime: a tool loop on LangGraph that can pause for a person and resume, even after a restart.

model -> tools -> model ... until the model gives a final answer. A write tool goes through the autonomy gate: if
the gate says no, a proposal is created and the graph pauses (interrupt). The paused state is saved in the tenant's
Postgres by LangGraph's checkpointer, so a new process can resume the same run when the person has decided.
Every step is written to agent_run and agent_step so a run can be followed afterwards.
"""

import json
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, TypedDict

import psycopg
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.postgres import PostgresSaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command
from langgraph.types import interrupt as lg_interrupt
from psycopg.rows import dict_row
from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import text
from sqlalchemy.orm import Session

from p2p.agents import autonomy
from p2p.agents.tools import Tool, ToolRegistry
from p2p.platform import audit, proposals, tenants
from p2p.platform.sod import Actor

Message = dict[str, Any]


class Decision(BaseModel):
    """What the model decided for one turn: a final answer, or one tool to call."""

    final: str | None = None
    tool: str | None = None
    args: dict[str, Any] = Field(default_factory=dict)
    confidence: float = 1.0
    reason: str = ""


Decider = Callable[[list[Message], list[dict[str, Any]]], Decision]


class State(TypedDict, total=False):
    messages: list[Message]
    pending: dict[str, Any] | None
    waiting_proposal: int | None
    result: str | None
    steps: int


@dataclass(frozen=True)
class RunOutcome:
    status: str  # done, waiting, failed
    thread_id: str
    run_id: int
    result: str | None = None
    proposal_id: int | None = None


def tenant_libpq_url(slug: str) -> str:
    engine = tenants.tenant_engine(slug)
    try:
        return engine.url.render_as_string(hide_password=False).replace("postgresql+psycopg://", "postgresql://")
    finally:
        engine.dispose()


def setup_checkpoints(owner_url: str) -> None:
    """Make or bring up to date LangGraph's checkpoint tables in a company's database. Only the owner may create tables,
    so this runs when the company is migrated (`tenants.migrate_tenant`), never while an agent runs."""
    with psycopg.connect(
        owner_url.replace("postgresql+psycopg://", "postgresql://"), autocommit=True, row_factory=dict_row
    ) as conn:
        PostgresSaver(conn).setup()


@contextmanager
def checkpointer(slug: str) -> Iterator[PostgresSaver]:
    conn = psycopg.connect(tenant_libpq_url(slug), autocommit=True, row_factory=dict_row)
    try:
        yield PostgresSaver(conn)  # its tables are made at migration (setup_checkpoints)
    finally:
        conn.close()


class AgentRuntime:
    def __init__(
        self,
        tenant: str,
        db: Session,
        tools: ToolRegistry,
        decide: Decider,
        *,
        agent: str,
        max_steps: int = 10,
        system: str = "",
    ) -> None:
        self.tenant, self.db, self.tools, self.decide = tenant, db, tools, decide
        self.agent, self.max_steps, self.system = agent, max_steps, system
        self.actor = Actor("agent", agent)
        self._current_run = 0

    # ---- public ------------------------------------------------------------------------------

    def start(self, task: str, thread_id: str) -> RunOutcome:
        run_id = int(
            self.db.execute(
                text("insert into agent_run (thread_id, agent, input) values (:t, :a, :i) returning id"),
                {"t": thread_id, "a": self.agent, "i": task},
            ).scalar_one()
        )
        self.db.commit()
        messages: list[Message] = [{"role": "user", "content": task}]
        if self.system:
            messages.insert(0, {"role": "system", "content": self.system})
        return self._drive(
            {"messages": messages, "pending": None, "waiting_proposal": None, "result": None, "steps": 0},
            thread_id,
            run_id,
        )

    def resume(self, thread_id: str, proposal_id: int) -> RunOutcome:
        """Continue a paused run after the person has decided the proposal. Works from a fresh process."""
        run_id = int(
            self.db.execute(text("select id from agent_run where thread_id = :t"), {"t": thread_id}).scalar_one()
        )
        row = self.db.execute(
            text("select status, execution, result, decision_note from proposal where id = :i"), {"i": proposal_id}
        ).one()
        if row[0] == "pending":
            raise proposals.ProposalError(f"proposal {proposal_id} has not been decided yet")
        answer = {"status": row[0], "execution": row[1], "result": row[2], "note": row[3]}
        self._log(run_id, "resume", None, {"proposal_id": proposal_id, **answer})
        self.db.execute(
            text("update agent_run set status = 'running', waiting_for = null where id = :i"), {"i": run_id}
        )
        self.db.commit()
        return self._drive(Command(resume=answer), thread_id, run_id)

    # ---- graph -------------------------------------------------------------------------------

    def _drive(self, start: Any, thread_id: str, run_id: int) -> RunOutcome:
        self._current_run = run_id
        with checkpointer(self.tenant) as saver:
            graph = self._build().compile(checkpointer=saver)
            cfg: RunnableConfig = {"configurable": {"thread_id": thread_id}}
            try:
                out = graph.invoke(start, cfg)
            except Exception as exc:  # noqa: BLE001 - recorded on the run
                self._log(run_id, "error", None, {"error": f"{type(exc).__name__}: {str(exc)[:300]}"})
                self._finish(run_id, "failed", f"{type(exc).__name__}: {str(exc)[:300]}")
                return RunOutcome("failed", thread_id, run_id, f"{type(exc).__name__}: {str(exc)[:300]}")
        if "__interrupt__" in out:
            proposal_id = int(out["__interrupt__"][0].value["proposal_id"])
            self.db.execute(
                text("update agent_run set status = 'waiting', waiting_for = :w where id = :i"),
                {"w": f"proposal {proposal_id}", "i": run_id},
            )
            self.db.commit()
            return RunOutcome("waiting", thread_id, run_id, None, proposal_id)
        self._finish(run_id, "done", out.get("result"))
        return RunOutcome("done", thread_id, run_id, out.get("result"))

    def _build(self) -> StateGraph[State, None, State, State]:
        g = StateGraph(State)
        g.add_node("model", self._model_node)
        g.add_node("tools", self._tools_node)
        g.add_node("wait", self._wait_node)
        g.add_edge(START, "model")
        g.add_conditional_edges(
            "model", lambda s: END if s.get("result") is not None else "tools", {END: END, "tools": "tools"}
        )
        g.add_conditional_edges(
            "tools", lambda s: "wait" if s.get("waiting_proposal") else "model", {"wait": "wait", "model": "model"}
        )
        g.add_edge("wait", "model")
        return g

    # nodes ---------------------------------------------------------------------------------

    def _model_node(self, state: State) -> dict[str, Any]:
        steps = state.get("steps", 0) + 1
        if steps > self.max_steps:
            return {
                "result": "Stopped: the agent used too many steps without finishing.",
                "steps": steps,
                "pending": None,
            }
        decision = self.decide(state["messages"], self.tools.specs())
        self._log(None, "model", None, decision.model_dump())
        if decision.final is not None:
            return {"result": decision.final, "steps": steps, "pending": None}
        return {
            "steps": steps,
            "pending": {"tool": decision.tool, "args": decision.args, "confidence": decision.confidence},
            "messages": [
                *state["messages"],
                {"role": "assistant", "content": json.dumps({"tool": decision.tool, "args": decision.args})},
            ],
        }

    def _tools_node(self, state: State) -> dict[str, Any]:
        pending = state.get("pending") or {}
        name = str(pending.get("tool"))
        tool = self.tools.get(name)
        messages = list(state["messages"])
        if tool is None:
            self._log(None, "error", name, {"error": "unknown tool"})
            return {
                "messages": [
                    *messages,
                    {"role": "tool", "name": name, "content": f"Error: there is no tool called '{name}'."},
                ],
                "pending": None,
            }
        try:
            params = tool.params.model_validate(pending.get("args") or {})
        except ValidationError as exc:
            self._log(None, "error", name, {"error": "bad arguments", "detail": str(exc)[:300]})
            return {
                "messages": [
                    *messages,
                    {"role": "tool", "name": name, "content": f"Error: bad arguments: {str(exc)[:300]}"},
                ],
                "pending": None,
            }
        if not tool.writes:
            result = tool.fn(self.db, params, self.actor)
            self._log(None, "tool", name, {"args": params.model_dump(mode="json"), "result": result})
            return {
                "messages": [*messages, {"role": "tool", "name": name, "content": json.dumps(result, default=str)}],
                "pending": None,
            }
        return self._write_tool(tool, params, pending, messages)

    def _write_tool(
        self, tool: Tool, params: BaseModel, pending: dict[str, Any], messages: list[Message]
    ) -> dict[str, Any]:
        value = tool.value_of(params)
        decision_type = tool.decision_type or tool.name
        verdict = autonomy.gate(self.db, decision_type, value=value, confidence=pending.get("confidence"))
        self._log(None, "gate", tool.name, {"allowed": verdict.allowed, "reason": verdict.reason, "value": value})
        if verdict.allowed:
            result = tool.fn(self.db, params, self.actor)
            autonomy.record_outcome(self.db, decision_type, auto=True, ref=tool.name)
            audit.record(
                self.db,
                self.actor,
                "agent.auto_action",
                "tool",
                tool.name,
                after={"args": params.model_dump(mode="json")},
                reason=verdict.reason,
            )
            self.db.commit()
            self._log(None, "tool", tool.name, {"args": params.model_dump(mode="json"), "result": result, "auto": True})
            return {
                "messages": [
                    *messages,
                    {"role": "tool", "name": tool.name, "content": json.dumps(result, default=str)},
                ],
                "pending": None,
            }
        pid = proposals.propose(
            self.db,
            self.actor,
            action=f"tool.{tool.name}",
            summary=tool.summary(params) or f"{self.agent} wants to run {tool.name}",
            payload=params.model_dump(mode="json"),
            required_permission=tool.required_permission or "",
            risk=tool.risk,
            document=tool.document,
            value=value,
        )
        self._log(None, "wait", tool.name, {"proposal_id": pid, "why": verdict.reason})
        return {"waiting_proposal": pid, "pending": None}

    def _wait_node(self, state: State) -> dict[str, Any]:
        pid = state["waiting_proposal"]
        answer = lg_interrupt({"proposal_id": pid})  # pauses here; resumes with the person's decision
        if answer.get("status") == "rejected":
            content = f"A person rejected this action: {answer.get('note') or 'no reason given'}."
        elif answer.get("execution") == "failed":
            content = f"The action was approved but failed: {json.dumps(answer.get('result'), default=str)}"
        else:
            content = f"A person approved the action. Result: {json.dumps(answer.get('result'), default=str)}"
        return {
            "messages": [*state["messages"], {"role": "tool", "name": "human_decision", "content": content}],
            "waiting_proposal": None,
        }

    # run log -------------------------------------------------------------------------------

    def _log(self, run_id: int | None, kind: str, name: str | None, detail: dict[str, Any]) -> None:
        rid = run_id or self._current_run
        if not rid:
            return
        self.db.execute(
            text("insert into agent_step (run_id, kind, name, detail) values (:r, :k, :n, cast(:d as jsonb))"),
            {"r": rid, "k": kind, "n": name, "d": json.dumps(detail, default=str)},
        )
        self.db.commit()

    def _finish(self, run_id: int, status: str, result: str | None) -> None:
        self.db.execute(
            text("update agent_run set status = :s, result = :r, finished_at = :n, waiting_for = null where id = :i"),
            {"s": status, "r": result, "n": datetime.now(UTC), "i": run_id},
        )
        self.db.commit()
