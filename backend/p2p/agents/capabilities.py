"""One switch per AI capability (task 11a.5, D49, D54).

The seven capabilities of D49 each own decision types (the automation settings) and worker jobs. A capability that is
switched off makes no model call: its gate refuses, its jobs hand their work to a person, its AI steps give the reading
to a person, and a run waiting for a decision does not continue. The kill switch stays separate: it stops automatic
actions; this switch stops a capability's model work too.

Switching on waits for a second administrator and records the D54 checks given (attested: the test file, the eval with
its threshold and result, tracing, the switch-off tried). Switching off is immediate and hands the open work over:
queued jobs, open workflows, waiting runs and recommendations without a proposal each become an `ai_handover` task for
the capability's owner (planning's explanations are dropped with no task: they only inform, block 2; the sourcing
records that only inform and the jobs that only reword them are left as they are or dropped, block 6).
"""

from collections.abc import Callable
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any

from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.orm import Session

from p2p.agents import autonomy
from p2p.formulas.words import count
from p2p.platform import audit, setting_changes, tasks, tenants, users, workflows
from p2p.platform.setting_changes import Change
from p2p.platform.sod import Actor

TASK_KIND = "ai_handover"
SLA_HOURS = 8
CHECKS = ("permission_tests", "eval", "eval_threshold", "eval_result")  # texts that must be given to switch on
FLAGS = ("tracing", "switch_off_tested")  # must be true


def _drop_planning(db: Session, tenant: str) -> int:
    from p2p.agents import planning  # noqa: PLC0415 - planning imports the runtime, which reads this module

    return planning.drop_queued(tenant)


@dataclass(frozen=True)
class Capability:
    code: str
    label: str
    decisions: tuple[str, ...]  # automation decision types it owns
    job_kinds: tuple[str, ...]  # worker jobs that do its work
    owner_roles: tuple[str, ...]  # who gets its handed-over work (first holder, tried in order)
    drain: Callable[[Session, str], int] | None = None  # its own clean-up at switch-off, instead of tasks for jobs
    hands_over: bool = True  # False: its open work only informs, and is dropped with no task
    owner_titles: tuple[str, ...] = ()  # designations tried before the roles (spec 2.6: the purchase manager)
    links: dict[str, str] = field(default_factory=dict)
    dropped_jobs: tuple[str, ...] = ()  # jobs that only reword: dropped at switch-off with no task (block 6)
    informs: tuple[str, ...] = ()  # recommendation kinds that only inform: kept as they are at switch-off (block 6)


CAPABILITIES: dict[str, Capability] = {
    c.code: c
    for c in (
        Capability(
            "intake", "Reads requests written in plain words", (), ("intake_email",), ("Buyer",),
            owner_titles=("Purchase Manager",),
        ),
        Capability(
            "planning", "Explains shortages", ("planning.explain",), ("planning_explain",), ("Store", "Buyer"),
            drain=_drop_planning, hands_over=False, owner_titles=("Storekeeper",),
        ),
        Capability(
            "sourcing", "Reads quotes, prepares RFQs", ("rfq.send", "rfq.award", "quote.read"),
            ("read_supplier_reply", "sourcing_note"), ("Buyer",), dropped_jobs=("sourcing_note",),
            informs=("ranking", "negotiation_brief", "review_note"),
        ),
        Capability(
            "execution", "Supplier mail and order follow-up", ("po.send", "po.issue", "supplier.change", "supplier.remind"),
            ("supplier_message",), ("Buyer",),
        ),
        Capability("invoice_ap", "Reads invoices, investigates blocks", ("invoice.read",), ("read_invoice_email",), ("AP", "Finance")),
        Capability("monitoring", "Cases and escalation", (), (), ("Admin",)),
        Capability("assistant", "Answers questions", (), (), ("Admin",)),
    )
}  # fmt: skip
OF_DECISION = {d: c.code for c in CAPABILITIES.values() for d in c.decisions}
OF_JOB = {k: c.code for c in CAPABILITIES.values() for k in c.job_kinds}


def of_decision(decision_type: str) -> Capability | None:
    code = OF_DECISION.get(decision_type)
    return CAPABILITIES[code] if code else None


def is_on(db: Session, code: str) -> bool:
    return bool(db.execute(text("select enabled from ai_capability where code = :c"), {"c": code}).scalar())


def _tenant(db: Session) -> str:
    return tenants.slug_of(str(db.get_bind().engine.url.database or "")) or ""


# ---- handing work to a person -----------------------------------------------------------------------------------


def _email_link(db: Session, email_id: int) -> tuple[str, str]:
    row = db.execute(
        text("select linked_type, linked_id, subject from email_message where id = :i"), {"i": email_id}
    ).first()
    if row is None:
        return f"email {email_id}", "#/exceptions"
    page = {"rfq": f"#/rfqs/{row[1]}", "po": f"#/purchase-orders/{row[1]}", "invoice": "#/invoices"}.get(
        str(row[0]), "#/requisitions" if row[0] is None else "#/exceptions"
    )
    return f"the email “{row[2] or 'no subject'}” ({email_id})", page


def _owner(db: Session, tenant: str, cap: Capability) -> int:
    """Who gets the capability's handed-over work: the first active person with one of its designations who has a
    login, else the first holder of one of its roles (else an administrator)."""
    from p2p.platform import notifications  # noqa: PLC0415 - notifications import the task layer

    for title in cap.owner_titles:
        codes = db.execute(
            text("select code from org_user where active and lower(designation) = lower(:t) order by code"),
            {"t": title},
        ).scalars()
        for code in list(codes):
            uid = notifications.login_of(db, tenant, str(code))
            if uid is not None:
                return uid
    return users.first_with_role(db, *cap.owner_roles, "Admin")


def hand_over(db: Session, tenant: str, code: str, *, kind: str, ref: str, what: str, link: str, reason: str) -> int:
    """One task for a person with the work the capability will not do. Commits (the task is opened)."""
    cap = CAPABILITIES[code]
    return tasks.create_task(
        db, tenant, kind=TASK_KIND, title=f"{cap.label} is switched off: {what} needs a person"[:200],
        assignee=_owner(db, tenant, cap), sla_hours=SLA_HOURS,
        payload={"capability": code, "kind": kind, "ref": ref, "link": link, "reason": reason},
    )  # fmt: skip


def hand_over_job(
    db: Session, tenant: str, kind: str, payload: dict[str, Any], reason: str | None = None
) -> int | None:
    """A job of a switched-off capability: its work goes to a person (the mail stays stored). None for planning."""
    code = OF_JOB[kind]
    cap = CAPABILITIES[code]
    if not cap.hands_over or kind in cap.dropped_jobs:
        return None
    if "email_id" in payload:
        what, link = _email_link(db, int(payload["email_id"]))
        ref = str(payload["email_id"])
    else:
        ref = str(next(iter(payload.values()), kind))
        what, link = f"{kind.replace('_', ' ')} {ref}", "#/exceptions"
    return hand_over(
        db, tenant, code, kind=kind, ref=ref, what=what, link=link,
        reason=reason or f"the {code} capability is switched off",
    )  # fmt: skip


def job_allowed(db: Session, tenant: str, kind: str, payload: dict[str, Any]) -> bool:
    """At the start of a capability's job: True to go on; False when it is switched off (the work was handed over)."""
    code = OF_JOB.get(kind)
    if code is None or is_on(db, code):
        return True
    hand_over_job(db, tenant, kind, payload)
    return False


# ---- switching off: immediate, and nothing is left behind -------------------------------------------------------


@dataclass
class Handover:
    tasks: int = 0
    jobs: int = 0
    runs: int = 0
    workflows: int = 0
    recommendations: int = 0


def _record(db: Session, actor: Actor, code: str, old: Any, new: Any, reason: str, action: str) -> None:
    from p2p.platform import setup_api  # noqa: PLC0415 - the setup screens import the agents

    setup_api.record_history(db, actor, "capability", code, old, new, reason)
    audit.record(db, actor, action, "ai_capability", code, before=old, after=new, reason=reason)


def _queued_jobs(tenant: str, kinds: tuple[str, ...], reason: str) -> list[tuple[str, dict[str, Any]]]:
    if not kinds:
        return []
    with tenants.control_engine().begin() as c:
        rows = c.execute(
            text(
                "update job set status = 'failed', last_error = :r, finished_at = now() "
                "where tenant_slug = :t and kind = any(:k) and status = 'queued' returning kind, payload"
            ),
            {"t": tenant, "k": list(kinds), "r": reason},
        ).all()
    return [(str(r[0]), dict(r[1] or {})) for r in rows]


def switch_off(db: Session, tenant: str, actor: Actor, code: str, reason: str) -> Handover:
    """Switch a capability off now and hand its open work to people (see the module). Commits."""
    cap = CAPABILITIES.get(code)
    if cap is None:
        raise HTTPException(404, f"there is no capability '{code}'")
    if not reason.strip():
        raise HTTPException(422, "a reason is needed")
    if not is_on(db, code):
        raise HTTPException(409, f"{cap.label} is already switched off")
    db.execute(
        text("update ai_capability set enabled = false, changed_at = now(), changed_by = :b where code = :c"),
        {"b": f"{actor.kind}:{actor.id}", "c": code},
    )
    _record(db, actor, code, {"enabled": True}, {"enabled": False}, reason, "capability.off")
    db.commit()  # from here every gate and job of the capability refuses
    out = Handover()
    why = f"capability {code} switched off"
    if cap.drain is not None:
        out.jobs += cap.drain(db, tenant)
    for kind, payload in _queued_jobs(tenant, cap.job_kinds, why):
        out.jobs += 1
        if hand_over_job(db, tenant, kind, payload, reason) is not None:
            out.tasks += 1
    for wf in workflows.open_for(db, code):
        task_id = None
        if cap.hands_over:
            task_id = hand_over(
                db, tenant, code, kind=str(wf["kind"]), ref=str(wf["business_key"]),
                what=f"{wf['kind']} for {wf['business_key']}", link="#/exceptions", reason=reason,
            )  # fmt: skip
            out.tasks += 1
        workflows.close(db, int(wf["id"]), "handed_over" if cap.hands_over else "cancelled", why, task_id=task_id)
        out.workflows += 1
    out.runs = len(
        db.execute(
            text(
                "update agent_run set status = 'failed', result = 'stopped: capability switched off', finished_at = now(), "
                "waiting_for = null where capability = :c and status = 'waiting' returning id"
            ),
            {"c": code},
        ).all()
    )  # their proposals stay pending: they already are a person's decision
    db.commit()
    out.recommendations, made = _hand_over_recommendations(db, tenant, cap, reason)
    out.tasks += made
    audit.record(db, actor, "capability.handed_over", "ai_capability", code, after=out.__dict__, reason=reason)
    db.commit()
    return out


def _hand_over_recommendations(db: Session, tenant: str, cap: Capability, reason: str) -> tuple[int, int]:
    from p2p.agents import recommendations  # noqa: PLC0415 - recommendations import the runtime's neighbours

    rows = db.execute(
        text(
            "select id, kind, business_key from recommendation where capability = :c and status = 'proposed' "
            "and proposal_id is null and not (kind = any(:keep)) order by id"
        ),
        {"c": cap.code, "keep": list(cap.informs)},
    ).all()
    made = 0
    for rec_id, kind, key in rows:
        if cap.hands_over:
            task_id = hand_over(
                db, tenant, cap.code, kind=str(kind), ref=str(key), what=f"the recommendation {kind} for {key}",
                link="#/exceptions", reason=reason,
            )  # fmt: skip
            recommendations.hand_over(db, int(rec_id), task_id)
            made += 1
        else:
            recommendations.withdraw(db, int(rec_id), reason)
        db.commit()
    return len(rows), made


# ---- switching on: a second administrator, with the D54 checks -----------------------------------------------------


def _number(value: str) -> Decimal | None:
    try:
        return Decimal(str(value).strip().rstrip("%"))
    except InvalidOperation:
        return None


def checked(checks: dict[str, Any]) -> dict[str, Any]:
    """The D54 checks as given, or a 422 naming what is missing."""
    missing = [k for k in CHECKS if not str(checks.get(k) or "").strip()]
    if missing:
        raise HTTPException(422, f"say {', '.join(missing)} before switching it on")
    false = [k for k in FLAGS if checks.get(k) is not True]
    if false:
        raise HTTPException(422, f"{' and '.join(false)} must be confirmed before switching it on")
    result, threshold = _number(str(checks["eval_result"])), _number(str(checks["eval_threshold"]))
    if result is not None and threshold is not None and result < threshold:
        raise HTTPException(
            422, f"the eval result {checks['eval_result']} is below its threshold {checks['eval_threshold']}"
        )
    return {k: (str(checks[k]).strip() if k in CHECKS else True) for k in (*CHECKS, *FLAGS)}


@setting_changes.handler("capability_on", "admin.settings")
def _capability_on(db: Session, actor: Actor, args: dict[str, Any], reason: str, apply: bool) -> Change:
    code = str(args.get("code"))
    cap = CAPABILITIES.get(code)
    if cap is None:
        raise HTTPException(404, f"there is no capability '{code}'")
    if is_on(db, code):
        raise HTTPException(409, f"{cap.label} is already switched on")
    checks = checked(dict(args.get("checks") or {}))
    change = Change(
        "capability", code, {"enabled": False}, {"enabled": True, "checks": checks}, {"code": code, "enabled": True}
    )
    if not apply:
        return change
    db.execute(
        text(
            "update ai_capability set enabled = true, checks = cast(:k as jsonb), changed_at = now(), changed_by = :b "
            "where code = :c"
        ),
        {"k": _json(checks), "b": f"{actor.kind}:{actor.id}", "c": code},
    )
    _record(db, actor, code, {"enabled": False}, {"enabled": True, "checks": checks}, reason, "capability.on")
    db.commit()
    return change


def _json(value: Any) -> str:
    import json  # noqa: PLC0415

    return json.dumps(value, default=str)


# ---- the list on the Automation page --------------------------------------------------------------------------------


def _queued_counts(tenant: str) -> dict[str, int]:
    with tenants.control_engine().connect() as c:
        rows = c.execute(
            text("select kind, count(*) from job where tenant_slug = :t and status = 'queued' group by kind"),
            {"t": tenant},
        ).all()
    return {str(r[0]): int(r[1]) for r in rows}


def listing(db: Session, tenant: str) -> list[dict[str, Any]]:
    rows = {
        str(r[0]): r
        for r in db.execute(text("select code, enabled, checks, changed_at, changed_by from ai_capability"))
    }
    levels = {str(r[0]): str(r[1]) for r in db.execute(text("select decision_type, level from autonomy_setting"))}
    waiting = {
        str(r[0]): int(r[1])
        for r in db.execute(
            text(
                "select payload->>'key', id from proposal where action = :a and status = 'pending' "
                "and payload->>'area' = 'capability' and (expires_at is null or expires_at > now())"
            ),
            {"a": setting_changes.ACTION},
        )
    }
    open_work = {
        k: {str(r[0]): int(r[1]) for r in db.execute(text(q))}
        for k, q in (
            ("workflows", "select capability, count(*) from workflow where status in ('open', 'waiting') group by 1"),
            ("runs", "select capability, count(*) from agent_run where status = 'waiting' group by 1"),
            ("recommendations", "select capability, count(*) from recommendation where status = 'proposed' group by 1"),
        )
    }
    jobs = _queued_counts(tenant)
    out = []
    for code, cap in CAPABILITIES.items():
        r = rows.get(code)
        out.append(
            {
                "code": code, "label": cap.label, "enabled": bool(r[1]) if r else False,
                "decisions": [{"decision_type": d, "label": autonomy.LABELS[d], "level": levels.get(d, "propose")} for d in cap.decisions],
                "checks": r[2] if r else None, "changed_at": r[3].isoformat() if r and r[3] else None,
                "changed_by": r[4] if r else None, "waiting": waiting.get(code),
                "open_work": {
                    "jobs": sum(jobs.get(k, 0) for k in cap.job_kinds),
                    **{k: v.get(code, 0) for k, v in open_work.items()},
                },
            }
        )  # fmt: skip
    return out


def handed_over_words(h: Handover) -> str:
    return ", ".join(
        count(n, noun) for n, noun in ((h.tasks, "task"), (h.jobs, "job"), (h.runs, "run"), (h.workflows, "workflow"))
    )
