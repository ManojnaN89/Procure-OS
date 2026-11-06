SKILL_NAME = "store_contract"
SKILL_DESCRIPTION = "Store a contract record for a vendor — terms, value, and date range."
SKILL_PARAMETERS = {
    "type": "object",
    "properties": {
        "vendor_id": {"type": "integer"},
        "title": {"type": "string"},
        "terms": {"type": "string"},
        "value": {"type": "number"},
        "start_date": {"type": "string", "description": "ISO date, e.g. 2026-01-01"},
        "end_date": {"type": "string", "description": "ISO date, e.g. 2026-12-31"},
    },
    "required": ["vendor_id", "title", "terms"],
}


def run(db, **kwargs):
    from datetime import datetime
    from models import Contract

    def parse_date(val):
        if not val:
            return None
        try:
            return datetime.fromisoformat(val)
        except ValueError:
            return None

    contract = Contract(
        vendor_id=kwargs["vendor_id"],
        title=kwargs["title"],
        terms=kwargs["terms"],
        value=kwargs.get("value"),
        start_date=parse_date(kwargs.get("start_date")),
        end_date=parse_date(kwargs.get("end_date")),
    )
    db.add(contract)
    db.commit()
    db.refresh(contract)
    return {"contract_id": contract.id, "vendor_id": contract.vendor_id, "title": contract.title}
