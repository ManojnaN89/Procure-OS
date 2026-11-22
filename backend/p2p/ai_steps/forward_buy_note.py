"""Explaining a forward-buy recommendation in plain words (one model call, task 13.5; the sourcing agent, WP-11b, will
call it too).

The model only rewords: it is given the record's figures and the evidence as text and writes a short summary and up to
three risks. Code then checks that every number and every date in what it wrote appears in that text; anything else
(a changed quantity, a made-up saving) refuses the whole answer and the fixed-words template stays. With no model, or
on any failure, the template stays and nobody is asked to check anything: the template is the fallback. A model never
changes a number on the record.
"""

from decimal import Decimal
from typing import Any

from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.orm import Session

from p2p.ai_steps.evidence import dates_in, numbers_in
from p2p.ai_steps.gateway import Gateway
from p2p.platform import audit
from p2p.platform.sod import Actor

NAME = "forward_buy_note"
PROMPT_VERSION = "1"
ACTOR = Actor("service", NAME)


class Note(BaseModel):
    summary: str = Field(
        max_length=600, description="two or three plain sentences: why buy more now, and what it saves"
    )
    risks: list[str] = Field(default_factory=list, max_length=3, description="up to three short risks, plain words")


SYSTEM = (
    "You explain to a purchase manager in India why the buying system recommends buying more of an item now, while its "
    "market price is low. You are given the recommendation's figures and its evidence. Reword them plainly in two or "
    "three sentences, then list up to three risks. Use only numbers and dates that appear in what you are given, "
    "written the same way; never work out a new number, never change a quantity, a price or a saving. "
    'Reply with JSON only: {"summary": str, "risks": [str]}.'
)


def source_text(rec: dict[str, Any], template: str) -> str:
    """The record and its evidence as text: the only place the model's numbers may come from."""
    months = "; ".join(
        f"{m['month']}: expected price {m['expected_price']}, quantity {m['qty']}, net saving per unit {m['net_per_unit']}"
        for m in rec.get("months") or []
    )
    return (
        f"{template}\n\nFigures: price now {rec['price_now']}, trend now {rec['trend_now']}, change a month "
        f"{rec['slope']}, below trend {rec['dip_pct']} percent, average use a month {rec['avg_use']}, quantity "
        f"{rec['qty']} {rec['uom']}, value {rec['value']}, gross saving {rec['gross_saving']}, holding cost "
        f"{rec['holding_cost']}, net saving {rec['net_saving']}. Months: {months}."
    )


def invented(note: Note, source: str) -> list[str]:
    """Numbers or dates in the answer that are not in the source."""
    allowed_numbers, allowed_dates = numbers_in(source), dates_in(source)
    text_out = " ".join([note.summary, *note.risks])
    bad = [f"number {n}" for n in sorted(numbers_in(text_out)) if n not in allowed_numbers and n != Decimal(0)]
    bad += [f"date {d}" for d in sorted(dates_in(text_out)) if d not in allowed_dates]
    return bad


def explain(db: Session, fid: int, gateway: Gateway) -> str:
    """Try the model; keep the template unless the answer passes. Returns 'ai' or 'template'."""
    rec = db.execute(text("select * from forward_buy where id = :i"), {"i": fid}).mappings().first()
    if rec is None or rec["explanation_source"] == "ai":
        return "template" if rec is None else "ai"
    data = {k: rec[k] for k in ("price_now", "trend_now", "slope", "dip_pct", "avg_use", "qty", "uom", "value",
                                "gross_saving", "holding_cost", "net_saving", "months")}  # fmt: skip
    source = source_text(data, str(rec["explanation"] or ""))
    messages = [{"role": "system", "content": SYSTEM}, {"role": "user", "content": f"<<<\n{source}\n>>>"}]
    try:
        got = gateway.extract(NAME, messages, Note, db=db, prompt_version=PROMPT_VERSION)
    except Exception as exc:  # noqa: BLE001 - no model, a bad answer, a timeout: the template stays
        audit.record(db, ACTOR, "ai_step.rejected", "forward_buy", fid, reason=f"{type(exc).__name__}: {str(exc)[:300]}",
                     prompt_version=PROMPT_VERSION)  # fmt: skip
        db.commit()
        return "template"
    note = got.value
    assert isinstance(note, Note)
    bad = invented(note, source)
    model = f"{got.completion.provider}/{got.completion.model}"
    if bad:
        audit.record(db, ACTOR, "ai_step.rejected", "forward_buy", fid, after=note.model_dump(mode="json"),
                     reason="not in the evidence: " + ", ".join(bad), model=model, prompt_version=PROMPT_VERSION)  # fmt: skip
        db.commit()
        return "template"
    words = note.summary.strip() + (" Risks: " + "; ".join(r.strip() for r in note.risks) + "." if note.risks else "")
    db.execute(
        text(
            "update forward_buy set explanation = :x, explanation_source = 'ai' where id = :i and status in ('open', 'accepted')"
        ),
        {"x": words, "i": fid},
    )
    audit.record(db, ACTOR, "ai_step.accepted", "forward_buy", fid, after=note.model_dump(mode="json"), model=model,
                 prompt_version=PROMPT_VERSION)  # fmt: skip
    db.commit()
    return "ai"
