"""
Read-only endpoints for the UI: list + detail views for every domain,
plus a global search and a filterable Agent Trail feed. Nothing here
writes data — every write still goes through the agent/skill layer.
"""
from datetime import datetime, timedelta
from fastapi import APIRouter, Query
from sqlalchemy import or_, func

from db import SessionLocal
from models import (
    Vendor, Auction, Bid, RFQ, Quote, Requisition,
    PurchaseOrder, Payment, AgentTrailLog, Budget, Contract, Notification,
)
from intelligence import compute_vendor_risk
from settings import get_all_settings, set_setting, DEFAULTS
from signals import live_message

router = APIRouter(prefix="/api")


def _iso(dt):
    return dt.isoformat() if isinstance(dt, datetime) else dt


def _vendor_brief(db, vendor_id):
    if vendor_id is None:
        return None
    v = db.get(Vendor, vendor_id)
    return {"id": v.id, "name": v.name} if v else None


# ---------------------------------------------------------------------------
# DASHBOARD
# Restructured per the final UX plan: 3 headline KPIs (Total Spend, Cost
# Savings, Spend Under Management w/ Maverick as its complement) plus a
# handful of inline stats meant to live inside existing panels rather than
# become new cards — SUM/Maverick are explicitly proxies (see note below)
# and the response says so, so the UI never has to hide that itself.
# ---------------------------------------------------------------------------
@router.get("/dashboard")
def dashboard():
    db = SessionLocal()
    try:
        req_counts = dict(
            db.query(Requisition.status, func.count(Requisition.id))
            .group_by(Requisition.status).all()
        )
        po_total = db.query(func.coalesce(func.sum(PurchaseOrder.amount), 0)).scalar()
        spend_by_category = dict(
            db.query(Vendor.category, func.coalesce(func.sum(PurchaseOrder.amount), 0))
            .join(PurchaseOrder, PurchaseOrder.vendor_id == Vendor.id)
            .group_by(Vendor.category).all()
        )

        # --- Spend Under Management / Maverick spend ---
        # Proxy: a PO linked to a requisition went through the formal
        # approval flow ("managed"); a directly-created PO bypassed it
        # ("maverick"). This is an honest approximation, not a measurement
        # against real contracts/approved-vendor lists — flagged as such
        # via is_proxy so the UI can show the explanation inline.
        all_pos = db.query(PurchaseOrder).all()
        managed_spend = sum(po.amount or 0 for po in all_pos if po.requisition_id is not None)
        maverick_spend = sum(po.amount or 0 for po in all_pos if po.requisition_id is None)
        sum_pct = round((managed_spend / po_total) * 100) if po_total else 0
        maverick_pct = 100 - sum_pct if po_total else 0

        # --- PO cycle time: requisition decided_at -> PO created_at ---
        cycle_days = []
        for po in all_pos:
            if po.requisition_id:
                req = db.get(Requisition, po.requisition_id)
                if req and req.decided_at and po.created_at:
                    cycle_days.append((po.created_at - req.decided_at).days)
        avg_cycle_days = round(sum(cycle_days) / len(cycle_days), 1) if cycle_days else None

        # --- On-time delivery rate (company-wide) ---
        delivered = [po for po in all_pos if po.expected_delivery_date and po.actual_delivery_date]
        on_time = [po for po in delivered if po.actual_delivery_date <= po.expected_delivery_date]
        on_time_pct = round((len(on_time) / len(delivered)) * 100) if delivered else None

        # --- Contract coverage: % of spend from vendors with >=1 contract on file ---
        vendor_ids_with_contract = {c.vendor_id for c in db.query(Contract).all()}
        covered_spend = sum(po.amount or 0 for po in all_pos if po.vendor_id in vendor_ids_with_contract)
        contract_coverage_pct = round((covered_spend / po_total) * 100) if po_total else 0

        # --- Cost savings this period: sum of (budget - actual) where actual < budget ---
        latest_month_row = db.query(Budget.month).order_by(Budget.month.desc()).first()
        cost_savings = 0
        if latest_month_row:
            latest_month = latest_month_row[0]
            for b in db.query(Budget).filter(Budget.month == latest_month).all():
                actual = sum(
                    po.amount for po in all_pos
                    if po.created_at and po.created_at.strftime("%Y-%m") == latest_month
                    and db.get(Vendor, po.vendor_id) and db.get(Vendor, po.vendor_id).category == b.category
                )
                if actual < b.budgeted_amount:
                    cost_savings += (b.budgeted_amount - actual)

        # --- Trend sparklines + %-change badges for the headline cards ---
        # Six real calendar months ending at the current month, zero-filled —
        # not just "whatever months happen to have data", so a client with a
        # gap month still gets a correctly-shaped 6-point line, not a
        # misleadingly compressed one.
        month_keys = []
        cursor = datetime.utcnow().replace(day=1)
        for _ in range(6):
            month_keys.append(cursor.strftime("%Y-%m"))
            cursor = (cursor - timedelta(days=1)).replace(day=1)
        month_keys.reverse()

        def pct_change(curr, prev):
            if not prev:
                return None
            return round(((curr - prev) / prev) * 100)

        # Total spend trend
        spend_by_month = {m: 0 for m in month_keys}
        for po in all_pos:
            key = po.created_at.strftime("%Y-%m") if po.created_at else None
            if key in spend_by_month:
                spend_by_month[key] += po.amount or 0
        total_spend_trend = [round(spend_by_month[m]) for m in month_keys]
        total_spend_change_pct = pct_change(total_spend_trend[-1], total_spend_trend[-2])

        # Cost savings trend (same under-budget logic as above, per month)
        savings_by_month = {m: 0 for m in month_keys}
        for m in month_keys:
            for b in db.query(Budget).filter(Budget.month == m).all():
                actual = sum(
                    po.amount for po in all_pos
                    if po.created_at and po.created_at.strftime("%Y-%m") == m
                    and db.get(Vendor, po.vendor_id) and db.get(Vendor, po.vendor_id).category == b.category
                )
                if actual < b.budgeted_amount:
                    savings_by_month[m] += (b.budgeted_amount - actual)
        cost_savings_trend = [round(savings_by_month[m]) for m in month_keys]
        cost_savings_change_pct = pct_change(cost_savings_trend[-1], cost_savings_trend[-2])

        # Exceptions: real flagged-item count, not folded into a percentage —
        # this is the KPI that was missing a headline number entirely before
        flagged_quotes = db.query(Quote).filter(Quote.flagged == True).all()  # noqa: E712
        flagged_pos = db.query(PurchaseOrder).filter(PurchaseOrder.flagged == True).all()  # noqa: E712
        exceptions_count = len(flagged_quotes) + len(flagged_pos)
        exc_by_month = {m: 0 for m in month_keys}
        for item in flagged_quotes + flagged_pos:
            key = item.created_at.strftime("%Y-%m") if item.created_at else None
            if key in exc_by_month:
                exc_by_month[key] += 1
        exceptions_trend = [exc_by_month[m] for m in month_keys]
        exceptions_change_pct = pct_change(exceptions_trend[-1], exceptions_trend[-2])

        return {
            "open_auctions": db.query(Auction).filter(Auction.status == "open").count(),
            "open_rfqs": db.query(RFQ).filter(RFQ.status == "sent").count(),
            "pending_requisitions": req_counts.get("pending", 0),
            "approved_requisitions": req_counts.get("approved", 0),
            "rejected_requisitions": req_counts.get("rejected", 0),
            "purchase_orders": db.query(PurchaseOrder).count(),
            "vendors": db.query(Vendor).count(),
            "total_po_value": po_total,
            "spend_by_category": spend_by_category,

            "headline": {
                "total_spend": po_total,
                "total_spend_trend": total_spend_trend,
                "total_spend_change_pct": total_spend_change_pct,
                "cost_savings_period": round(cost_savings),
                "cost_savings_trend": cost_savings_trend,
                "cost_savings_change_pct": cost_savings_change_pct,
                "spend_under_management_pct": sum_pct,
                "maverick_spend_pct": maverick_pct,
                "exceptions_count": exceptions_count,
                "exceptions_trend": exceptions_trend,
                "exceptions_change_pct": exceptions_change_pct,
                "is_proxy": True,
                "proxy_note": "Approximated from PO-to-requisition linkage, not measured against real contracts or an approved-vendor list.",
            },
            "inline_stats": {
                "po_cycle_time_days": avg_cycle_days,
                "on_time_delivery_rate_pct": on_time_pct,
                "contract_coverage_pct": contract_coverage_pct,
            },
        }
    finally:
        db.close()


# ---------------------------------------------------------------------------
# AUCTIONS
# ---------------------------------------------------------------------------
@router.get("/auctions")
def list_auctions():
    db = SessionLocal()
    try:
        rows = db.query(Auction).order_by(Auction.created_at.desc()).all()
        return [{
            "id": a.id, "title": a.title, "auction_type": a.auction_type,
            "status": a.status, "items": a.items, "vendor_ids": a.vendor_ids,
            "winner_vendor_id": a.winner_vendor_id, "created_at": _iso(a.created_at),
        } for a in rows]
    finally:
        db.close()


@router.get("/auctions/{auction_id}")
def get_auction(auction_id: int):
    db = SessionLocal()
    try:
        a = db.get(Auction, auction_id)
        if not a:
            return {"error": "Auction not found"}
        bids = db.query(Bid).filter(Bid.auction_id == auction_id).order_by(Bid.amount.asc()).all()
        return {
            "id": a.id, "title": a.title, "auction_type": a.auction_type,
            "status": a.status, "items": a.items, "winner_vendor_id": a.winner_vendor_id,
            "created_at": _iso(a.created_at), "closed_at": _iso(a.closed_at),
            "bids": [{
                "id": b.id, "vendor": _vendor_brief(db, b.vendor_id),
                "amount": b.amount, "created_at": _iso(b.created_at),
            } for b in bids],
        }
    finally:
        db.close()


# ---------------------------------------------------------------------------
# RFQs
# ---------------------------------------------------------------------------
@router.get("/rfqs")
def list_rfqs():
    db = SessionLocal()
    try:
        rows = db.query(RFQ).order_by(RFQ.created_at.desc()).all()
        out = []
        for r in rows:
            quote_count = db.query(Quote).filter(Quote.rfq_id == r.id).count()
            out.append({
                "id": r.id, "title": r.title, "status": r.status,
                "items": r.items, "vendor_count": len(r.vendor_ids or []),
                "quote_count": quote_count, "created_at": _iso(r.created_at),
            })
        return out
    finally:
        db.close()


@router.get("/rfqs/{rfq_id}")
def get_rfq(rfq_id: int):
    db = SessionLocal()
    try:
        r = db.get(RFQ, rfq_id)
        if not r:
            return {"error": "RFQ not found"}
        quotes = db.query(Quote).filter(Quote.rfq_id == rfq_id).order_by(Quote.price.asc()).all()
        return {
            "id": r.id, "title": r.title, "status": r.status, "items": r.items,
            "created_at": _iso(r.created_at),
            "quotes": [{
                "id": q.id, "vendor": _vendor_brief(db, q.vendor_id),
                "price": q.price, "delivery_days": q.delivery_days,
                "notes": q.notes, "flagged": q.flagged, "flag_reason": q.flag_reason,
                "created_at": _iso(q.created_at),
            } for q in quotes],
        }
    finally:
        db.close()


# ---------------------------------------------------------------------------
# REQUISITIONS
# ---------------------------------------------------------------------------
@router.get("/requisitions")
def list_requisitions():
    db = SessionLocal()
    try:
        rows = db.query(Requisition).order_by(Requisition.created_at.desc()).all()
        return [{
            "id": r.id, "title": r.title, "amount": r.amount, "status": r.status,
            "requested_by": r.requested_by, "created_at": _iso(r.created_at),
        } for r in rows]
    finally:
        db.close()


@router.get("/requisitions/{req_id}")
def get_requisition(req_id: int):
    db = SessionLocal()
    try:
        r = db.get(Requisition, req_id)
        if not r:
            return {"error": "Requisition not found"}
        po = db.query(PurchaseOrder).filter(PurchaseOrder.requisition_id == req_id).first()
        return {
            "id": r.id, "title": r.title, "description": r.description,
            "amount": r.amount, "status": r.status, "requested_by": r.requested_by,
            "created_at": _iso(r.created_at), "decided_at": _iso(r.decided_at),
            "purchase_order": {"id": po.id, "status": po.status} if po else None,
        }
    finally:
        db.close()


# ---------------------------------------------------------------------------
# VENDORS
# ---------------------------------------------------------------------------
@router.get("/vendors")
def list_vendors():
    db = SessionLocal()
    try:
        rows = db.query(Vendor).order_by(Vendor.name.asc()).all()
        out = []
        for v in rows:
            spend = db.query(func.coalesce(func.sum(PurchaseOrder.amount), 0)) \
                .filter(PurchaseOrder.vendor_id == v.id).scalar()
            out.append({
                "id": v.id, "name": v.name, "email": v.email, "category": v.category,
                "rating": v.rating, "total_spend": spend, "created_at": _iso(v.created_at),
                "under_review": bool(v.under_review),
                "active": bool(v.active) if v.active is not None else True,
                "verified": bool(v.verified),
            })
        return out
    finally:
        db.close()


@router.get("/vendors/{vendor_id}")
def get_vendor(vendor_id: int):
    db = SessionLocal()
    try:
        v = db.get(Vendor, vendor_id)
        if not v:
            return {"error": "Vendor not found"}
        pos = db.query(PurchaseOrder).filter(PurchaseOrder.vendor_id == vendor_id).all()
        # JSON column membership check done in Python — dataset is small (demo scale)
        # and the generic JSON type doesn't support a portable containment query.
        rfq_count = sum(
            1 for r in db.query(RFQ).all() if vendor_id in (r.vendor_ids or [])
        )
        return {
            "id": v.id, "name": v.name, "email": v.email, "category": v.category,
            "rating": v.rating, "created_at": _iso(v.created_at),
            "under_review": bool(v.under_review),
            "active": bool(v.active) if v.active is not None else True,
            "verified": bool(v.verified),
            "verification_note": v.verification_note,
            "rfq_count": rfq_count,
            "risk": compute_vendor_risk(db, vendor_id),
            "purchase_orders": [{
                "id": po.id, "amount": po.amount, "status": po.status,
                "flagged": po.flagged, "flag_reason": po.flag_reason,
                "expected_delivery_date": _iso(po.expected_delivery_date),
                "actual_delivery_date": _iso(po.actual_delivery_date),
                "created_at": _iso(po.created_at),
            } for po in pos],
            "total_spend": sum(po.amount or 0 for po in pos),
        }
    finally:
        db.close()


# ---------------------------------------------------------------------------
# PURCHASE ORDERS
# ---------------------------------------------------------------------------
@router.get("/purchase-orders")
def list_purchase_orders():
    db = SessionLocal()
    try:
        rows = db.query(PurchaseOrder).order_by(PurchaseOrder.created_at.desc()).all()
        return [{
            "id": po.id, "vendor": _vendor_brief(db, po.vendor_id),
            "items": po.items, "amount": po.amount, "status": po.status,
            "flagged": po.flagged, "flag_reason": po.flag_reason,
            "requisition_id": po.requisition_id, "created_at": _iso(po.created_at),
        } for po in rows]
    finally:
        db.close()


@router.get("/purchase-orders/{po_id}")
def get_purchase_order(po_id: int):
    db = SessionLocal()
    try:
        po = db.get(PurchaseOrder, po_id)
        if not po:
            return {"error": "Purchase order not found"}
        payments = db.query(Payment).filter(Payment.purchase_order_id == po_id).all()
        return {
            "id": po.id, "vendor": _vendor_brief(db, po.vendor_id),
            "items": po.items, "amount": po.amount, "status": po.status,
            "flagged": po.flagged, "flag_reason": po.flag_reason,
            "expected_delivery_date": _iso(po.expected_delivery_date),
            "actual_delivery_date": _iso(po.actual_delivery_date),
            "requisition_id": po.requisition_id, "created_at": _iso(po.created_at),
            "payments": [{
                "id": p.id, "amount": p.amount, "status": p.status,
                "created_at": _iso(p.created_at),
            } for p in payments],
        }
    finally:
        db.close()


# ---------------------------------------------------------------------------
# PAYMENTS
# ---------------------------------------------------------------------------
@router.get("/payments")
def list_payments():
    db = SessionLocal()
    try:
        rows = db.query(Payment).order_by(Payment.created_at.desc()).all()
        out = []
        for p in rows:
            po = db.get(PurchaseOrder, p.purchase_order_id)
            out.append({
                "id": p.id, "purchase_order_id": p.purchase_order_id,
                "vendor": _vendor_brief(db, po.vendor_id) if po else None,
                "amount": p.amount, "status": p.status, "created_at": _iso(p.created_at),
            })
        return out
    finally:
        db.close()


# ---------------------------------------------------------------------------
# GLOBAL SEARCH
# ---------------------------------------------------------------------------
@router.get("/search")
def search(q: str = Query(..., min_length=1)):
    db = SessionLocal()
    try:
        like = f"%{q}%"
        results = []

        for v in db.query(Vendor).filter(Vendor.name.ilike(like)).limit(5):
            results.append({"type": "vendor", "id": v.id, "label": v.name, "meta": v.category})

        for r in db.query(RFQ).filter(RFQ.title.ilike(like)).limit(5):
            results.append({"type": "rfq", "id": r.id, "label": r.title, "meta": r.status})

        for a in db.query(Auction).filter(Auction.title.ilike(like)).limit(5):
            results.append({"type": "auction", "id": a.id, "label": a.title, "meta": a.status})

        for req in db.query(Requisition).filter(Requisition.title.ilike(like)).limit(5):
            results.append({"type": "requisition", "id": req.id, "label": req.title, "meta": req.status})

        for po in db.query(PurchaseOrder).filter(
            or_(PurchaseOrder.status.ilike(like))
        ).limit(5):
            results.append({"type": "purchase_order", "id": po.id, "label": f"PO #{po.id}", "meta": po.status})

        return results
    finally:
        db.close()


# ---------------------------------------------------------------------------
# AGENT TRAIL — full filterable feed (for the promoted drawer/full page)
# ---------------------------------------------------------------------------
@router.get("/trail")
def trail_feed(
    session_id: str | None = None,
    agent: str | None = None,
    limit: int = 100,
):
    db = SessionLocal()
    try:
        query = db.query(AgentTrailLog)
        if session_id:
            query = query.filter(AgentTrailLog.session_id == session_id)
        if agent:
            query = query.filter(AgentTrailLog.agent == agent)
        rows = query.order_by(AgentTrailLog.created_at.desc()).limit(limit).all()
        return [{
            "id": t.id, "session_id": t.session_id, "agent": t.agent, "skill": t.skill,
            "args": t.args, "result": t.result, "created_at": _iso(t.created_at),
        } for t in rows]
    finally:
        db.close()


# ---------------------------------------------------------------------------
# BUDGETS — budget vs. actual, per category, most recent month with data
# ---------------------------------------------------------------------------
@router.get("/budgets/variance")
def budget_variance():
    db = SessionLocal()
    try:
        latest_month_row = db.query(Budget.month).order_by(Budget.month.desc()).first()
        if not latest_month_row:
            return []
        latest_month = latest_month_row[0]

        budgets = db.query(Budget).filter(Budget.month == latest_month).all()
        out = []
        for b in budgets:
            # Filtered in Python rather than with a SQL date-format function —
            # strftime/to_char syntax differs between SQLite and Postgres, and
            # this dataset is small enough (demo scale) that it doesn't matter.
            pos = (
                db.query(PurchaseOrder)
                .join(Vendor, PurchaseOrder.vendor_id == Vendor.id)
                .filter(Vendor.category == b.category)
                .all()
            )
            actual = sum(
                po.amount for po in pos
                if po.created_at and po.created_at.strftime("%Y-%m") == latest_month
            )
            out.append({
                "category": b.category,
                "month": b.month,
                "budgeted_amount": b.budgeted_amount,
                "actual_amount": actual,
                "variance": b.budgeted_amount - actual,
                "over_budget": actual > b.budgeted_amount,
            })
        return out
    finally:
        db.close()


# ---------------------------------------------------------------------------
# CONTRACTS — minimal repository
# ---------------------------------------------------------------------------
@router.get("/contracts")
def list_contracts():
    db = SessionLocal()
    try:
        rows = db.query(Contract).order_by(Contract.created_at.desc()).all()
        return [{
            "id": c.id, "vendor": _vendor_brief(db, c.vendor_id), "title": c.title,
            "value": c.value, "start_date": _iso(c.start_date), "end_date": _iso(c.end_date),
        } for c in rows]
    finally:
        db.close()


@router.get("/contracts/{contract_id}")
def get_contract(contract_id: int):
    db = SessionLocal()
    try:
        c = db.get(Contract, contract_id)
        if not c:
            return {"error": "Contract not found"}
        return {
            "id": c.id, "vendor": _vendor_brief(db, c.vendor_id), "title": c.title,
            "terms": c.terms, "value": c.value,
            "start_date": _iso(c.start_date), "end_date": _iso(c.end_date),
            "created_at": _iso(c.created_at),
        }
    finally:
        db.close()


# ---------------------------------------------------------------------------
# NOTIFICATIONS — proactive signals (Phase H2). Read = seen; resolved =
# the underlying condition cleared, checked by the scheduler, not the user.
# ---------------------------------------------------------------------------
@router.get("/notifications")
@router.get("/notifications")
def list_notifications(
    unread_only: bool = False,
    unresolved_only: bool = True,
    type: str | None = None,
    severity: str | None = None,
    limit: int = 50,
):
    db = SessionLocal()
    try:
        query = db.query(Notification)
        if unread_only:
            query = query.filter(Notification.read == False)  # noqa: E712
        if unresolved_only:
            query = query.filter(Notification.resolved == False)  # noqa: E712
        if type:
            query = query.filter(Notification.type == type)
        if severity:
            query = query.filter(Notification.severity == severity)
        rows = query.order_by(Notification.created_at.desc()).limit(limit).all()
        return [{
            "id": n.id, "type": n.type, "severity": n.severity, "message": live_message(db, n),
            "related_type": n.related_type, "related_id": n.related_id,
            "read": n.read, "resolved": n.resolved,
            "created_at": _iso(n.created_at), "resolved_at": _iso(n.resolved_at),
        } for n in rows]
    finally:
        db.close()


@router.get("/notifications/unread-count")
def unread_notification_count():
    db = SessionLocal()
    try:
        count = db.query(Notification).filter(
            Notification.read == False, Notification.resolved == False  # noqa: E712
        ).count()
        return {"count": count}
    finally:
        db.close()


@router.post("/notifications/{notification_id}/read")
def mark_notification_read(notification_id: int):
    db = SessionLocal()
    try:
        n = db.get(Notification, notification_id)
        if not n:
            return {"error": "Notification not found"}
        n.read = True
        db.commit()
        return {"id": n.id, "read": True}
    finally:
        db.close()


@router.post("/notifications/{notification_id}/resolve")
def mark_notification_resolved(notification_id: int):
    """Manual dismiss — the one path exception_flagged notifications ever
    get resolved through, since that signal never auto-clears on its own."""
    db = SessionLocal()
    try:
        n = db.get(Notification, notification_id)
        if not n:
            return {"error": "Notification not found"}
        n.resolved = True
        n.resolved_at = datetime.utcnow()
        db.commit()
        return {"id": n.id, "resolved": True}
    finally:
        db.close()


# ---------------------------------------------------------------------------
# SETTINGS — configurable thresholds (Configuration page). Exposes
# thresholds/cutoffs, deliberately not the underlying scoring formulas —
# see settings.py's module docstring.
# ---------------------------------------------------------------------------
@router.get("/settings")
def list_settings():
    return get_all_settings()


@router.post("/settings")
def update_settings(payload: dict):
    """Accepts one or more {key: value} pairs. Rejects unknown keys outright
    rather than silently accepting typos that would never take effect."""
    unknown = [k for k in payload if k not in DEFAULTS]
    if unknown:
        return {"error": f"Unknown setting(s): {', '.join(unknown)}"}

    for key, value in payload.items():
        try:
            set_setting(key, str(value))
        except ValueError as e:
            return {"error": str(e)}

    if "signal_check_interval_minutes" in payload:
        import scheduler
        scheduler.apply_interval_change()

    return {"updated": list(payload.keys())}


# ---------------------------------------------------------------------------
# BRIEFING — bundles the same data already powering the Needs Attention
# panel and the dashboard headline cards into one call, for the Focus Mode
# launch briefing. Read-only, no new intelligence logic — the facts are the
# same facts shown elsewhere; this just gathers them in one place. Time-of-
# day phrasing is deliberately left to the frontend, which knows the
# person's actual local time — computing it here risked a timezone bug.
# ---------------------------------------------------------------------------
@router.get("/briefing")
def get_briefing():
    db = SessionLocal()
    try:
        unresolved = db.query(Notification).filter(Notification.resolved == False).all()  # noqa: E712
        counts = {"high": 0, "medium": 0, "low": 0}
        for n in unresolved:
            counts[n.severity] = counts.get(n.severity, 0) + 1

        # Severity first (high > medium > low), oldest known signal as the
        # tie-break within the same severity — computed across the FULL
        # unresolved set, not just whatever happens to be shown, so the
        # single most pressing item is never silently excluded just
        # because it isn't also one of the most recent ones.
        severity_rank = {"high": 0, "medium": 1, "low": 2}
        ranked = sorted(
            unresolved,
            key=lambda n: (severity_rank.get(n.severity, 3), n.created_at or datetime.min),
        )
        top_signals = ranked[:5]
        top_priority = ranked[0] if ranked else None

        total_spend = db.query(func.coalesce(func.sum(PurchaseOrder.amount), 0)).scalar()

        latest_month_row = db.query(Budget.month).order_by(Budget.month.desc()).first()
        cost_savings = 0
        if latest_month_row:
            latest_month = latest_month_row[0]
            all_pos = db.query(PurchaseOrder).all()
            for b in db.query(Budget).filter(Budget.month == latest_month).all():
                actual = sum(
                    po.amount for po in all_pos
                    if po.created_at and po.created_at.strftime("%Y-%m") == latest_month
                    and db.get(Vendor, po.vendor_id) and db.get(Vendor, po.vendor_id).category == b.category
                )
                if actual < b.budgeted_amount:
                    cost_savings += (b.budgeted_amount - actual)

        def _signal_dict(n):
            return {
                "id": n.id, "type": n.type, "severity": n.severity, "message": live_message(db, n),
                "related_type": n.related_type, "related_id": n.related_id,
                "created_at": _iso(n.created_at),
            }

        return {
            "signals": [_signal_dict(n) for n in top_signals],
            "top_priority_signal": _signal_dict(top_priority) if top_priority else None,
            "signal_counts": counts,
            "total_unresolved": len(unresolved),
            "total_spend": round(total_spend),
            "cost_savings_period": round(cost_savings),
        }
    finally:
        db.close()
