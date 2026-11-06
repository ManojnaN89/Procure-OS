SKILL_NAME = "get_rfq_detail"
SKILL_DESCRIPTION = (
    "Get an RFQ's title, status, items, and how many vendors/quotes are involved. Use "
    "when the user asks what a specific RFQ is about or its status. For a full price "
    "comparison across quotes with the cheapest highlighted, use compare_quotes instead "
    "— this is for the RFQ's own details, not quote comparison. Read-only."
)
SKILL_PARAMETERS = {
    "type": "object",
    "properties": {"rfq_id": {"type": "integer"}},
    "required": ["rfq_id"],
}


def run(db, **kwargs):
    from models import RFQ, Quote

    r = db.get(RFQ, kwargs["rfq_id"])
    if not r:
        return {"error": f"RFQ {kwargs['rfq_id']} not found"}

    quote_count = db.query(Quote).filter(Quote.rfq_id == r.id).count()
    return {
        "id": r.id, "title": r.title, "status": r.status, "items": r.items,
        "vendor_count": len(r.vendor_ids or []), "quote_count": quote_count,
    }
