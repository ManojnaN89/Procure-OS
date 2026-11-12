# Run 3: a contract price far above the estimate holds the order until an approver accepts it

Result: **passed**

| # | Who | Did | The screen then showed | Picture |
|---|---|---|---|---|
| 1 | Neha Kulkarni, Buyer | checks the stock levels (a preview, nothing is created yet) | Requests What people have asked for, and where each request is in approval Check stock levels Export Stock check 1 item at or below their reorder point would become 1 request, about ₹7,200 in all. Of these: 1 approves automatically. Create and submit Close Al… | [01-buyer-checks-the-stock-levels-a-preview-nothing-is-cre.jpg](held/01-buyer-checks-the-stock-levels-a-preview-nothing-is-cre.jpg) |
| 2 | Neha Kulkarni, Buyer | creates the request and sends it for approval | Requests What people have asked for, and where each request is in approval Check stock levels Export Created 1 request: 1 approved automatically, 0 waiting for approval. Approved1 All1 REQUEST AMOUNT REQUESTED BY STATUS Pump shaft REQ-CKN-2627-00001 ₹7,200 Au… | [02-buyer-creates-the-request-and-sends-it-for-approval.jpg](held/02-buyer-creates-the-request-and-sends-it-for-approval.jpg) |
| 3 | Neha Kulkarni, Buyer | sees the new order in the list, held: the price is above the estimate | Purchase orders What has been ordered from suppliers, and where each order is Export NUMBER SUPPLIER FOR REQUEST DELIVER TO AMOUNT TIER STATUS PO-CKN-2627-00001 Deccan Castings Pvt Ltd SUP-001 REQ-CKN-2627-00001 Plant, Chakan (Pune) ₹9,450 Draft held: price a… | [03-buyer-sees-the-new-order-in-the-list-held-the-price-is.jpg](held/03-buyer-sees-the-new-order-in-the-list-held-the-price-is.jpg) |
| 4 | Neha Kulkarni, Buyer | opens the order: it says why it is on hold, and offers nothing to send | ← Purchase orders PO-CKN-2627-00001 · Deccan Castings Pvt Ltd Draft · ₹9,450 · for REQ-CKN-2627-00001 · contract CNT-0001 ✓ Request ✓ Approval ✓ Price 4 Order 5 Receipt 6 Invoice 7 Payment Now: Held until an approver accepts the price or contract limit Approv… | [04-buyer-opens-the-order-it-says-why-it-is-on-hold-and-of.jpg](held/04-buyer-opens-the-order-it-says-why-it-is-on-hold-and-of.jpg) |
| 5 | Sanjay Rao, Purchase Manager | has no such order to approve: it is not yet up for approval | Good morning, Sanjay Rao What needs you today, and where the purchases in progress stand. My work · Approver Orders held for a price or ceiling 1 See all Accept a price above the estimate on PO-CKN-2627-00001: Price above the estimate by more t Decide › Where… | [05-proc_head-has-no-such-order-to-approve-it-is-not-yet-up-fo.jpg](held/05-proc_head-has-no-such-order-to-approve-it-is-not-yet-up-fo.jpg) |
| 6 | Sanjay Rao, Purchase Manager | finds 'PO-CKN-2627-00001' in the work waiting for them | Approvals Actions an agent or service proposed. Nothing runs until you approve it. Accept a price above the estimate on PO-CKN-2627-00001: Price above the estimate by more than 15%: Pump shaft: ₹ 225.00 is 25.00% above the estimate ₹ 180.00 Proposed by servic… | [06-proc_head-finds-po-ckn-2627-00001-in-the-work-waiting-for-.jpg](held/06-proc_head-finds-po-ckn-2627-00001-in-the-work-waiting-for-.jpg) |
| 7 | Sanjay Rao, Purchase Manager | reads the reason and accepts the price | Approvals Actions an agent or service proposed. Nothing runs until you approve it. Nothing is waiting for you. | [07-proc_head-reads-the-reason-and-accepts-the-price.jpg](held/07-proc_head-reads-the-reason-and-accepts-the-price.jpg) |
| 8 | Neha Kulkarni, Buyer | finds the approved order to send | ← Purchase orders PO-CKN-2627-00001 · Deccan Castings Pvt Ltd Approved · ₹9,450 · for REQ-CKN-2627-00001 · contract CNT-0001 ✓ Request ✓ Approval ✓ Price 4 Order 5 Receipt 6 Invoice 7 Payment Now: Approved: a buyer sends it to the supplier Approval · Buyer ti… | [08-buyer-finds-the-approved-order-to-send.jpg](held/08-buyer-finds-the-approved-order-to-send.jpg) |
| 9 | Neha Kulkarni, Buyer | sends the order to the supplier | ← Purchase orders PO-CKN-2627-00001 · Deccan Castings Pvt Ltd Sent · ₹9,450 · for REQ-CKN-2627-00001 · contract CNT-0001 ✓ Request ✓ Approval ✓ Price 4 Order 5 Receipt 6 Invoice 7 Payment Now: Sent: waiting for the supplier to confirm and give a delivery date… | [09-buyer-sends-the-order-to-the-supplier.jpg](held/09-buyer-sends-the-order-to-the-supplier.jpg) |
| 10 | Neha Kulkarni, Buyer | records the supplier's acknowledgement, delivery promised 2026-11-11 | ← Purchase orders PO-CKN-2627-00001 · Deccan Castings Pvt Ltd Acknowledged · ₹9,450 · for REQ-CKN-2627-00001 · contract CNT-0001 ✓ Request ✓ Approval ✓ Price ✓ Order 5 Receipt 6 Invoice 7 Payment Now: Confirmed: waiting for the goods to arrive at the store Ap… | [10-buyer-records-the-supplier-s-acknowledgement-delivery-.jpg](held/10-buyer-records-the-supplier-s-acknowledgement-delivery-.jpg) |

## What the data showed at the end

- no request exists after the preview: 0
- the request is for the item that fell to its reorder point: RM-05
- quantity: up to the maximum, not below the minimum order: 40.0000
- estimate: the item's standard price: 180.0000
- the request's value: 7200.00
- how the rule table routes a request of this value: auto
- delivery place: PLT-CKN
- request status straight after the check: approved
- how the item is bought: a contract price is in force, so no quotation is asked for: contract
- the supplier and contract, from the price records in force: ('SUP-001', 'CNT-0001')
- no quotation request was made: 0
- the contract price 225.00 is 25.0% above the estimate 180.00: more than the 15% allowed
- order: supplier: SUP-001
- order: price before tax (quantity x net price): 9000.00
- order: GST split (CGST, SGST, IGST: supplier in state 27, delivery in 27): (Decimal('225.00'), Decimal('225.00'), Decimal('0.00'))
- order: total: 9450.00
- order: our GSTIN is the delivery place's: 27APXCA0000P1ZL
- order status: draft
- the order carries the reason it is held, and a proposal for an approver
- the procurement head cannot approve it while it is held (refused): 409
- the buyer cannot send it while it is held (refused): 409
- no mail went to the supplier: 0
- order status: accepted, so it follows the approval band for its value: approved
- the hold is lifted: None
- the proposal was decided by the purchase manager (the rule for price variances): 301
- the acceptance is in the audit log
- the order of 9,000 is within the band that needs no approver: approved by the rule: approved
- the buyer cannot approve an order (refused)
- the order mail goes to the supplier's main contact, once: ['sales@deccancastings.example']
- the order mail states the order total: 9450.00
- order status after the acknowledgement: acknowledged
- the price on the order is the contract price, not the estimate: 225.0000
- the audit log has a row for each of the 4 actions
- the audit log holds 29 rows in all, for 20 kinds of action
- the audit log, in order, with who did it: masters.import by service:system; user.create by service:system; demo.configure by service:system; role.grant by service:system; request.create by user:295; replenishment.run by user:295; request.submit by user:295; request.auto_approve by rule:REQ-01; sourcing.decide by service:sourcing; po.create by service:ordering; proposal.create by service:ordering; po.variance_accepted by user:301; po.submit by user:301; po.auto_approve by rule:PO-01; po.touchless_check by rule:touchless_po v1; proposal.approved by user:301; email.send by service:system; po.send by user:295; po.acknowledge by user:295
- audit: 'request.submit' (requester) was done by Neha Kulkarni, Buyer: {'user:295'}
- audit: 'po.variance_accepted' (approver) was done by Sanjay Rao, Purchase Manager: {'user:301'}
- audit: 'po.send' was done by Neha Kulkarni, Buyer: {'user:295'}
- audit: 'po.acknowledge' was done by Neha Kulkarni, Buyer: {'user:295'}
- separation of duties: requester, approver are different people in this case
