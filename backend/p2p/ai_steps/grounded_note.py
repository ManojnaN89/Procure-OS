"""Shared by the AI steps that only reword fixed words (block 5's award note, block 6's sourcing notes).

The model is given the template and the engine's figures as text; every number and date in its answer must appear in
that text, else the whole answer is refused and the template stays. Each outcome is an audit row.
"""

import json
from decimal import Decimal
from typing import Any

from sqlalchemy.orm import Session

from p2p.ai_steps.evidence import dates_in, numbers_in
from p2p.platform import audit
from p2p.platform.sod import Actor


def source_text(template: str, figures: dict[str, Any]) -> str:
    """The template and the engine's figures as text: the only place the model's numbers may come from."""
    return f"{template}\n\nFigures: {json.dumps(figures, sort_keys=True, default=str)}"


def invented(words: list[str], source: str) -> list[str]:
    """Numbers or dates in the answer that are not in the source."""
    allowed_numbers, allowed_dates = numbers_in(source), dates_in(source)
    said = " ".join(words)
    bad = [f"number {n}" for n in sorted(numbers_in(said)) if n not in allowed_numbers and n != Decimal(0)]
    return bad + [f"date {d}" for d in sorted(dates_in(said)) if d not in allowed_dates]


def rejected(
    db: Session, actor: Actor, entity: str, entity_id: Any, reason: str, prompt_version: str, *,
    answer: dict[str, Any] | None = None, model: str | None = None,
) -> None:  # fmt: skip
    audit.record(db, actor, "ai_step.rejected", entity, entity_id, after=answer, reason=reason[:500], model=model,
                 prompt_version=prompt_version)  # fmt: skip


def accepted(
    db: Session, actor: Actor, entity: str, entity_id: Any, answer: dict[str, Any], model: str, prompt_version: str
) -> None:
    audit.record(db, actor, "ai_step.accepted", entity, entity_id, after=answer, model=model,
                 prompt_version=prompt_version)  # fmt: skip
