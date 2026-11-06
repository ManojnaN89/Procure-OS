SKILL_NAME = "compare_quotes"
SKILL_DESCRIPTION = "Compare all quotes received so far for a given RFQ, sorted by price (cheapest first)."
SKILL_PARAMETERS = {
    "type": "object",
    "properties": {"rfq_id": {"type": "integer"}},
    "required": ["rfq_id"],
}


def run(db, **kwargs):
    from models import Quote, Vendor
    quotes = db.query(Quote).filter(Quote.rfq_id == kwargs["rfq_id"]).order_by(Quote.price.asc()).all()

    details = []
    for q in quotes:
        vendor = db.get(Vendor, q.vendor_id)
        details.append({
            "quote_id": q.id, "vendor_id": q.vendor_id,
            "vendor_name": vendor.name if vendor else f"Vendor #{q.vendor_id}",
            "price": q.price, "delivery_days": q.delivery_days,
            "flagged": bool(getattr(q, "flagged", False)),
            "flag_reason": getattr(q, "flag_reason", None),
        })

    result = {"rfq_id": kwargs["rfq_id"], "quotes": details}
    if details:
        result["chart"] = {
            "chart_type": "bar",  # a comparison across vendors, not a part-of-whole split
            "title": f"RFQ #{kwargs['rfq_id']} quote comparison",
            "labels": [d["vendor_name"] for d in details],
            "datasets": [{"label": "Price (Rs.)", "data": [d["price"] for d in details]}],
        }
    return result
