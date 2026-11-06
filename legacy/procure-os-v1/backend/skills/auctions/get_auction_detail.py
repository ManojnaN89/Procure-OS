SKILL_NAME = "get_auction_detail"
SKILL_DESCRIPTION = (
    "Get full detail on a specific auction: title, type, status, items, and every bid "
    "placed with vendor and amount, plus the winner if closed. Use when the user asks "
    "about a specific auction by ID — its status, who's bidding, who won. Read-only."
)
SKILL_PARAMETERS = {
    "type": "object",
    "properties": {"auction_id": {"type": "integer"}},
    "required": ["auction_id"],
}


def run(db, **kwargs):
    from models import Auction, Bid, Vendor

    a = db.get(Auction, kwargs["auction_id"])
    if not a:
        return {"error": f"Auction {kwargs['auction_id']} not found"}

    bids = db.query(Bid).filter(Bid.auction_id == a.id).order_by(Bid.amount.asc()).all()
    winner = db.get(Vendor, a.winner_vendor_id) if a.winner_vendor_id else None

    bid_list = []
    for b in bids:
        v = db.get(Vendor, b.vendor_id)
        bid_list.append({"vendor_name": v.name if v else None, "amount": b.amount})

    return {
        "id": a.id, "title": a.title, "auction_type": a.auction_type, "status": a.status,
        "items": a.items, "winner_vendor_name": winner.name if winner else None,
        "bids": bid_list,
    }
