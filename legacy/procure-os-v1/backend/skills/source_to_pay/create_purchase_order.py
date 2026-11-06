SKILL_NAME = "create_purchase_order"
SKILL_DESCRIPTION = "Directly create a purchase order for a vendor, outside the requisition-approval flow."
SKILL_PARAMETERS = {
    "type": "object",
    "properties": {
        "vendor_id": {"type": "integer"},
        "items": {"type": "array", "items": {"type": "string"}},
        "amount": {"type": "number"},
    },
    "required": ["vendor_id", "items", "amount"],
}


def run(db, **kwargs):
    from models import PurchaseOrder
    from intelligence import check_po_exception

    flagged, reason = check_po_exception(db, kwargs["vendor_id"], kwargs["amount"])

    po = PurchaseOrder(
        vendor_id=kwargs["vendor_id"], items=kwargs["items"], amount=kwargs["amount"],
        status="created", flagged=flagged, flag_reason=reason,
    )
    db.add(po)
    db.commit()
    db.refresh(po)

    result = {"purchase_order_id": po.id, "status": po.status}
    if flagged:
        result["flagged"] = True
        result["flag_reason"] = reason
    return result
