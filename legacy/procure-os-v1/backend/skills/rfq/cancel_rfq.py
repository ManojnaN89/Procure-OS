SKILL_NAME = "cancel_rfq"
SKILL_DESCRIPTION = (
    "Close out an RFQ without accepting any quote — e.g. sourcing decided differently, "
    "or it's no longer needed. Use when the user wants to cancel or close an RFQ."
)
SKILL_PARAMETERS = {
    "type": "object",
    "properties": {
        "rfq_id": {"type": "integer"},
        "reason": {"type": "string", "description": "Optional reason for the cancellation"},
    },
    "required": ["rfq_id"],
}


def run(db, **kwargs):
    from models import RFQ

    r = db.get(RFQ, kwargs["rfq_id"])
    if not r:
        return {"error": f"RFQ {kwargs['rfq_id']} not found"}
    if r.status == "cancelled":
        return {"rfq_id": r.id, "already_cancelled": True}

    r.status = "cancelled"
    db.commit()
    return {"rfq_id": r.id, "status": r.status, "reason": kwargs.get("reason")}
