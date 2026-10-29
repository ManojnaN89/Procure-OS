SKILL_NAME = "list_payments"
SKILL_DESCRIPTION = (
    "List every payment made, with its ID, the purchase order it's for, the vendor, "
    "and the amount. Use whenever the user wants to see, list, or browse payments. "
    "Read-only."
)
SKILL_PARAMETERS = {"type": "object", "properties": {}, "required": []}


def run(db, **kwargs):
    from models import Payment, PurchaseOrder, Vendor

    rows = db.query(Payment).order_by(Payment.created_at.desc()).all()
    out = []
    for p in rows:
        po = db.get(PurchaseOrder, p.purchase_order_id)
        v = db.get(Vendor, po.vendor_id) if po else None
        out.append({
            "id": p.id, "purchase_order_id": p.purchase_order_id,
            "vendor_name": v.name if v else None, "amount": p.amount, "status": p.status,
        })
    return {"count": len(out), "payments": out}
