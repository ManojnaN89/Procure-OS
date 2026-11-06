SKILL_NAME = "draft_rfq"
SKILL_DESCRIPTION = "Create and send a Request for Quotation to a list of vendors for specified items."
SKILL_PARAMETERS = {
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "items": {"type": "array", "items": {"type": "string"}},
        "vendor_ids": {"type": "array", "items": {"type": "integer"}},
    },
    "required": ["title", "items", "vendor_ids"],
}


def run(db, **kwargs):
    from models import RFQ
    rfq = RFQ(title=kwargs["title"], items=kwargs["items"], vendor_ids=kwargs["vendor_ids"], status="sent")
    db.add(rfq)
    db.commit()
    db.refresh(rfq)
    return {"rfq_id": rfq.id, "status": rfq.status, "vendors_notified": len(rfq.vendor_ids)}
