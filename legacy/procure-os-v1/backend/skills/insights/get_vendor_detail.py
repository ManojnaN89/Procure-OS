SKILL_NAME = "get_vendor_detail"
SKILL_DESCRIPTION = (
    "Get full detail on a specific vendor: risk level and breakdown, rating, category, "
    "total spend, review status, active/verified status. Use when the user asks about a "
    "specific vendor by name or ID — their risk, their rating, their status, or a general "
    "'tell me about this vendor' question. Read-only."
)
SKILL_PARAMETERS = {
    "type": "object",
    "properties": {"vendor_id": {"type": "integer"}},
    "required": ["vendor_id"],
}


def run(db, **kwargs):
    from models import Vendor, PurchaseOrder
    from intelligence import compute_vendor_risk

    v = db.get(Vendor, kwargs["vendor_id"])
    if not v:
        return {"error": f"Vendor {kwargs['vendor_id']} not found"}

    pos = db.query(PurchaseOrder).filter(PurchaseOrder.vendor_id == v.id).all()
    total_spend = sum(po.amount or 0 for po in pos)

    return {
        "id": v.id, "name": v.name, "category": v.category, "rating": v.rating,
        "total_spend": round(total_spend), "purchase_order_count": len(pos),
        "under_review": bool(v.under_review),
        "active": bool(v.active) if v.active is not None else True,
        "verified": bool(v.verified),
        "verification_note": v.verification_note,
        "risk": compute_vendor_risk(db, v.id),
    }
