"""The sourcing note (task 11b.2, block 6): one model call that rewords a sourcing recommendation's fixed words.

The engine writes every record and its template words (sourcing/notes.py); when the sourcing capability is on, the
model is given that template and the figures as text and writes a short plain note. Every number and date in it must
appear in that text (ai_steps/grounded_note.py), else it is refused and the template stands. With the capability off,
no model, the month's AI budget used up or a timeout, the template stays and nobody is asked to check anything. A model
never sets a price, a target, a path or a supplier.
"""

from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.orm import Session

from p2p.agents import capabilities, trace
from p2p.ai_steps import grounded_note
from p2p.platform.sod import Actor

NAME = "sourcing_note"
PROMPT_VERSION = "1"
ACTOR = Actor("service", NAME)
ENTITY = "recommendation"


class Note(BaseModel):
    note: str = Field(max_length=600, description="the same facts in two or three plain sentences")


SYSTEM = (
    "You help a buyer in India. You are given a short note written by the purchasing system and the figures it came "
    "from. Reword the note plainly in two or three sentences for the buyer. Use only numbers and dates that appear in "
    "what you are given, written the same way; never work out a new number, never change a supplier, a price, a "
    'target or a recommendation. Reply with JSON only: {"note": str}.'
)


def explain(db: Session, rec_id: int, gateway: Any) -> str:
    """Try the model; keep the template unless the answer passes. Returns 'ai' or 'template'."""
    row = db.execute(
        text("select status, summary, engine_figures, ai_note from recommendation where id = :i"), {"i": rec_id}
    ).first()
    if row is None or row[0] != "proposed":
        return "template"
    if row[3]:
        return "ai"
    if not capabilities.is_on(db, "sourcing"):
        return "template"  # a switched-off capability makes no model call
    source = grounded_note.source_text(str(row[1]), dict(row[2] or {}))
    messages = [{"role": "system", "content": SYSTEM}, {"role": "user", "content": f"<<<\n{source}\n>>>"}]
    try:
        with trace.span(capability="sourcing", entity=ENTITY, entity_id=str(rec_id)):
            got = gateway.extract(NAME, messages, Note, db=db, prompt_version=PROMPT_VERSION)
    except Exception as exc:  # noqa: BLE001 - no model, the cost cap, a bad answer, a timeout: the template stays
        db.rollback()
        grounded_note.rejected(db, ACTOR, ENTITY, rec_id, f"{type(exc).__name__}: {str(exc)[:300]}", PROMPT_VERSION)
        db.commit()
        return "template"
    note = got.value
    assert isinstance(note, Note)
    model = f"{got.completion.provider}/{got.completion.model}"
    bad = grounded_note.invented([note.note], source)
    if bad or not note.note.strip():
        why = "not in the evidence: " + ", ".join(bad) if bad else "an empty note"
        grounded_note.rejected(db, ACTOR, ENTITY, rec_id, why, PROMPT_VERSION, answer=note.model_dump(mode="json"),
                               model=model)  # fmt: skip
        db.commit()
        return "template"
    db.execute(
        text(
            "update recommendation set ai_note = :n, ai_note_model = :m, ai_note_at = :at "
            "where id = :i and status = 'proposed'"
        ),
        {"n": note.note.strip(), "m": model, "at": datetime.now(UTC), "i": rec_id},
    )
    grounded_note.accepted(db, ACTOR, ENTITY, rec_id, note.model_dump(mode="json"), model, PROMPT_VERSION)
    db.commit()
    return "ai"
