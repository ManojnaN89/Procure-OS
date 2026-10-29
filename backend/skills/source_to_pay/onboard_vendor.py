SKILL_NAME = "onboard_vendor"
SKILL_DESCRIPTION = "Onboard a new vendor into the system."
SKILL_PARAMETERS = {
    "type": "object",
    "properties": {
        "name": {"type": "string"},
        "email": {"type": "string"},
        "category": {"type": "string"},
    },
    "required": ["name", "email", "category"],
}


def run(db, **kwargs):
    from models import Vendor
    vendor = Vendor(name=kwargs["name"], email=kwargs["email"], category=kwargs["category"])
    db.add(vendor)
    db.commit()
    db.refresh(vendor)
    return {"vendor_id": vendor.id, "name": vendor.name}
