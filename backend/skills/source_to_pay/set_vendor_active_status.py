SKILL_NAME = "set_vendor_active_status"
SKILL_DESCRIPTION = (
    "Deactivate a vendor (mark them as no longer active/in use), or reactivate one. "
    "Use when the user wants to close out, deactivate, or stop working with a vendor, "
    "or bring a previously deactivated one back. This only sets a status flag — it does "
    "NOT block new purchase orders or RFQs against this vendor; that enforcement isn't "
    "built yet, so mention that plainly if the user seems to expect it."
)
SKILL_PARAMETERS = {
    "type": "object",
    "properties": {
        "vendor_id": {"type": "integer"},
        "active": {"type": "boolean", "description": "false to deactivate, true to reactivate"},
    },
    "required": ["vendor_id", "active"],
}


def run(db, **kwargs):
    from models import Vendor

    v = db.get(Vendor, kwargs["vendor_id"])
    if not v:
        return {"error": f"Vendor {kwargs['vendor_id']} not found"}

    v.active = bool(kwargs["active"])
    db.commit()
    return {"vendor_id": v.id, "name": v.name, "active": v.active}
