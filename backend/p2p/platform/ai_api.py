"""The AI foundation's screens (WP-11a): the capability switches, the month's AI spend, the recommendation records.

Switching a capability on waits for a second administrator (D-22, D54 check 1); switching it off is immediate and hands
its open work to people. Reading needs `admin.settings` or `audit.view`.
"""

from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.orm import Session

from p2p.agents import capabilities
from p2p.ai_steps import costs
from p2p.platform import permissions, routing, setting_changes
from p2p.platform.permissions import require
from p2p.platform.rows import rows as _rows
from p2p.platform.sod import user as user_actor

router = APIRouter(tags=["ai"])
Db = Annotated[Session, Depends(routing.tenant_session)]
Ctx = Annotated[routing.TenantContext, Depends(routing.tenant_context)]
SettingsAdmin = Annotated[routing.TenantContext, Depends(require("admin.settings"))]
READERS = ("admin.settings", "audit.view")


# ---- capabilities --------------------------------------------------------------------------------------------------


@router.get("/api/setup/capabilities")
def list_capabilities(db: Db, ctx: Ctx) -> dict[str, Any]:
    permissions.need_any(db, ctx.user_id, *READERS)
    kill = bool(db.execute(text("select kill_switch from autonomy_global where id = 1")).scalar_one())
    return {"capabilities": capabilities.listing(db, ctx.slug), "kill_switch": kill}


class CapabilityBody(BaseModel):
    on: bool
    reason: str | None = None
    checks: dict[str, Any] = Field(default_factory=dict)


@router.put("/api/setup/capabilities/{code}")
def put_capability(code: str, body: CapabilityBody, db: Db, ctx: SettingsAdmin) -> Any:
    """On: 202, waits for another administrator. Off: 200, at once, with what was handed to people."""
    reason = (body.reason or "").strip()
    if code not in capabilities.CAPABILITIES:
        raise HTTPException(404, f"there is no capability '{code}'")
    if not reason:
        raise HTTPException(422, "a reason is needed")
    if body.on:
        got = setting_changes.submit(
            db, user_actor(ctx.user_id), "capability_on", {"code": code, "checks": body.checks}, reason, tenant=ctx.slug
        )
        return JSONResponse(got, status_code=202)
    done = capabilities.switch_off(db, ctx.slug, user_actor(ctx.user_id), code, reason)
    return {"enabled": False, "handed_over": done.__dict__}


# ---- the month's AI spend ------------------------------------------------------------------------------------------


@router.get("/api/setup/ai-cost")
def ai_cost(db: Db, ctx: Ctx, month: str | None = None) -> dict[str, Any]:
    permissions.need_any(db, ctx.user_id, *READERS)
    try:
        at = datetime.strptime(month, "%Y-%m").replace(tzinfo=UTC) if month else datetime.now(UTC)
    except ValueError:
        raise HTTPException(422, "month is YYYY-MM") from None
    start, end = costs.month_bounds(at)
    where = "where at >= :s and at < :e and outcome in ('ok', 'invalid')"
    span = {"s": start, "e": end}
    by_capability = _rows(
        db,
        f"select coalesce(capability, 'unknown') as capability, count(*) as calls, sum(cost_usd) as cost_usd "  # noqa: S608
        f"from model_call {where} group by 1 order by 1",
        **span,
    )
    by_model = _rows(
        db,
        f"select provider, model, count(*) as calls, sum(cost_usd) as cost_usd from model_call {where} "  # noqa: S608
        "group by 1, 2 order by 1, 2",
        **span,
    )
    used = _rows(
        db,
        "select distinct provider, model from model_call where at >= :s and at < :e and provider is not null",
        **span,
    )  # every model asked or refused this month, whatever came of it
    unpriced = sorted(
        {f"{r['provider']}/{r['model']}" for r in used if costs.price(r["provider"], r["model"], start.date()) is None}
    )
    cap = costs.cap(db)
    return {
        "month": f"{at:%Y-%m}",
        "spent_usd": str(costs.month_spend(db, at)),
        "cap_usd": None if cap is None else str(cap),
        "warn_pct": costs.warn_pct(db),
        "by_capability": by_capability,
        "by_model": by_model,
        "unpriced_models": unpriced,
    }


# ---- recommendation records ------------------------------------------------------------------------------------


@router.get("/api/admin/recommendations", dependencies=[Depends(require("audit.view"))])
def recommendations_list(
    db: Db,
    capability: str | None = None,
    status: str | None = None,
    business_key: str | None = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    return _rows(
        db,
        "select id, capability, kind, business_key, summary, status, created_by, directed_by, decided_by, decided_at, "
        "created_at, proposal_id, run_id from recommendation where (cast(:c as text) is null or capability = :c) "
        "and (cast(:s as text) is null or status = :s) and (cast(:b as text) is null or business_key = :b) "
        "order by id desc limit :n",
        c=capability,
        s=status,
        b=business_key,
        n=min(limit, 200),
    )


@router.get("/api/admin/recommendations/{rec_id}", dependencies=[Depends(require("audit.view"))])
def recommendation_one(rec_id: int, db: Db) -> dict[str, Any]:
    found = _rows(db, "select * from recommendation where id = :i", i=rec_id)
    if not found:
        raise HTTPException(404, "Recommendation not found")
    rec = found[0]
    proposal = (
        _rows(
            db,
            "select id, action, status, decided_by, decided_at, decision_note from proposal where id = :i",
            i=rec["proposal_id"],
        )
        if rec["proposal_id"]
        else []
    )
    run = (
        _rows(db, "select id, agent, capability, status, started_by, user_id, started_at, finished_at from agent_run where id = :i", i=rec["run_id"])
        if rec["run_id"]
        else []
    )  # fmt: skip
    return {**rec, "proposal": proposal[0] if proposal else None, "run": run[0] if run else None}
