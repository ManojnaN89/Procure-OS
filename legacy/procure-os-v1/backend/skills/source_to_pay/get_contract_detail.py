SKILL_NAME = "get_contract_detail"
SKILL_DESCRIPTION = (
    "Get full detail on a specific contract: title, vendor, terms, value, and date "
    "range. Use when the user asks about a specific contract's details. Read-only."
)
SKILL_PARAMETERS = {
    "type": "object",
    "properties": {"contract_id": {"type": "integer"}},
    "required": ["contract_id"],
}


def run(db, **kwargs):
    from models import Contract, Vendor

    c = db.get(Contract, kwargs["contract_id"])
    if not c:
        return {"error": f"Contract {kwargs['contract_id']} not found"}

    v = db.get(Vendor, c.vendor_id)
    return {
        "id": c.id, "title": c.title, "vendor_name": v.name if v else None,
        "terms": c.terms, "value": c.value,
        "start_date": c.start_date.strftime("%Y-%m-%d") if c.start_date else None,
        "end_date": c.end_date.strftime("%Y-%m-%d") if c.end_date else None,
        "terminated": bool(c.terminated), "termination_reason": c.termination_reason,
    }
