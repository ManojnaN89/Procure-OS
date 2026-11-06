"""The API behind the Procure-OS interface (ui/).

The interface was built for a single-company demo and expects particular response shapes. This module gives it those
shapes from the platform's own data, behind the platform's login, so the screens keep their look and behaviour while the
data comes from the real services. Pages whose services are not built yet return empty lists.
"""

import asyncio
import json
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from decimal import Decimal
from typing import Annotated, Any

import jwt
from fastapi import APIRouter, Depends, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import StreamingResponse
from sqlalchemy import text
from sqlalchemy.orm import Session

from p2p.formulas.calendar import IST
from p2p.formulas.money import format_qty, round_money
from p2p.platform import routing, sod
from p2p.platform.permissions import require
from p2p.platform.tokens import decode_access_token
from p2p.receiving.stock import stock_levels
from p2p.requests import jobs, replenishment

router = APIRouter(prefix="/api", tags=["ui"])
sockets = APIRouter(tags=["ui"])
Db = Annotated[Session, Depends(routing.tenant_session)]
Ctx = Annotated[routing.TenantContext, Depends(routing.tenant_context)]

DEFAULT_SETTINGS: dict[str, str] = {
    "exception_flag_threshold_pct": "30",
    "stale_requisition_days": "3",
    "contract_expiring_days": "30",
    "signal_check_interval_minutes": "2",
    "risk_score_medium_cutoff": "25",
    "risk_score_high_cutoff": "55",
    "currency_preset": "INR",
    "launch_in_focus_mode": "true",
    "auto_replenishment": "false",
}
BOOLEAN_SETTINGS = {"launch_in_focus_mode", "auto_replenishment"}
CURRENCIES = {"INR", "USD", "EUR", "GBP"}


def _iso(value: datetime | None) -> str | None:
    return value.astimezone(UTC).isoformat() if value else None


def _rows(db: Session, sql: str, **params: Any) -> list[dict[str, Any]]:
    return [dict(r._mapping) for r in db.execute(text(sql), params)]


# ---- settings ---------------------------------------------------------------------------------


def all_settings(db: Session) -> dict[str, str]:
    stored = {r[0]: r[1] for r in db.execute(text("select key, value from app_setting"))}
    return {**DEFAULT_SETTINGS, **stored}


@router.get("/settings")
def get_settings(db: Db, ctx: Ctx) -> dict[str, str]:
    return all_settings(db)


@router.post("/settings")
def update_settings(
    payload: dict[str, Any], db: Db, ctx: Annotated[routing.TenantContext, Depends(require("admin.settings"))]
) -> dict[str, Any]:
    unknown = [k for k in payload if k not in DEFAULT_SETTINGS]
    if unknown:
        return {"error": f"Unknown setting(s): {', '.join(unknown)}"}
    for key, value in payload.items():
        value = str(value)
        if key == "currency_preset" and value not in CURRENCIES:
            return {"error": f"currency_preset must be one of {', '.join(sorted(CURRENCIES))}"}
        if key in BOOLEAN_SETTINGS and value not in ("true", "false"):
            return {"error": f"{key} must be true or false"}
        if key != "currency_preset" and key not in BOOLEAN_SETTINGS and not value.replace(".", "", 1).isdigit():
            return {"error": f"{key} must be a number"}
        db.execute(
            text(
                "insert into app_setting (key, value) values (:k, :v) "
                "on conflict (key) do update set value = :v, updated_at = now()"
            ),
            {"k": key, "v": value},
        )
    db.commit()
    if str(payload.get("auto_replenishment")) == "true":
        jobs.schedule(ctx.slug)
    return {"updated": list(payload)}


# ---- notifications (alerts) -------------------------------------------------------------------

_MINE = "(user_id = :u or user_id is null)"


def _notification(r: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": r["id"],
        "type": r["kind"],
        "severity": r["severity"],
        "message": r["title"],
        "detail": r["body"],
        "related_type": r["related_type"],
        "related_id": r["related_id"],
        "read": r["read_at"] is not None,
        "resolved": r["resolved"],
        "created_at": _iso(r["created_at"]),
        "resolved_at": _iso(r["resolved_at"]),
    }


@router.get("/notifications")
def list_notifications(
    db: Db,
    ctx: Ctx,
    unread_only: bool = False,
    unresolved_only: bool = True,
    type: str | None = None,
    severity: str | None = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    where = [_MINE]
    if unread_only:
        where.append("read_at is null")
    if unresolved_only:
        where.append("not resolved")
    if type:
        where.append("kind = :type")
    if severity:
        where.append("severity = :severity")
    rows = _rows(
        db,
        f"select * from notification where {' and '.join(where)} order by id desc limit :n",  # noqa: S608
        u=ctx.user_id,
        type=type,
        severity=severity,
        n=min(limit, 200),
    )
    return [_notification(r) for r in rows]


@router.get("/notifications/unread-count")
def unread_count(db: Db, ctx: Ctx) -> dict[str, int]:
    n = db.execute(
        text(f"select count(*) from notification where {_MINE} and read_at is null and not resolved"),  # noqa: S608
        {"u": ctx.user_id},
    ).scalar_one()
    return {"count": int(n)}


@router.post("/notifications/{notification_id}/read")
def mark_read(notification_id: int, db: Db, ctx: Ctx) -> dict[str, Any]:
    updated = db.execute(
        text(f"update notification set read_at = now() where id = :i and {_MINE} returning id"),  # noqa: S608
        {"i": notification_id, "u": ctx.user_id},
    ).first()
    db.commit()
    return {"id": notification_id, "read": True} if updated else {"error": "Notification not found"}


@router.post("/notifications/{notification_id}/resolve")
def mark_resolved(notification_id: int, db: Db, ctx: Ctx) -> dict[str, Any]:
    updated = db.execute(
        text(
            f"update notification set resolved = true, resolved_at = now() where id = :i and {_MINE} returning id"  # noqa: S608
        ),
        {"i": notification_id, "u": ctx.user_id},
    ).first()
    db.commit()
    return {"id": notification_id, "resolved": True} if updated else {"error": "Notification not found"}


@router.get("/briefing")
def briefing(db: Db, ctx: Ctx) -> dict[str, Any]:
    rows = _rows(
        db,
        f"select * from notification where {_MINE} and not resolved order by id",  # noqa: S608
        u=ctx.user_id,
    )
    rank = {"high": 0, "medium": 1, "low": 2}
    ranked = sorted(rows, key=lambda r: (rank.get(r["severity"], 3), r["id"]))
    counts = {"high": 0, "medium": 0, "low": 0}
    for r in rows:
        counts[r["severity"]] += 1
    return {
        "signals": [_notification(r) for r in ranked[:5]],
        "top_priority_signal": _notification(ranked[0]) if ranked else None,
        "signal_counts": counts,
        "total_unresolved": len(rows),
        "total_spend": 0,
        "cost_savings_period": 0,
    }


# ---- the agent trail --------------------------------------------------------------------------

_TRAIL_KINDS = {"tool": None, "gate": "autonomy_gate", "wait": "awaiting_approval", "resume": "human_decision"}


def _trail_row(r: dict[str, Any]) -> dict[str, Any]:
    detail = r["detail"] or {}
    skill = _TRAIL_KINDS.get(r["kind"]) or r["name"] or r["kind"]
    return {
        "id": r["id"],
        "session_id": r["thread_id"],
        "agent": r["agent"],
        "skill": skill if r["kind"] == "tool" else f"{skill} ({r['name']})" if r["name"] else skill,
        "args": detail.get("args", {k: v for k, v in detail.items() if k not in ("result", "error")}),
        "result": detail.get("result", detail),
        "created_at": _iso(r["at"]),
    }


_TRAIL_SQL = (
    "select s.id, s.at, s.kind, s.name, s.detail, r.agent, r.thread_id from agent_step s join agent_run r on r.id = s.run_id "
    "where s.kind in ('tool', 'gate', 'wait', 'resume')"
)


@router.get("/trail")
def trail(
    db: Db, ctx: Ctx, session_id: str | None = None, agent: str | None = None, limit: int = 100
) -> list[dict[str, Any]]:
    # Everything the company's agents did. The interface sends its chat session, but agents also work on their own.
    extra = " and r.agent = :agent" if agent else ""
    rows = _rows(db, f"{_TRAIL_SQL}{extra} order by s.id desc limit :n", agent=agent, n=min(limit, 500))  # noqa: S608
    return [_trail_row(r) for r in rows]


# ---- the data pages: empty until their services exist ----------------------------------------


def empty_dashboard() -> dict[str, Any]:
    zeros = [0] * 6
    return {
        "open_rfqs": 0,
        "pending_requisitions": 0,
        "approved_requisitions": 0,
        "rejected_requisitions": 0,
        "purchase_orders": 0,
        "vendors": 0,
        "total_po_value": 0,
        "spend_by_category": {},
        "headline": {
            "total_spend": 0,
            "total_spend_trend": zeros,
            "total_spend_change_pct": None,
            "cost_savings_period": 0,
            "cost_savings_trend": zeros,
            "cost_savings_change_pct": None,
            "spend_under_management_pct": 0,
            "maverick_spend_pct": 0,
            "exceptions_count": 0,
            "exceptions_trend": zeros,
            "exceptions_change_pct": None,
            "is_proxy": False,
            "proxy_note": "",
        },
        "inline_stats": {"po_cycle_time_days": None, "on_time_delivery_rate_pct": None, "contract_coverage_pct": 0},
    }


@router.get("/dashboard")
def dashboard(db: Db, ctx: Ctx) -> dict[str, Any]:
    return empty_dashboard()


def _empty_list() -> list[Any]:
    return []


_VENDOR = """
    select s.code as id, coalesce(s.trade_name, s.legal_name) as name, s.legal_name, s.gstin, s.pan, s.status,
           s.msme_category, s.payment_terms_code,
           (select email from supplier_contact c where c.supplier_code = s.code order by is_primary desc, id limit 1) as email,
           (select string_agg(distinct ic.name, ', ') from supplier_category sc join item_category ic on ic.code = sc.category_code
             where sc.supplier_code = s.code) as category,
           (select count(*) from supplier_document d where d.supplier_code = s.code and not coalesce(d.verified, false)) as unverified_docs
    from supplier s
"""


def _vendor(r: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": r["id"],
        "name": r["name"],
        "email": r["email"],
        "category": r["category"],
        "rating": None,
        "total_spend": 0,
        "created_at": None,
        "under_review": r["status"] == "draft",
        "active": r["status"] == "active",
        "verified": r["status"] == "active" and r["unverified_docs"] == 0,
    }


@router.get("/vendors")
def list_vendors(db: Db, ctx: Ctx) -> list[dict[str, Any]]:
    return [_vendor(r) for r in _rows(db, _VENDOR + " order by 2")]


@router.get("/vendors/{vendor_id}")
def get_vendor(vendor_id: str, db: Db, ctx: Ctx) -> dict[str, Any]:
    rows = _rows(db, _VENDOR + " where s.code = :c", c=vendor_id)
    if not rows:
        raise HTTPException(404, "Not found")
    r = rows[0]
    note = f"{r['legal_name']} · GSTIN {r['gstin'] or 'none'} · PAN {r['pan'] or 'none'} · MSME {r['msme_category'] or 'no'} · terms {r['payment_terms_code']}"
    return {
        **_vendor(r),
        "verification_note": note,
        "rfq_count": 0,
        "risk": {
            "risk_score": 0,
            "risk_level": "low",
            "late_delivery_rate_pct": 0,
            "price_volatility_pct": 0,
            "flagged_incidents": 0,
            "basis": "No orders yet, so there is no history to score.",
        },
        "purchase_orders": [],
    }


def _qty(value: Decimal | None) -> str | None:
    """A quantity as text, so it never passes through a float on the way to the screen."""
    return None if value is None else format_qty(value)


@router.get("/inventory")
def inventory(db: Db, ctx: Ctx, location: str | None = None, status: str | None = None) -> dict[str, Any]:
    lines = stock_levels(db, location=location)
    counts: dict[str, int] = {}
    for line in lines:
        counts[line.status] = counts.get(line.status, 0) + 1
    shown = [line for line in lines if not status or line.status == status]
    return {
        "summary": {"total": len(lines), "by_status": counts},
        "locations": _rows(db, "select code, name from location where is_stock_location order by code"),
        "lines": [
            {
                "item_code": line.item_code,
                "item_name": line.item_name,
                "uom": line.uom,
                "category": line.category,
                "location_code": line.location_code,
                "location_name": line.location_name,
                "on_hand": _qty(line.on_hand),
                "on_order": _qty(line.on_order),
                "reorder_point": _qty(line.reorder_point),
                "safety_stock": _qty(line.safety_stock),
                "max_qty": _qty(line.max_qty),
                "policy": line.policy,
                "last_movement": line.last_movement,
                "status": line.status,
            }
            for line in shown
        ],
    }


_REQUISITION_STATUS = {"submitted": "pending"}  # the screen calls a request waiting for approval "pending"


def _requisition(r: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": r["no"],
        "title": r["justification"] or f"Request for {r['ship_to_location']}",
        "amount": str(r["total_value"]),
        "requested_by": r["requester"] or "Automatic replenishment",
        "status": _REQUISITION_STATUS.get(r["status"], r["status"]),
        "priority": r["priority"],
        "created_at": _iso(r["created_at"]),
    }


@router.get("/requisitions")
def list_requisitions(db: Db, ctx: Ctx) -> list[dict[str, Any]]:
    return [_requisition(r) for r in _rows(db, "select * from request order by created_at desc, no desc limit 500")]


@router.get("/requisitions/{no}")
def get_requisition(no: str, db: Db, ctx: Ctx) -> dict[str, Any]:
    rows = _rows(db, "select * from request where no = :n", n=no)
    if not rows:
        raise HTTPException(404, "Not found")
    lines = _rows(
        db,
        "select rl.line_no, rl.item_code, coalesce(i.name, rl.free_text) as name, rl.qty, rl.uom, rl.est_price, rl.need_by, "
        "rl.reason, rl.status, rl.delivery_location from request_line rl left join item i on i.code = rl.item_code "
        "where rl.request_no = :n order by rl.line_no",
        n=no,
    )
    return {
        **_requisition(rows[0]),
        "description": rows[0]["justification"],
        "ship_to": rows[0]["ship_to_location"],
        "need_by": rows[0]["need_by"].isoformat() if rows[0]["need_by"] else None,
        "channel": rows[0]["channel"],
        "lines": [
            {
                "line_no": line["line_no"],
                "item_code": line["item_code"],
                "name": line["name"],
                "qty": _qty(line["qty"]),
                "uom": line["uom"],
                "est_price": str(line["est_price"]) if line["est_price"] is not None else None,
                "value": str(round_money(line["qty"] * line["est_price"])) if line["est_price"] is not None else None,
                "need_by": line["need_by"].isoformat() if line["need_by"] else None,
                "reason": line["reason"],
                "status": line["status"],
            }
            for line in lines
        ],
        "purchase_order": None,
    }


@router.post("/replenishment/run")
def run_replenishment(
    payload: dict[str, Any],
    db: Db,
    ctx: Annotated[routing.TenantContext, Depends(require("replenishment.run"))],
) -> dict[str, Any]:
    """Preview (dry_run) or create the draft requests for stock at its reorder point."""
    result = replenishment.run(
        db,
        as_of=datetime.now(IST).date(),
        actor=sod.user(ctx.user_id),
        dry_run=bool(payload.get("dry_run", True)),
    )
    return replenishment.summary(result)


for _name in ("rfqs", "purchase-orders", "payments", "contracts"):

    def _list(db: Db, ctx: Ctx) -> list[Any]:
        return []

    def _detail(item_id: int, db: Db, ctx: Ctx) -> None:
        raise HTTPException(404, "Not found")

    router.add_api_route(f"/{_name}", _list, methods=["GET"], name=f"list_{_name}")
    router.add_api_route(f"/{_name}/{{item_id}}", _detail, methods=["GET"], name=f"get_{_name}")


@router.get("/search")
def search(db: Db, ctx: Ctx, q: str = Query(..., min_length=1)) -> list[dict[str, Any]]:
    like = f"%{q}%"
    vendors = _rows(db, _VENDOR + " where s.legal_name ilike :q or s.trade_name ilike :q order by 2 limit 5", q=like)
    return [{"type": "vendor", "id": v["id"], "label": v["name"], "meta": v["category"]} for v in vendors]


@router.get("/budgets/variance")
def budget_variance(db: Db, ctx: Ctx) -> dict[str, Any]:
    return {"month": None, "categories": []}


# ---- the assistant: connected in a later step ------------------------------------------------


@router.post("/chat")
async def chat(payload: dict[str, Any], ctx: Ctx) -> StreamingResponse:
    session = str(payload.get("session_id") or uuid.uuid4())

    async def stream() -> AsyncIterator[str]:
        yield f"data: {json.dumps({'type': 'session', 'session_id': session, 'category': 'assistant'})}\n\n"
        reply = (
            "The assistant is not connected yet. You can already look around the pages, approvals and the agent trail."
        )
        yield f"data: {json.dumps({'type': 'token', 'content': reply})}\n\n"
        yield f"data: {json.dumps({'type': 'done'})}\n\n"

    return StreamingResponse(stream(), media_type="text/event-stream")


# ---- live updates (the interface opens two WebSockets) ---------------------------------------


def _slug_from_token(token: str) -> tuple[str, int] | None:
    try:
        claims = decode_access_token(token)
    except jwt.PyJWTError:
        return None
    return str(claims["tenant"]), int(claims["sub"])


async def _push_new_rows(
    ws: WebSocket, slug: str, max_sql: str, query: str, params: dict[str, Any], shape: Any
) -> None:
    """Send rows created after the connection was opened, checking every two seconds, until the client goes away."""
    engine = routing.engine_for(slug)

    def current_max() -> int:
        with Session(engine) as db:
            return int(db.execute(text(max_sql)).scalar_one())

    def fetch(after: int) -> list[dict[str, Any]]:
        with Session(engine) as db:
            return _rows(db, query, after=after, **params)

    async def pusher() -> None:
        last = await run_in_threadpool(current_max)  # what exists now is history; only newer rows are pushed
        while True:
            await asyncio.sleep(2)
            for row in await run_in_threadpool(fetch, last):
                await ws.send_json(shape(row))
                last = max(last, row["id"])

    task = asyncio.create_task(pusher())
    try:
        while True:
            message = await ws.receive()  # nothing is expected from the client; this notices when it disconnects
            if message["type"] == "websocket.disconnect":
                break
    finally:
        task.cancel()


@sockets.websocket("/ws/notifications")
async def ws_notifications(ws: WebSocket, token: str = "") -> None:
    who = _slug_from_token(token)
    if who is None:
        await ws.close(code=4401)
        return
    await ws.accept()
    slug, user = who
    try:
        await _push_new_rows(
            ws,
            slug,
            "select coalesce(max(id), 0) from notification",
            "select * from notification where id > :after and (user_id = :u or user_id is null) order by id limit 50",
            {"u": user},
            _notification,
        )
    except WebSocketDisconnect:
        return


@sockets.websocket("/ws/trail/{session_id}")
async def ws_trail(ws: WebSocket, session_id: str, token: str = "") -> None:
    who = _slug_from_token(token)
    if who is None:
        await ws.close(code=4401)
        return
    await ws.accept()
    slug, _ = who
    try:
        await _push_new_rows(
            ws,
            slug,
            "select coalesce(max(id), 0) from agent_step",
            f"{_TRAIL_SQL} and s.id > :after order by s.id limit 50",  # noqa: S608
            {},
            _trail_row,
        )
    except WebSocketDisconnect:
        return
