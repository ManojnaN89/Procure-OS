"""Tool registry for agents.

A tool is a service function with a name, a description and typed parameters. An agent can call only the tools it is
given. A tool that writes never runs on its own say-so: it goes through the autonomy gate and, unless the gate allows
it, becomes a proposal for a person.
"""

from collections.abc import Callable
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from pydantic import BaseModel
from sqlalchemy.orm import Session

from p2p.platform import proposals
from p2p.platform.sod import Actor

ToolFn = Callable[[Session, Any, Actor], dict[str, Any]]


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    params: type[BaseModel]
    fn: ToolFn
    writes: bool = False
    decision_type: str | None = None  # key into the autonomy settings; defaults to the tool name
    required_permission: str | None = None  # who may approve the proposal
    risk: str = "medium"
    document: str | None = None  # approval-limit document type, when the action has a value
    value_of: Callable[[BaseModel], Decimal | None] = field(default=lambda p: None)
    summary: Callable[[BaseModel], str] = field(default=lambda p: "")


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> Tool:
        if tool.writes and not tool.required_permission:
            raise ValueError(f"write tool '{tool.name}' needs a required_permission for its proposals")
        self._tools[tool.name] = tool
        if tool.writes:
            proposals.EXECUTORS[f"tool.{tool.name}"] = _executor_for(tool)
        return tool

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def names(self) -> list[str]:
        return sorted(self._tools)

    def specs(self) -> list[dict[str, Any]]:
        """The tools in the shape model providers expect."""
        return [
            {
                "type": "function",
                "function": {"name": t.name, "description": t.description, "parameters": t.params.model_json_schema()},
            }
            for t in self._tools.values()
        ]


def _executor_for(tool: Tool) -> proposals.Executor:
    def run(db: Session, payload: dict[str, Any], approver: Actor) -> dict[str, Any] | None:
        args = {k: v for k, v in payload.items() if not k.startswith("_")}
        return tool.fn(db, tool.params.model_validate(args), approver)

    return run
