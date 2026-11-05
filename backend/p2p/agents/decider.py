"""A decider that lets a model choose each turn through the model gateway."""

import json
from typing import Any

from sqlalchemy.orm import Session

from p2p.agents.runtime import Decision, Message

SYSTEM = (
    "You are an assistant that works by calling tools. Each turn reply with JSON: either "
    '{"final": "<answer for the user>"} or {"tool": "<tool name>", "args": {...}, "confidence": <0 to 1>}. '
    "Call one tool at a time and use only the tools listed. Never invent values; if something is missing, "
    "say so in the final answer."
)


class GatewayDecider:
    def __init__(self, gateway: Any, step: str = "agent", db: Session | None = None) -> None:
        self.gateway, self.step, self.db = gateway, step, db

    def __call__(self, messages: list[Message], specs: list[dict[str, Any]]) -> Decision:
        tools = "\n".join(
            f"- {s['function']['name']}: {s['function']['description']} "
            f"parameters {json.dumps(s['function']['parameters'])}"
            for s in specs
        )
        convo: list[dict[str, str]] = [{"role": "system", "content": f"{SYSTEM}\nTools:\n{tools}"}]
        for m in messages:
            if m["role"] == "system":
                continue
            prefix = f"[{m.get('name', 'tool')} result] " if m["role"] == "tool" else ""
            convo.append(
                {"role": "assistant" if m["role"] == "assistant" else "user", "content": prefix + str(m["content"])}
            )
        got = self.gateway.extract(self.step, convo, Decision, db=self.db, prompt_version="agent-1")
        decision: Decision = got.value
        return decision
