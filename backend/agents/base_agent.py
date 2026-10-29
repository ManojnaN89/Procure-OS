"""
BaseAgent runs the Groq tool-calling loop for a domain agent:
1. Send conversation + this agent's tool schemas to Llama 3.3 on Groq.
2. If the model requests a tool call, execute the matching skill against Postgres.
3. Feed the result back to the model and repeat until it gives a final answer.

Every skill execution is reported via trail_cb so the frontend's Live Agent
Trail can show exactly what happened.
"""
import json
from groq_client import chat_completion
from skill_loader import build_tools
from settings import currency_note


class BaseAgent:
    MAX_TOOL_ITERATIONS = 5

    def __init__(self, name, system_prompt, skill_names, all_modules):
        self.name = name
        self.system_prompt = system_prompt
        self.tools, self.skill_map = build_tools(skill_names, all_modules)

    def handle(self, db, history, trail_cb=None):
        """
        history: list of {"role": "user"|"assistant", "content": str}
        trail_cb: optional callable(agent_name, skill_name, args, result)
        Returns: final assistant text reply (str)
        """
        messages = [{"role": "system", "content": self.system_prompt + currency_note()}] + history

        for _ in range(self.MAX_TOOL_ITERATIONS):
            response = chat_completion(messages, tools=self.tools, stream=False)
            msg = response.choices[0].message

            if getattr(msg, "tool_calls", None):
                messages.append({
                    "role": "assistant",
                    "content": msg.content or "",
                    "tool_calls": [
                        {
                            "id": tc.id,
                            "type": "function",
                            "function": {"name": tc.function.name, "arguments": tc.function.arguments},
                        }
                        for tc in msg.tool_calls
                    ],
                })
                for tc in msg.tool_calls:
                    skill_mod = self.skill_map[tc.function.name]
                    args = json.loads(tc.function.arguments or "{}")
                    try:
                        result = skill_mod.run(db, **args)
                    except Exception as exc:  # keep the agent alive even if a skill fails
                        result = {"error": str(exc)}
                    if trail_cb:
                        trail_cb(self.name, tc.function.name, args, result)
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "content": json.dumps(result),
                    })
                continue

            return msg.content

        return "I wasn't able to complete that action cleanly — could you rephrase or break it into steps?"
