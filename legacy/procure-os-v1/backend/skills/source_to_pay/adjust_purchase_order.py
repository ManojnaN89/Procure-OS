SKILL_NAME = "adjust_purchase_order"
SKILL_DESCRIPTION = (
    "Change a purchase order's amount after it's been created — e.g. correcting a wrong "
    "figure, or updating a flagged PO's price. Use when the user wants to adjust, correct, "
    "or change a PO's amount. This re-runs the same exception check used at creation, so "
    "the PO's flagged status may change as a result — mention that in your reply. Cannot "
    "adjust one that's already been paid or cancelled."
)
SKILL_PARAMETERS = {
    "type": "object",
    "properties": {
        "purchase_order_id": {"type": "integer"},
        "new_amount": {"type": "number"},
    },
    "required": ["purchase_order_id", "new_amount"],
}


def run(db, **kwargs):
    from models import PurchaseOrder
    from intelligence import check_po_exception

    po = db.get(PurchaseOrder, kwargs["purchase_order_id"])
    if not po:
        return {"error": f"Purchase order {kwargs['purchase_order_id']} not found"}
    if po.status in ("paid", "cancelled"):
        return {"error": f"PO {po.id} is {po.status} and can't be adjusted"}

    old_amount = po.amount
    po.amount = kwargs["new_amount"]

    # re-run the same check used at creation, excluding this PO's own old
    # amount from the baseline so it doesn't skew the comparison against itself
    flagged, reason = check_po_exception(db, po.vendor_id, po.amount, exclude_po_id=po.id)
    po.flagged = flagged
    po.flag_reason = reason
    db.commit()

    return {
        "purchase_order_id": po.id, "old_amount": old_amount, "new_amount": po.amount,
        "flagged": po.flagged, "flag_reason": po.flag_reason,
    }
