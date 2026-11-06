# Skill Domain: Purchase Requisition

Handles internal purchase requests and their approval-to-PO lifecycle.

## create_requisition
Use when the user wants to RAISE a new internal purchase request.
Requires: title, amount, requested_by, description (optional)

## decide_requisition
Use when the user wants to APPROVE or REJECT a pending requisition.
Requires: requisition_id, decision (approved/rejected)

## convert_to_po
Use when the user wants to turn an APPROVED requisition into a purchase order for a vendor.
Requires: requisition_id, vendor_id, items

## list_requisitions
Use whenever the user wants to see, list, or browse requisitions, including to find one's
ID before approving/rejecting it.
Optional: status ('pending', 'approved', 'rejected')
