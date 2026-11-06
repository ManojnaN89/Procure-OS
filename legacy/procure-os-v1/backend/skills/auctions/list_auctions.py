SKILL_NAME = "list_auctions"
SKILL_DESCRIPTION = (
    "List every auction with its ID, title, type, status, and winner if closed. Use "
    "whenever the user wants to see, list, or browse auctions. Read-only."
)
SKILL_PARAMETERS = {
    "type": "object",
    "properties": {
        "status": {"type": "string", "description": "Optional — filter to 'open' or 'closed'"},
    },
    "required": [],
}


def run(db, **kwargs):
    from models import Auction

    q = db.query(Auction)
    if kwargs.get("status"):
        q = q.filter(Auction.status == kwargs["status"])
    rows = q.order_by(Auction.created_at.desc()).all()

    return {"count": len(rows), "auctions": [{
        "id": a.id, "title": a.title, "auction_type": a.auction_type,
        "status": a.status, "winner_vendor_id": a.winner_vendor_id,
    } for a in rows]}
