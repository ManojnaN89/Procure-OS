SKILL_NAME = "get_purchase_order_detail"
SKILL_DESCRIPTION = (
    "Get full detail on a specific purchase order: vendor, items, amount, status, "
    "whether it's flagged and why, delivery dates, and payment history. Use when the "
    "user asks about a specific PO's status or details. Read-only."
)
SKILL_PARAMETERS = {
    "type": "object",
    "properties": {"purchase_order_id": {"type": "integer"}},
    "required": ["purchase_order_id"],
}


def run(db, **kwargs):
    from models import PurchaseOrder, Vendor, Payment

    po = db.get(PurchaseOrder, kwargs["purchase_order_id"])
    if not po:
        return {"error": f"Purchase order {kwargs['purchase_order_id']} not found"}

    v = db.get(Vendor, po.vendor_id)
    payments = db.query(Payment).filter(Payment.purchase_order_id == po.id).all()

    return {
        "id": po.id, "vendor_name": v.name if v else None,
        "items": po.items, "amount": po.amount, "status": po.status,
        "flagged": po.flagged, "flag_reason": po.flag_reason,
        "payments": [{"amount": p.amount, "status": p.status} for p in payments],
    }
