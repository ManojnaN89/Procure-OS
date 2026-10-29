SKILL_NAME = "compare_vendors"
SKILL_DESCRIPTION = (
    "Compare two or more specific vendors by total spend. Use this when the user "
    "names vendors to compare. Read-only — does not change any data."
)
SKILL_PARAMETERS = {
    "type": "object",
    "properties": {
        "vendor_ids": {"type": "array", "items": {"type": "integer"}, "description": "Vendor IDs to compare"},
    },
    "required": ["vendor_ids"],
}


def run(db, **kwargs):
    from models import Vendor, PurchaseOrder

    labels = []
    values = []
    details = []
    for vid in kwargs["vendor_ids"]:
        v = db.get(Vendor, vid)
        if not v:
            continue
        pos = db.query(PurchaseOrder).filter(PurchaseOrder.vendor_id == vid).all()
        total = sum(po.amount or 0 for po in pos)
        labels.append(v.name)
        values.append(round(total))
        details.append({"vendor_id": v.id, "name": v.name, "total_spend": round(total), "po_count": len(pos)})

    if not details:
        return {"error": "None of the given vendor IDs were found"}

    return {
        "comparison": details,
        "chart": {
            "chart_type": "bar",
            "title": "Vendor spend comparison",
            "labels": labels,
            "datasets": [{"label": "Total spend (Rs.)", "data": values}],
        },
    }
