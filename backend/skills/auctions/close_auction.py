SKILL_NAME = "close_auction"
SKILL_DESCRIPTION = (
    "Close an auction and determine the winning vendor from submitted bids. "
    "Lowest bid wins for reverse auctions, highest bid wins for forward auctions."
)
SKILL_PARAMETERS = {
    "type": "object",
    "properties": {"auction_id": {"type": "integer"}},
    "required": ["auction_id"],
}


def run(db, **kwargs):
    from models import Auction, Bid
    from datetime import datetime

    auction = db.get(Auction, kwargs["auction_id"])
    if not auction:
        return {"error": "Auction not found"}
    bids = db.query(Bid).filter(Bid.auction_id == auction.id).all()
    if not bids:
        return {"error": "No bids submitted yet"}

    winner = min(bids, key=lambda b: b.amount) if auction.auction_type == "reverse" else max(bids, key=lambda b: b.amount)
    auction.status = "closed"
    auction.winner_vendor_id = winner.vendor_id
    auction.closed_at = datetime.utcnow()
    db.commit()
    return {"auction_id": auction.id, "winner_vendor_id": winner.vendor_id, "winning_amount": winner.amount}
