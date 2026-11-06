from datetime import datetime
from sqlalchemy import Column, Integer, String, Float, DateTime, ForeignKey, Text, JSON, Boolean
from db import Base


class Vendor(Base):
    __tablename__ = "vendors"
    id = Column(Integer, primary_key=True)
    name = Column(String, nullable=False)
    email = Column(String)
    category = Column(String)
    rating = Column(Float, default=0)
    created_at = Column(DateTime, default=datetime.utcnow)
    # A genuine human decision, not a computed state — recorded when someone
    # actively decides to keep watching a risky vendor. Deliberately does not
    # change the computed risk score itself (that would be dishonest — the
    # underlying risk isn't lower just because someone flagged it).
    under_review = Column(Boolean, default=False)
    # Closure — a settable status only, deliberately not enforced anywhere
    # else yet: an inactive vendor can still be used in a new PO or RFQ,
    # since blocking that is a separate change to those write paths, not
    # silently bundled into a status flag.
    active = Column(Boolean, default=True)
    # Verification — a human recording that they checked something outside
    # the system (a tax ID, a registration number). Not automated
    # compliance checking; there's no external lookup confirming the note
    # is actually true.
    verified = Column(Boolean, default=False)
    verification_note = Column(Text, nullable=True)


class Auction(Base):
    __tablename__ = "auctions"
    id = Column(Integer, primary_key=True)
    title = Column(String)
    auction_type = Column(String)  # forward | reverse
    items = Column(JSON)
    vendor_ids = Column(JSON)
    status = Column(String, default="open")
    winner_vendor_id = Column(Integer, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    closed_at = Column(DateTime, nullable=True)


class Bid(Base):
    __tablename__ = "bids"
    id = Column(Integer, primary_key=True)
    auction_id = Column(Integer, ForeignKey("auctions.id"))
    vendor_id = Column(Integer, ForeignKey("vendors.id"))
    amount = Column(Float)
    created_at = Column(DateTime, default=datetime.utcnow)


class RFQ(Base):
    __tablename__ = "rfqs"
    id = Column(Integer, primary_key=True)
    title = Column(String)
    items = Column(JSON)
    vendor_ids = Column(JSON)
    status = Column(String, default="sent")
    created_at = Column(DateTime, default=datetime.utcnow)


class Quote(Base):
    __tablename__ = "quotes"
    id = Column(Integer, primary_key=True)
    rfq_id = Column(Integer, ForeignKey("rfqs.id"))
    vendor_id = Column(Integer, ForeignKey("vendors.id"))
    price = Column(Float)
    delivery_days = Column(Integer)
    notes = Column(Text)
    flagged = Column(Boolean, default=False)  # true if price is an outlier vs. vendor/category history
    flag_reason = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class Requisition(Base):
    __tablename__ = "requisitions"
    id = Column(Integer, primary_key=True)
    title = Column(String)
    description = Column(Text)
    amount = Column(Float)
    requested_by = Column(String)
    status = Column(String, default="pending")  # pending | approved | rejected
    created_at = Column(DateTime, default=datetime.utcnow)
    decided_at = Column(DateTime, nullable=True)


class PurchaseOrder(Base):
    __tablename__ = "purchase_orders"
    id = Column(Integer, primary_key=True)
    vendor_id = Column(Integer, ForeignKey("vendors.id"))
    requisition_id = Column(Integer, ForeignKey("requisitions.id"), nullable=True)
    items = Column(JSON)
    amount = Column(Float)
    status = Column(String, default="created")  # created | received | paid
    expected_delivery_date = Column(DateTime, nullable=True)
    actual_delivery_date = Column(DateTime, nullable=True)  # null until delivered
    flagged = Column(Boolean, default=False)  # true if amount is an outlier vs. vendor/category history
    flag_reason = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class Payment(Base):
    __tablename__ = "payments"
    id = Column(Integer, primary_key=True)
    purchase_order_id = Column(Integer, ForeignKey("purchase_orders.id"))
    amount = Column(Float)
    status = Column(String, default="processed")
    created_at = Column(DateTime, default=datetime.utcnow)


class AgentTrailLog(Base):
    __tablename__ = "agent_trail_log"
    id = Column(Integer, primary_key=True)
    session_id = Column(String)
    agent = Column(String)
    skill = Column(String)
    args = Column(JSON)
    result = Column(JSON)
    created_at = Column(DateTime, default=datetime.utcnow)


class Budget(Base):
    """One row = one category's budget for one calendar month. Used for the
    budget-vs-actual chart; kept as a flat table rather than nested inside
    Requisition since a budget covers a whole category, not one request."""
    __tablename__ = "budgets"
    id = Column(Integer, primary_key=True)
    category = Column(String, nullable=False)
    month = Column(String, nullable=False)  # "YYYY-MM"
    budgeted_amount = Column(Float, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)


class Contract(Base):
    """Minimal contract repository — foundation for future negotiated-vs-paid
    comparison. Not required by any other feature in this phase."""
    __tablename__ = "contracts"
    id = Column(Integer, primary_key=True)
    vendor_id = Column(Integer, ForeignKey("vendors.id"))
    title = Column(String)
    terms = Column(Text)
    value = Column(Float, nullable=True)
    start_date = Column(DateTime, nullable=True)
    end_date = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    # A contract that ended early should be honestly distinguishable from
    # one that simply expired on schedule — a dedicated field rather than
    # quietly overloading end_date to mean two different things.
    terminated = Column(Boolean, default=False)
    termination_reason = Column(Text, nullable=True)


class Notification(Base):
    """A proactively-detected signal (Phase H2). 'read' = user has seen it;
    'resolved' = the underlying condition is no longer true, checked
    automatically each scheduler cycle for most types (exception_flagged
    is the one type that never auto-resolves — see signals.py)."""
    __tablename__ = "notifications"
    id = Column(Integer, primary_key=True)
    type = Column(String, nullable=False)  # exception_flagged | vendor_risk | stale_requisition | budget_overrun | contract_expiring
    severity = Column(String, nullable=False)  # high | medium | low
    message = Column(String, nullable=False)
    related_type = Column(String, nullable=True)  # vendor | requisition | budget | contract | rfq | purchase_order
    related_id = Column(Integer, nullable=True)
    read = Column(Boolean, default=False)
    resolved = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    resolved_at = Column(DateTime, nullable=True)


class Setting(Base):
    """Key/value configuration store — deliberately generic rather than one
    column per threshold, so a new configurable value later doesn't need
    another schema migration. Same philosophy Suplari itself describes:
    configurable thresholds/definitions, not the underlying scoring math."""
    __tablename__ = "settings"
    key = Column(String, primary_key=True)
    value = Column(String, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
