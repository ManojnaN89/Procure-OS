# Skill Domain: Source-to-Pay

Handles vendor onboarding, direct PO creation, and payments.

## onboard_vendor
Use when the user wants to ADD a new vendor to the system.
Requires: name, email, category

## create_purchase_order
Use when the user wants to create a PO directly (outside the requisition flow).
Requires: vendor_id, items, amount
Automatically flags the PO if amount is 30%+ above the category's historical average.

## process_payment
Use when the user wants to PAY a purchase order (goods/services received).
Requires: purchase_order_id, amount

## import_vendors_csv
Use after a CSV file of vendors has been uploaded and parsed into rows — bulk-creates
them in one call. For a single vendor, use onboard_vendor instead.
Requires: vendors (list of {name, email, category, rating})

## store_contract
Use when the user wants to record a contract's terms, value, and date range for a vendor.
Requires: vendor_id, title, terms. Optional: value, start_date, end_date

## renew_contract
Use when the user wants to renew, extend, or update an existing contract's end date.
Requires: contract_id, new_end_date. Optional: new_value, new_terms

## adjust_budget
Use when the user wants to raise or lower a category's budgeted amount for a month.
Requires: category, month, new_budgeted_amount

## set_vendor_review_status
Use when the user wants to flag a risky vendor for active review, or remove that flag.
Requires: vendor_id, under_review (true/false)

## set_vendor_active_status
Use when the user wants to deactivate/close out a vendor or reactivate one. Status only —
does not block new POs/RFQs against the vendor.
Requires: vendor_id, active (true/false)

## set_vendor_verification_status
Use when the user wants to mark a vendor as verified (or remove that mark), optionally
with a note like a tax ID. Records a human decision, not automated verification.
Requires: vendor_id, verified (true/false). Optional: note

## list_vendors
Use whenever the user wants to see, list, or browse vendors, including to find a
vendor's ID before doing something else with it.
Optional: category, active_only

## list_purchase_orders
Use whenever the user wants to see, list, or browse purchase orders.
Optional: status, flagged_only

## list_payments
Use whenever the user wants to see, list, or browse payments.

## list_contracts
Use whenever the user wants to see, list, or browse contracts.
Optional: expiring_soon_only
