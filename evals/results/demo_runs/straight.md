# Run 1: from stock at its reorder point to a paid and closed order (by quotation, everything typed in)

Result: **passed**

| # | Who | Did | The screen then showed | Picture |
|---|---|---|---|---|
| 1 | Rohan Mehta, Buyer | signs in: the dashboard shows nothing waiting yet | Procurement overview RFQs, requisitions and spend across all categories — live from the database. My work · Buyer Nothing needs your action right now. 0 open RFQs 0 pending requisitions 0 vendors 0 purchase orders Needs attention View all Surfaced automatical… | [01-buyer-signs-in-the-dashboard-shows-nothing-waiting-yet.jpg](straight/01-buyer-signs-in-the-dashboard-shows-nothing-waiting-yet.jpg) |
| 2 | Rohan Mehta, Buyer | checks the stock levels (a preview, nothing is created yet) | Requisitions Internal purchase requests and their approval status Check stock levels New requisition Stock check 1 item at or below their reorder point would become 1 request, about ₹41,280 in all. Of these: 1 needs a person's approval. Create and submit Clos… | [02-buyer-checks-the-stock-levels-a-preview-nothing-is-cre.jpg](straight/02-buyer-checks-the-stock-levels-a-preview-nothing-is-cre.jpg) |
| 3 | Rohan Mehta, Buyer | creates the request and sends it for approval | Requisitions Internal purchase requests and their approval status Check stock levels New requisition Created 1 request: 0 approved automatically, 1 waiting for approval. NUMBER TITLE AMOUNT REQUESTED BY STATUS REQ-VAD-2627-00001 Automatic replenishment: 1 ite… | [03-buyer-creates-the-request-and-sends-it-for-approval.jpg](straight/03-buyer-creates-the-request-and-sends-it-for-approval.jpg) |
| 4 | Prakash Gowda, Head of Stores | finds the request in the work waiting for them | ← Requisitions Automatic replenishment: 1 item(s) at or below their reorder point REQ-VAD-2627-00001 · ₹41,280 · Automatic replenishment · Pending · deliver to PLT-VAD · needed by 13 Nov 2026 Approval Waiting for approval by Prakash Gowda under rule REQ-02 v2… | [04-stores_head-finds-the-request-in-the-work-waiting-for-them.jpg](straight/04-stores_head-finds-the-request-in-the-work-waiting-for-them.jpg) |
| 5 | Prakash Gowda, Head of Stores | approves the request | ← Requisitions Automatic replenishment: 1 item(s) at or below their reorder point REQ-VAD-2627-00001 · ₹41,280 · Automatic replenishment · Approved · deliver to PLT-VAD · needed by 13 Nov 2026 Approval Approved by Prakash Gowda under rule REQ-02 v2 Budget GL … | [05-stores_head-approves-the-request.jpg](straight/05-stores_head-approves-the-request.jpg) |
| 6 | Rohan Mehta, Buyer | finds 'RFQ-2627-00001' in the work waiting for them | Approvals Actions an agent or service proposed. Nothing runs until you approve it. Send RFQ-2627-00001 to 3 suppliers ('rfq.send' is set to propose, not auto) Proposed by service sourcing · 10 Nov 2026, 11:27 pm low { "rfq_no": "RFQ-2627-00001" } Note (option… | [06-buyer-finds-rfq-2627-00001-in-the-work-waiting-for-the.jpg](straight/06-buyer-finds-rfq-2627-00001-in-the-work-waiting-for-the.jpg) |
| 7 | Rohan Mehta, Buyer | approves sending the quotation request to the suppliers | Approvals Actions an agent or service proposed. Nothing runs until you approve it. Nothing is waiting for you. | [07-buyer-approves-sending-the-quotation-request-to-the-su.jpg](straight/07-buyer-approves-sending-the-quotation-request-to-the-su.jpg) |
| 8 | Rohan Mehta, Buyer | opens the quotation request: no quotes yet | ← RFQs MRO spares (mechanical): 1 item(s) for REQ-VAD-2627-00001 RFQ-2627-00001 · Sent · for REQ-VAD-2627-00001 · reply by 17 Nov 2026 · sent 10 Nov 2026 SUPPLIER EMAIL STATE Pinnacle Bearings & Tools SUP-0005 hetal.jadhav@pinnaclebearingstools.example invite… | [08-buyer-opens-the-quotation-request-no-quotes-yet.jpg](straight/08-buyer-opens-the-quotation-request-no-quotes-yet.jpg) |
| 9 | Rohan Mehta, Buyer | enters the quote of Pinnacle Bearings & Tools: 330 less 0% | ← RFQs MRO spares (mechanical): 1 item(s) for REQ-VAD-2627-00001 RFQ-2627-00001 · Sent · for REQ-VAD-2627-00001 · reply by 17 Nov 2026 · sent 10 Nov 2026 SUPPLIER EMAIL STATE Pinnacle Bearings & Tools SUP-0005 hetal.jadhav@pinnaclebearingstools.example replie… | [09-buyer-enters-the-quote-of-pinnacle-bearings-tools-330-.jpg](straight/09-buyer-enters-the-quote-of-pinnacle-bearings-tools-330-.jpg) |
| 10 | Rohan Mehta, Buyer | enters the quote of Western Industrial Supplies: 305 less 2% | ← RFQs MRO spares (mechanical): 1 item(s) for REQ-VAD-2627-00001 RFQ-2627-00001 · Sent · for REQ-VAD-2627-00001 · reply by 17 Nov 2026 · sent 10 Nov 2026 SUPPLIER EMAIL STATE Pinnacle Bearings & Tools SUP-0005 hetal.jadhav@pinnaclebearingstools.example replie… | [10-buyer-enters-the-quote-of-western-industrial-supplies-.jpg](straight/10-buyer-enters-the-quote-of-western-industrial-supplies-.jpg) |
| 11 | Rohan Mehta, Buyer | compares the quotes: the cheaper one after discount is recommended; awards it | ← RFQs MRO spares (mechanical): 1 item(s) for REQ-VAD-2627-00001 RFQ-2627-00001 · Awarded · for REQ-VAD-2627-00001 · reply by 17 Nov 2026 · sent 10 Nov 2026 SUPPLIER EMAIL STATE Pinnacle Bearings & Tools SUP-0005 hetal.jadhav@pinnaclebearingstools.example rep… | [11-buyer-compares-the-quotes-the-cheaper-one-after-discou.jpg](straight/11-buyer-compares-the-quotes-the-cheaper-one-after-discou.jpg) |
| 12 | Vikram Joshi, Head of Procurement | finds the order in the work waiting for them | ← Purchase orders PO-VAD-2627-00001 · Western Industrial Supplies Pending · ₹45,498.56 · for REQ-VAD-2627-00001 · RFQ-2627-00001 Approval Waiting for approval by Vikram Joshi under rule PO-01 v1 Vikram Joshi · Head - Procurement Waiting Rule PO-01 version 1 A… | [12-proc_head-finds-the-order-in-the-work-waiting-for-them.jpg](straight/12-proc_head-finds-the-order-in-the-work-waiting-for-them.jpg) |
| 13 | Vikram Joshi, Head of Procurement | checks the order's figures and approves it | ← Purchase orders PO-VAD-2627-00001 · Western Industrial Supplies Approved · ₹45,498.56 · for REQ-VAD-2627-00001 · RFQ-2627-00001 Approval Approved by Vikram Joshi under rule PO-01 v1 Vikram Joshi · Head - Procurement Approved 10 Nov 2026 Rule PO-01 version 1… | [13-proc_head-checks-the-order-s-figures-and-approves-it.jpg](straight/13-proc_head-checks-the-order-s-figures-and-approves-it.jpg) |
| 14 | Rohan Mehta, Buyer | finds the approved order to send | ← Purchase orders PO-VAD-2627-00001 · Western Industrial Supplies Approved · ₹45,498.56 · for REQ-VAD-2627-00001 · RFQ-2627-00001 Approval Approved by Vikram Joshi under rule PO-01 v1 Vikram Joshi · Head - Procurement Approved 10 Nov 2026 Rule PO-01 version 1… | [14-buyer-finds-the-approved-order-to-send.jpg](straight/14-buyer-finds-the-approved-order-to-send.jpg) |
| 15 | Rohan Mehta, Buyer | sends the order to the supplier | ← Purchase orders PO-VAD-2627-00001 · Western Industrial Supplies Sent · ₹45,498.56 · for REQ-VAD-2627-00001 · RFQ-2627-00001 Approval Approved by Vikram Joshi under rule PO-01 v1 Vikram Joshi · Head - Procurement Approved 10 Nov 2026 Rule PO-01 version 1 Sup… | [15-buyer-sends-the-order-to-the-supplier.jpg](straight/15-buyer-sends-the-order-to-the-supplier.jpg) |
| 16 | Rohan Mehta, Buyer | records the supplier's acknowledgement, delivery promised 2026-11-11 | ← Purchase orders PO-VAD-2627-00001 · Western Industrial Supplies Acknowledged · ₹45,498.56 · for REQ-VAD-2627-00001 · RFQ-2627-00001 Approval Approved by Vikram Joshi under rule PO-01 v1 Vikram Joshi · Head - Procurement Approved 10 Nov 2026 Rule PO-01 versi… | [16-buyer-records-the-supplier-s-acknowledgement-delivery-.jpg](straight/16-buyer-records-the-supplier-s-acknowledgement-delivery-.jpg) |
| 17 | Imran Pasha, Storekeeper | sees the order in the work waiting for them and posts the goods receipt | ← Goods receipt GRN-VAD-2627-00001 for PO-VAD-2627-00001 · 10 Nov 2026 · received by U024 Challan DC/2611/0412 · deliver to PLT-VAD ITEM RECEIVED ACCEPTED REJECTED Ball bearing 6205-2RS ITM-00068 129 NOS 129 0 | [17-store-sees-the-order-in-the-work-waiting-for-them-and-.jpg](straight/17-store-sees-the-order-in-the-work-waiting-for-them-and-.jpg) |
| 18 | Sunita Patil, Accounts | sees the goods waiting for an invoice and enters the supplier's invoice | ← Invoices PI-2627-000001 · Western Industrial Supplies Approved for payment · ₹45,499 · supplier's number WIS/26-27/0412 · 6 Nov 2026 ORDER LINE ITEM QTY PRICE TAXABLE MATCH PO-VAD-2627-00001 / 1 Ball bearing 6205-2RS 129 NOS ₹298.90 ₹38,558.10 matched Taxab… | [18-ap-sees-the-goods-waiting-for-an-invoice-and-enters.jpg](straight/18-ap-sees-the-goods-waiting-for-an-invoice-and-enters.jpg) |
| 19 | Anil Deshpande, Finance Manager | on 14 Dec 2026 finds the invoice that falls due in the work waiting for them | Payments Payment runs: what is due, approved by the owner, paid through the bank Plan a payment run pays everything due within days; small suppliers first include advances on orders See what is due Send the run for approval No payment runs yet. | [19-finance-on-14-dec-2026-finds-the-invoice-that-falls-due-.jpg](straight/19-finance-on-14-dec-2026-finds-the-invoice-that-falls-due-.jpg) |
| 20 | Anil Deshpande, Finance Manager | previews what is due and sends a payment run for the owner's approval | ← Payments PR-2627-0001 Waiting for approval · ₹45,499 · run date 14 Dec 2026 Waiting for the owner to approve: Pay 1 invoice(s) to 1 supplier(s), 45,499.00 in all, on 14 Dec 2026 (under Admin → Approvals). Nothing is paid until then. Western Industrial Suppl… | [20-finance-previews-what-is-due-and-sends-a-payment-run-for.jpg](straight/20-finance-previews-what-is-due-and-sends-a-payment-run-for.jpg) |
| 21 | Rajesh Kulkarni, Managing Director | finds 'Pay ' in the work waiting for them | Approvals Actions an agent or service proposed. Nothing runs until you approve it. Pay 1 invoice(s) to 1 supplier(s), 45,499.00 in all, on 14 Dec 2026 Proposed by user 35 · 10 Nov 2026, 11:27 pm high { "run_no": "PR-2627-0001" } Note (optional) Approve Edit f… | [21-owner-finds-pay-in-the-work-waiting-for-them.jpg](straight/21-owner-finds-pay-in-the-work-waiting-for-them.jpg) |
| 22 | Rajesh Kulkarni, Managing Director | approves the payment run | Approvals Actions an agent or service proposed. Nothing runs until you approve it. Nothing is waiting for you. | [22-owner-approves-the-payment-run.jpg](straight/22-owner-approves-the-payment-run.jpg) |
| 23 | Anil Deshpande, Finance Manager | sees the approved run waiting for its bank file | ← Payments PR-2627-0001 Approved · ₹45,499 · run date 14 Dec 2026 Western Industrial Supplies PAY-2627-000001 · NEFT ₹45,499 PI-2627-000001 WIS/26-27/0412 ₹45,499 Make the bank file | [23-finance-sees-the-approved-run-waiting-for-its-bank-file.jpg](straight/23-finance-sees-the-approved-run-waiting-for-its-bank-file.jpg) |
| 24 | Anil Deshpande, Finance Manager | makes the bank file for the approved run | ← Payments PR-2627-0001 Bank file made · ₹45,499 · run date 14 Dec 2026 Western Industrial Supplies PAY-2627-000001 · NEFT ₹45,499 PI-2627-000001 WIS/26-27/0412 ₹45,499 Show the bank file The bank has paid this run payment_no,beneficiary,account_no,ifsc,amoun… | [24-finance-makes-the-bank-file-for-the-approved-run.jpg](straight/24-finance-makes-the-bank-file-for-the-approved-run.jpg) |
| 25 | Anil Deshpande, Finance Manager | records that the bank has paid, on 2026-12-14 | ← Payments PR-2627-0001 Paid · ₹45,499 · run date 14 Dec 2026 Western Industrial Supplies PAY-2627-000001 · NEFT · bank reference UTR26121400010001 · paid 14 Dec 2026 ₹45,499 PI-2627-000001 WIS/26-27/0412 ₹45,499 Show the bank file | [25-finance-records-that-the-bank-has-paid-on-2026-12-14.jpg](straight/25-finance-records-that-the-bank-has-paid-on-2026-12-14.jpg) |
| 26 | Rohan Mehta, Buyer | sees that the order is paid and closed in the notifications | Procurement overview RFQs, requisitions and spend across all categories — live from the database. My work · Buyer Nothing needs your action right now. 0 open RFQs 0 pending requisitions 0 vendors 0 purchase orders Needs attention 1 low View all Surfaced autom… | [26-buyer-sees-that-the-order-is-paid-and-closed-in-the-no.jpg](straight/26-buyer-sees-that-the-order-is-paid-and-closed-in-the-no.jpg) |

## What the data showed at the end

- no request exists after the preview: 0
- the request is for the item that fell to its reorder point: ITM-00068
- quantity: up to the maximum, not below the minimum order: 129.0000
- estimate: the item's standard price: 320.0000
- the request's value: 41280.00
- how the rule table routes a request of this value: manager
- delivery place: PLT-VAD
- request status straight after the check: submitted
- request status: approved
- nothing was sent to a supplier yet: sending needs a buyer's approval: 0
- no contract price is in force for the item, so quotations were asked for
- the suppliers asked (SUP-0005, SUP-0006, SUP-0008) are active, approved for MRO and have an email address
- one quotation request mail per supplier asked: ['hetal.jadhav@pinnaclebearingstools.example', 'jignesh.gaikwad@westernindustrialsupplies.example', 'komal.kulkarni@vishwakarmahardwaretraders.example']
- SUP-0006 is among those asked
- RFQ status: awarded
- order: supplier: SUP-0006
- order: price before tax (quantity x net price): 38558.10
- order: GST split (CGST, SGST, IGST: supplier in state 27, delivery in 24): (Decimal('0.00'), Decimal('0.00'), Decimal('6940.46'))
- order: total: 45498.56
- order: our GSTIN is the delivery place's: 24AXQCC0000M1ZM
- order status: submitted
- order status after approval: approved
- the buyer cannot approve an order (refused)
- the order mail goes to the supplier's main contact, once: ['jignesh.gaikwad@westernindustrialsupplies.example']
- the order mail states the order total: 45498.56
- order status after the acknowledgement: acknowledged
- goods received on the order line: 129.0000
- order status after the receipt: received
- stock at the delivery place: the reorder point plus what arrived is the maximum: 233.0000
- the store person is not the buyer, an approver or the requester
- invoice: status (price and quantity agree with the order and what was received): approved
- invoice: price before tax and GST are the order's, the total is rounded to the rupee: (Decimal('38558.10'), Decimal('0.00'), Decimal('0.00'), Decimal('6940.46'), Decimal('45499.00'))
- invoice: the round-off line: 0.44
- invoice: due date from the supplier's payment terms, the day the goods were accepted (2026-11-06): 2026-12-21
- invoice: tax held back at source (far below the yearly limit): 0.00
- invoice was entered by the accounts clerk: 32
- invoice: total of the order, to the rupee: 45499
- payment run status: proposed
- the payment: the invoice total less tax held back, no interest: (Decimal('45499.00'), 'SUP-0006', Decimal('0.00'), Decimal('0.00'))
- the person who proposed the run cannot approve it (refused): 403
- no bank file before the owner approves (refused): 409
- payment run status after the owner approves: approved
- the bank file has one payment line: 2
- bank file line: the supplier's verified account and exactly the approved amount: ('000774658904', 'UBIN0962150', Decimal('45499.00'))
- invoice status: paid
- order status: closed
- budget actual booked: the price before tax: 38558.10
- the supplier was told what was paid, with the bank reference (mails): 1
- the audit log has a row for each of the 12 actions
- the audit log holds 53 rows in all, for 32 kinds of action
- the audit log, in order, with who did it: masters.import by service:system; user.create by service:system; role.grant by service:system; request.create by user:29; replenishment.run by user:29; request.submit by user:29; task.create by service:system; request.approve by user:39; request.approved by user:39; task.complete by user:36,user:39; rfq.create by service:sourcing; sourcing.decide by service:sourcing; proposal.create by service:sourcing,user:35; email.send by service:system; rfq.send by user:29; proposal.approved by user:29,user:34; quote.enter by user:29; rfq.award by user:29; po.create by service:ordering; po.submit by service:ordering; po.approve by user:36; po.approved by user:36; po.send by user:29; po.acknowledge by user:29; grn.post by user:31; invoice.enter by user:32; invoice.match by user:32; payment_run.propose by user:35; payment_run.approve by user:34; payment_run.file by user:35; po.close by user:35; payment_run.paid by user:35
- audit: 'request.submit' (requester) was done by Rohan Mehta, Buyer: {'user:29'}
- audit: 'request.approve' (approver) was done by Prakash Gowda, Head of Stores: {'user:39'}
- audit: 'po.approve' (approver) was done by Vikram Joshi, Head of Procurement: {'user:36'}
- audit: 'payment_run.approve' (approver) was done by Rajesh Kulkarni, Managing Director: {'user:34'}
- audit: 'grn.post' (receiver) was done by Imran Pasha, Storekeeper: {'user:31'}
- audit: 'payment_run.propose' (payer) was done by Anil Deshpande, Finance Manager: {'user:35'}
- audit: 'payment_run.paid' (payer) was done by Anil Deshpande, Finance Manager: {'user:35'}
- audit: 'rfq.send' was done by Rohan Mehta, Buyer: {'user:29'}
- audit: 'quote.enter' was done by Rohan Mehta, Buyer: {'user:29'}
- audit: 'rfq.award' was done by Rohan Mehta, Buyer: {'user:29'}
- audit: 'po.send' was done by Rohan Mehta, Buyer: {'user:29'}
- audit: 'invoice.enter' was done by Sunita Patil, Accounts: {'user:32'}
- audit: 'invoice.match' was done by Sunita Patil, Accounts: {'user:32'}
- audit: 'payment_run.file' was done by Anil Deshpande, Finance Manager: {'user:35'}
- separation of duties: requester, approver, receiver, payer are different people in this case
