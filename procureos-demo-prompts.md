## 1. Catch a bad price automatically

Attach `sample-files/vertex-electronics-quote.pdf`, then type:

> Record this quote from Vertex Electronics (vendor 6) on RFQ #2.

- **Expect:** the quote (₹5,88,000, 45 days) is saved and flagged as **46% above the Electronics category average**. Within 2 minutes a new item appears in "Needs attention" on its own.
- **Shows:** the AI reading a file, the price rule firing, and background monitoring.
- **Why RFQ #2:** RFQ #1 already has an unresolved alert, so the system wouldn't raise a second one there.

## 2. Compare and decide

> Compare all quotes on RFQ #2 and recommend which vendor I should go with.

- **Expect:** a bar chart of the quotes:

  | Vendor             | Price     | Delivery                       |
  | ------------------ | --------- | ------------------------------ |
  | Summit Electronics | ₹3,99,900 | 11 days (cheapest and fastest) |
  | Acme Supplies      | ₹4,13,600 | 15 days                        |
  | Vertex Electronics | ₹4,52,600 | 12 days                        |
  | Vertex Electronics | ₹5,88,000 | 45 days (flagged)              |

- **Shows:** the AI turning data into a recommendation.

## 3. Spot risky vendors

> Which of my vendors are the riskiest right now, and why?

- **Expect:**
  - **Meridian Raw Materials** scores 52 (medium) because 100% of its deliveries were late.
  - **Acme Supplies** scores 28 (medium): 25% late deliveries plus 1 flagged quote.
  - All other vendors are low.
- **Shows:** the risk score, which is built from your own order history (delays, price swings, flags), not third-party data.

## 4. Act, and get warned

> Approve requisition #2 and convert it to a purchase order with Summit Electronics (vendor 11) for monitors. Tell me if anything gets flagged.

- **Expect:** it approves requisition #2 and creates the PO. The PO is flagged as **about 67% above the Electronics category average** (₹6,89,400).
- **Shows:** a real action, and the rule checking it as it happens.
- **Why say vendor 11:** the Requisition Agent has no tool to look a vendor up by name.

## 5. Explain spend

> Why has my spending gone up, and are any categories over budget?

- **Expect:** a monthly spend chart, plus a budget check. **No category is over budget** this month, and the AI should say so rather than invent one.
- **Shows:** spend analysis, and honesty when there's nothing wrong.
- **Dates:** the months on the chart depend on when the database was seeded, so the exact figures aren't listed here.

---

## Talking point

The rules **spot** problems (price flags, risk scores, stuck approvals, budget overruns, expiring contracts) and run every 2 minutes. The AI (gpt-oss-120b on Groq) **explains** them and **acts** on them through conversation, and every action is logged in the Agent Trail.
