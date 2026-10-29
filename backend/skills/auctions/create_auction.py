SKILL_NAME = "create_auction"
SKILL_DESCRIPTION = (
    "Create a forward or reverse auction to source items from multiple vendors. "
    "Use when the user wants to run competitive bidding."
)
SKILL_PARAMETERS = {
    "type": "object",
    "properties": {
        "title": {"type": "string", "description": "Auction title"},
        "auction_type": {"type": "string", "enum": ["forward", "reverse"]},
        "items": {"type": "array", "items": {"type": "string"}, "description": "Items being auctioned"},
        "vendor_ids": {"type": "array", "items": {"type": "integer"}, "description": "Vendor IDs invited to bid"},
    },
    "required": ["title", "auction_type", "items", "vendor_ids"],
}


def run(db, **kwargs):
    from models import Auction
    auction = Auction(
        title=kwargs["title"],
        auction_type=kwargs["auction_type"],
        items=kwargs["items"],
        vendor_ids=kwargs["vendor_ids"],
        status="open",
    )
    db.add(auction)
    db.commit()
    db.refresh(auction)
    return {"auction_id": auction.id, "status": auction.status, "title": auction.title}
