SKILL_NAME = "adjust_requisition"
SKILL_DESCRIPTION = (
    "Edit a requisition's amount or description before it's been decided. Use when the "
    "user wants to correct or update a still-pending requisition. Cannot adjust one "
    "that's already been approved, rejected, or cancelled."
)
SKILL_PARAMETERS = {
    "type": "object",
    "properties": {
        "requisition_id": {"type": "integer"},
        "new_amount": {"type": "number", "description": "Optional new amount"},
        "new_description": {"type": "string", "description": "Optional new description"},
    },
    "required": ["requisition_id"],
}


def run(db, **kwargs):
    from models import Requisition

    r = db.get(Requisition, kwargs["requisition_id"])
    if not r:
        return {"error": f"Requisition {kwargs['requisition_id']} not found"}
    if r.status != "pending":
        return {"error": f"Requisition {r.id} is already {r.status} and can't be edited"}
    if kwargs.get("new_amount") is None and not kwargs.get("new_description"):
        return {"error": "Provide new_amount, new_description, or both"}

    if kwargs.get("new_amount") is not None:
        r.amount = kwargs["new_amount"]
    if kwargs.get("new_description"):
        r.description = kwargs["new_description"]
    db.commit()
    return {"requisition_id": r.id, "amount": r.amount, "description": r.description}
