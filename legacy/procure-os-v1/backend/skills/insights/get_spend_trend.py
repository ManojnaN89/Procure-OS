SKILL_NAME = "get_spend_trend"
SKILL_DESCRIPTION = (
    "Get total purchase order spend per month over recent months. Use this for "
    "questions about spend trends, why spend increased/decreased, or spend over time. "
    "Read-only — does not change any data."
)
SKILL_PARAMETERS = {
    "type": "object",
    "properties": {
        "months": {"type": "integer", "description": "How many recent months to include (default 6)"},
    },
    "required": [],
}


def run(db, **kwargs):
    from collections import defaultdict
    from models import PurchaseOrder

    months = kwargs.get("months") or 6
    pos = db.query(PurchaseOrder).all()

    by_month = defaultdict(float)
    for po in pos:
        if po.created_at:
            key = po.created_at.strftime("%Y-%m")
            by_month[key] += po.amount or 0

    sorted_months = sorted(by_month.keys())[-months:]
    labels = sorted_months
    values = [round(by_month[m]) for m in sorted_months]

    return {
        "trend": [{"month": m, "amount": round(by_month[m])} for m in sorted_months],
        "chart": {
            "chart_type": "line",
            "title": "Spend trend",
            "labels": labels,
            "datasets": [{"label": "Total spend (Rs.)", "data": values}],
        },
    }
