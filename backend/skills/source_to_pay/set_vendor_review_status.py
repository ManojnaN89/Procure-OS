SKILL_NAME = "set_vendor_review_status"
SKILL_DESCRIPTION = (
    "Flag a vendor for active review, or remove that flag. Use when the user wants to "
    "mark a risky vendor as being actively watched, or to clear that flag once it's no "
    "longer needed. This records a human decision to keep watching the vendor — it does "
    "NOT change the vendor's computed risk score, since flagging doesn't make the "
    "underlying risk actually lower. To resolve the original risk notification itself, "
    "use dismiss_notification separately if the user wants that too."
)
SKILL_PARAMETERS = {
    "type": "object",
    "properties": {
        "vendor_id": {"type": "integer"},
        "under_review": {"type": "boolean", "description": "true to flag for review, false to remove the flag"},
    },
    "required": ["vendor_id", "under_review"],
}


def run(db, **kwargs):
    from models import Vendor

    v = db.get(Vendor, kwargs["vendor_id"])
    if not v:
        return {"error": f"Vendor {kwargs['vendor_id']} not found"}

    v.under_review = bool(kwargs["under_review"])
    db.commit()
    return {"vendor_id": v.id, "name": v.name, "under_review": v.under_review}
