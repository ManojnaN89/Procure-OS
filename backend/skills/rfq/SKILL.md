# Skill Domain: RFQ (Request for Quotation)

Handles sending quote requests to vendors and comparing what comes back.

## draft_rfq
Use when the user wants to REQUEST QUOTES from vendors for items/services.
Trigger phrases: "send an RFQ", "ask vendors for a quote", "get pricing from suppliers"
Requires: title, items, vendor_ids

## record_quote
Use when a vendor's quote (price/delivery) needs to be logged against an RFQ.
Requires: rfq_id, vendor_id, price, delivery_days, notes (optional)
Automatically flags the quote if price is 30%+ above the category's historical average.

## compare_quotes
Use when the user wants to see/compare all quotes received for an RFQ.
Requires: rfq_id

## find_vendor_by_name
Use when a vendor is referenced by name (e.g. from an uploaded quote document) and you
need their vendor_id before calling another skill. Supports partial name matching.
Requires: name

## list_rfqs
Use whenever the user wants to see, list, or browse RFQs.
Optional: status
