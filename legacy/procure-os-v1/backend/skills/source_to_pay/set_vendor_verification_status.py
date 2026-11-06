SKILL_NAME = "set_vendor_verification_status"
SKILL_DESCRIPTION = (
    "Mark a vendor as verified (or remove that mark), optionally recording a note like a "
    "tax ID or registration number. Use when the user wants to mark a vendor as verified "
    "after checking their details themselves. This records that a human verified "
    "something OUTSIDE this system — it is NOT automated compliance checking, there is "
    "no external lookup confirming the note is accurate. Phrase your reply as 'marked as "
    "verified', not 'verified', to keep that distinction clear."
)
SKILL_PARAMETERS = {
    "type": "object",
    "properties": {
        "vendor_id": {"type": "integer"},
        "verified": {"type": "boolean", "description": "true to mark verified, false to remove the mark"},
        "note": {"type": "string", "description": "Optional note, e.g. a tax ID or registration number"},
    },
    "required": ["vendor_id", "verified"],
}


def run(db, **kwargs):
    from models import Vendor

    v = db.get(Vendor, kwargs["vendor_id"])
    if not v:
        return {"error": f"Vendor {kwargs['vendor_id']} not found"}

    v.verified = bool(kwargs["verified"])
    if kwargs.get("note"):
        v.verification_note = kwargs["note"]
    db.commit()
    return {"vendor_id": v.id, "name": v.name, "verified": v.verified, "verification_note": v.verification_note}
