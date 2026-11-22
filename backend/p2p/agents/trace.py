"""Tracing (task 11a.4): what a model call belongs to, carried down the call stack without changing signatures.

An agent run and an AI step open a span with what they know (the run, the capability, the person, the workflow, the
document read); every model call made inside it is logged with those fields (ai_steps/gateway.py). A nested span adds
to the outer one and restores it on exit.
"""

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, replace
from typing import Any


@dataclass(frozen=True)
class Trace:
    run_id: int | None = None
    capability: str | None = None
    user_id: int | None = None
    workflow_id: int | None = None
    entity: str | None = None
    entity_id: str | None = None


CURRENT: ContextVar[Trace | None] = ContextVar("ai_trace", default=None)


def current() -> Trace:
    return CURRENT.get() or Trace()


@contextmanager
def span(**fields: Any) -> Iterator[Trace]:
    """Set these fields on top of the current trace (a None leaves the outer value) for the duration of the block."""
    now = replace(current(), **{k: v for k, v in fields.items() if v is not None})
    token = CURRENT.set(now)
    try:
        yield now
    finally:
        CURRENT.reset(token)
