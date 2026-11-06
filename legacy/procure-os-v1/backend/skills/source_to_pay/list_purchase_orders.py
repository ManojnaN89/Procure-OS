SKILL_NAME = "list_purchase_orders"
SKILL_DESCRIPTION = (
    "List every purchase order with its ID, vendor, amount, status, and whether it's "
    "flagged. Use whenever the user wants to see, list, or browse purchase orders. "
    "Read-only."
)
SKILL_PARAMETERS = {
    "type": "object",
    "properties": {
        "status": {"type": "string", "description": "Optional — filter to a specific status, e.g. 'paid'"},
        "flagged_only": {"type": "boolean", "description": "Optional — true to show only flagged POs"},
    },
    "required": [],
}


def run(db, **kwargs):
    from models import PurchaseOrder, Vendor

    q = db.query(PurchaseOrder)
    if kwargs.get("status"):
        q = q.filter(PurchaseOrder.status == kwargs["status"])
    if kwargs.get("flagged_only"):
        q = q.filter(PurchaseOrder.flagged == True)  # noqa: E712
    rows = q.order_by(PurchaseOrder.created_at.desc()).all()

    out = []
    for po in rows:
        v = db.get(Vendor, po.vendor_id)
        out.append({
            "id": po.id, "vendor_id": po.vendor_id, "vendor_name": v.name if v else None,
            "amount": po.amount, "status": po.status, "flagged": po.flagged,
        })
    return {"count": len(out), "purchase_orders": out}
