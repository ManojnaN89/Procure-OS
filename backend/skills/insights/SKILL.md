# Skill Domain: Insights

Read-only analytical skills. These never write data — they query existing
Postgres data and return a chart-ready payload alongside a plain summary,
so the assistant can answer analytical questions ("why did spending
increase") with a visual, not just prose.

## get_spend_trend
Use when the user asks about spend over time — trends, increases, comparisons
across months. Returns monthly totals as a line chart.
Optional: months (default 6)

## get_category_breakdown
Use when the user asks how spend splits across categories, or which category
is driving cost. Returns a bar chart of spend by category.

## compare_vendors
Use when the user wants to compare two or more specific vendors by spend.
Requires: vendor_ids (list of vendor IDs)

## get_category_detail
Use when the user asks about one specific category by name. Returns that category's
vendors and their individual spend, as a bar chart.
Requires: category

## get_budget_variance
Use for any question about budget vs. actual spend, or which categories are over/under
budget. Returns the most recent month's variance by category, as a bar chart.

## get_dashboard_summary
Use when the user asks for a general overview or summary, not a narrow question about
one category or vendor. Returns total spend, savings, exceptions, and status counts
across auctions/RFQs/requisitions/vendors/POs in one call.

## get_vendor_detail
Use when the user asks about a specific vendor by name or ID — their risk, rating,
status, or a general "tell me about this vendor" question.
Requires: vendor_id

## get_vendor_pending_bills
Use when the user asks what a vendor is still owed or what bills are pending for them,
without a specific PO number already known.
Requires: vendor_id

## list_vendors / list_auctions / list_rfqs / list_requisitions / list_purchase_orders / list_payments / list_contracts
Use whenever the user wants to see, list, or browse any records — including to find a
record's ID before doing something else with it. Each is read-only and takes optional
filters (see each domain's own SKILL.md for specifics).
