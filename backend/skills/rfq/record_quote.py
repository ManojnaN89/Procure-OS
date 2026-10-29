SKILL_NAME = "record_quote"
SKILL_DESCRIPTION = "Record a vendor's quote (price, delivery time) received for an existing RFQ."
SKILL_PARAMETERS = {
    "type": "object",
    "properties": {
        "rfq_id": {"type": "integer"},
        "vendor_id": {"type": "integer"},
        "price": {"type": "number"},
        "delivery_days": {"type": "integer"},
        "notes": {"type": "string"},
    },
    "required": ["rfq_id", "vendor_id", "price", "delivery_days"],
}


def run(db, **kwargs):
    from models import Quote
    from intelligence import check_quote_exception

    flagged, reason = check_quote_exception(db, kwargs["vendor_id"], kwargs["price"])

    quote = Quote(
        rfq_id=kwargs["rfq_id"],
        vendor_id=kwargs["vendor_id"],
        price=kwargs["price"],
        delivery_days=kwargs["delivery_days"],
        notes=kwargs.get("notes", ""),
        flagged=flagged,
        flag_reason=reason,
    )
    db.add(quote)
    db.commit()
    db.refresh(quote)

    result = {"quote_id": quote.id, "rfq_id": quote.rfq_id, "vendor_id": quote.vendor_id, "price": quote.price}
    if flagged:
        result["flagged"] = True
        result["flag_reason"] = reason
    return result
