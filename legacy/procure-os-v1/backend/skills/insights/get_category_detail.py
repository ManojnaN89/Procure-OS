SKILL_NAME = "get_category_detail"
SKILL_DESCRIPTION = (
    "Get spend detail for one specific category: total spend and the individual "
    "vendors in it with their own spend. Use when the user asks about a specific "
    "category by name. Read-only — does not change any data."
)
SKILL_PARAMETERS = {
    "type": "object",
    "properties": {
        "category": {"type": "string", "description": "Category name, e.g. Electronics"},
    },
    "required": ["category"],
}


def run(db, **kwargs):
    from models import Vendor, PurchaseOrder

    category = kwargs["category"]
    vendors = db.query(Vendor).filter(Vendor.category.ilike(category)).all()
    if not vendors:
        return {"error": f"No vendors found in category '{category}'"}

    details = []
    total = 0
    for v in vendors:
        pos = db.query(PurchaseOrder).filter(PurchaseOrder.vendor_id == v.id).all()
        spend = sum(po.amount or 0 for po in pos)
        total += spend
        details.append({"vendor_id": v.id, "name": v.name, "spend": round(spend), "po_count": len(pos)})

    details.sort(key=lambda d: -d["spend"])
    labels = [d["name"] for d in details]
    values = [d["spend"] for d in details]

    return {
        "category": category,
        "total_spend": round(total),
        "vendors": details,
        "chart": {
            "chart_type": "bar",
            "title": f"{category} spend by vendor",
            "labels": labels,
            "datasets": [{"label": "Spend (Rs.)", "data": values}],
        },
    }
