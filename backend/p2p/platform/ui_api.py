"""The API behind the Procure-OS interface (ui/).

The interface was built for a single-company demo and expects particular response shapes. This module gives it those
shapes from the platform's own data, behind the platform's login, so the screens keep their look and behaviour while the
data comes from the real services. Pages whose services are not built yet return empty lists.
"""

import asyncio
import json
import uuid
from collections.abc import AsyncIterator, Callable
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Annotated, Any

import jwt
from fastapi import APIRouter, Depends, HTTPException, Query, Response, WebSocket, WebSocketDisconnect
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import StreamingResponse
from sqlalchemy import text
from sqlalchemy.orm import Session

from p2p.agents import assistant, intake
from p2p.ai_steps.gateway import Gateway
from p2p.approvals import budget, deputies
from p2p.approvals import rules as approval_rules
from p2p.approvals import workflow as approvals
from p2p.formulas.calendar import business_today
from p2p.formulas.contracts import ContractUse
from p2p.formulas.money import format_qty, round_money
from p2p.formulas.scoring import risk_level
from p2p.formulas.suppliers import LABELS
from p2p.formulas.words import count
from p2p.masters import supplier_status
from p2p.orders import contracts as contract_usage
from p2p.orders import dispatch, dispatch_notes, quotes
from p2p.orders import service as orders  # noqa: F401 - registers the price-variance proposal executor
from p2p.orders import workflow as po_workflow
from p2p.platform import audit, ratelimit, routing, sod
from p2p.platform.permissions import PermissionDenied, can_anywhere, require
from p2p.platform.rows import rows as _rows
from p2p.platform.tokens import decode_access_token
from p2p.receiving import goods
from p2p.receiving.kinds import service_lines
from p2p.receiving.stock import StockError, post_count, post_issue, stock_levels
from p2p.requests import combining, duplicates, jobs, lifecycle, replenishment
from p2p.sourcing import documents as supplier_documents
from p2p.sourcing import replies, rfq_document
from p2p.sourcing import service as sourcing
from p2p.suppliers import scoring

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
    "launch_in_focus_mode": "false",
    "auto_replenishment": "false",
    "replenishment_department": "",
    "rfq_supplier_count": "3",
    "assistant_enabled": "true",
    "sourcing_mode": "compare",
    "score_weight_price": "40",
    "score_weight_delivery": "40",
    "score_weight_quality": "20",
    "duplicate_window_days": "14",
}
BOOLEAN_SETTINGS = {"launch_in_focus_mode", "auto_replenishment", "assistant_enabled"}
TEXT_SETTINGS = {"replenishment_department", "sourcing_mode"}
CURRENCIES = {"INR", "USD", "EUR", "GBP"}


def _iso(value: datetime | None) -> str | None:
    return value.astimezone(UTC).isoformat() if value else None


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
        return {"error": f"Unknown {'setting' if len(unknown) == 1 else 'settings'}: {', '.join(unknown)}"}
    current = all_settings(db)
    for key, value in payload.items():
        value = str(value)
        if key == "currency_preset" and value not in CURRENCIES:
            return {"error": f"currency_preset must be one of {', '.join(sorted(CURRENCIES))}"}
        if key in BOOLEAN_SETTINGS and value not in ("true", "false"):
            return {"error": f"{key} must be true or false"}
        if key == "sourcing_mode" and value not in ("compare", "best_vendor"):
            return {"error": "sourcing_mode must be compare or best_vendor"}
        if (
            key == "replenishment_department"
            and value
            and not db.execute(text("select 1 from department where code = :c"), {"c": value}).first()
        ):
            return {"error": f"replenishment_department: there is no department with code {value}"}
        if (
            key != "currency_preset"
            and key not in BOOLEAN_SETTINGS | TEXT_SETTINGS
            and not value.replace(".", "", 1).isdigit()
        ):
            return {"error": f"{key} must be a number"}
        db.execute(
            text(
                "insert into app_setting (key, value) values (:k, :v) "
                "on conflict (key) do update set value = :v, updated_at = now()"
            ),
            {"k": key, "v": value},
        )
        if current.get(key) != value:  # every change says who made it and what it was before
            audit.record(
                db,
                sod.user(ctx.user_id),
                "setting.change",
                "app_setting",
                key,
                before={"value": current.get(key)},
                after={"value": value},
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
        "total_spend": _ordered_value(db),
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


_ORDERED = "po.status not in ('draft', 'cancelled', 'rejected')"  # an order that is really on its way to the supplier


def _ordered_value(db: Session) -> str:
    return str(db.execute(text(f"select coalesce(sum(po.total), 0) from po where {_ORDERED}")).scalar_one())  # noqa: S608


@router.get("/dashboard")
def dashboard(db: Db, ctx: Ctx) -> dict[str, Any]:
    """The figures behind the Dashboard, counted from the real tables. What needs the signed-in person is in /api/workspace;
    where every case stands is in /api/control-tower. This is the company-wide picture: no figure here is invented."""
    by_status = {
        r["status"]: int(r["n"]) for r in _rows(db, "select status, count(*) as n from request group by status")
    }

    def count(sql: str) -> int:
        return int(db.execute(text(sql)).scalar_one())

    spend = {
        r["name"]: str(r["amount"])
        for r in _rows(
            db,
            "select coalesce(c.name, 'Other') as name, sum(round(l.qty * l.unit_price, 2)) as amount "  # noqa: S608
            "from po_line l join po on po.no = l.po_no left join item i on i.code = l.item_code "
            f"left join item_category c on c.code = i.category_code where {_ORDERED} and l.status <> 'cancelled' "
            "group by 1 order by 2 desc",
        )
    }
    return {
        "open_rfqs": count("select count(*) from rfq where status in ('draft', 'sent')"),
        "draft_requisitions": by_status.get("draft", 0) + by_status.get("returned", 0),
        "pending_requisitions": by_status.get("submitted", 0),
        "approved_requisitions": by_status.get("approved", 0),
        "rejected_requisitions": by_status.get("rejected", 0),
        "purchase_orders": count(f"select count(*) from po where {_ORDERED}"),  # noqa: S608
        "vendors": count("select count(*) from supplier where status = 'active'"),
        "total_po_value": _ordered_value(db),
        "spend_by_category": spend,
        "blocked_invoices": count("select count(*) from invoice where status = 'blocked'"),
        "invoices_to_pay": count("select count(*) from invoice where status = 'approved'"),
        "paid_total": str(
            db.execute(
                text("select coalesce(sum(total), 0) from invoice where status = 'paid' and type = 'invoice'")
            ).scalar_one()
        ),
    }


_VENDOR = """
    select s.code as id, coalesce(s.trade_name, s.legal_name) as name, s.legal_name, s.gstin, s.pan, s.status,
           s.msme_category, s.payment_terms_code,
           (select email from supplier_contact c where c.supplier_code = s.code order by is_primary desc, id limit 1) as email,
           (select string_agg(distinct ic.name, ', ') from supplier_category sc join item_category ic on ic.code = sc.category_code
             where sc.supplier_code = s.code) as category,
           (select count(*) from supplier_document d where d.supplier_code = s.code and not coalesce(d.verified, false)) as unverified_docs,
           (select coalesce(sum(p.total), 0) from po p where p.supplier_code = s.code
             and p.status not in ('draft', 'cancelled', 'rejected')) as ordered
    from supplier s
"""


def _vendor(r: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": r["id"],
        "name": r["name"],
        "email": r["email"],
        "category": r["category"],
        "rating": None,
        "total_spend": str(r["ordered"]),
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
    today = business_today()
    check = supplier_documents.check_supplier(db, vendor_id, today)
    docs = _rows(
        db,
        "select id, doc_type, number, issued_on, expires_on, verified from supplier_document "
        "where supplier_code = :c order by doc_type, issued_on",
        c=vendor_id,
    )
    score = scoring.score_supplier(db, vendor_id, today)
    score_numbers = score.numbers()
    return {
        **_vendor(r),
        "can_add_documents": can_anywhere(db, ctx.user_id, "supplier.documents"),
        "can_verify_documents": can_anywhere(db, ctx.user_id, "supplier.verify_docs"),
        "documents": {
            "ok": check.ok,
            "problems": check.blocking,
            "warnings": check.warnings,
            "on_file": [
                {
                    "id": d["id"],
                    "type": LABELS.get(d["doc_type"], d["doc_type"]),
                    "number": d["number"],
                    "expires_on": d["expires_on"].isoformat() if d["expires_on"] else None,
                    "verified": bool(d["verified"]),
                    "expired": bool(d["expires_on"] and d["expires_on"] < today),
                }
                for d in docs
            ],
        },
        "verification_note": note,
        "status": r["status"],
        "pending_change": supplier_status.pending_change(db, vendor_id),
        "can_propose": can_anywhere(db, ctx.user_id, "supplier.propose"),
        "rfq_count": 0,
        "score": score_numbers,
        "risk": {
            "risk_score": str(100 - score.total),
            "risk_level": risk_level(
                score.total,
                Decimal(all_settings(db)["risk_score_medium_cutoff"]),
                Decimal(all_settings(db)["risk_score_high_cutoff"]),
            ),
            "late_delivery_rate_pct": str(Decimal(100) - Decimal(score_numbers["on_time_pct"] or 100)),
            "price_volatility_pct": score_numbers["price_gap_pct"],
            "flagged_incidents": score.history.receipt_lines - int(score.history.accepted_shares)
            if score.history.receipt_lines
            else 0,
            "basis": (
                f"Score {score.total} of 100 from {count(score.history.receipts, 'delivery line')}, "
                f"{count(score.history.receipt_lines, 'receipt line')} and {count(score.price_items, 'item price')} compared."
            ),
        },
        "purchase_orders": [],
    }


@router.post("/vendors/{vendor_id}/status")
def propose_vendor_status(
    vendor_id: str,
    payload: dict[str, Any],
    db: Db,
    ctx: Annotated[routing.TenantContext, Depends(require("supplier.propose"))],
) -> dict[str, Any]:
    """Ask for a supplier to be activated, blocked or put under review. A person with the right permission approves."""
    try:
        pid = supplier_status.request_change(
            db,
            vendor_id,
            str(payload.get("to") or ""),
            sod.user(ctx.user_id),
            str(payload.get("reason") or ""),
            today=business_today(),
        )
    except supplier_status.SupplierStatusError as exc:
        db.rollback()
        raise HTTPException(409 if "does not exist" not in str(exc) else 404, str(exc)) from None
    return {"proposal_id": pid, "status": "waiting for approval"}


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
        "can_issue": can_anywhere(db, ctx.user_id, "stock.issue"),
        "can_adjust": can_anywhere(db, ctx.user_id, "stock.adjust"),
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
                "reserved": _qty(line.reserved),
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


def _stock_post(call: Any) -> dict[str, Any]:
    try:
        movement = call()
    except StockError as exc:
        raise HTTPException(409, str(exc)) from None
    return {"movement": movement}


def _quantity(payload: dict[str, Any], key: str) -> Decimal:
    try:
        value = Decimal(str(payload[key]))
    except (KeyError, InvalidOperation):
        raise HTTPException(422, f"{key} must be a number") from None
    if not value.is_finite():
        raise HTTPException(422, f"{key} must be a number")
    return value


@router.post("/inventory/issue")
def issue_stock(
    payload: dict[str, Any], db: Db, ctx: Annotated[routing.TenantContext, Depends(require("stock.issue"))]
) -> dict[str, Any]:
    """Take stock out for use. The store person says what, where and how much; the books cannot go below zero."""
    qty = _quantity(payload, "qty")
    return _stock_post(
        lambda: post_issue(
            db, ctx.user_id, str(payload.get("item_code") or ""), str(payload.get("location") or ""), qty,
            reference=str(payload.get("reference") or "") or None,
        )
    )  # fmt: skip


@router.post("/inventory/count")
def count_stock(
    payload: dict[str, Any], db: Db, ctx: Annotated[routing.TenantContext, Depends(require("stock.adjust"))]
) -> dict[str, Any]:
    """After counting the shelf: correct the books to match it. The difference is worked out by the server."""
    counted = _quantity(payload, "counted_qty")
    return _stock_post(
        lambda: post_count(
            db, ctx.user_id, str(payload.get("item_code") or ""), str(payload.get("location") or ""), counted,
            str(payload.get("reason") or ""),
        )
    )  # fmt: skip


_REQUISITION_STATUS = {"submitted": "pending"}  # the screen calls a request waiting for approval "pending"


def _requisition(r: dict[str, Any]) -> dict[str, Any]:
    n_lines = int(r.get("n_lines") or 0)
    first = r.get("first_item")
    if first and n_lines > 1:
        title = f"{first} and {n_lines - 1} more item{'s' if n_lines > 2 else ''}"
    else:
        title = first or r["justification"] or f"Request for {r['ship_to_location']}"
    return {
        "id": r["no"],
        "title": title,
        "reason": r["justification"],
        "items": n_lines,
        "amount": str(r["total_value"]),
        "requested_by": r.get("requester_name") or "Automatic restock",
        "status": _REQUISITION_STATUS.get(r["status"], r["status"]),
        "priority": r["priority"],
        "created_at": _iso(r["created_at"]),
        "waiting_for": r.get("waiting_for"),
        "mine": bool(r.get("mine")),
        "needs_me": bool(r.get("needs_me")),
    }


_WAITING = (
    "(select a.name from approval_step s join org_user a on a.code = coalesce(s.delegate, s.approver) "
    "where s.request_no = r.no and s.round = r.approval_round and s.status = 'pending' limit 1)"
)


@router.get("/requisitions")
def list_requisitions(db: Db, ctx: Ctx) -> list[dict[str, Any]]:
    me = approvals.org_code_of(db, ctx.user_id)
    sql = (
        f"select r.*, {_WAITING} as waiting_for, u.name as requester_name, "  # noqa: S608
        "(select count(*) from request_line l where l.request_no = r.no) as n_lines, "
        "(select coalesce(i.name, l.free_text) from request_line l left join item i on i.code = l.item_code "
        " where l.request_no = r.no order by l.line_no limit 1) as first_item, "
        "(r.requester is not null and r.requester = :me) as mine, "
        "exists (select 1 from approval_step s where s.request_no = r.no and s.round = r.approval_round "
        " and s.status = 'pending' and coalesce(s.delegate, s.approver) = :me) as needs_me "
        "from request r left join org_user u on u.code = r.requester order by r.created_at desc, r.no desc limit 500"
    )
    return [_requisition(r) for r in _rows(db, sql, me=me)]


@router.get("/requisition-form")
def requisition_form(
    db: Db, ctx: Annotated[routing.TenantContext, Depends(require("request.create"))]
) -> dict[str, Any]:
    """What the New request screen needs: the catalogue, the places goods can be delivered to, and sensible defaults."""
    me = approvals.org_code_of(db, ctx.user_id)
    own = db.execute(text("select location_code from org_user where code = :c"), {"c": me}).scalar() if me else None
    places = _rows(db, "select code, name from location where is_stock_location order by code")
    return {
        "items": _rows(
            db,
            "select code, name, coalesce(purchase_uom, base_uom) as uom, category_code as category from item "
            "where coalesce(active, true) order by name",
        ),
        "locations": places,
        "location": own if any(p["code"] == own for p in places) else (places[0]["code"] if len(places) == 1 else None),
        "need_by": (business_today() + timedelta(days=7)).isoformat(),
    }


@router.post("/requisitions")
def create_requisition(
    payload: dict[str, Any], db: Db, ctx: Annotated[routing.TenantContext, Depends(require("request.create"))]
) -> dict[str, Any]:
    """A request typed on the screen. It is saved as a draft; the person submits it when it is right."""
    raw = payload.get("lines")
    if not isinstance(raw, list):
        raise HTTPException(422, "lines must be a list of items and quantities")
    lines: list[tuple[str, Decimal]] = []
    try:
        for entry in raw:
            lines.append((str(entry["item_code"]), Decimal(str(entry["qty"]))))
        need_by = date.fromisoformat(str(payload.get("need_by")))
    except (KeyError, TypeError, ValueError, InvalidOperation):
        raise HTTPException(
            422, "each line needs an item_code and a number qty, and need_by must be a date like 2026-11-20"
        ) from None
    try:
        result = intake.create_from_form(
            db, ctx.user_id, lines, need_by, payload.get("location") or None, payload.get("reason") or None
        )
    except intake.FormError as exc:
        db.rollback()
        raise HTTPException(422, str(exc)) from None
    return {"id": result.request_nos[0], "request_nos": result.request_nos, "warnings": result.warnings}


@router.get("/requisitions/{no}")
def get_requisition(no: str, db: Db, ctx: Ctx) -> dict[str, Any]:
    rows = _rows(
        db,
        f"select r.*, u.name as requester_name, {_WAITING} as waiting_for, "  # noqa: S608
        "(select count(*) from request_line l where l.request_no = r.no) as n_lines, "
        "(select coalesce(i.name, l.free_text) from request_line l left join item i on i.code = l.item_code "
        " where l.request_no = r.no order by l.line_no limit 1) as first_item "
        "from request r left join org_user u on u.code = r.requester where r.no = :n",
        n=no,
    )
    if not rows:
        raise HTTPException(404, "Not found")
    lines = _rows(
        db,
        "select rl.line_no, rl.item_code, coalesce(i.name, rl.free_text) as name, rl.qty, rl.uom, rl.est_price, rl.need_by, "
        "rl.reason, rl.status, rl.delivery_location, d.method as source_method, d.reason as source_reason, "
        "d.rfq_no as source_rfq, d.contract_code as source_contract, d.route, d.route_rule, d.checks as route_checks, "
        "d.min_quotes, d.window_value, (select status from proposal where id = d.proposal_id) as agreement, "
        "(select p.po_no from po_line p "
        "where p.request_no = rl.request_no and p.request_line_no = rl.line_no and p.status <> 'cancelled' limit 1) as po_no "
        "from request_line rl "
        "left join item i on i.code = rl.item_code left join sourcing_decision d "
        "on d.request_no = rl.request_no and d.line_no = rl.line_no where rl.request_no = :n order by rl.line_no",
        n=no,
    )
    bought_for = combining.sources(db, no)
    return {
        **_requisition(rows[0]),
        "checks": {
            "lines": [
                {
                    "line_no": c.line_no,
                    "item_code": c.item_code,
                    "name": c.name,
                    "uom": c.uom,
                    "on_hand": _qty(c.on_hand),
                    "open_requests": _qty(c.open_requests),
                    "open_po_balance": _qty(c.open_po_balance),
                    "inbound": _qty(c.inbound),
                    "suggested_net_qty": _qty(c.suggested_net_qty),
                }
                for c in duplicates.line_checks(db, no)
            ],
            "duplicates": [
                _duplicate_json(d) for d in duplicates.with_overrides(db, no, duplicates.find_duplicates(db, no))
            ],
        },
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
                "bought_for": bought_for.get(line["line_no"], []),
                "source": {
                    "method": line["source_method"],
                    "reason": line["source_reason"],
                    "rfq_no": line["source_rfq"],
                    "contract": line["source_contract"],
                    "po_no": line["po_no"],
                    "route": line["route"],
                    "rule": line["route_rule"],
                    "checks": line["route_checks"] or [],
                    "min_quotes": line["min_quotes"],
                    "window_value": str(line["window_value"]) if line["window_value"] is not None else None,
                    "agreement": line["agreement"],  # the purchase manager's answer on a direct buy, if one was needed
                }
                if line["source_method"]
                else None,
            }
            for line in lines
        ],
        "purchase_order": next(
            (
                {"id": po["no"], "status": _PO_STATUS.get(po["status"], po["status"])}
                for po in _rows(db, "select no, status from po where request_no = :n order by no", n=no)
            ),
            None,
        ),
        "approval": {
            "rule_code": rows[0]["rule_code"],
            "rule_version": rows[0]["rule_version"],
            "decision": rows[0]["decision_reason"],
            "budget": rows[0]["budget_check"] or [],
            "steps": [
                {
                    "step": s["step"],
                    "role": s["role"],
                    "name": s["approver_name"],
                    "designation": s["designation"],
                    "stand_in": s["delegate_name"],
                    "status": s["status"],
                    "acted_at": _iso(s["acted_at"]),
                    "comment": s["comment"],
                    "why": s["route_reason"],
                    "reminded_at": _iso(s["reminded_at"]),
                }
                for s in approvals.steps_of(db, no)
            ],
            "can_act": approvals.can_act(db, no, ctx.user_id),
            "can_remind": rows[0]["status"] == "submitted"
            and not approvals.can_act(db, no, ctx.user_id)
            and (
                approvals.org_code_of(db, ctx.user_id) == rows[0]["requester"]
                or can_anywhere(db, ctx.user_id, "request.view")
            ),
            **{f"can_{k}": v for k, v in lifecycle.allowed(db, no, ctx.user_id).items()},
            "can_submit": rows[0]["status"] in ("draft", "returned")
            and can_anywhere(db, ctx.user_id, "request.submit"),
            "can_source": rows[0]["status"] == "approved"
            and any(line["status"] == "open" and not line["source_method"] for line in lines)
            and can_anywhere(db, ctx.user_id, "rfq.create"),
        },
    }


def _approval_action(call: Any) -> dict[str, str]:
    """Run an approval action and turn what can go wrong into a clear answer for the screen."""
    try:
        return {"status": call()}
    except approvals.ApprovalError as exc:
        raise HTTPException(409, str(exc)) from None
    except approval_rules.ApprovalRuleError as exc:
        raise HTTPException(422, str(exc)) from None
    except PermissionDenied as exc:
        raise HTTPException(403, str(exc)) from None


def _duplicate_json(d: duplicates.Duplicate) -> dict[str, Any]:
    return {
        "other_request": d.other_request,
        "other_status": d.other_status,
        "similarity": str(d.similarity),
        "kind": d.kind,
        "likely": d.likely,
        "lines": d.lines,
        "overridden": d.overridden,
        "override_reason": d.override_reason,
    }


@router.post("/requisitions/{no}/submit")
def submit_requisition(
    no: str,
    db: Db,
    ctx: Annotated[routing.TenantContext, Depends(require("request.submit"))],
    payload: dict[str, Any] | None = None,
) -> dict[str, str]:
    """Submit for approval. A request that looks like a repeat of another needs a reason to go ahead (override_reason)."""
    try:
        duplicates.gate(db, no, (payload or {}).get("override_reason"), ctx.user_id)
    except duplicates.DuplicateError as exc:
        db.rollback()
        if "does not exist" in str(exc):
            raise HTTPException(404, "Not found") from None
        raise HTTPException(409, str(exc)) from None
    return _approval_action(lambda: approvals.submit(db, ctx.slug, no, sod.user(ctx.user_id)).status)


def _lifecycle_action(call: Any) -> dict[str, Any]:
    try:
        result: dict[str, Any] = call()
        return result
    except lifecycle.LifecycleError as exc:
        raise HTTPException(404 if "does not exist" in str(exc) else 409, str(exc)) from None
    except PermissionDenied as exc:
        raise HTTPException(403, str(exc)) from None


@router.post("/requisitions/{no}/withdraw")
def withdraw_requisition(no: str, db: Db, ctx: Ctx) -> dict[str, Any]:
    """The requester takes a request back from approval; it is a draft again."""
    return _lifecycle_action(lambda: {"status": lifecycle.withdraw(db, ctx.slug, no, ctx.user_id)})


@router.post("/requisitions/{no}/cancel")
def cancel_requisition(no: str, payload: dict[str, Any], db: Db, ctx: Ctx) -> dict[str, Any]:
    """Stop a request for good (a reason is needed)."""
    return _lifecycle_action(
        lambda: {"status": lifecycle.cancel(db, ctx.slug, no, ctx.user_id, str(payload.get("reason") or ""))}
    )


@router.post("/requisitions/{no}/copy")
def copy_requisition(no: str, db: Db, ctx: Ctx, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    """A new draft with the same lines, in the caller's name."""
    raw = (payload or {}).get("need_by")
    try:
        need_by = date.fromisoformat(str(raw)) if raw else None
    except ValueError:
        raise HTTPException(422, "need_by must be a date like 2026-11-20") from None
    return _lifecycle_action(lambda: {"id": lifecycle.copy(db, ctx.user_id, no, need_by)})


@router.post("/requisitions/{no}/duplicate-override")
def override_duplicate(
    no: str,
    payload: dict[str, Any],
    db: Db,
    ctx: Annotated[routing.TenantContext, Depends(require("request.submit"))],
) -> dict[str, Any]:
    try:
        done = duplicates.override(db, no, str(payload.get("reason") or ""), ctx.user_id)
    except duplicates.DuplicateError as exc:
        db.rollback()
        raise HTTPException(404 if "does not exist" in str(exc) else 409, str(exc)) from None
    return {"overridden": [d.other_request for d in done]}


@router.post("/requisitions/{no}/approve")
def approve_requisition(
    no: str, payload: dict[str, Any], db: Db, ctx: Annotated[routing.TenantContext, Depends(require("request.approve"))]
) -> dict[str, str]:
    quantities: dict[int, Decimal] = {}
    for line, qty in (payload.get("quantities") or {}).items():
        try:
            quantities[int(line)] = Decimal(str(qty))
        except (ValueError, InvalidOperation):
            raise HTTPException(422, f"line {line}: the quantity must be a number") from None
    return _approval_action(
        lambda: approvals.approve(db, ctx.slug, no, ctx.user_id, payload.get("comment"), quantities=quantities or None)
    )


@router.post("/requisitions/bulk-approve")
def bulk_approve_requisitions(
    payload: dict[str, Any], db: Db, ctx: Annotated[routing.TenantContext, Depends(require("request.approve"))]
) -> dict[str, Any]:
    """Approve several requests from the inbox. Each is decided on its own; the ones that are not this person's turn are
    reported with the reason and left alone."""
    nos = [str(n) for n in (payload.get("requests") or [])]
    if not nos or len(nos) > 50:
        raise HTTPException(422, "choose between 1 and 50 requests")
    results = approvals.bulk_approve(db, ctx.slug, nos, ctx.user_id, payload.get("comment"))
    return {
        "approved": [n for n, r in results.items() if "status" in r],
        "refused": {n: r["error"] for n, r in results.items() if "error" in r},
    }


@router.post("/requisitions/{no}/remind-approver")
def remind_approver(no: str, db: Db, ctx: Ctx) -> dict[str, str]:
    """The requester nudges the approver whose turn it is."""
    try:
        return {"reminded": approvals.remind_approver(db, ctx.slug, no, ctx.user_id)}
    except approvals.ApprovalError as exc:
        raise HTTPException(409, str(exc)) from None
    except PermissionDenied as exc:
        raise HTTPException(403, str(exc)) from None


Approver = Annotated[routing.TenantContext, Depends(require("request.approve"))]


@router.get("/me/deputies")
def my_deputies(db: Db, ctx: Approver) -> dict[str, Any]:
    """The deputies this approver has named for when they are away."""
    try:
        return {"deputies": deputies.mine(db, ctx.user_id), "people": deputies.candidates(db, ctx.user_id)}
    except deputies.DeputyError as exc:
        raise HTTPException(409, str(exc)) from None


@router.post("/me/deputies", status_code=201)
def name_deputy(payload: dict[str, Any], db: Db, ctx: Approver) -> dict[str, int]:
    """Name a deputy for a date range: approvals that open meanwhile go to them."""
    try:
        start, end = date.fromisoformat(str(payload.get("start"))), date.fromisoformat(str(payload.get("end")))
    except ValueError:
        raise HTTPException(422, "start and end must be dates (YYYY-MM-DD)") from None
    try:
        new_id = deputies.name_deputy(
            db, ctx.slug, ctx.user_id, str(payload.get("deputy") or ""), start, end, payload.get("reason")
        )
    except deputies.DeputyError as exc:
        raise HTTPException(409, str(exc)) from None
    return {"id": new_id}


@router.delete("/me/deputies/{delegation_id}")
def remove_deputy(delegation_id: int, db: Db, ctx: Approver) -> dict[str, str]:
    try:
        deputies.remove(db, ctx.user_id, delegation_id)
    except deputies.DeputyError as exc:
        raise HTTPException(404, str(exc)) from None
    return {"status": "removed"}


@router.post("/requisitions/{no}/reject")
def reject_requisition(
    no: str, payload: dict[str, Any], db: Db, ctx: Annotated[routing.TenantContext, Depends(require("request.approve"))]
) -> dict[str, str]:
    return _approval_action(lambda: approvals.reject(db, no, ctx.user_id, payload.get("reason"), tenant=ctx.slug))


@router.post("/requisitions/{no}/return")
def return_requisition(
    no: str, payload: dict[str, Any], db: Db, ctx: Annotated[routing.TenantContext, Depends(require("request.approve"))]
) -> dict[str, str]:
    return _approval_action(
        lambda: approvals.return_for_changes(db, no, ctx.user_id, payload.get("reason"), tenant=ctx.slug)
    )


@router.post("/requisitions/{no}/source")
def source_requisition(
    no: str, db: Db, ctx: Annotated[routing.TenantContext, Depends(require("rfq.create"))]
) -> dict[str, Any]:
    """Decide how an approved request is bought and ask suppliers for quotes (the same thing the worker does on approval)."""
    try:
        result = sourcing.source_request(db, ctx.slug, no, sod.user(ctx.user_id), as_of=business_today())
    except sourcing.SourcingError as exc:
        raise HTTPException(409, str(exc)) from None
    return sourcing.summary(result)


@router.post("/requisitions/{no}/lines/{line_no}/route")
def change_route(
    no: str,
    line_no: int,
    payload: dict[str, Any],
    db: Db,
    ctx: Annotated[routing.TenantContext, Depends(require("rfq.create"))],
) -> dict[str, Any]:
    """A buyer tightens a line's buying route ('rfq', at once) or asks to loosen it ('direct' with a supplier and a
    price, which the purchase manager agrees to)."""
    try:
        price = Decimal(str(payload["price"])) if payload.get("price") not in (None, "") else None
    except InvalidOperation:
        raise HTTPException(422, "price is not a number") from None
    try:
        return sourcing.change_route(
            db,
            ctx.slug,
            no,
            line_no,
            str(payload.get("route") or ""),
            sod.user(ctx.user_id),
            reason=str(payload.get("reason") or ""),
            as_of=business_today(),
            supplier_code=payload.get("supplier_code"),
            price=price,
        )
    except sourcing.SourcingError as exc:
        db.rollback()
        raise HTTPException(409, str(exc)) from None


@router.get("/rfqs")
def list_rfqs(db: Db, ctx: Ctx) -> list[dict[str, Any]]:
    rows = _rows(
        db,
        "select r.no, r.status, r.due_date, r.request_no, r.created_at, r.proposal_id, c.name as category, "
        "(select count(*) from rfq_line l where l.rfq_no = r.no) as items, "
        "(select count(*) from rfq_supplier s where s.rfq_no = r.no) as vendors, "
        "(select count(distinct q.supplier_code) from quote q where q.rfq_no = r.no) as quoted "
        "from rfq r left join item_category c on c.code = r.category_code order by r.created_at desc, r.no desc limit 500",
    )
    return [
        {
            "id": r["no"],
            "title": f"{r['category'] or 'Quotation request'}: {count(r['items'], 'item')} for {r['request_no']}",
            "status": r["status"],
            "vendor_count": r["vendors"],
            "quote_count": r["quoted"],
            "due_date": r["due_date"].isoformat(),
            "request_no": r["request_no"],
            "created_at": _iso(r["created_at"]),
            "waiting_to_send": r["status"] == "draft" and r["proposal_id"] is not None,
        }
        for r in rows
    ]


@router.get("/rfqs/{no}")
def get_rfq(no: str, db: Db, ctx: Ctx) -> dict[str, Any]:
    rows = _rows(
        db,
        "select r.*, c.name as category from rfq r left join item_category c on c.code = r.category_code where r.no = :n",
        n=no,
    )
    if not rows:
        raise HTTPException(404, "Not found")
    r = rows[0]
    lines = _rows(db, "select * from rfq_line where rfq_no = :n order by line_no", n=no)
    suppliers = _rows(
        db,
        "select s.supplier_code, coalesce(v.trade_name, v.legal_name) as name, s.email, s.invited_at, s.send_error, "
        "s.responded_at, s.declined, s.reminders_sent, s.last_reminded_at from rfq_supplier s join supplier v on v.code = s.supplier_code "
        "where s.rfq_no = :n order by s.supplier_code",
        n=no,
    )

    def state(s: dict[str, Any]) -> str:
        if s["declined"]:
            return "declined"
        if s["responded_at"]:
            return "replied"
        if s["invited_at"]:
            return "invited"
        return "failed" if s["send_error"] else "not yet sent"

    return {
        "id": r["no"],
        "title": f"{r['category'] or 'Quotation request'}: {count(len(lines), 'item')} for {r['request_no']}",
        "status": r["status"],
        "request_no": r["request_no"],
        "due_date": r["due_date"].isoformat(),
        "created_at": _iso(r["created_at"]),
        "sent_at": _iso(r["sent_at"]),
        "note": r["note"],
        "proposal_id": r["proposal_id"],
        "items": [line["description"] for line in lines],
        "lines": [
            {
                "line_no": line["line_no"],
                "item_code": line["item_code"],
                "name": line["description"],
                "qty": _qty(line["qty"]),
                "uom": line["uom"],
                "need_by": line["need_by"].isoformat() if line["need_by"] else None,
            }
            for line in lines
        ],
        "suppliers": [
            {
                "code": s["supplier_code"],
                "name": s["name"],
                "email": s["email"],
                "invited_at": _iso(s["invited_at"]),
                "error": s["send_error"],
                "reminders": s["reminders_sent"],
                "last_reminded_at": _iso(s["last_reminded_at"]),
                "state": state(s),
            }
            for s in suppliers
        ],
        "comparison": _comparison_json(db, no, ctx.user_id),
        "drafts": [_draft_json(d) for d in replies.drafts_of(db, no)],
        "quotes": [],
    }


@router.post("/replenishment/run")
def run_replenishment(
    payload: dict[str, Any],
    db: Db,
    ctx: Annotated[routing.TenantContext, Depends(require("replenishment.run"))],
) -> dict[str, Any]:
    """Preview (dry_run) or create the requests for stock at its reorder point, and send them through approval."""
    result = replenishment.run(
        db,
        as_of=business_today(),
        actor=sod.user(ctx.user_id),
        dry_run=bool(payload.get("dry_run", True)),
        tenant=ctx.slug,
        submit=bool(payload.get("submit", True)),
    )
    return replenishment.summary(result)


_PO_STATUS = {"submitted": "pending"}


def _money(value: Decimal | None) -> str | None:
    return None if value is None else str(value)


def _comparison_json(db: Session, rfq_no: str, user_id: int) -> dict[str, Any]:
    comp = quotes.comparison(db, rfq_no, business_today())
    status = db.execute(text("select status from rfq where no = :n"), {"n": rfq_no}).scalar_one()
    return {
        "min_quotes": comp.min_quotes,
        "quotes_received": comp.quotes_received,
        "invited": comp.invited,
        "waiting_for": comp.waiting_for,
        "rule": comp.rule,
        "basis": comp.basis,
        "can_quote": status == "sent" and can_anywhere(db, user_id, "rfq.create"),
        "can_award": status == "sent" and comp.quotes_received > 0 and can_anywhere(db, user_id, "rfq.create"),
        "suppliers": [
            {
                "code": s["supplier_code"],
                "name": s["supplier_name"],
                "quote_no": s["quote_no"],
                "revision": s["revision"],
                "valid_until": s["valid_until"].isoformat() if s["valid_until"] else None,
                "freight": _money(s["freight"]),
                "packing_forwarding": _money(s["packing_forwarding"]),
                "lines_quoted": s["lines_quoted"],
            }
            for s in comp.suppliers
        ],
        "lines": [
            {
                "rfq_line_no": line.rfq_line_no,
                "name": line.name,
                "qty": _qty(line.qty),
                "uom": line.uom,
                "need_by": line.need_by.isoformat() if line.need_by else None,
                "recommended": line.recommended,
                "why": line.why,
                "awarded": line.awarded,
                "awarded_parts": [{"supplier_code": s, "qty": _qty(q)} for s, q in line.awarded_parts],
                "needs_question": line.needs_question,
                "cheapest": line.cheapest,
                "qualifying": line.qualifying,
                "split": {
                    "worthwhile": line.split.worthwhile,
                    "reason": line.split.reason(),
                    "saving": _money(line.split.saving),
                    "extra_freight": _money(line.split.extra_freight),
                    "parts": [{"supplier_code": p.supplier_code, "qty": _qty(p.qty)} for p in line.split.parts],
                }
                if line.split
                else None,
                "offers": [
                    {
                        "supplier_code": o.supplier_code,
                        "supplier_name": o.supplier_name,
                        "quote_no": o.quote_no,
                        "unit_price": _money(o.unit_price),
                        "discount_pct": _qty(o.discount_pct),
                        "net_price": _money(o.net_price),
                        "value": _money(o.value),
                        "gst_rate": _qty(o.gst_rate),
                        "delivery_days": o.delivery_days,
                        "flags": o.flags,
                        "compliant": o.compliant,
                        "charges": _money(o.charges),
                        "tax": _money(o.tax),
                        "landed": _money(o.landed),
                        "cost": _money(o.cost),
                        "rank": o.rank,
                        "qty_offered": _qty(o.qty_offered) if o.qty_offered is not None else None,
                        "payment_days": o.payment_days,
                        "last_price": _money(o.last_price),
                        "questions": o.questions,
                        "score": _money(o.score),
                    }
                    for o in line.offers
                ],
            }
            for line in comp.lines
        ],
    }


def _decimal(value: Any, field: str, default: Decimal | None = None) -> Decimal:
    if value in (None, ""):
        if default is None:
            raise HTTPException(422, f"{field} is required")
        return default
    try:
        number = Decimal(str(value))
    except InvalidOperation:
        raise HTTPException(422, f"{field} is not a number") from None
    if not number.is_finite():  # NaN and Infinity read as numbers but are not amounts
        raise HTTPException(422, f"{field} is not a number")
    return number


def _draft_json(d: dict[str, Any]) -> dict[str, Any]:
    reading = d["reading"] or {}
    proposal = d["proposal"] or {}
    return {
        "id": d["id"],
        "rfq_no": d["rfq_no"],
        "supplier_code": d["supplier_code"],
        "supplier_name": d["supplier_name"],
        "from": d["from_addr"],
        "subject": d["subject"],
        "intent": d["intent"],
        "status": d["status"],
        "confidence": str(d["confidence"]) if d["confidence"] is not None else None,
        "problems": d["problems"],
        "assumptions": d["assumptions"],
        "decline_reason": reading.get("decline_reason"),
        "question": reading.get("question"),
        "valid_until": proposal.get("valid_until"),
        "freight": proposal.get("freight"),
        "packing_forwarding": proposal.get("packing_forwarding"),
        "lines": [
            {
                "rfq_line_no": ln["rfq_line_no"],
                "name": ln["name"],
                "unit_price": str(ln["unit_price"]),
                "gst_rate": str(ln["gst_rate"]),
                "discount_pct": str(ln["discount_pct"]),
                "delivery_days": ln["delivery_days"],
                "notes": ln["notes"],
            }
            for ln in proposal.get("lines", [])
        ],
        "quote_no": d["quote_no"],
        "created_at": _iso(d["created_at"]),
    }


@router.post("/quote-drafts/{draft_id}/confirm")
def confirm_quote_draft(
    draft_id: int,
    payload: dict[str, Any],
    db: Db,
    ctx: Annotated[routing.TenantContext, Depends(require("rfq.create"))],
) -> dict[str, Any]:
    """A buyer confirms what was read from a supplier's email (optionally correcting line values first)."""
    try:
        edits = {int(k): dict(v) for k, v in dict(payload.get("edits", {})).items()}
        return replies.confirm_draft(db, ctx.slug, draft_id, sod.user(ctx.user_id), as_of=business_today(), edits=edits)
    except (ValueError, TypeError) as exc:
        raise HTTPException(422, f"the correction could not be read: {exc}") from None
    except replies.ReplyError as exc:
        raise HTTPException(409, str(exc)) from None


@router.post("/quote-drafts/{draft_id}/dismiss")
def dismiss_quote_draft(
    draft_id: int,
    payload: dict[str, Any],
    db: Db,
    ctx: Annotated[routing.TenantContext, Depends(require("rfq.create"))],
) -> dict[str, str]:
    try:
        replies.dismiss_draft(db, draft_id, sod.user(ctx.user_id), payload.get("note"))
    except replies.ReplyError as exc:
        raise HTTPException(409, str(exc)) from None
    return {"status": "dismissed"}


@router.post("/rfqs/{no}/quotes")
def enter_rfq_quote(
    no: str, payload: dict[str, Any], db: Db, ctx: Annotated[routing.TenantContext, Depends(require("rfq.create"))]
) -> dict[str, Any]:
    """A buyer enters what a supplier offered (it arrived by email or phone, or on paper)."""
    today = business_today()
    try:
        data = quotes.QuoteInput(
            supplier_code=str(payload.get("supplier_code", "")),
            valid_until=date.fromisoformat(payload["valid_until"]) if payload.get("valid_until") else None,
            payment_terms_code=payload.get("payment_terms_code") or None,
            delivery_terms=payload.get("delivery_terms") or None,
            packing_forwarding=_decimal(payload.get("packing_forwarding"), "packing_forwarding", Decimal(0)),
            freight=_decimal(payload.get("freight"), "freight", Decimal(0)),
            warranty_months=int(payload["warranty_months"]) if payload.get("warranty_months") else None,
            deviations=payload.get("deviations") or None,
            lines=[
                quotes.QuoteLineInput(
                    rfq_line_no=int(line["rfq_line_no"]),
                    unit_price=_decimal(line.get("unit_price"), "unit_price"),
                    gst_rate=_decimal(line.get("gst_rate"), "gst_rate"),
                    discount_pct=_decimal(line.get("discount_pct"), "discount_pct", Decimal(0)),
                    delivery_days=int(line["delivery_days"]) if line.get("delivery_days") not in (None, "") else None,
                    offered_brand=line.get("offered_brand") or None,
                    moq=_decimal(line["moq"], "moq") if line.get("moq") not in (None, "") else None,
                    deviation=line.get("deviation") or None,
                    qty=_decimal(line["qty"], "qty") if line.get("qty") not in (None, "") else None,
                )
                for line in payload.get("lines", [])
            ],
        )
        quote_no = quotes.enter_quote(db, no, data, sod.user(ctx.user_id))
    except (ValueError, KeyError, TypeError) as exc:
        raise HTTPException(422, f"the quote could not be read: {exc}") from None
    except quotes.QuoteError as exc:
        raise HTTPException(409, str(exc)) from None
    auto = quotes.auto_award(
        db, ctx.slug, no, as_of=today
    )  # only when the autonomy gate allows it and nothing is in doubt
    return {"quote_no": quote_no, "auto_awarded": auto is not None}


@router.post("/rfqs/{no}/award")
def award_rfq(
    no: str, payload: dict[str, Any], db: Db, ctx: Annotated[routing.TenantContext, Depends(require("rfq.create"))]
) -> dict[str, Any]:
    """A buyer selects the supplier of each line (choices: line to supplier), or shares a line between suppliers
    (splits: line to a list of [supplier, quantity])."""
    try:
        choices = {int(k): str(v) for k, v in dict(payload.get("choices", {})).items()}
        splits = {
            int(k): [(str(s), _decimal(q, "qty")) for s, q in parts]
            for k, parts in dict(payload.get("splits", {})).items()
        }
        done = quotes.award(
            db,
            no,
            choices,
            sod.user(ctx.user_id),
            reason=payload.get("reason"),
            as_of=business_today(),
            tenant=ctx.slug,
            splits=splits,
        )
    except (ValueError, TypeError) as exc:
        raise HTTPException(422, f"the selection could not be read: {exc}") from None
    except quotes.QuoteError as exc:
        raise HTTPException(409, str(exc)) from None
    return {
        "awarded": {str(k): v for k, v in done.awarded.items()},
        "overridden": done.overridden,
        "complete": done.complete,
    }


@router.get("/rfqs/{no}/suppliers/{supplier_code}/pdf")
def rfq_pdf(no: str, supplier_code: str, db: Db, ctx: Ctx) -> Response:
    """The quotation request as a printable PDF for one supplier that was asked."""
    try:
        pdf = rfq_document.rfq_document(db, no, supplier_code)
    except rfq_document.RfqDocumentError:
        raise HTTPException(404, "Not found") from None
    return Response(
        pdf,
        media_type="application/pdf",
        headers={"Content-Disposition": f'inline; filename="{no}-{supplier_code}.pdf"'},
    )


def _po(r: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": r["no"],
        "supplier": r["supplier_name"],
        "supplier_code": r["supplier_code"],
        "amount": str(r["total"]),
        "status": _PO_STATUS.get(r["status"], r["status"]),
        "request_no": r["request_no"],
        "created_at": _iso(r["created_at"]),
        "held": bool(r["hold_reason"]),
        "po_type": r["po_type"],
        "tier": r["approval_tier"],
        "approver": (
            ("the system (touchless)" if r["touchless"] else r["buyer_name"] or "the buyer")
            if r["approval_tier"] == "buyer"
            else r["approvers"]
        ),
        "ship_to": r["ship_to_location"],
        "ship_to_name": r["ship_to_name"],
        "touchless": bool(r["touchless"]),
        "touchless_note": r["touchless_note"],
    }


_PO_SELECT = (
    "select p.*, coalesce(s.trade_name, s.legal_name) as supplier_name, loc.name as ship_to_name, b.name as buyer_name, "
    "(select a.name from po_approval_step st join org_user a on a.code = coalesce(st.delegate, st.approver) "
    "where st.po_no = p.no and st.round = p.approval_round and st.status = 'pending' limit 1) as waiting_for, "
    "(select string_agg(a.name, ' then ' order by st.step) from po_approval_step st "
    "join org_user a on a.code = coalesce(st.acted_by, st.delegate, st.approver) "
    "where st.po_no = p.no and st.round = p.approval_round and st.status <> 'cancelled') as approvers "
    "from po p join supplier s on s.code = p.supplier_code left join location loc on loc.code = p.ship_to_location "
    "left join org_user b on b.code = p.buyer"
)


@router.get("/purchase-orders")
def list_purchase_orders(db: Db, ctx: Ctx) -> list[dict[str, Any]]:
    rows = _rows(db, _PO_SELECT + " order by p.created_at desc, p.no desc limit 500")
    return [{**_po(r), "waiting_for": r["waiting_for"]} for r in rows]


@router.get("/purchase-orders/{no}")
def get_purchase_order(no: str, db: Db, ctx: Ctx) -> dict[str, Any]:
    rows = _rows(db, _PO_SELECT + " where p.no = :n", n=no)
    if not rows:
        raise HTTPException(404, "Not found")
    r = rows[0]
    lines = _rows(db, "select * from po_line where po_no = :n order by line_no", n=no)
    charges = _rows(db, "select * from po_charge where po_no = :n order by id", n=no)
    promised = [line["promised_date"] for line in lines if line["promised_date"]]
    can_dispatch = can_anywhere(db, ctx.user_id, "po.send")
    return {
        **_po(r),
        "sent_at": _iso(r["sent_at"]),
        "sent_to": r["sent_to"],
        "acknowledged_at": _iso(r["acknowledged_at"]),
        "ack_note": r["ack_note"],
        "promised_date": max(promised).isoformat() if promised else None,
        "ack_overdue": r["status"] == "sent" and dispatch.ack_overdue(db, r["sent_at"], business_today()),
        "can_send": can_dispatch and r["status"] == "approved" and not r["hold_reason"],
        "can_acknowledge": can_dispatch and r["status"] == "sent",
        "can_receive": r["status"] in goods.RECEIVABLE and can_anywhere(db, ctx.user_id, "grn.create"),
        "receipts": [
            {"id": g["no"], "inspection": g["inspection_status"], "at": _iso(g["gate_entry_at"])}
            for g in _rows(
                db,
                "select no, inspection_status, gate_entry_at from goods_receipt where po_no = :n order by no",
                n=no,
            )
        ],
        "waiting_for": r["waiting_for"],
        "buyer_gstin": r["buyer_gstin"],
        "supplier_gstin": r["supplier_gstin"],
        "touchless_checks": r["touchless_checks"] or [],
        "touchless_rule": r["touchless_rule"],
        "contract_code": r["contract_code"],
        "rfq_no": r["rfq_no"],
        "payment_terms": r["payment_terms_code"],
        "delivery_terms": r["delivery_terms"],
        "hold_reason": r["hold_reason"],
        "cancel_reason": r["cancel_reason"],
        "taxable": _money(r["taxable"]),
        "cgst": _money(r["cgst"]),
        "sgst": _money(r["sgst"]),
        "igst": _money(r["igst"]),
        "total": _money(r["total"]),
        "lines": [
            {
                "line_no": line["line_no"],
                "item_code": line["item_code"],
                "name": line["description"],
                "qty": _qty(line["qty"]),
                "uom": line["uom"],
                "unit_price": _money(line["unit_price"]),
                "est_price": _money(line["est_price"]),
                "gst_rate": _qty(line["gst_rate"]),
                "taxable": _money(line["taxable"]),
                "cgst": _money(line["cgst"]),
                "sgst": _money(line["sgst"]),
                "igst": _money(line["igst"]),
                "need_by": line["need_by"].isoformat() if line["need_by"] else None,
                "promised_date": line["promised_date"].isoformat() if line["promised_date"] else None,
                "received_qty": _qty(line["received_qty"]),
                "source": line["source"],
                "source_ref": line["source_ref"],
            }
            for line in lines
        ],
        "charges": [
            {
                "type": c["type"],
                "amount": _money(c["amount"]),
                "gst_rate": _qty(c["gst_rate"]),
                "cgst": _money(c["cgst"]),
                "sgst": _money(c["sgst"]),
                "igst": _money(c["igst"]),
            }
            for c in charges
        ],
        "approval": {
            "rule_code": r["rule_code"],
            "rule_version": r["rule_version"],
            "decision": r["decision_reason"],
            "steps": [
                {
                    "step": s["step"],
                    "role": s["role"],
                    "name": s["approver_name"],
                    "designation": s["designation"],
                    "stand_in": s["delegate_name"],
                    "status": s["status"],
                    "acted_at": _iso(s["acted_at"]),
                    "comment": s["comment"],
                }
                for s in po_workflow.steps_of(db, no)
            ],
            "can_act": po_workflow.can_act(db, no, ctx.user_id),
        },
    }


def _po_action(call: Any) -> dict[str, str]:
    try:
        return {"status": call()}
    except approvals.ApprovalError as exc:
        raise HTTPException(409, str(exc)) from None
    except approval_rules.ApprovalRuleError as exc:
        raise HTTPException(422, str(exc)) from None
    except PermissionDenied as exc:
        raise HTTPException(403, str(exc)) from None


def _receipt_error(call: Callable[[], dict[str, Any]]) -> dict[str, Any]:
    try:
        return call()
    except goods.ReceiptError as exc:
        message = str(exc)
        if "does not exist" in message:
            raise HTTPException(404, message) from None
        raise HTTPException(403 if message.startswith("segregation") else 409, message) from None


@router.get("/receiving/waiting")
def goods_waiting(db: Db, ctx: Ctx) -> list[dict[str, Any]]:
    """Orders sent to suppliers that still have goods to come."""
    orders_ = _rows(
        db,
        "select p.no, p.status, coalesce(s.trade_name, s.legal_name) as supplier, p.ship_to_location, "
        "(select max(promised_date) from po_line l where l.po_no = p.no) as promised "
        "from po p join supplier s on s.code = p.supplier_code where p.status = any(:st) "
        "order by p.sent_at nulls last, p.no",
        st=list(goods.RECEIVABLE),
    )
    out: list[dict[str, Any]] = []
    for o in orders_:
        lines = _rows(
            db,
            "select l.line_no, l.description, l.item_code, l.qty, l.uom, l.received_qty, coalesce(i.serialised, false) "
            "as serialised from po_line l left join item i on i.code = l.item_code "
            "where l.po_no = :n and l.status in ('open', 'part_received') order by l.line_no",
            n=o["no"],
        )
        services = service_lines(db, o["no"])  # the requester confirms these, the store does not receive them
        lines = [ln for ln in lines if ln["line_no"] not in services]
        if not lines:
            continue
        out.append(
            {
                "id": o["no"],
                "status": o["status"],
                "supplier": o["supplier"],
                "ship_to": o["ship_to_location"],
                "promised_date": o["promised"].isoformat() if o["promised"] else None,
                "dispatches": dispatch_notes.notes(db, o["no"]),  # what the supplier says is on the way
                "lines": [
                    {
                        "line_no": ln["line_no"],
                        "name": ln["description"],
                        "item_code": ln["item_code"],
                        "ordered": _qty(ln["qty"]),
                        "received": _qty(ln["received_qty"]),
                        "uom": ln["uom"],
                        "serialised": bool(ln["serialised"]),
                    }
                    for ln in lines
                ],
            }
        )
    return out


@router.get("/receipts")
def list_receipts(db: Db, ctx: Ctx) -> list[dict[str, Any]]:
    rows = _rows(
        db,
        "select g.no, g.po_no, g.location_code, g.gate_entry_at, g.inspection_status, g.received_by, "
        "coalesce(s.trade_name, s.legal_name) as supplier, (select count(*) from gr_line l where l.gr_no = g.no) as lines "
        "from goods_receipt g join po on po.no = g.po_no join supplier s on s.code = po.supplier_code "
        "order by g.created_at desc, g.no desc limit 500",
    )
    return [
        {
            "id": r["no"],
            "po_no": r["po_no"],
            "supplier": r["supplier"],
            "location": r["location_code"],
            "received_at": _iso(r["gate_entry_at"]),
            "inspection": r["inspection_status"],
            "received_by": r["received_by"],
            "lines": r["lines"],
        }
        for r in rows
    ]


@router.get("/receipts/{no}")
def get_receipt(no: str, db: Db, ctx: Ctx) -> dict[str, Any]:
    rows = _rows(db, "select * from goods_receipt where no = :n", n=no)
    if not rows:
        raise HTTPException(404, "Not found")
    g = rows[0]
    lines = _rows(
        db,
        "select l.*, d.description, d.uom, d.item_code from gr_line l "
        "join po_line d on d.po_no = l.po_no and d.line_no = l.po_line_no where l.gr_no = :n order by l.po_line_no",
        n=no,
    )
    return {
        "id": g["no"],
        "po_no": g["po_no"],
        "location": g["location_code"],
        "received_at": _iso(g["gate_entry_at"]),
        "received_by": g["received_by"],
        "challan": g["delivery_challan_no"],
        "invoice_ref": g["supplier_invoice_ref"],
        "eway_bill": g["eway_bill_no"],
        "vehicle": g["vehicle_no"],
        "inspection": g["inspection_status"],
        "accepted_on": g["accepted_on"].isoformat() if g["accepted_on"] else None,
        "remarks": g["remarks"],
        "can_inspect": g["inspection_status"] == "pending" and can_anywhere(db, ctx.user_id, "grn.create"),
        "lines": [
            {
                "line_no": ln["po_line_no"],
                "name": ln["description"],
                "item_code": ln["item_code"],
                "uom": ln["uom"],
                "received": _qty(ln["qty_received"]),
                "accepted": _qty(ln["qty_accepted"]),
                "rejected": _qty(ln["qty_rejected"]),
                "reason": ln["reject_reason"],
            }
            for ln in lines
        ],
    }


@router.post("/purchase-orders/{no}/receive")
def receive_goods(
    no: str, payload: dict[str, Any], db: Db, ctx: Annotated[routing.TenantContext, Depends(require("grn.create"))]
) -> dict[str, Any]:
    raw_lines = payload.get("lines")
    if not isinstance(raw_lines, list) or not raw_lines:
        raise HTTPException(422, "lines must be a list with at least one line")
    try:
        lines = [
            goods.LineIn(
                int(raw["line_no"]),
                _decimal(raw.get("qty_received"), "qty_received"),
                _decimal(raw["qty_rejected"], "qty_rejected") if raw.get("qty_rejected") not in (None, "") else None,
                raw.get("reject_reason"),
                [str(s) for s in raw.get("serials") or []] or None,
            )
            for raw in raw_lines
        ]
    except (KeyError, TypeError, ValueError):
        raise HTTPException(422, "each line needs a line_no and a qty_received") from None
    header = goods.Header(
        payload.get("challan"),
        payload.get("invoice_ref"),
        payload.get("eway_bill"),
        payload.get("vehicle"),
        payload.get("remarks"),
    )

    def post() -> dict[str, Any]:
        done = goods.post_receipt(db, no, lines, header, ctx.user_id, as_of=business_today())
        goods.tell_requesters(db, ctx.slug, done.no)
        return {
            "receipt": done.no,
            "po_status": done.po_status,
            "pending_inspection": done.pending_inspection,
            "accepted": {k: str(v) for k, v in done.accepted.items()},
        }

    return _receipt_error(post)


@router.post("/receipts/{no}/inspect")
def inspect_goods(
    no: str, payload: dict[str, Any], db: Db, ctx: Annotated[routing.TenantContext, Depends(require("grn.create"))]
) -> dict[str, Any]:
    raw_lines = payload.get("lines")
    if not isinstance(raw_lines, list) or not raw_lines:
        raise HTTPException(422, "lines must be a list")
    try:
        decisions = [
            goods.Decision(
                int(raw["line_no"]),
                _decimal(raw.get("qty_accepted"), "qty_accepted"),
                _decimal(raw.get("qty_rejected", 0), "qty_rejected"),
                raw.get("reason"),
            )
            for raw in raw_lines
        ]
    except (KeyError, TypeError, ValueError):
        raise HTTPException(422, "each line needs a line_no, qty_accepted and qty_rejected") from None

    def decide() -> dict[str, Any]:
        done = goods.inspect(db, no, decisions, ctx.user_id, as_of=business_today())
        goods.tell_requesters(db, ctx.slug, done.no, inspected=True)
        return {
            "receipt": done.no,
            "po_status": done.po_status,
            "accepted": {k: str(v) for k, v in done.accepted.items()},
        }

    return _receipt_error(decide)


def _dispatch_action(call: Callable[[], dict[str, Any]]) -> dict[str, Any]:
    try:
        return call()
    except dispatch.DispatchError as exc:
        raise HTTPException(409 if "does not exist" not in str(exc) else 404, str(exc)) from None


@router.post("/purchase-orders/{no}/send")
def send_purchase_order(
    no: str, db: Db, ctx: Annotated[routing.TenantContext, Depends(require("po.send"))]
) -> dict[str, Any]:
    return _dispatch_action(
        lambda: {"sent_to": dispatch.send_po(db, no, sod.user(ctx.user_id), tenant=ctx.slug), "status": "sent"}
    )


@router.post("/purchase-orders/{no}/acknowledge")
def acknowledge_purchase_order(
    no: str, payload: dict[str, Any], db: Db, ctx: Annotated[routing.TenantContext, Depends(require("po.send"))]
) -> dict[str, Any]:
    raw = payload.get("promised_date")
    try:
        promised = date.fromisoformat(str(raw)) if raw else None
    except ValueError:
        raise HTTPException(422, "promised_date must be a date like 2026-11-20") from None
    return _dispatch_action(
        lambda: {
            "status": "acknowledged",
            "warnings": dispatch.acknowledge(
                db, no, sod.user(ctx.user_id), promised=promised, note=payload.get("note")
            ),
        }
    )


@router.post("/purchase-orders/{no}/approve")
def approve_purchase_order(
    no: str, payload: dict[str, Any], db: Db, ctx: Annotated[routing.TenantContext, Depends(require("po.approve"))]
) -> dict[str, str]:
    return _po_action(lambda: po_workflow.approve(db, ctx.slug, no, ctx.user_id, payload.get("comment")))


@router.post("/purchase-orders/{no}/reject")
def reject_purchase_order(
    no: str, payload: dict[str, Any], db: Db, ctx: Annotated[routing.TenantContext, Depends(require("po.approve"))]
) -> dict[str, str]:
    return _po_action(lambda: po_workflow.reject(db, no, ctx.user_id, payload.get("reason")))


_CONTRACT_TYPES = {
    "rate_contract": "Rate contract",
    "service_contract": "Service contract",
    "framework": "Framework agreement",
}


def _contract_json(c: dict[str, Any], use: ContractUse) -> dict[str, Any]:
    return {
        "id": c["code"],
        "title": f"{_CONTRACT_TYPES.get(c['type'], (c['type'] or 'Contract').replace('_', ' ').capitalize())} {c['code']}",
        "vendor": {"id": c["supplier_code"], "name": c["supplier_name"]},
        "status": c["status"],
        "value": _money(use.max_value),
        "used": str(use.used.quantize(Decimal("0.01"))),
        "remaining": _money(use.remaining),
        "pct_used": None if use.pct_used is None else str(use.pct_used),
        "start_date": c["valid_from"].isoformat() if c["valid_from"] else None,
        "end_date": c["valid_to"].isoformat() if c["valid_to"] else None,
    }


_CONTRACT = (
    "select c.*, coalesce(s.trade_name, s.legal_name) as supplier_name from contract c "
    "left join supplier s on s.code = c.supplier_code"
)


@router.get("/contracts")
def list_contracts(db: Db, ctx: Ctx) -> list[dict[str, Any]]:
    rows = _rows(db, _CONTRACT + " order by c.status = 'active' desc, c.valid_to desc nulls last, c.code")
    return [_contract_json(c, contract_usage.usage(db, c["code"])) for c in rows]


@router.get("/contracts/{code}")
def get_contract(code: str, db: Db, ctx: Ctx) -> dict[str, Any]:
    rows = _rows(db, _CONTRACT + " where c.code = :c", c=code)
    if not rows:
        raise HTTPException(404, "Not found")
    c = rows[0]
    terms = [
        f"{label}: {value}"
        for label, value in (
            ("Payment terms", c["payment_terms_code"]),
            ("Delivery", c["delivery_terms"]),
            ("Freight", c["freight_terms"]),
            ("Warranty (months)", c["warranty_months"]),
            ("Service levels", c["sla_text"]),
        )
        if value
    ]
    releases = _rows(
        db,
        "select po.no, po.status, po.created_at, sum(l.taxable) as taxable from po_line l join po on po.no = l.po_no "
        "where l.source = 'contract' and l.source_ref = :c and po.status <> 'cancelled' and l.status <> 'cancelled' "
        "group by po.no, po.status, po.created_at order by po.created_at desc",
        c=code,
    )
    prices = _rows(
        db,
        "select p.id, p.item_code, i.name, p.uom, p.price, p.min_qty, p.valid_from, p.valid_to, p.lead_days from price_record p "
        "left join item i on i.code = p.item_code where p.contract_code = :c order by p.item_code, p.min_qty",
        c=code,
    )
    return {
        **_contract_json(c, contract_usage.usage(db, code)),
        "can_manage": can_anywhere(db, ctx.user_id, "contract.manage") and c["status"] == "draft",
        "can_activate": can_anywhere(db, ctx.user_id, "contract.activate") and c["status"] == "draft",
        "prices": [
            {
                "id": p["id"],
                "item_code": p["item_code"],
                "name": p["name"],
                "uom": p["uom"],
                "price": _money(p["price"]),
                "min_qty": _qty(p["min_qty"]),
                "valid_from": p["valid_from"].isoformat() if p["valid_from"] else None,
                "valid_to": p["valid_to"].isoformat() if p["valid_to"] else None,
                "lead_days": p["lead_days"],
            }
            for p in prices
        ],
        "terms": "\n".join(terms),
        "orders": [
            {
                "id": r["no"],
                "status": _PO_STATUS.get(r["status"], r["status"]),
                "amount": _money(r["taxable"]),
                "created_at": _iso(r["created_at"]),
            }
            for r in releases
        ],
    }


for _name in ("payments",):

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
def budget_variance(db: Db, ctx: Annotated[routing.TenantContext, Depends(require("budget.view"))]) -> dict[str, Any]:
    """Budget against what is held and spent, financial year to date, per cost centre and GL account."""
    return budget.variance(db, business_today())


# ---- the assistant: connected in a later step ------------------------------------------------


_GATEWAY: Gateway | None = None


def assistant_gateway() -> Gateway:
    """One model gateway for the whole process, so the providers' rate limits are shared by every conversation."""
    global _GATEWAY
    if _GATEWAY is None:
        _GATEWAY = Gateway()
    return _GATEWAY


def use_gateway(gateway: Gateway | None) -> None:
    """Replace the gateway (tests give it a scripted model); None goes back to the configured one."""
    global _GATEWAY
    _GATEWAY = gateway


SUGGESTIONS = ["What is waiting for me?", "Which requests have been approved?", "Show the latest purchase orders"]


def _answer(slug: str, user_id: int, message: str) -> str:
    engine = routing.engine_for(slug)
    with Session(engine) as db:
        if all_settings(db).get("assistant_enabled") != "true":
            return "The assistant is switched off for this company."
        if intake.wants(db, user_id, message):  # a request for goods: the intake agent drafts it (it never submits)
            return intake.handle(db, slug, user_id, message, assistant_gateway()).message
        return assistant.ask(db, slug, user_id, message, assistant_gateway()).text


@router.post("/chat", dependencies=[ratelimit.limited("model")])
async def chat(payload: dict[str, Any], ctx: Ctx) -> StreamingResponse:
    session = str(payload.get("session_id") or uuid.uuid4())
    message = str(payload.get("message") or "").strip()

    async def stream() -> AsyncIterator[str]:
        yield f"data: {json.dumps({'type': 'session', 'session_id': session, 'category': 'assistant'})}\n\n"
        reply = (
            await run_in_threadpool(_answer, ctx.slug, ctx.user_id, message)
            if message
            else "Ask me about a request, a quotation, a purchase order or an item in stock."
        )
        for part in reply.splitlines(keepends=True) or [reply]:
            yield f"data: {json.dumps({'type': 'token', 'content': part})}\n\n"
        yield f"data: {json.dumps({'type': 'suggestions', 'suggestions': SUGGESTIONS})}\n\n"
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
