# Demo Guide

---

## Reset and reseed the database

```
psql -U postgres -h localhost -c "DROP DATABASE procureos;"
psql -U postgres -h localhost -c "CREATE DATABASE procureos;"
```
Start the server:
```
uvicorn app:app --reload --port 8000
```
In a second terminal, same folder:
```
python seed.py
```
This creates 12 seeded vendors (IDs 1–12). A newly onboarded vendor will
land on ID 13.

---

## Vendor lifecycle — end to end, starting from a new vendor

| Question | Purpose |
|---|---|
| "Onboard a vendor called Everline Logistics, email ops@everlinelogistics.com, category Facilities" | Create a new supplier record |
| "List all vendors" | Confirm the new vendor (ID 13) appears |
| "Mark vendor 13 as verified, tax ID is GST-2024-11234" | Record a verification decision |
| "Tell me about vendor 13" | Full profile in one answer |
| "What's the risk score for vendor 13?" | Confirm a clean baseline for a brand-new vendor |
| "What does vendor 13 still owe?" | Confirm nothing owed yet, no orders exist |
| "Create a purchase order for vendor 13, items: warehouse racking installation, amount 280000" | Generate a real transaction |
| "What does vendor 13 still owe?" | Confirm the new order now shows up |
| "Flag vendor 13 for review" | Mark for closer watch |
| "Remove vendor 13 from review" | Clear that flag |
| "Deactivate vendor 13, the relationship has ended" | Close the vendor out |
| "List all vendors" | Confirm it's still listed, marked inactive, not deleted |
| "Reactivate vendor 13" | Bring it back |

### Purchase order adjust / cancel, using the order just created

| Question | Purpose |
|---|---|
| "What's the status of purchase order [id]?" | Check current state and flag status |
| "Adjust purchase order [id] to amount 90000" | Correct a flagged amount, flag re-checks automatically |
| "Create a purchase order for vendor 13, items: extra logistics support, amount 300000" | Create a second order, likely flagged again |
| "Cancel that purchase order" | Withdraw it entirely |
| "Adjust that cancelled purchase order to amount 50000" | Confirm this gets refused, not silently allowed |

---

## Auctions

| Question | Purpose |
|---|---|
| "List all auctions" | Browse current auctions |
| "Start a reverse auction called Q4 Packaging Deal for cardboard boxes, vendors 2 and 7" | Create a new auction |
| "What's the status of auction [id]?" | Full detail, including bids |
| "Cancel auction [id]" | Withdraw before it closes |

---

## RFQs

| Question | Purpose |
|---|---|
| "List all RFQs" | Browse current RFQs |
| "Draft an RFQ for 100 office chairs to vendors 3 and 8" | Create a new request for quotation |
| "Compare quotes for RFQ 1" | Side-by-side price comparison, cheapest highlighted |
| "Cancel RFQ [id]" | Close out without accepting any quote |

---

## Requisitions

| Question | Purpose |
|---|---|
| "List pending requisitions" | Filtered browse |
| "Create a requisition for a new laptop, amount 85000, requested by Priya" | Raise a new internal request |
| "Adjust requisition [id] to amount 95000" | Correct the amount before a decision is made |
| "Approve requisition 2" | Make a decision on a known pending request |
| "Cancel requisition [id]" | Withdraw a request entirely |

---

## Spend analytics

| Question | Purpose |
|---|---|
| "Why has spending gone up?" | Trend analysis with an inline chart |
| "Show me spend by category" | Category breakdown |
| "Compare vendor 1 and vendor 6 by spend" | Side-by-side spend comparison |
| "Give me a full summary of the dashboard" | One consolidated overview, charts included |

---

## Proactive monitoring

| Question | Purpose |
|---|---|
| "Anything I should know?" | On-demand check for unresolved issues, any time, not just at launch |
| "What have you done recently?" | Recent activity log, in conversation |
| "What's the current flag threshold?" | Current configuration, without opening a settings page |

---

## Contracts

| Question | Purpose |
|---|---|
| "List all contracts" | Browse stored contracts |
| "Store a contract for vendor 13 titled Annual Facilities Agreement, terms net-30, value 1200000, end date 2026-12-31" | Record new contract terms |
| "Renew contract [id] with a new end date of 2027-12-31" | Extend an existing contract |
| "Terminate contract [id], reason: relationship ended early" | End one ahead of schedule, distinct from natural expiry |

---

## Focus Mode

Toggle Focus Mode on from the top bar. A full-screen, distraction-free
conversation view — no dashboard, no sidebar. Ask any of the questions
above from inside it; the same answers, charts, and actions apply.
Press Escape to exit — no button, by design.
