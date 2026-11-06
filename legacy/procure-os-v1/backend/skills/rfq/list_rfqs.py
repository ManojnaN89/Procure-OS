SKILL_NAME = "list_rfqs"
SKILL_DESCRIPTION = (
    "List every RFQ with its ID, title, status, and how many vendors/quotes are "
    "involved. Use whenever the user wants to see, list, or browse RFQs. Read-only."
)
SKILL_PARAMETERS = {
    "type": "object",
    "properties": {
        "status": {"type": "string", "description": "Optional — filter to a specific status, e.g. 'sent'"},
    },
    "required": [],
}


def run(db, **kwargs):
    from models import RFQ, Quote

    q = db.query(RFQ)
    if kwargs.get("status"):
        q = q.filter(RFQ.status == kwargs["status"])
    rows = q.order_by(RFQ.created_at.desc()).all()

    out = []
    for r in rows:
        quote_count = db.query(Quote).filter(Quote.rfq_id == r.id).count()
        out.append({
            "id": r.id, "title": r.title, "status": r.status,
            "vendor_count": len(r.vendor_ids or []), "quote_count": quote_count,
        })
    return {"count": len(out), "rfqs": out}
