SKILL_NAME = "process_payment"
SKILL_DESCRIPTION = "Process payment for a purchase order once goods or services are received."
SKILL_PARAMETERS = {
    "type": "object",
    "properties": {
        "purchase_order_id": {"type": "integer"},
        "amount": {"type": "number"},
    },
    "required": ["purchase_order_id", "amount"],
}


def run(db, **kwargs):
    from models import PurchaseOrder, Payment

    po = db.get(PurchaseOrder, kwargs["purchase_order_id"])
    if not po:
        return {"error": "Purchase order not found"}
    payment = Payment(purchase_order_id=po.id, amount=kwargs["amount"], status="processed")
    po.status = "paid"
    db.add(payment)
    db.commit()
    db.refresh(payment)
    return {"payment_id": payment.id, "purchase_order_id": po.id, "status": payment.status}
