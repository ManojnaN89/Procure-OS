SKILL_NAME = "cancel_requisition"
SKILL_DESCRIPTION = (
    "Cancel a requisition the requester no longer needs — distinct from rejecting one, "
    "which implies someone else made that call. Use when the user (as the requester) "
    "wants to withdraw or cancel their own requisition. Cannot cancel one that's already "
    "been approved or rejected."
)
SKILL_PARAMETERS = {
    "type": "object",
    "properties": {
        "requisition_id": {"type": "integer"},
        "reason": {"type": "string", "description": "Optional reason for the cancellation"},
    },
    "required": ["requisition_id"],
}


def run(db, **kwargs):
    from models import Requisition

    r = db.get(Requisition, kwargs["requisition_id"])
    if not r:
        return {"error": f"Requisition {kwargs['requisition_id']} not found"}
    if r.status != "pending":
        return {"error": f"Requisition {r.id} is already {r.status} and can't be cancelled"}

    r.status = "cancelled"
    db.commit()
    return {"requisition_id": r.id, "status": r.status, "reason": kwargs.get("reason")}
