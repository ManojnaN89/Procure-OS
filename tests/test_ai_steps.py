import uuid
from collections.abc import Iterator
from datetime import date
from decimal import Decimal

import pytest
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session

from p2p.ai_steps.adapters import Messages, RawResponse
from p2p.ai_steps.evidence import check_evidence, dates_in, normalise_text, numbers_in
from p2p.ai_steps.gateway import Gateway
from p2p.ai_steps.runner import AiStep, run_step
from p2p.platform import notifications, routing, tenants, users

# ---- FR38 evidence check ---------------------------------------------------------------------


def test_numbers_are_read_after_removing_separators_and_units() -> None:
    nums = numbers_in("Total Rs 1,25,000.50, advance 5 lakh, cap 2.5 crore, fee 40k, qty 40, 12,345")
    assert {
        Decimal("125000.50"),
        Decimal("500000"),
        Decimal("25000000.0"),
        Decimal("40000"),
        Decimal("40"),
        Decimal("12345"),
    } <= nums


def test_dates_are_read_in_the_common_indian_formats() -> None:
    found = dates_in(
        "Delivery 30/11/2026, valid till 5 Dec 2026, PO dated 2026-11-02, also 1st January, 2027 and 31/02/2026"
    )
    assert found == {date(2026, 11, 30), date(2026, 12, 5), date(2026, 11, 2), date(2027, 1, 1)}  # 31 Feb is not a date


def test_text_ignores_case_punctuation_and_spacing() -> None:
    assert normalise_text("  Shree-Steel  Traders, Pvt. Ltd. ") == "shree steel traders pvt ltd"


class Line(BaseModel):
    description: str
    qty: int
    unit_price: Decimal


class Read(BaseModel):
    supplier: str | None = None
    total: int | None = None
    valid_until: date | None = None
    lines: list[Line] = []
    confidence: float = 1.0


SOURCE = "Quote Q-77 from Shree Steel Traders Pvt Ltd.\n40 ball bearings at Rs 125 each.\nTotal Rs 5,000. Valid till 30 Nov 2026."
PATHS = ["supplier", "total", "valid_until", "lines[].description", "lines[].qty", "lines[].unit_price"]


def good() -> Read:
    return Read(
        supplier="Shree Steel Traders",
        total=5000,
        valid_until=date(2026, 11, 30),
        lines=[Line(description="ball bearings", qty=40, unit_price=Decimal("125"))],
    )


def test_a_correct_reading_passes_the_evidence_check() -> None:
    assert check_evidence(good(), SOURCE, PATHS) == []


def test_a_hallucinated_quantity_is_rejected() -> None:
    bad = good()
    bad.lines[0].qty = 50
    failures = check_evidence(bad, SOURCE, PATHS)
    assert [(f.path, f.value) for f in failures] == [("lines[0].qty", 50)]


def test_each_kind_of_value_is_checked_on_its_own_terms() -> None:
    bad = Read(
        supplier="Other Traders",
        total=5001,
        valid_until=date(2026, 12, 31),
        lines=[Line(description="gears", qty=40, unit_price=Decimal("125"))],
    )
    paths = {f.path for f in check_evidence(bad, SOURCE, PATHS)}
    assert paths == {"supplier", "total", "valid_until", "lines[0].description"}


def test_a_missing_value_is_not_a_failure_but_a_wrong_one_is() -> None:
    assert check_evidence(Read(supplier=None, total=None), SOURCE, PATHS) == []
    assert (
        check_evidence({"supplier": "Shree Steel"}, SOURCE, ["supplier"]) == []
    )  # a part of the name is still in the text


# ---- the runner ------------------------------------------------------------------------------


class Scripted:
    """A fake provider: each model name has a list of answers, handed out in order."""

    def __init__(self, answers: dict[str, list[str]]) -> None:
        self.answers = answers
        self.calls: list[str] = []

    def chat(
        self, model: str, messages: Messages, *, temperature: float, max_tokens: int, json_output: bool
    ) -> RawResponse:
        self.calls.append(model)
        return RawResponse(self.answers[model].pop(0), 100, 20, "stop")


STEP = AiStep(
    name="quote_reader",
    prompt_version="v3",
    system="Read the supplier quote. Use only what is in the text.",
    user_template="Quote text:\n{source}",
    schema=Read,
    evidence_paths=PATHS,
    min_confidence=0.6,
)


def as_json(r: Read) -> str:
    return r.model_dump_json()


@pytest.fixture
def db(control_db: str) -> Iterator[Session]:
    slug = "t" + uuid.uuid4().hex[:8]
    tenants.create_tenant(slug, load_reference=False)
    with Session(routing.engine_for(slug)) as session:
        session.info["slug"] = slug
        session.info["reviewer"] = users.create_user(
            slug, f"rev{uuid.uuid4().hex[:6]}@example.com", "correct horse battery", "Reviewer", "Buyer"
        )
        yield session
    routing.clear_engine_cache()


def run(db: Session, provider: Scripted):
    gw = Gateway(
        {"fake": provider},
        {"quote_reader": [("fake", "cheap")], "quote_reader.strong": [("fake", "strong")]},
        sleep=lambda s: None,
    )
    return run_step(
        db,
        db.info["slug"],
        STEP,
        SOURCE,
        gateway=gw,
        review_assignee=db.info["reviewer"],
        entity="quote",
        entity_id="Q-77",
    )


def test_a_correct_reading_is_accepted_and_the_audit_row_names_the_model_and_prompt(db: Session) -> None:
    p = Scripted({"cheap": [as_json(good())], "strong": []})
    result = run(db, p)

    assert result.status == "ok" and not result.used_stronger_model and p.calls == ["cheap"]
    row = db.execute(
        text("select action, model, prompt_version from audit_event where action = 'ai_step.accepted'")
    ).one()
    assert tuple(row) == ("ai_step.accepted", "fake/cheap", "v3")


def test_a_wrong_reading_is_retried_on_the_stronger_model(db: Session) -> None:
    wrong = good()
    wrong.lines[0].qty = 50  # not in the source text
    p = Scripted({"cheap": [as_json(wrong)], "strong": [as_json(good())]})
    result = run(db, p)

    assert result.status == "ok" and result.used_stronger_model and p.calls == ["cheap", "strong"]
    assert result.value is not None and result.value.model_dump()["lines"][0]["qty"] == 40  # type: ignore[attr-defined]


def test_a_reading_that_stays_wrong_goes_to_a_person_and_is_not_used(db: Session) -> None:
    wrong = good()
    wrong.total = 9999
    p = Scripted({"cheap": [as_json(wrong)], "strong": [as_json(wrong)]})
    result = run(db, p)

    assert result.status == "needs_person" and result.task_id is not None
    assert any("total=9999" in f for f in result.failures)
    task = db.execute(
        text("select kind, assignee, payload->>'step', payload->'why_rejected'->>0 from task where id = :i"),
        {"i": result.task_id},
    ).one()
    assert (
        task[0] == "ai_review"
        and task[1] == db.info["reviewer"]
        and task[2] == "quote_reader"
        and "total=9999" in task[3]
    )
    assert notifications.unread(db, db.info["reviewer"])[0][1] == "task"
    row = db.execute(text("select action, reason, model from audit_event where action = 'ai_step.rejected'")).one()
    assert row[0] == "ai_step.rejected" and "total=9999" in row[1] and row[2] == "fake/strong"


def test_a_low_confidence_answer_is_not_trusted_even_if_every_value_is_in_the_source(db: Session) -> None:
    unsure = good()
    unsure.confidence = 0.3
    p = Scripted({"cheap": [as_json(unsure)], "strong": [as_json(unsure)]})
    result = run(db, p)
    assert result.status == "needs_person" and "confidence 0.3 is below 0.6" in result.failures


def test_an_answer_that_is_not_json_twice_over_ends_with_a_person(db: Session) -> None:
    p = Scripted({"cheap": ["sorry, no", "still no"], "strong": ["nope", "nope again"]})
    result = run(db, p)
    assert result.status == "needs_person" and result.value is None
    assert result.failures and "InvalidOutput" in result.failures[0]
