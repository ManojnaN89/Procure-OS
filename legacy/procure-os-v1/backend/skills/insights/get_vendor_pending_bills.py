SKILL_NAME = "get_vendor_pending_bills"
SKILL_DESCRIPTION = (
    "Get every unpaid purchase order for a specific vendor, with amounts and how long "
    "they've been outstanding. Use when the user asks what a vendor is still owed, what "
    "bills are pending, or similar — without needing to already know a specific PO "
    "number. Read-only."
)
SKILL_PARAMETERS = {
    "type": "object",
    "properties": {"vendor_id": {"type": "integer"}},
    "required": ["vendor_id"],
}


def run(db, **kwargs):
    from datetime import datetime
    from models import Vendor, PurchaseOrder

    v = db.get(Vendor, kwargs["vendor_id"])
    if not v:
        return {"error": f"Vendor {kwargs['vendor_id']} not found"}

    unpaid = db.query(PurchaseOrder).filter(
        PurchaseOrder.vendor_id == v.id, PurchaseOrder.status != "paid"
    ).all()

    bills = [{
        "purchase_order_id": po.id,
        "amount": po.amount,
        "status": po.status,
        "days_outstanding": (datetime.utcnow() - po.created_at).days if po.created_at else None,
    } for po in unpaid]

    return {
        "vendor_id": v.id, "vendor_name": v.name,
        "total_outstanding": round(sum(b["amount"] or 0 for b in bills)),
        "bills": bills,
    }
