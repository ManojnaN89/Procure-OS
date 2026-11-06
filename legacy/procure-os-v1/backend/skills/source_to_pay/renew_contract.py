SKILL_NAME = "renew_contract"
SKILL_DESCRIPTION = (
    "Renew a contract by updating its end date, and optionally its value or terms. Use "
    "when the user wants to renew, extend, or update an existing contract. This is a real "
    "record update — once the new end date is far enough out, the expiring-contract "
    "notification for it resolves automatically on the next check, the same way it "
    "would if you'd never renewed it in the first place; no separate dismiss needed."
)
SKILL_PARAMETERS = {
    "type": "object",
    "properties": {
        "contract_id": {"type": "integer"},
        "new_end_date": {"type": "string", "description": "New end date, format YYYY-MM-DD"},
        "new_value": {"type": "number", "description": "Optional new contract value"},
        "new_terms": {"type": "string", "description": "Optional updated terms text"},
    },
    "required": ["contract_id", "new_end_date"],
}


def run(db, **kwargs):
    from datetime import datetime
    from models import Contract

    c = db.get(Contract, kwargs["contract_id"])
    if not c:
        return {"error": f"Contract {kwargs['contract_id']} not found"}

    try:
        new_end = datetime.strptime(kwargs["new_end_date"], "%Y-%m-%d")
    except ValueError:
        return {"error": "new_end_date must be in YYYY-MM-DD format"}

    c.end_date = new_end
    if kwargs.get("new_value") is not None:
        c.value = kwargs["new_value"]
    if kwargs.get("new_terms"):
        c.terms = kwargs["new_terms"]
    db.commit()

    return {
        "contract_id": c.id, "title": c.title,
        "new_end_date": c.end_date.strftime("%Y-%m-%d"),
        "value": c.value,
    }
