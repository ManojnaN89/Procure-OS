SKILL_NAME = "cancel_auction"
SKILL_DESCRIPTION = (
    "Cancel an auction before it closes — e.g. wrong items, no longer needed. Use when "
    "the user wants to cancel an auction. Cannot cancel one that's already closed."
)
SKILL_PARAMETERS = {
    "type": "object",
    "properties": {
        "auction_id": {"type": "integer"},
        "reason": {"type": "string", "description": "Optional reason for the cancellation"},
    },
    "required": ["auction_id"],
}


def run(db, **kwargs):
    from models import Auction

    a = db.get(Auction, kwargs["auction_id"])
    if not a:
        return {"error": f"Auction {kwargs['auction_id']} not found"}
    if a.status == "closed":
        return {"error": f"Auction {a.id} is already closed and can't be cancelled"}
    if a.status == "cancelled":
        return {"auction_id": a.id, "already_cancelled": True}

    a.status = "cancelled"
    db.commit()
    return {"auction_id": a.id, "status": a.status, "reason": kwargs.get("reason")}
