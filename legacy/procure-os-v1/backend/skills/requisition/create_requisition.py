SKILL_NAME = "create_requisition"
SKILL_DESCRIPTION = "Create a new purchase requisition for internal approval."
SKILL_PARAMETERS = {
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "description": {"type": "string"},
        "amount": {"type": "number"},
        "requested_by": {"type": "string"},
    },
    "required": ["title", "amount", "requested_by"],
}


def run(db, **kwargs):
    from models import Requisition
    req = Requisition(
        title=kwargs["title"],
        description=kwargs.get("description", ""),
        amount=kwargs["amount"],
        requested_by=kwargs["requested_by"],
        status="pending",
    )
    db.add(req)
    db.commit()
    db.refresh(req)
    return {"requisition_id": req.id, "status": req.status}
