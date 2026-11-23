"""The award note's AI step (decision 14, block 5): the template is always written first from the engine's figures;
the model may only reword it, every number and date it writes must be in what it was given, a switched-off sourcing
capability makes no call, and the month's AI budget used up leaves the template."""

from dataclasses import dataclass
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session
from test_sourcing import db  # noqa: F401 - fixtures

from p2p.agents import recommendations
from p2p.ai_steps import award_note
from p2p.ai_steps.costs import CostCapReached
from p2p.platform.sod import Actor

TEMPLATE = (
    "Neha Kulkarni proposes to award RFQ-2627-00001 for 01 Jan 2027 to 31 Mar 2027 at Plant, Chakan (Pune). "
    "Line 1, Copper winding wire: Pune Copper Co. at ₹ 880.00 per KGS for 900 KGS, ₹ 7,92,000.00 before GST; "
    "ranked 1 of 3 qualifying quotes on total cost. Award value ₹ 9,34,560.00 with GST."
)
FIGURES = {
    "lines": [{"line": 1, "item": "RM-08", "supplier": "SUP-005", "quote": "RFQ-2627-00001-SUP-005-R1", "revision": 1,
               "qty": "900", "net_price": "880.0000", "taxable": "792000.00", "rank": 1, "qualifying": 3}],
    "value_taxable": "792000.00", "cgst": "71280.00", "sgst": "71280.00", "igst": "0.00", "value_total": "934560.00",
}  # fmt: skip


@dataclass
class _Done:
    provider: str = "fake"
    model: str = "m"


@dataclass
class _Got:
    value: Any
    completion: _Done


class FakeGateway:
    def __init__(self, answer: award_note.Note | Exception) -> None:
        self.answer, self.calls = answer, 0

    def extract(self, name: str, messages: list[dict[str, str]], schema: Any, **kw: Any) -> _Got:
        self.calls += 1
        assert name == "award_note" and "934560.00" in messages[1]["content"]
        if isinstance(self.answer, Exception):
            raise self.answer
        return _Got(self.answer, _Done())


def _decision(db: Session) -> int:  # noqa: F811
    db.execute(
        text(
            "insert into rfq (no, due_date, status, kind, period_from, period_to, delivery_location) values "
            "('RFQ-2627-00001', '2026-11-30', 'award_proposed', 'period', '2027-01-01', '2027-03-31', 'WH-BLR')"
        )
    )
    rec = recommendations.record(
        db, capability="sourcing", kind="award", business_key="RFQ-2627-00001", summary=TEMPLATE,
        evidence=[{"kind": "comparison"}], engine_figures=FIGURES, actor=Actor("user", "3"), directed_by=3,
    )  # fmt: skip
    did = db.execute(
        text(
            "insert into award_decision (rfq_no, round, status, proposed_by, value_taxable, cgst, sgst, igst, "
            "value_total, note, note_source, recommendation_id) values ('RFQ-2627-00001', 1, 'waiting', '3', 792000, "
            "71280, 71280, 0, 934560, :n, 'template', :r) returning id"
        ),
        {"n": TEMPLATE, "r": rec},
    ).scalar_one()
    db.commit()
    return int(did)


def _note(db: Session, did: int) -> tuple[str, str]:  # noqa: F811
    db.rollback()
    return tuple(db.execute(text("select note, note_source from award_decision where id = :i"), {"i": did}).one())  # type: ignore[return-value]


def _steps(db: Session, did: int) -> list[str]:  # noqa: F811
    return list(
        db.execute(
            text("select action from audit_event where entity = 'award_decision' and entity_id = :i order by id"),
            {"i": str(did)},
        ).scalars()
    )


def test_a_good_answer_rewords_the_note_with_only_the_figures_given(db: Session) -> None:  # noqa: F811
    did = _decision(db)
    answer = award_note.Note(
        summary="Pune Copper Co. is proposed for 900 KGS at ₹ 880.00, ₹ 9,34,560.00 with GST, from 01 Jan 2027.",
        risks=["Only 3 qualifying quotes"],
    )
    gateway = FakeGateway(answer)

    assert award_note.explain(db, did, gateway) == "ai"

    note, source = _note(db, did)
    assert (
        source == "ai" and note.startswith("Pune Copper Co. is proposed") and "Risks: Only 3 qualifying quotes." in note
    )
    assert _steps(db, did) == ["ai_step.accepted"]


def test_a_made_up_number_or_date_refuses_the_whole_answer_and_the_template_stays(db: Session) -> None:  # noqa: F811
    did = _decision(db)
    for invented in ("It saves ₹ 45,000.00 against the next quote.", "Delivery starts on 15 Feb 2027."):
        assert award_note.explain(db, did, FakeGateway(award_note.Note(summary=invented))) == "template"
        assert _note(db, did) == (TEMPLATE, "template")
    assert _steps(db, did) == ["ai_step.rejected", "ai_step.rejected"]
    reason = db.execute(
        text("select reason from audit_event where entity = 'award_decision' order by id limit 1")
    ).scalar_one()
    assert reason.startswith("not in the evidence: number 45000")


def test_with_the_sourcing_capability_off_no_model_is_called(db: Session) -> None:  # noqa: F811
    did = _decision(db)
    db.execute(text("update ai_capability set enabled = false where code = 'sourcing'"))
    db.commit()
    gateway = FakeGateway(award_note.Note(summary="anything"))

    assert award_note.explain(db, did, gateway) == "template"

    assert gateway.calls == 0 and _note(db, did) == (TEMPLATE, "template") and _steps(db, did) == []


def test_the_months_ai_budget_used_up_leaves_the_template_and_asks_nobody(db: Session) -> None:  # noqa: F811
    did = _decision(db)
    gateway = FakeGateway(CostCapReached("this month's AI budget of $5 is used up (spent $5)"))

    assert award_note.explain(db, did, gateway) == "template"

    assert _note(db, did) == (TEMPLATE, "template") and _steps(db, did) == ["ai_step.rejected"]
    assert db.execute(text("select count(*) from task")).scalar_one() == 0


def test_a_decided_award_is_never_reworded(db: Session) -> None:  # noqa: F811
    did = _decision(db)
    db.execute(text("update award_decision set status = 'approved' where id = :i"), {"i": did})
    db.commit()
    gateway = FakeGateway(award_note.Note(summary="Pune Copper Co. at ₹ 880.00"))

    assert award_note.explain(db, did, gateway) == "template" and gateway.calls == 0
