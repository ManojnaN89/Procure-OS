"""
Shared intelligence logic used by skills (exception flagging at write time)
and by reads.py (vendor risk score at read time). Kept in one place so the
"what counts as an outlier" definition can't drift between the two.

Thresholds are read from settings.py (configurable via the Configuration
page) rather than hardcoded, but the underlying scoring formula stays
fixed in code — see settings.py's module docstring for why.
"""
from settings import get_setting_float, get_setting_int


def _flag_threshold():
    return 1 + (get_setting_float("exception_flag_threshold_pct") / 100)


def _category_baseline(db, category, exclude_quote_id=None, exclude_po_id=None):
    """Average price across all quotes AND purchase orders in a category,
    since both represent 'what we've paid this category before'."""
    from models import Quote, Vendor, PurchaseOrder

    quotes = (
        db.query(Quote)
        .join(Vendor, Quote.vendor_id == Vendor.id)
        .filter(Vendor.category == category)
    )
    if exclude_quote_id:
        quotes = quotes.filter(Quote.id != exclude_quote_id)
    quote_prices = [q.price for q in quotes.all()]

    pos = (
        db.query(PurchaseOrder)
        .join(Vendor, PurchaseOrder.vendor_id == Vendor.id)
        .filter(Vendor.category == category)
    )
    if exclude_po_id:
        pos = pos.filter(PurchaseOrder.id != exclude_po_id)
    po_amounts = [p.amount for p in pos.all()]

    all_values = quote_prices + po_amounts
    if not all_values:
        return None
    return sum(all_values) / len(all_values)


def check_quote_exception(db, vendor_id, price, exclude_quote_id=None):
    """Returns (flagged: bool, reason: str|None) for a quote amount."""
    from models import Vendor

    vendor = db.get(Vendor, vendor_id)
    if not vendor or not vendor.category:
        return False, None
    baseline = _category_baseline(db, vendor.category, exclude_quote_id=exclude_quote_id)
    if not baseline or baseline <= 0:
        return False, None
    if price > baseline * _flag_threshold():
        pct = round((price / baseline - 1) * 100)
        return True, f"Price {pct}% above {vendor.category} category average"
    return False, None


def check_po_exception(db, vendor_id, amount, exclude_po_id=None):
    """Returns (flagged: bool, reason: str|None) for a purchase order amount."""
    from models import Vendor

    vendor = db.get(Vendor, vendor_id)
    if not vendor or not vendor.category:
        return False, None
    baseline = _category_baseline(db, vendor.category, exclude_po_id=exclude_po_id)
    if not baseline or baseline <= 0:
        return False, None
    if amount > baseline * _flag_threshold():
        pct = round((amount / baseline - 1) * 100)
        return True, f"Amount {pct}% above {vendor.category} category average"
    return False, None


def compute_vendor_risk(db, vendor_id):
    """
    Self-derived risk score (0-100) from ProcureOS's own transaction history only —
    NOT third-party risk data. Combines:
      - late delivery rate (0-50 points)
      - price volatility across quotes, coefficient of variation (0-30 points)
      - count of flagged exceptions (10 points each, capped)
    """
    from models import PurchaseOrder, Quote

    pos = db.query(PurchaseOrder).filter(PurchaseOrder.vendor_id == vendor_id).all()
    delivered = [p for p in pos if p.expected_delivery_date and p.actual_delivery_date]
    late = [p for p in delivered if p.actual_delivery_date > p.expected_delivery_date]
    late_rate = (len(late) / len(delivered)) if delivered else 0.0

    quotes = db.query(Quote).filter(Quote.vendor_id == vendor_id).all()
    prices = [q.price for q in quotes]
    if len(prices) >= 2:
        mean_price = sum(prices) / len(prices)
        variance = sum((p - mean_price) ** 2 for p in prices) / len(prices)
        stdev = variance ** 0.5
        volatility = (stdev / mean_price) if mean_price else 0.0
    else:
        volatility = 0.0

    flagged_count = sum(1 for q in quotes if q.flagged) + sum(1 for p in pos if p.flagged)

    score = round(
        min(late_rate, 1.0) * 50
        + min(volatility, 1.0) * 30
        + min(flagged_count, 3) * 10
    )
    score = min(score, 100)

    if score < get_setting_int("risk_score_medium_cutoff"):
        level = "low"
    elif score < get_setting_int("risk_score_high_cutoff"):
        level = "medium"
    else:
        level = "high"

    return {
        "risk_score": score,
        "risk_level": level,
        "late_delivery_rate_pct": round(late_rate * 100),
        "price_volatility_pct": round(volatility * 100),
        "flagged_incidents": flagged_count,
        "basis": "Derived from this vendor's own order and quote history in ProcureOS \u2014 not third-party risk data.",
    }
