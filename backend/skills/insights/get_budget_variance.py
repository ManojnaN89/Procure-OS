SKILL_NAME = "get_budget_variance"
SKILL_DESCRIPTION = (
    "Get budget versus actual spend for the most recent month, by category, "
    "including which categories are over or under budget. Use for any budget-related "
    "question. Read-only — does not change any data."
)
SKILL_PARAMETERS = {"type": "object", "properties": {}, "required": []}


def run(db, **kwargs):
    from models import Budget, PurchaseOrder, Vendor

    latest_row = db.query(Budget.month).order_by(Budget.month.desc()).first()
    if not latest_row:
        return {"error": "No budget data available yet"}
    latest_month = latest_row[0]

    budgets = db.query(Budget).filter(Budget.month == latest_month).all()
    rows = []
    for b in budgets:
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
        rows.append({
            "category": b.category,
            "budgeted": b.budgeted_amount,
            "actual": round(actual),
            "over_budget": actual > b.budgeted_amount,
        })

    return {
        "month": latest_month,
        "variance": rows,
        "chart": {
            "chart_type": "bar",
            "title": f"Budget vs. actual, {latest_month}",
            "labels": [r["category"] for r in rows],
            "datasets": [
                {"label": "Budgeted (Rs.)", "data": [r["budgeted"] for r in rows]},
                {"label": "Actual (Rs.)", "data": [r["actual"] for r in rows]},
            ],
        },
    }
