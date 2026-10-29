SKILL_NAME = "get_dashboard_summary"
SKILL_DESCRIPTION = (
    "Get a complete overview of the procurement operation in one call: total spend, "
    "cost savings, spend under management, exceptions, requisition status breakdown, "
    "and counts of auctions/RFQs/vendors/purchase orders. Use this when the user asks "
    "for a general summary, overview, or 'what's on my dashboard' type question, rather "
    "than a narrow question about one category or vendor. Read-only. "
    "IMPORTANT: the result includes charts, a progress bar, and an expandable list that "
    "already visually display the requisition status split, the spend-under-management "
    "percentage, and the pending requisition details. Do not repeat those exact numbers "
    "as a bullet list in your reply — they are already shown. Keep your written reply to "
    "brief interpretation only (e.g. what the numbers mean, anything that stands out), "
    "not a restatement of the raw data."
)
SKILL_PARAMETERS = {"type": "object", "properties": {}, "required": []}


def run(db, **kwargs):
    from collections import defaultdict
    from models import Vendor, Auction, RFQ, Requisition, PurchaseOrder, Budget, Contract, Quote

    req_counts = defaultdict(int)
    for (status,) in db.query(Requisition.status).all():
        req_counts[status] += 1
    approved = req_counts.get("approved", 0)
    pending = req_counts.get("pending", 0)
    rejected = req_counts.get("rejected", 0)

    all_pos = db.query(PurchaseOrder).all()
    total_spend = sum(po.amount or 0 for po in all_pos)

    managed_spend = sum(po.amount or 0 for po in all_pos if po.requisition_id is not None)
    maverick_spend = total_spend - managed_spend
    sum_pct = round((managed_spend / total_spend) * 100) if total_spend else 0

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

    exceptions_count = (
        db.query(Quote).filter(Quote.flagged == True).count()  # noqa: E712
        + db.query(PurchaseOrder).filter(PurchaseOrder.flagged == True).count()  # noqa: E712
    )

    # the actual pending requisitions, not just the count — powers the
    # expandable "show the N pending ones" detail in the UI
    pending_reqs = db.query(Requisition).filter(Requisition.status == "pending").all()
    from datetime import datetime
    pending_details = [{
        "id": r.id,
        "title": r.title,
        "days_pending": (datetime.utcnow() - r.created_at).days if r.created_at else None,
    } for r in pending_reqs]

    charts = []
    if approved or pending or rejected:
        charts.append({
            "chart_type": "doughnut",  # part-of-whole split reads faster as a donut than a bar
            "title": "Requisition status",
            "value_unit": "count",  # a count of requisitions, not currency — the frontend must not run this through formatCurrency()
            "labels": ["Approved", "Pending", "Rejected"],
            "datasets": [{"label": "Requisitions", "data": [approved, pending, rejected]}],
        })
    if total_spend:
        charts.append({
            "chart_type": "doughnut",
            "title": "Spend under management",
            "value_unit": "currency",
            "labels": ["Managed", "Maverick"],
            "datasets": [{"label": "Spend (Rs.)", "data": [round(managed_spend), round(maverick_spend)]}],
        })

    extras = [{
        "type": "progress_bar",
        "label": "Spend under management",
        "value_pct": sum_pct,
        "secondary_label": f"{100 - sum_pct}% maverick spend",
    }]
    if pending_details:
        extras.append({
            "type": "expandable_list",
            "label": f"{len(pending_details)} pending requisition{'s' if len(pending_details) != 1 else ''}",
            "items": [{
                "text": f"Requisition #{p['id']} \u2014 {p['title']}"
                        + (f" \u2014 {p['days_pending']} days pending" if p["days_pending"] is not None else ""),
                "route": f"#/requisitions/{p['id']}",
            } for p in pending_details],
        })

    return {
        "total_spend": round(total_spend),
        "cost_savings_period": round(cost_savings),
        "spend_under_management_pct": sum_pct,
        "exceptions_count": exceptions_count,
        "requisition_status": dict(req_counts),
        "pending_requisitions": pending_details,
        "open_auctions": db.query(Auction).filter(Auction.status == "open").count(),
        "open_rfqs": db.query(RFQ).filter(RFQ.status == "sent").count(),
        "vendors": db.query(Vendor).count(),
        "purchase_orders": len(all_pos),
        "contracts": db.query(Contract).count(),
        "charts": charts,
        "extras": extras,
    }
