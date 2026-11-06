SKILL_NAME = "cancel_purchase_order"
SKILL_DESCRIPTION = (
    "Cancel a purchase order that shouldn't have been created or is no longer needed. "
    "Use when the user wants to cancel, void, or undo a PO. Cannot cancel one that's "
    "already been paid — process a reversal for that instead, which isn't supported yet, "
    "so say so plainly if asked."
)
SKILL_PARAMETERS = {
    "type": "object",
    "properties": {
        "purchase_order_id": {"type": "integer"},
        "reason": {"type": "string", "description": "Optional reason for the cancellation"},
    },
    "required": ["purchase_order_id"],
}


def run(db, **kwargs):
    from models import PurchaseOrder

    po = db.get(PurchaseOrder, kwargs["purchase_order_id"])
    if not po:
        return {"error": f"Purchase order {kwargs['purchase_order_id']} not found"}
    if po.status == "paid":
        return {"error": f"PO {po.id} has already been paid and can't be cancelled"}
    if po.status == "cancelled":
        return {"purchase_order_id": po.id, "already_cancelled": True}

    po.status = "cancelled"
    db.commit()
    return {"purchase_order_id": po.id, "status": po.status, "reason": kwargs.get("reason")}
