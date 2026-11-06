SKILL_NAME = "convert_to_po"
SKILL_DESCRIPTION = "Convert an approved requisition into a purchase order for a chosen vendor."
SKILL_PARAMETERS = {
    "type": "object",
    "properties": {
        "requisition_id": {"type": "integer"},
        "vendor_id": {"type": "integer"},
        "items": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["requisition_id", "vendor_id", "items"],
}


def run(db, **kwargs):
    from models import Requisition, PurchaseOrder
    from intelligence import check_po_exception

    req = db.get(Requisition, kwargs["requisition_id"])
    if not req or req.status != "approved":
        return {"error": "Requisition must be approved before converting to a PO"}

    flagged, reason = check_po_exception(db, kwargs["vendor_id"], req.amount)

    po = PurchaseOrder(
        vendor_id=kwargs["vendor_id"],
        requisition_id=req.id,
        items=kwargs["items"],
        amount=req.amount,
        status="created",
        flagged=flagged,
        flag_reason=reason,
    )
    db.add(po)
    db.commit()
    db.refresh(po)

    result = {"purchase_order_id": po.id, "vendor_id": po.vendor_id, "amount": po.amount}
    if flagged:
        result["flagged"] = True
        result["flag_reason"] = reason
    return result
