SKILL_NAME = "list_requisitions"
SKILL_DESCRIPTION = (
    "List every requisition with its ID, title, status, amount, and who requested it. "
    "Use whenever the user wants to see, list, or browse requisitions — including to "
    "find one's ID before approving/rejecting it. Read-only."
)
SKILL_PARAMETERS = {
    "type": "object",
    "properties": {
        "status": {"type": "string", "description": "Optional — filter to 'pending', 'approved', or 'rejected'"},
    },
    "required": [],
}


def run(db, **kwargs):
    from models import Requisition

    q = db.query(Requisition)
    if kwargs.get("status"):
        q = q.filter(Requisition.status == kwargs["status"])
    rows = q.order_by(Requisition.created_at.desc()).all()

    return {"count": len(rows), "requisitions": [{
        "id": r.id, "title": r.title, "amount": r.amount,
        "status": r.status, "requested_by": r.requested_by,
    } for r in rows]}
