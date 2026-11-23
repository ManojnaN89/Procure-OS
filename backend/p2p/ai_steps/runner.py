"""AI step runner: the shared wrapper for a single model call.

render prompt -> call the gateway with a schema -> validate -> evidence check (FR38) -> confidence check.
If a check fails the step is tried once on a stronger model. If it still fails, the result is not used: a task is
created for a person with everything the model said and why it was rejected. A step never passes unchecked output on.
"""

import json
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from pydantic import BaseModel
from sqlalchemy.orm import Session

from p2p.agents import trace
from p2p.ai_steps.adapters import InvalidOutput, ModelError
from p2p.ai_steps.evidence import EvidenceFailure, check_evidence
from p2p.ai_steps.gateway import Completion, Gateway
from p2p.platform import audit, tasks
from p2p.platform.sod import Actor

# The capability (D49) each AI step belongs to, for a caller that does not say.
CAPABILITY_OF = {
    "request_reading": "intake",
    "item_mapping": "intake",
    "quote_reading": "sourcing",
    "certificate_reading": "sourcing",
    "supplier_message": "execution",
    "reply_classification": "execution",
    "invoice_reading": "invoice_ap",
    "award_note": "sourcing",
    "sourcing_note": "sourcing",
}


def _capability_on(db: Session, code: str) -> bool:
    from p2p.agents import capabilities  # noqa: PLC0415 - the switch imports the AI steps' callers

    return capabilities.is_on(db, code)


@dataclass(frozen=True)
class AiStep:
    name: str
    prompt_version: str
    system: str
    user_template: str  # uses {source} and any other input names
    schema: type[BaseModel]
    evidence_paths: list[str] = field(default_factory=list)  # fields whose values must appear in the source text
    min_confidence: float | None = None  # needs a `confidence` field (0 to 1) on the schema


@dataclass
class StepResult:
    status: str  # 'ok' or 'needs_person'
    value: BaseModel | None
    used_stronger_model: bool
    failures: list[str]
    completion: Completion | None = None
    task_id: int | None = None


def _problems(step: AiStep, value: BaseModel, source: str) -> list[str]:
    problems = [f"{f.path}={f.value!r}: {f.reason}" for f in _evidence(step, value, source)]
    if step.min_confidence is not None:
        confidence = getattr(value, "confidence", None)
        if confidence is None:
            problems.append("no confidence value in the answer")
        elif confidence < step.min_confidence:
            problems.append(f"confidence {confidence} is below {step.min_confidence}")
    return problems


def _evidence(step: AiStep, value: BaseModel, source: str) -> list[EvidenceFailure]:
    return check_evidence(value, source, step.evidence_paths)


def run_step(
    db: Session,
    tenant: str,
    step: AiStep,
    source: str,
    *,
    gateway: Gateway,
    review_assignee: int,
    entity: str,
    entity_id: str,
    inputs: dict[str, Any] | None = None,
    review_sla_hours: Decimal | int = 8,
    case_id: int | None = None,
    capability: str | None = None,
) -> StepResult:
    """Run the step (see the module). Its capability (by its name when not given) must be switched on; when it is off,
    no model is asked and a person gets the reading to do."""
    capability = capability or CAPABILITY_OF.get(step.name)
    if capability is not None and not _capability_on(db, capability):
        return _send_to_person(
            db, tenant, step, None, [f"the {capability} capability is switched off"], entity, entity_id,
            review_assignee, review_sla_hours, case_id,
        )  # fmt: skip
    with trace.span(capability=capability, entity=entity, entity_id=entity_id):
        return _run(db, tenant, step, source, gateway, review_assignee, entity, entity_id, inputs, review_sla_hours,
                    case_id)  # fmt: skip


def _run(
    db: Session,
    tenant: str,
    step: AiStep,
    source: str,
    gateway: Gateway,
    review_assignee: int,
    entity: str,
    entity_id: str,
    inputs: dict[str, Any] | None,
    review_sla_hours: Decimal | int,
    case_id: int | None,
) -> StepResult:
    messages = [
        {"role": "system", "content": step.system},
        {"role": "user", "content": step.user_template.format(source=source, **(inputs or {}))},
    ]
    failures: list[str] = []
    last: tuple[BaseModel, Completion] | None = None
    for attempt, route_name in enumerate((step.name, f"{step.name}.strong")):
        try:
            got = gateway.extract(route_name, messages, step.schema, db=db, prompt_version=step.prompt_version)
        except (InvalidOutput, ModelError) as exc:
            failures = [f"{route_name}: {type(exc).__name__}: {str(exc)[:200]}"]
            continue
        last = (got.value, got.completion)
        failures = _problems(step, got.value, source)
        capability = trace.current().capability
        if capability is not None and not _capability_on(db, capability):  # switched off while the model answered
            failures = [f"the {capability} capability was switched off before the reading was used"]
            break
        if not failures:
            audit.record(
                db,
                Actor("service", step.name),
                "ai_step.accepted",
                entity,
                entity_id,
                after=got.value.model_dump(mode="json"),
                model=f"{got.completion.provider}/{got.completion.model}",
                prompt_version=step.prompt_version,
            )
            db.commit()
            return StepResult("ok", got.value, attempt == 1, [], got.completion)
    return _send_to_person(
        db, tenant, step, last, failures, entity, entity_id, review_assignee, review_sla_hours, case_id
    )


def _send_to_person(
    db: Session,
    tenant: str,
    step: AiStep,
    last: tuple[BaseModel, Completion] | None,
    failures: list[str],
    entity: str,
    entity_id: str,
    assignee: int,
    sla_hours: Decimal | int,
    case_id: int | None,
) -> StepResult:
    value = last[0] if last else None
    payload = {
        "step": step.name,
        "prompt_version": step.prompt_version,
        "model_answer": value.model_dump(mode="json") if value else None,
        "why_rejected": failures,
    }
    audit.record(
        db,
        Actor("service", step.name),
        "ai_step.rejected",
        entity,
        entity_id,
        after=payload,
        reason="; ".join(failures)[:500],
        model=f"{last[1].provider}/{last[1].model}" if last else None,
        prompt_version=step.prompt_version,
    )
    db.commit()
    task_id = tasks.create_task(
        db,
        tenant,
        kind="ai_review",
        title=f"Check {entity} {entity_id}: the automatic reading was not reliable",
        assignee=assignee,
        sla_hours=sla_hours,
        case_id=case_id,
        payload=json.loads(json.dumps(payload)),
    )
    return StepResult("needs_person", value, True, failures, last[1] if last else None, task_id)
