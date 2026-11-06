SKILL_NAME = "find_vendor_by_name"
SKILL_DESCRIPTION = (
    "Look up a vendor's ID by name (partial match allowed). Use this when a quote or "
    "document mentions a vendor by name and you need their vendor_id before calling "
    "another skill like record_quote."
)
SKILL_PARAMETERS = {
    "type": "object",
    "properties": {
        "name": {"type": "string", "description": "Full or partial vendor name to search for"},
    },
    "required": ["name"],
}


def run(db, **kwargs):
    from models import Vendor
    like = f"%{kwargs['name']}%"
    matches = db.query(Vendor).filter(Vendor.name.ilike(like)).limit(5).all()
    if not matches:
        return {"matches": [], "message": "No vendor found with that name"}
    return {"matches": [{"vendor_id": v.id, "name": v.name, "category": v.category} for v in matches]}
