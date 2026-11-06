SKILL_NAME = "get_requisition_detail"
SKILL_DESCRIPTION = (
    "Get full detail on a specific requisition: title, description, amount, status, who "
    "requested it, when it was decided, and the resulting PO if it was converted. Use "
    "when the user asks about a specific requisition's status or details. Read-only."
)
SKILL_PARAMETERS = {
    "type": "object",
    "properties": {"requisition_id": {"type": "integer"}},
    "required": ["requisition_id"],
}


def run(db, **kwargs):
    from models import Requisition, PurchaseOrder

    r = db.get(Requisition, kwargs["requisition_id"])
    if not r:
        return {"error": f"Requisition {kwargs['requisition_id']} not found"}

    po = db.query(PurchaseOrder).filter(PurchaseOrder.requisition_id == r.id).first()
    return {
        "id": r.id, "title": r.title, "description": r.description,
        "amount": r.amount, "status": r.status, "requested_by": r.requested_by,
        "purchase_order_id": po.id if po else None,
    }
