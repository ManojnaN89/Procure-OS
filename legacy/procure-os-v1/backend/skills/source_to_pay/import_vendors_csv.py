SKILL_NAME = "import_vendors_csv"
SKILL_DESCRIPTION = (
    "Bulk-import multiple vendors at once from parsed CSV rows. Use this after a CSV "
    "file has been uploaded and its rows extracted — do not use for a single vendor, "
    "use onboard_vendor for that."
)
SKILL_PARAMETERS = {
    "type": "object",
    "properties": {
        "vendors": {
            "type": "array",
            "description": "List of vendor rows to import",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "email": {"type": "string"},
                    "category": {"type": "string"},
                    "rating": {"type": "number"},
                },
                "required": ["name"],
            },
        },
    },
    "required": ["vendors"],
}


def run(db, **kwargs):
    from models import Vendor

    created = []
    skipped = []
    for row in kwargs["vendors"]:
        name = (row.get("name") or "").strip()
        if not name:
            skipped.append(row)
            continue
        existing = db.query(Vendor).filter(Vendor.name.ilike(name)).first()
        if existing:
            skipped.append({"name": name, "reason": "already exists"})
            continue
        vendor = Vendor(
            name=name,
            email=row.get("email"),
            category=row.get("category"),
            rating=row.get("rating"),
        )
        db.add(vendor)
        created.append(vendor)

    db.commit()
    for v in created:
        db.refresh(v)

    return {
        "imported": len(created),
        "skipped": len(skipped),
        "vendor_ids": [v.id for v in created],
    }
