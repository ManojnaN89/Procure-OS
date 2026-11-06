SKILL_NAME = "list_vendors"
SKILL_DESCRIPTION = (
    "List every vendor in the system with their ID, name, category, rating, spend, "
    "risk level, and status (active, verified, under review). Use whenever the user "
    "wants to see, list, or browse vendors — including to find a vendor's ID before "
    "doing something else with it, or to rank/compare vendors by risk (the risk level "
    "is included for every vendor in one call, so you can sort or reason over the "
    "whole list yourself). Read-only."
)
SKILL_PARAMETERS = {
    "type": "object",
    "properties": {
        "category": {"type": "string", "description": "Optional — filter to one category, e.g. Electronics"},
        "active_only": {"type": "boolean", "description": "Optional — true to exclude deactivated vendors"},
    },
    "required": [],
}


def run(db, **kwargs):
    from models import Vendor, PurchaseOrder
    from intelligence import compute_vendor_risk

    q = db.query(Vendor)
    if kwargs.get("category"):
        q = q.filter(Vendor.category == kwargs["category"])
    rows = q.order_by(Vendor.name.asc()).all()
    if kwargs.get("active_only"):
        rows = [v for v in rows if v.active is not False]

    out = []
    for v in rows:
        spend = sum(
            po.amount or 0 for po in db.query(PurchaseOrder).filter(PurchaseOrder.vendor_id == v.id).all()
        )
        risk = compute_vendor_risk(db, v.id)
        out.append({
            "id": v.id, "name": v.name, "category": v.category, "rating": v.rating,
            "total_spend": round(spend), "active": bool(v.active) if v.active is not None else True,
            "verified": bool(v.verified), "under_review": bool(v.under_review),
            "risk_level": risk["risk_level"],
        })
    return {"count": len(out), "vendors": out}
