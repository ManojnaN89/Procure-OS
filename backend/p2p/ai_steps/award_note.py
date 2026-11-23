"""The award note the approver reads (decision 14, block 5): one model call that rewords the template, never decides.

The template note (sourcing/award_approval.py, fixed words from the engine's figures) is always written first. When the
sourcing capability is on, the model is given that note and the figures as text and writes a short summary and up to
three risks. Code then checks that every number and date in what it wrote appears in that text; anything else refuses
the whole answer (`ai_step.rejected`) and the template stays. With no model, the month's AI budget used up, or any
other failure, the template stays and nobody is asked to check anything. A model never sets a supplier, a price, a
quantity or a value.
"""

from typing import Any

from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.orm import Session

from p2p.agents import capabilities
from p2p.ai_steps import grounded_note
from p2p.platform.sod import Actor

NAME = "award_note"
PROMPT_VERSION = "1"
ACTOR = Actor("service", NAME)


class Note(BaseModel):
    summary: str = Field(max_length=700, description="two or three plain sentences: what is awarded, to whom, and why")
    risks: list[str] = Field(default_factory=list, max_length=3, description="up to three short risks, plain words")


SYSTEM = (
    "You help an approver in India decide a buyer's proposed award of a request for quotation for a period. You are "
    "given the buyer's note and the engine's figures. Reword them plainly in two or three sentences, then list up to "
    "three risks. Use only numbers and dates that appear in what you are given, written the same way; never work out a "
    "new number, never change a supplier, a quantity, a price or a value. "
    'Reply with JSON only: {"summary": str, "risks": [str]}.'
)


source_text = grounded_note.source_text


def invented(note: Note, source: str) -> list[str]:
    """Numbers or dates in the answer that are not in the source."""
    return grounded_note.invented([note.summary, *note.risks], source)


def explain(db: Session, decision_id: int, gateway: Any) -> str:
    """Try the model; keep the template unless the answer passes. Returns 'ai' or 'template'."""
    row = db.execute(
        text(
            "select d.status, d.note, d.note_source, r.engine_figures from award_decision d "
            "left join recommendation r on r.id = d.recommendation_id where d.id = :i"
        ),
        {"i": decision_id},
    ).first()
    if row is None or row[0] != "waiting" or row[2] == "ai":
        return "ai" if row is not None and row[2] == "ai" else "template"
    if not capabilities.is_on(db, "sourcing"):
        return "template"  # a switched-off capability makes no model call
    source = source_text(str(row[1] or ""), dict(row[3] or {}))
    messages = [{"role": "system", "content": SYSTEM}, {"role": "user", "content": f"<<<\n{source}\n>>>"}]
    try:
        got = gateway.extract(NAME, messages, Note, db=db, prompt_version=PROMPT_VERSION)
    except Exception as exc:  # noqa: BLE001 - no model, the cost cap, a bad answer, a timeout: the template stays
        db.rollback()
        grounded_note.rejected(db, ACTOR, "award_decision", decision_id, f"{type(exc).__name__}: {str(exc)[:300]}",
                               PROMPT_VERSION)  # fmt: skip
        db.commit()
        return "template"
    note = got.value
    assert isinstance(note, Note)
    model = f"{got.completion.provider}/{got.completion.model}"
    bad = invented(note, source)
    if bad:
        grounded_note.rejected(db, ACTOR, "award_decision", decision_id, "not in the evidence: " + ", ".join(bad),
                               PROMPT_VERSION, answer=note.model_dump(mode="json"), model=model)  # fmt: skip
        db.commit()
        return "template"
    words = note.summary.strip() + (" Risks: " + "; ".join(r.strip() for r in note.risks) + "." if note.risks else "")
    db.execute(
        text("update award_decision set note = :n, note_source = 'ai' where id = :i and status = 'waiting'"),
        {"n": words, "i": decision_id},
    )
    grounded_note.accepted(
        db, ACTOR, "award_decision", decision_id, note.model_dump(mode="json"), model, PROMPT_VERSION
    )
    db.commit()
    return "ai"
