"""
The five signal rules that make ProcureOS proactive instead of purely
reactive. Each rule has two halves:
  - CREATE: is there a new instance of this problem with no existing
    unresolved notification for it yet?
  - RESOLVE: for existing unresolved notifications of this type, is the
    underlying condition still true? If not, mark resolved.

run_signal_checks() runs both halves for all five rules and returns the
list of newly-created notifications (for broadcasting). This is called by
the scheduler on a timer, but is a plain function — testable directly
against seeded data without needing the scheduler running at all.
"""
from datetime import datetime, timedelta

from db import SessionLocal
from models import Vendor, Requisition, Budget, Contract, Quote, PurchaseOrder, Notification
from intelligence import compute_vendor_risk
from settings import get_setting_int


def _has_unresolved(db, type_, related_type, related_id):
    return db.query(Notification).filter(
        Notification.type == type_,
        Notification.related_type == related_type,
        Notification.related_id == related_id,
        Notification.resolved == False,  # noqa: E712
    ).first() is not None


def _create(db, type_, severity, message, related_type, related_id):
    n = Notification(
        type=type_, severity=severity, message=message,
        related_type=related_type, related_id=related_id,
    )
    db.add(n)
    return n


# ---------------------------------------------------------------------------
# Rule 1: exception flagged (never auto-resolves — a flag is a historical
# fact, not a condition that becomes untrue on its own; cleared only by
# manually marking it resolved)
# ---------------------------------------------------------------------------
def check_exception_flagged(db):
    created = []
    for q in db.query(Quote).filter(Quote.flagged == True).all():  # noqa: E712
        if not _has_unresolved(db, "exception_flagged", "rfq", q.rfq_id):
            v = db.get(Vendor, q.vendor_id)
            msg = f"Flagged quote from {v.name if v else 'a vendor'}: {q.flag_reason}"
            created.append(_create(db, "exception_flagged", "medium", msg, "rfq", q.rfq_id))

    for po in db.query(PurchaseOrder).filter(PurchaseOrder.flagged == True).all():  # noqa: E712
        if not _has_unresolved(db, "exception_flagged", "purchase_order", po.id):
            v = db.get(Vendor, po.vendor_id)
            msg = f"Flagged PO #{po.id} from {v.name if v else 'a vendor'}: {po.flag_reason}"
            created.append(_create(db, "exception_flagged", "medium", msg, "purchase_order", po.id))
    return created


# ---------------------------------------------------------------------------
# Rule 2: vendor risk escalation — creates when a vendor is currently high
# risk with no unresolved notification yet; resolves when risk drops
# ---------------------------------------------------------------------------
def check_vendor_risk(db):
    created = []
    for v in db.query(Vendor).all():
        risk = compute_vendor_risk(db, v.id)
        is_high = risk["risk_level"] == "high"
        existing = db.query(Notification).filter(
            Notification.type == "vendor_risk", Notification.related_type == "vendor",
            Notification.related_id == v.id, Notification.resolved == False,  # noqa: E712
        ).first()

        if is_high and not existing:
            msg = f"{v.name} crossed into high risk \u2014 {risk['late_delivery_rate_pct']}% late deliveries, {risk['flagged_incidents']} flagged incidents"
            created.append(_create(db, "vendor_risk", "high", msg, "vendor", v.id))
        elif not is_high and existing:
            existing.resolved = True
            existing.resolved_at = datetime.utcnow()
    return created


# ---------------------------------------------------------------------------
# Rule 3: requisition stuck pending too long — resolves once decided
# ---------------------------------------------------------------------------
def check_stale_requisitions(db):
    created = []
    cutoff = datetime.utcnow() - timedelta(days=get_setting_int("stale_requisition_days"))

    for r in db.query(Requisition).filter(Requisition.status == "pending").all():
        if r.created_at and r.created_at <= cutoff:
            if not _has_unresolved(db, "stale_requisition", "requisition", r.id):
                days_pending = (datetime.utcnow() - r.created_at).days
                msg = f"Requisition #{r.id} (\"{r.title}\") has been pending {days_pending} days with no decision"
                created.append(_create(db, "stale_requisition", "medium", msg, "requisition", r.id))

    # resolve: any unresolved stale_requisition notification whose requisition is no longer pending
    for n in db.query(Notification).filter(
        Notification.type == "stale_requisition", Notification.resolved == False  # noqa: E712
    ).all():
        r = db.get(Requisition, n.related_id)
        if not r or r.status != "pending":
            n.resolved = True
            n.resolved_at = datetime.utcnow()
    return created


# ---------------------------------------------------------------------------
# Rule 4: category over budget this month — resolves if back under budget
# ---------------------------------------------------------------------------
def check_budget_overruns(db):
    created = []
    latest_month_row = db.query(Budget.month).order_by(Budget.month.desc()).first()
    if not latest_month_row:
        return created
    latest_month = latest_month_row[0]

    budgets = db.query(Budget).filter(Budget.month == latest_month).all()
    for b in budgets:
        pos = (
            db.query(PurchaseOrder)
            .join(Vendor, PurchaseOrder.vendor_id == Vendor.id)
            .filter(Vendor.category == b.category)
            .all()
        )
        actual = sum(po.amount for po in pos if po.created_at and po.created_at.strftime("%Y-%m") == latest_month)
        over = actual > b.budgeted_amount
        existing = db.query(Notification).filter(
            Notification.type == "budget_overrun", Notification.related_type == "budget",
            Notification.related_id == b.id, Notification.resolved == False,  # noqa: E712
        ).first()

        if over and not existing:
            pct = round((actual / b.budgeted_amount - 1) * 100) if b.budgeted_amount else 0
            msg = f"{b.category} is {pct}% over its {latest_month} budget"
            created.append(_create(db, "budget_overrun", "medium", msg, "budget", b.id))
        elif not over and existing:
            existing.resolved = True
            existing.resolved_at = datetime.utcnow()

    # Resolve any budget_overrun notification whose month has passed. The
    # loop above only ever re-evaluates the LATEST month's budget rows —
    # meaning once the calendar rolled to a new month, an older month's
    # overrun notification had no budget row left to compare against and
    # could never be resolved again, automatically or manually, ever. A
    # month that's no longer the latest one is over; the signal it
    # represented is stale by definition, not something to leave orphaned.
    for n in db.query(Notification).filter(
        Notification.type == "budget_overrun", Notification.resolved == False  # noqa: E712
    ).all():
        b = db.get(Budget, n.related_id)
        if b and b.month != latest_month:
            n.resolved = True
            n.resolved_at = datetime.utcnow()

    return created


# ---------------------------------------------------------------------------
# Rule 5: contract expiring within 30 days — resolves if renewed further out
# ---------------------------------------------------------------------------
def check_contracts_expiring(db):
    created = []
    horizon = datetime.utcnow() + timedelta(days=get_setting_int("contract_expiring_days"))

    for c in db.query(Contract).filter(Contract.end_date.isnot(None)).all():
        expiring_soon = c.end_date <= horizon
        existing = db.query(Notification).filter(
            Notification.type == "contract_expiring", Notification.related_type == "contract",
            Notification.related_id == c.id, Notification.resolved == False,  # noqa: E712
        ).first()

        if expiring_soon and not existing:
            days_left = (c.end_date - datetime.utcnow()).days
            msg = f"\"{c.title}\" expires in {days_left} days"
            created.append(_create(db, "contract_expiring", "low", msg, "contract", c.id))
        elif not expiring_soon and existing:
            existing.resolved = True
            existing.resolved_at = datetime.utcnow()
    return created


def live_message(db, n):
    """
    Both stale_requisition and contract_expiring bake an elapsed-time value
    directly into their stored message text at creation ("has been pending
    23 days", "expires in 9 days") — never rewritten afterward, even though
    the notification can stay unresolved and visible for a long time. This
    recomputes the live, current value at serve-time instead of trusting
    the frozen text, so the same underlying record never shows two
    different numbers depending on which endpoint served it. Every other
    notification type's stored message doesn't have this staleness
    pattern and is returned as-is.
    """
    if n.type == "stale_requisition" and n.related_type == "requisition":
        r = db.get(Requisition, n.related_id)
        if r and r.created_at:
            days_pending = (datetime.utcnow() - r.created_at).days
            return f"Requisition #{r.id} (\"{r.title}\") has been pending {days_pending} days with no decision"
    elif n.type == "contract_expiring" and n.related_type == "contract":
        c = db.get(Contract, n.related_id)
        if c and c.end_date:
            days_left = (c.end_date - datetime.utcnow()).days
            return f"\"{c.title}\" expires in {days_left} days"
    return n.message


def run_signal_checks():
    """Runs all five rules in one pass, commits, and returns newly-created
    notifications (as plain dicts, safe to use after the session closes)."""
    db = SessionLocal()
    try:
        created = []
        created += check_exception_flagged(db)
        created += check_vendor_risk(db)
        created += check_stale_requisitions(db)
        created += check_budget_overruns(db)
        created += check_contracts_expiring(db)
        db.commit()

        result = [{
            "id": n.id, "type": n.type, "severity": n.severity, "message": n.message,
            "related_type": n.related_type, "related_id": n.related_id,
            "created_at": n.created_at.isoformat() if n.created_at else None,
        } for n in created]
        return result
    finally:
        db.close()
