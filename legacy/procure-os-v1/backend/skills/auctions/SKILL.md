# Skill Domain: Auctions

Handles forward and reverse auctions where multiple vendors compete on price.

## create_auction
Use when the user wants to START a new auction.
Trigger phrases: "start an auction", "run a reverse auction", "get vendors to bid on X"
Requires: title, auction_type (forward/reverse), items, vendor_ids

## submit_bid
Use when a vendor's bid needs to be recorded against an open auction.
Requires: auction_id, vendor_id, amount

## close_auction
Use when the user wants to CLOSE an auction and determine the winner.
For reverse auctions, lowest bid wins. For forward auctions, highest bid wins.
Requires: auction_id

## list_auctions
Use whenever the user wants to see, list, or browse auctions.
Optional: status ('open' or 'closed')
