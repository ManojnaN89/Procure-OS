SKILL_NAME = "decide_requisition"
SKILL_DESCRIPTION = "Approve or reject a pending purchase requisition."
SKILL_PARAMETERS = {
    "type": "object",
    "properties": {
        "requisition_id": {"type": "integer"},
        "decision": {"type": "string", "enum": ["approved", "rejected"]},
    },
    "required": ["requisition_id", "decision"],
}


def run(db, **kwargs):
    from models import Requisition
    from datetime import datetime

    req = db.get(Requisition, kwargs["requisition_id"])
    if not req:
        return {"error": "Requisition not found"}
    req.status = kwargs["decision"]
    req.decided_at = datetime.utcnow()
    db.commit()
    return {"requisition_id": req.id, "status": req.status}
