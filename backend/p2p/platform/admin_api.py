"""Admin API: run viewer, model calls, rule evaluations, audit search, proposals, rules and users.

Every route needs a login and a permission. Read-only views need audit.view; rules need admin.rules; users need
admin.users. Proposals are decided by anyone who has the permission the proposal asks for. A new login is never an
Admin, and a role or a limit waits for another administrator (D-22), as on the setup screen.
"""

from datetime import datetime
from decimal import Decimal
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session

from p2p.masters import suppliers
from p2p.platform import permissions, proposals, routing, setup_api, users
from p2p.platform.permissions import require
from p2p.platform.rows import rows as _rows
from p2p.platform.sod import Actor
from p2p.platform.sod import user as user_actor
from p2p.rules import engine as rules

router = APIRouter(prefix="/api/admin", tags=["admin"])
Db = Annotated[Session, Depends(routing.tenant_session)]
Ctx = Annotated[routing.TenantContext, Depends(routing.tenant_context)]
Rows = list[dict[str, Any]]


# ---- run viewer, model calls, rule evaluations, audit ----------------------------------------


@router.get("/agent-runs", dependencies=[Depends(require("audit.view"))])
def agent_runs(db: Db, limit: int = 50) -> Rows:
    return _rows(
        db,
        "select id, thread_id, agent, status, input, result, waiting_for, started_at, finished_at from agent_run "
        "order by id desc limit :n",
        n=min(limit, 200),
    )


@router.get("/agent-runs/{run_id}", dependencies=[Depends(require("audit.view"))])
def agent_run(run_id: int, db: Db) -> dict[str, Any]:
    run = _rows(db, "select * from agent_run where id = :i", i=run_id)
    if not run:
        raise HTTPException(404, "Run not found")
    steps = _rows(db, "select id, at, kind, name, detail from agent_step where run_id = :i order by id", i=run_id)
    calls = _rows(
        db, "select * from audit_event where entity = 'tool' and at >= :s order by id", s=run[0]["started_at"]
    )
    return {"run": run[0], "steps": steps, "tool_audit": calls}


@router.get("/model-calls", dependencies=[Depends(require("audit.view"))])
def model_calls(db: Db, step: str | None = None, limit: int = 100) -> dict[str, Any]:
    where = "where step = :s" if step else ""
    params: dict[str, Any] = {"n": min(limit, 500), **({"s": step} if step else {})}
    calls = _rows(
        db,
        f"select id, at, step, provider, model, prompt_version, tokens_in, tokens_out, cost_usd, ms, attempts, outcome, error "  # noqa: S608
        f"from model_call {where} order by id desc limit :n",
        **params,
    )
    totals = _rows(
        db,
        f"select count(*) as calls, coalesce(sum(tokens_in), 0) as tokens_in, coalesce(sum(tokens_out), 0) as tokens_out, "  # noqa: S608
        f"coalesce(sum(cost_usd), 0) as cost_usd from model_call {where}",
        **({"s": step} if step else {}),
    )[0]
    return {"calls": calls, "totals": totals}


@router.get("/rule-evaluations", dependencies=[Depends(require("audit.view"))])
def rule_evaluations(db: Db, key: str | None = None, limit: int = 100) -> Rows:
    where = "where key = :k" if key else ""
    return _rows(
        db,
        f"select id, at, key, version, input, output, actor, case_ref from rule_evaluation {where} "  # noqa: S608
        "order by id desc limit :n",
        n=min(limit, 500),
        **({"k": key} if key else {}),
    )


@router.get("/rule-evaluations/{evaluation_id}", dependencies=[Depends(require("audit.view"))])
def rule_evaluation(evaluation_id: int, db: Db) -> dict[str, Any]:
    """One evaluation with the trace of which rule row matched, for explaining a decision."""
    rows = _rows(db, "select * from rule_evaluation where id = :i", i=evaluation_id)
    if not rows:
        raise HTTPException(404, "Evaluation not found")
    return rows[0]


@router.get("/audit", dependencies=[Depends(require("audit.view"))])
def audit_search(
    db: Db,
    entity: str | None = None,
    entity_id: str | None = None,
    action: str | None = None,
    actor_id: str | None = None,
    since: datetime | None = None,
    limit: int = 100,
) -> Rows:
    clauses: list[str] = []
    params: dict[str, Any] = {"n": min(limit, 500)}
    for column, value in (("entity", entity), ("entity_id", entity_id), ("action", action), ("actor_id", actor_id)):
        if value:
            clauses.append(f"{column} = :{column}")
            params[column] = value
    if since:
        clauses.append("at >= :since")
        params["since"] = since
    where = ("where " + " and ".join(clauses)) if clauses else ""
    return _rows(
        db,
        "select id, at, actor_type, actor_id, action, entity, entity_id, before, after, reason, rule_version, model, "  # noqa: S608
        f"prompt_version from audit_event {where} order by id desc limit :n",
        **params,
    )


# ---- proposals -------------------------------------------------------------------------------


class Decision(BaseModel):
    note: str | None = None
    edited_payload: dict[str, Any] | None = None
    reason: str | None = None


@router.get("/proposals")
def list_proposals(db: Db, ctx: Ctx, status: str = "pending") -> Rows:
    """Proposals the caller is allowed to decide (those asking for a permission they hold)."""
    rows = _rows(
        db,
        "select id, action, summary, payload, proposed_by_type, proposed_by_id, risk, required_permission, document, value, "
        "status, created_at, expires_at, decision_note from proposal where status = :s order by id desc limit 200",
        s=status,
    )
    mine = [r for r in rows if permissions.can_anywhere(db, ctx.user_id, r["required_permission"])]
    for r in mine:  # a bank account is never listed in full: only its last four digits (F1)
        if isinstance(r.get("payload"), dict) and r["payload"].get("account_no"):
            r["payload"] = {**r["payload"], "account_no": suppliers.mask(str(r["payload"]["account_no"]))}
    return mine


def _known(db: Session, proposal_id: int) -> None:
    """A proposal of this company, else 404 (another company's id is simply not here)."""
    if db.execute(text("select 1 from proposal where id = :i"), {"i": proposal_id}).first() is None:
        raise HTTPException(404, "Not found")


@router.post("/proposals/{proposal_id}/approve")
def approve_proposal(proposal_id: int, body: Decision, db: Db, ctx: Ctx) -> dict[str, Any]:
    _known(db, proposal_id)
    if body.edited_payload and "*" in str(body.edited_payload.get("account_no", "")):
        raise HTTPException(422, "bank details are not changed here: ask for a bank change on the supplier's page")
    try:
        ref = proposals.approve(db, proposal_id, ctx.user_id, edited_payload=body.edited_payload, note=body.note)
    except permissions.PermissionDenied as exc:
        raise HTTPException(403, str(exc)) from None
    except proposals.ProposalError as exc:
        raise HTTPException(409, str(exc)) from None
    ran = db.execute(text("select execution, result from proposal where id = :i"), {"i": ref.id}).one()
    return {"id": ref.id, "status": ref.status, "execution": ran[0], "result": ran[1]}  # did it run, and what it did


@router.post("/proposals/{proposal_id}/reject")
def reject_proposal(proposal_id: int, body: Decision, db: Db, ctx: Ctx) -> dict[str, Any]:
    _known(db, proposal_id)
    try:
        proposals.reject(db, proposal_id, ctx.user_id, body.reason or "")
    except permissions.PermissionDenied as exc:
        raise HTTPException(403, str(exc)) from None
    except proposals.ProposalError as exc:
        raise HTTPException(409, str(exc)) from None
    return {"id": proposal_id, "status": "rejected"}


# ---- decision tables -------------------------------------------------------------------------


class RuleBody(BaseModel):
    jdm: dict[str, Any]
    note: str | None = None


@router.get("/rules", dependencies=[Depends(require("admin.rules"))])
def list_rules(db: Db) -> Rows:
    return _rows(
        db,
        "select key, max(version) filter (where status = 'published') as published_version, "
        "max(version) filter (where status = 'draft') as draft_version from rule_table group by key order by key",
    )


@router.get("/rules/{key}", dependencies=[Depends(require("admin.rules"))])
def get_rule(key: str, db: Db) -> Rows:
    rows = _rows(
        db,
        "select version, status, jdm, note, created_by, created_at, published_at from rule_table where key = :k order by version desc",
        k=key,
    )
    if not rows:
        raise HTTPException(404, "No such rule table")
    return rows


@router.put("/rules/{key}/draft")
def save_rule_draft(
    key: str, body: RuleBody, db: Db, ctx: Annotated[routing.TenantContext, Depends(require("admin.rules"))]
) -> dict[str, int]:
    try:
        return {"version": rules.save_draft(db, key, body.jdm, user_actor(ctx.user_id), body.note)}
    except rules.RuleError as exc:
        raise HTTPException(422, str(exc)) from None


@router.post("/rules/{key}/publish")
def publish_rule(
    key: str, db: Db, ctx: Annotated[routing.TenantContext, Depends(require("admin.rules"))], reason: str | None = None
) -> dict[str, int]:
    try:
        return {"version": rules.publish(db, key, user_actor(ctx.user_id), reason)}
    except rules.RuleError as exc:
        raise HTTPException(409, str(exc)) from None


# ---- users and roles -------------------------------------------------------------------------


class NewUser(BaseModel):
    email: str
    password: str
    display_name: str
    role: str = "Requester"


class NewRole(BaseModel):
    role: str
    location: str | None = None
    department: str | None = None
    reason: str | None = None


class NewLimit(BaseModel):
    document: str
    max_value: Decimal
    category: str | None = None
    location: str | None = None
    reason: str | None = None


REASON = "Users and roles page"  # what a change from that page says when no reason is given


@router.get("/users", dependencies=[Depends(require("admin.users"))])
def list_users(db: Db) -> Rows:
    return _rows(
        db,
        "select p.user_id, p.display_name, p.role as primary_role, "
        "coalesce(json_agg(json_build_object('role', r.role, 'location', r.scope_location, 'department', r.scope_department)) "
        "filter (where r.id is not null), '[]') as roles from user_profile p left join user_role r on r.user_id = p.user_id "
        "group by p.user_id, p.display_name, p.role order by p.user_id",
    )


def _admin_actor(ctx: routing.TenantContext) -> Actor:
    return user_actor(ctx.user_id)


@router.post("/users", status_code=201)
def create_user(
    body: NewUser, ctx: Annotated[routing.TenantContext, Depends(require("admin.users"))]
) -> dict[str, int]:
    if body.role == "Admin":
        raise HTTPException(403, setup_api.ADMIN_NEEDS_A_SECOND)
    try:
        uid = users.create_user(
            ctx.slug, body.email, body.password, body.display_name, body.role, actor=_admin_actor(ctx)
        )
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None
    return {"user_id": uid}


@router.post("/users/{user_id}/roles", status_code=202)
def grant_role(
    user_id: int, body: NewRole, db: Db, ctx: Annotated[routing.TenantContext, Depends(require("admin.users"))]
) -> dict[str, Any]:
    """Saved for another administrator to approve, as on the setup screen."""
    grant = setup_api.RoleGrant(**{**body.model_dump(), "reason": body.reason or REASON})
    return setup_api.give_role(user_id, grant, db, ctx)


@router.post("/users/{user_id}/limits", status_code=202)
def set_limit(
    user_id: int, body: NewLimit, db: Db, ctx: Annotated[routing.TenantContext, Depends(require("admin.users"))]
) -> dict[str, Any]:
    """Saved for another administrator to approve, as on the setup screen."""
    limit = setup_api.LimitBody(**{**body.model_dump(), "reason": body.reason or REASON})
    return setup_api.put_limit(user_id, limit, db, ctx)
