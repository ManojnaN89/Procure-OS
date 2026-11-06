SKILL_NAME = "get_category_breakdown"
SKILL_DESCRIPTION = (
    "Get total purchase order spend broken down by vendor category. Use this for "
    "questions about which category is driving cost or how spend splits across "
    "categories. Read-only — does not change any data."
)
SKILL_PARAMETERS = {"type": "object", "properties": {}, "required": []}


def run(db, **kwargs):
    from collections import defaultdict
    from models import PurchaseOrder, Vendor

    rows = (
        db.query(Vendor.category, PurchaseOrder.amount)
        .join(PurchaseOrder, PurchaseOrder.vendor_id == Vendor.id)
        .all()
    )

    by_category = defaultdict(float)
    for category, amount in rows:
        by_category[category or "Uncategorized"] += amount or 0

    sorted_items = sorted(by_category.items(), key=lambda x: -x[1])
    labels = [c for c, _ in sorted_items]
    values = [round(v) for _, v in sorted_items]

    return {
        "breakdown": [{"category": c, "amount": round(v)} for c, v in sorted_items],
        "chart": {
            "chart_type": "bar",
            "title": "Spend by category",
            "labels": labels,
            "datasets": [{"label": "Total spend (Rs.)", "data": values}],
        },
    }
