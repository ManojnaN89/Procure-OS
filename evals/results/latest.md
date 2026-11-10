# Evals report

Run on 2026-11-10 14:53; AI evals used a scripted model.

| ID | What it checks | Result | Seconds | Key figures |
|---|---|---|---|---|
| E18 | Goods receipt: tolerance, rejections, inspection, stock and order progress are right | **PASS** | 18.1 | order lines to receive 94; scenarios {'full': 17, 'over_limit': 15, 'partial_then_rest': 17, 'reject': 16, 'over_refused': 12, 'inspect': 17}; stock positions changed 94; receipts posted 111 |

**1 of 1 passed.**

## Details

### E18 Goods receipt: tolerance, rejections, inspection, stock and order progress are right

Checks: Plan: goods are received against a sent order; over-delivery is accepted up to the category's tolerance; rejected goods stay out of stock; items needing inspection wait; the order line and the stock follow what was accepted

- order lines to receive: 94
- scenarios: {'full': 17, 'over_limit': 15, 'partial_then_rest': 17, 'reject': 16, 'over_refused': 12, 'inspect': 17}
- stock positions changed: 94
- receipts posted: 111
