SKILL_NAME = "terminate_contract"
SKILL_DESCRIPTION = (
    "End a contract early, before its natural end date — e.g. ending the relationship "
    "ahead of schedule. Use when the user wants to terminate a contract, not extend one "
    "(use renew_contract for that). This is recorded separately from the contract's end "
    "date, so a contract terminated early stays honestly distinguishable from one that "
    "simply expired on schedule."
)
SKILL_PARAMETERS = {
    "type": "object",
    "properties": {
        "contract_id": {"type": "integer"},
        "reason": {"type": "string", "description": "Reason for terminating early"},
    },
    "required": ["contract_id", "reason"],
}


def run(db, **kwargs):
    from models import Contract

    c = db.get(Contract, kwargs["contract_id"])
    if not c:
        return {"error": f"Contract {kwargs['contract_id']} not found"}
    if c.terminated:
        return {"contract_id": c.id, "already_terminated": True}

    c.terminated = True
    c.termination_reason = kwargs["reason"]
    db.commit()
    return {"contract_id": c.id, "title": c.title, "terminated": True, "reason": c.termination_reason}
