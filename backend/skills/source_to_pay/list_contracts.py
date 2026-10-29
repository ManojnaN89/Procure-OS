SKILL_NAME = "list_contracts"
SKILL_DESCRIPTION = (
    "List every stored contract with its ID, title, vendor, value, and end date. Use "
    "whenever the user wants to see, list, or browse contracts. Read-only."
)
SKILL_PARAMETERS = {
    "type": "object",
    "properties": {
        "expiring_soon_only": {"type": "boolean", "description": "Optional — true to show only contracts ending within 30 days"},
    },
    "required": [],
}


def run(db, **kwargs):
    from datetime import datetime, timedelta
    from models import Contract, Vendor

    rows = db.query(Contract).order_by(Contract.end_date.asc()).all()
    if kwargs.get("expiring_soon_only"):
        horizon = datetime.utcnow() + timedelta(days=30)
        rows = [c for c in rows if c.end_date and c.end_date <= horizon]

    out = []
    for c in rows:
        v = db.get(Vendor, c.vendor_id)
        out.append({
            "id": c.id, "title": c.title, "vendor_name": v.name if v else None,
            "value": c.value, "end_date": c.end_date.strftime("%Y-%m-%d") if c.end_date else None,
            "terminated": bool(c.terminated),
        })
    return {"count": len(out), "contracts": out}
