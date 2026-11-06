SKILL_NAME = "submit_bid"
SKILL_DESCRIPTION = "Submit a vendor's bid into an open auction."
SKILL_PARAMETERS = {
    "type": "object",
    "properties": {
        "auction_id": {"type": "integer"},
        "vendor_id": {"type": "integer"},
        "amount": {"type": "number"},
    },
    "required": ["auction_id", "vendor_id", "amount"],
}


def run(db, **kwargs):
    from models import Bid
    bid = Bid(auction_id=kwargs["auction_id"], vendor_id=kwargs["vendor_id"], amount=kwargs["amount"])
    db.add(bid)
    db.commit()
    db.refresh(bid)
    return {"bid_id": bid.id, "auction_id": bid.auction_id, "vendor_id": bid.vendor_id, "amount": bid.amount}
