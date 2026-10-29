"""
Seeds ProcureOS with 6 months of realistic history so the intelligence
features have something real to compute against:
  - exception flagging needs a price baseline per vendor/category
  - vendor risk score needs some late deliveries and price variance
  - the spend-trend and budget-vs-actual charts need data spread over time
  - the dashboard/status breakdowns need a healthy mix of statuses

Run this AFTER init_db() has created the schema. Safe to re-run against an
empty database; does not check for existing data, so don't run it twice
without wiping the tables first.

Usage:
    cd backend
    python seed.py
"""
import random
from datetime import datetime, timedelta

from db import init_db, SessionLocal
from models import (
    Vendor, Auction, Bid, RFQ, Quote, Requisition,
    PurchaseOrder, Payment, AgentTrailLog, Budget, Contract,
)

random.seed(42)  # reproducible seed data across runs

TODAY = datetime.utcnow()

CATEGORIES = ["Electronics", "Packaging", "Facilities", "IT Services", "Raw Materials"]

VENDORS = [
    ("Acme Supplies", "acme@supplies.com", "Electronics", 4.3),
    ("Delta Traders", "delta@traders.com", "Packaging", 3.9),
    ("Nimbus Facilities Co", "hello@nimbusfac.com", "Facilities", 4.1),
    ("Orion IT Services", "contact@orionit.com", "IT Services", 4.6),
    ("Meridian Raw Materials", "sales@meridianrm.com", "Raw Materials", 3.7),
    ("Vertex Electronics", "info@vertexelec.com", "Electronics", 4.0),
    ("Blue Harbor Packaging", "team@blueharbor.com", "Packaging", 3.5),
    ("Ridgeline Facilities", "ops@ridgelinefac.com", "Facilities", 4.4),
    ("Cobalt IT Partners", "hi@cobaltit.com", "IT Services", 3.8),
    ("Foundry Raw Materials", "sales@foundryrm.com", "Raw Materials", 4.2),
    ("Summit Electronics", "orders@summitelec.com", "Electronics", 4.5),
    ("Harborline Packaging", "info@harborline.com", "Packaging", 4.0),
]

# Roughly realistic base unit prices per category, used to generate believable quote/PO amounts
CATEGORY_BASE_PRICE = {
    "Electronics": 420000,
    "Packaging": 85000,
    "Facilities": 150000,
    "IT Services": 260000,
    "Raw Materials": 310000,
}

ITEM_NAMES = {
    "Electronics": ["laptops", "monitors", "networking switches", "servers"],
    "Packaging": ["cardboard boxes", "packing tape", "pallet wrap", "labels"],
    "Facilities": ["office furniture", "HVAC maintenance", "cleaning contract", "security services"],
    "IT Services": ["cloud hosting", "helpdesk support", "software licenses", "network monitoring"],
    "Raw Materials": ["steel sheets", "resin pellets", "aluminum stock", "industrial adhesives"],
}

REQUESTERS = ["Priya", "Arjun", "Meera", "Kabir", "Ananya", "Rohan"]


def months_back(n):
    """Returns a datetime roughly n months before today, with a randomized day offset."""
    return TODAY - timedelta(days=30 * n + random.randint(0, 25))


def seed():
    init_db()
    db = SessionLocal()

    # ---- Vendors ----
    vendors = []
    for name, email, category, rating in VENDORS:
        v = Vendor(name=name, email=email, category=category, rating=rating,
                    created_at=months_back(7))
        db.add(v)
        vendors.append(v)
    db.commit()
    for v in vendors:
        db.refresh(v)

    vendors_by_category = {}
    for v in vendors:
        vendors_by_category.setdefault(v.category, []).append(v)

    # ---- Historical RFQs + Quotes (with one deliberate price outlier) ----
    rfqs = []
    quotes = []
    for category, cat_vendors in vendors_by_category.items():
        base_price = CATEGORY_BASE_PRICE[category]
        for i in range(3):  # 3 RFQs per category over the past 6 months
            item = random.choice(ITEM_NAMES[category])
            rfq = RFQ(
                title=f"{item.title()} sourcing round {i + 1}",
                items=[item],
                vendor_ids=[v.id for v in cat_vendors],
                status="sent",
                created_at=months_back(6 - i * 2),
            )
            db.add(rfq)
            db.commit()
            db.refresh(rfq)
            rfqs.append(rfq)

            for v in cat_vendors:
                # normal variance: +/- 15% of category base price
                price = round(base_price * random.uniform(0.85, 1.15), -2)
                quotes.append(Quote(
                    rfq_id=rfq.id, vendor_id=v.id, price=price,
                    delivery_days=random.randint(7, 30),
                    notes="", flagged=False,
                    created_at=rfq.created_at + timedelta(days=random.randint(1, 5)),
                ))

    # deliberate outlier: one quote at 40% above category base price, clearly flaggable
    outlier_vendor = vendors_by_category["Electronics"][0]
    outlier_rfq = next(r for r in rfqs if "laptops sourcing round 1" in r.title.lower()
                        or r.vendor_ids and outlier_vendor.id in r.vendor_ids)
    quotes.append(Quote(
        rfq_id=outlier_rfq.id, vendor_id=outlier_vendor.id,
        price=round(CATEGORY_BASE_PRICE["Electronics"] * 1.4, -2),
        delivery_days=45, notes="Rush order surcharge applied",
        flagged=True, flag_reason="Price 40% above vendor/category historical average",
        created_at=TODAY - timedelta(days=3),
    ))

    db.add_all(quotes)
    db.commit()
    flagged_quote_count = sum(1 for q in quotes if q.flagged)

    # ---- Historical Requisitions ----
    requisitions = []
    for i in range(24):
        category = random.choice(CATEGORIES)
        item = random.choice(ITEM_NAMES[category])
        amount = round(CATEGORY_BASE_PRICE[category] * random.uniform(0.5, 1.8), -2)
        status = random.choices(["approved", "rejected", "pending"], weights=[65, 15, 20])[0]
        created = months_back(random.randint(0, 6))
        req = Requisition(
            title=f"{item.title()} procurement request",
            description=f"Requesting {item} for ongoing operations.",
            amount=amount,
            requested_by=random.choice(REQUESTERS),
            status=status,
            created_at=created,
            decided_at=created + timedelta(days=random.randint(1, 5)) if status != "pending" else None,
        )
        db.add(req)
        requisitions.append(req)
    db.commit()
    for r in requisitions:
        db.refresh(r)

    # ---- Historical Purchase Orders (some linked to approved requisitions, some direct) ----
    purchase_orders = []
    approved_reqs = [r for r in requisitions if r.status == "approved"]

    for req in approved_reqs:
        category = next((c for c in CATEGORIES if c.lower() in req.title.lower()
                          or any(it in req.title.lower() for it in ITEM_NAMES[c])), random.choice(CATEGORIES))
        vendor = random.choice(vendors_by_category[category])
        expected = req.decided_at + timedelta(days=random.randint(7, 21))
        # ~20% of POs are late; a couple of vendors are deliberately made consistently late
        is_late_vendor = vendor.name in ("Blue Harbor Packaging", "Meridian Raw Materials")
        late = is_late_vendor or random.random() < 0.12
        actual = expected + timedelta(days=random.randint(3, 12)) if late else expected - timedelta(days=random.randint(0, 2))
        status = random.choices(["created", "paid"], weights=[30, 70])[0]

        po = PurchaseOrder(
            vendor_id=vendor.id, requisition_id=req.id,
            items=[random.choice(ITEM_NAMES[category])], amount=req.amount,
            status=status,
            expected_delivery_date=expected,
            actual_delivery_date=actual if actual <= TODAY else None,
            flagged=False,
            created_at=req.decided_at,
        )
        db.add(po)
        purchase_orders.append(po)

    # a handful of direct POs not linked to any requisition
    for _ in range(4):
        category = random.choice(CATEGORIES)
        vendor = random.choice(vendors_by_category[category])
        amount = round(CATEGORY_BASE_PRICE[category] * random.uniform(0.6, 1.3), -2)
        created = months_back(random.randint(0, 5))
        expected = created + timedelta(days=random.randint(7, 21))
        po = PurchaseOrder(
            vendor_id=vendor.id, requisition_id=None,
            items=[random.choice(ITEM_NAMES[category])], amount=amount,
            status="paid", expected_delivery_date=expected, actual_delivery_date=expected,
            flagged=False, created_at=created,
        )
        db.add(po)
        purchase_orders.append(po)

    db.commit()
    for po in purchase_orders:
        db.refresh(po)

    # ---- Payments for paid POs ----
    payments = []
    for po in purchase_orders:
        if po.status == "paid":
            payments.append(Payment(
                purchase_order_id=po.id, amount=po.amount, status="processed",
                created_at=po.created_at + timedelta(days=random.randint(5, 15)),
            ))
    db.add_all(payments)

    # ---- Budgets: monthly per category, past 6 months ----
    budgets = []
    for category in CATEGORIES:
        base = CATEGORY_BASE_PRICE[category]
        for m in range(6):
            month_dt = TODAY - timedelta(days=30 * m)
            budgets.append(Budget(
                category=category,
                month=month_dt.strftime("%Y-%m"),
                budgeted_amount=round(base * random.uniform(2.2, 3.0), -2),
                created_at=months_back(6),
            ))
    db.add_all(budgets)

    # ---- One sample auction, open, with a couple of bids ----
    auction_vendors = vendors_by_category["Packaging"]
    auction = Auction(
        title="Packaging materials Q4 sourcing",
        auction_type="reverse",
        items=["cardboard boxes", "packing tape"],
        vendor_ids=[v.id for v in auction_vendors],
        status="open",
        created_at=TODAY - timedelta(days=2),
    )
    db.add(auction)
    db.commit()
    db.refresh(auction)
    db.add_all([
        Bid(auction_id=auction.id, vendor_id=auction_vendors[0].id, amount=82000,
            created_at=TODAY - timedelta(days=1)),
        Bid(auction_id=auction.id, vendor_id=auction_vendors[1].id, amount=79500,
            created_at=TODAY - timedelta(hours=6)),
    ])

    # ---- A couple of sample contracts (foundation only, not deeply used yet) ----
    db.add_all([
        Contract(
            vendor_id=vendors_by_category["IT Services"][0].id,
            title="Cloud hosting master agreement",
            terms="Annual cloud hosting agreement, net-30 payment terms, 5% volume discount above ₹20L/quarter.",
            value=2600000,
            start_date=months_back(6), end_date=TODAY + timedelta(days=180),
        ),
        Contract(
            vendor_id=vendors_by_category["Facilities"][0].id,
            title="Facilities maintenance contract",
            terms="Quarterly HVAC and cleaning services, net-45 payment terms.",
            value=600000,
            start_date=months_back(5), end_date=TODAY + timedelta(days=270),
        ),
    ])

    # ---- A handful of Agent Trail entries so the drawer/ticker isn't empty on first load ----
    db.add_all([
        AgentTrailLog(session_id="seed-demo", agent="RFQ Agent", skill="draft_rfq",
                       args={"title": "Cloud hosting renewal"}, result={"rfq_id": rfqs[0].id, "status": "sent"},
                       created_at=TODAY - timedelta(hours=3)),
        AgentTrailLog(session_id="seed-demo", agent="Requisition Agent", skill="decide_requisition",
                       args={"requisition_id": approved_reqs[0].id, "decision": "approved"},
                       result={"status": "approved"}, created_at=TODAY - timedelta(hours=2)),
        AgentTrailLog(session_id="seed-demo", agent="Auction Agent", skill="submit_bid",
                       args={"auction_id": auction.id, "amount": 79500},
                       result={"status": "recorded"}, created_at=TODAY - timedelta(hours=1)),
    ])

    vendor_count = len(vendors)
    rfq_count = len(rfqs)
    quote_count = len(quotes)
    requisition_count = len(requisitions)
    po_count = len(purchase_orders)
    payment_count = len(payments)
    budget_count = len(budgets)

    db.commit()
    db.close()

    print(f"Seeded: {vendor_count} vendors, {rfq_count} RFQs, {quote_count} quotes "
          f"({flagged_quote_count} flagged), {requisition_count} requisitions, "
          f"{po_count} purchase orders, {payment_count} payments, "
          f"{budget_count} budget rows, 1 auction, 2 contracts.")


if __name__ == "__main__":
    seed()
