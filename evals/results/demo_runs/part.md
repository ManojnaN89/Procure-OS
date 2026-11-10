# Run 4: a part delivery: the balance is late and chased, the second delivery completes the order, two invoices, one payment

Result: **passed**

| # | Who | Did | The screen then showed | Picture |
|---|---|---|---|---|
| 1 | Rohan Mehta, Buyer | signs in: the dashboard shows nothing waiting yet | Procurement overview RFQs, requisitions and spend across all categories — live from the database. My work · Buyer Nothing needs your action right now. 0 open RFQs 0 pending requisitions 0 vendors 0 purchase orders Needs attention View all Surfaced automatical… | [01-buyer-signs-in-the-dashboard-shows-nothing-waiting-yet.jpg](part/01-buyer-signs-in-the-dashboard-shows-nothing-waiting-yet.jpg) |
| 2 | Rohan Mehta, Buyer | checks the stock levels (a preview, nothing is created yet) | Requisitions Internal purchase requests and their approval status Check stock levels New requisition Stock check 1 item at or below their reorder point would become 1 request, about ₹41,280 in all. Of these: 1 needs a person's approval. Create and submit Clos… | [02-buyer-checks-the-stock-levels-a-preview-nothing-is-cre.jpg](part/02-buyer-checks-the-stock-levels-a-preview-nothing-is-cre.jpg) |
| 3 | Rohan Mehta, Buyer | creates the request and sends it for approval | Requisitions Internal purchase requests and their approval status Check stock levels New requisition Created 1 request: 0 approved automatically, 1 waiting for approval. NUMBER TITLE AMOUNT REQUESTED BY STATUS REQ-VAD-2627-00001 Automatic replenishment: 1 ite… | [03-buyer-creates-the-request-and-sends-it-for-approval.jpg](part/03-buyer-creates-the-request-and-sends-it-for-approval.jpg) |
| 4 | Prakash Gowda, Head of Stores | finds the request in the work waiting for them | ← Requisitions Automatic replenishment: 1 item(s) at or below their reorder point REQ-VAD-2627-00001 · ₹41,280 · Automatic replenishment · Pending · deliver to PLT-VAD · needed by 13 Nov 2026 Approval Waiting for approval by Prakash Gowda under rule REQ-02 v2… | [04-stores_head-finds-the-request-in-the-work-waiting-for-them.jpg](part/04-stores_head-finds-the-request-in-the-work-waiting-for-them.jpg) |
| 5 | Prakash Gowda, Head of Stores | approves the request | ← Requisitions Automatic replenishment: 1 item(s) at or below their reorder point REQ-VAD-2627-00001 · ₹41,280 · Automatic replenishment · Approved · deliver to PLT-VAD · needed by 13 Nov 2026 Approval Approved by Prakash Gowda under rule REQ-02 v2 Budget GL … | [05-stores_head-approves-the-request.jpg](part/05-stores_head-approves-the-request.jpg) |
| 6 | Rohan Mehta, Buyer | finds 'RFQ-2627-00001' in the work waiting for them | Approvals Actions an agent or service proposed. Nothing runs until you approve it. Send RFQ-2627-00001 to 3 suppliers ('rfq.send' is set to propose, not auto) Proposed by service sourcing · 10 Nov 2026, 11:28 pm low { "rfq_no": "RFQ-2627-00001" } Note (option… | [06-buyer-finds-rfq-2627-00001-in-the-work-waiting-for-the.jpg](part/06-buyer-finds-rfq-2627-00001-in-the-work-waiting-for-the.jpg) |
| 7 | Rohan Mehta, Buyer | approves sending the quotation request to the suppliers | Approvals Actions an agent or service proposed. Nothing runs until you approve it. Nothing is waiting for you. | [07-buyer-approves-sending-the-quotation-request-to-the-su.jpg](part/07-buyer-approves-sending-the-quotation-request-to-the-su.jpg) |
| 8 | Rohan Mehta, Buyer | opens the quotation request: no quotes yet | ← RFQs MRO spares (mechanical): 1 item(s) for REQ-VAD-2627-00001 RFQ-2627-00001 · Sent · for REQ-VAD-2627-00001 · reply by 17 Nov 2026 · sent 10 Nov 2026 SUPPLIER EMAIL STATE Pinnacle Bearings & Tools SUP-0005 hetal.jadhav@pinnaclebearingstools.example invite… | [08-buyer-opens-the-quotation-request-no-quotes-yet.jpg](part/08-buyer-opens-the-quotation-request-no-quotes-yet.jpg) |
| 9 | Rohan Mehta, Buyer | enters the quote of Pinnacle Bearings & Tools: 330 less 0% | ← RFQs MRO spares (mechanical): 1 item(s) for REQ-VAD-2627-00001 RFQ-2627-00001 · Sent · for REQ-VAD-2627-00001 · reply by 17 Nov 2026 · sent 10 Nov 2026 SUPPLIER EMAIL STATE Pinnacle Bearings & Tools SUP-0005 hetal.jadhav@pinnaclebearingstools.example replie… | [09-buyer-enters-the-quote-of-pinnacle-bearings-tools-330-.jpg](part/09-buyer-enters-the-quote-of-pinnacle-bearings-tools-330-.jpg) |
| 10 | Rohan Mehta, Buyer | enters the quote of Western Industrial Supplies: 305 less 2% | ← RFQs MRO spares (mechanical): 1 item(s) for REQ-VAD-2627-00001 RFQ-2627-00001 · Sent · for REQ-VAD-2627-00001 · reply by 17 Nov 2026 · sent 10 Nov 2026 SUPPLIER EMAIL STATE Pinnacle Bearings & Tools SUP-0005 hetal.jadhav@pinnaclebearingstools.example replie… | [10-buyer-enters-the-quote-of-western-industrial-supplies-.jpg](part/10-buyer-enters-the-quote-of-western-industrial-supplies-.jpg) |
| 11 | Rohan Mehta, Buyer | compares the quotes: the cheaper one after discount is recommended; awards it | ← RFQs MRO spares (mechanical): 1 item(s) for REQ-VAD-2627-00001 RFQ-2627-00001 · Awarded · for REQ-VAD-2627-00001 · reply by 17 Nov 2026 · sent 10 Nov 2026 SUPPLIER EMAIL STATE Pinnacle Bearings & Tools SUP-0005 hetal.jadhav@pinnaclebearingstools.example rep… | [11-buyer-compares-the-quotes-the-cheaper-one-after-discou.jpg](part/11-buyer-compares-the-quotes-the-cheaper-one-after-discou.jpg) |
| 12 | Vikram Joshi, Head of Procurement | finds the order in the work waiting for them | ← Purchase orders PO-VAD-2627-00001 · Western Industrial Supplies Pending · ₹45,498.56 · for REQ-VAD-2627-00001 · RFQ-2627-00001 Approval Waiting for approval by Vikram Joshi under rule PO-01 v1 Vikram Joshi · Head - Procurement Waiting Rule PO-01 version 1 A… | [12-proc_head-finds-the-order-in-the-work-waiting-for-them.jpg](part/12-proc_head-finds-the-order-in-the-work-waiting-for-them.jpg) |
| 13 | Vikram Joshi, Head of Procurement | checks the order's figures and approves it | ← Purchase orders PO-VAD-2627-00001 · Western Industrial Supplies Approved · ₹45,498.56 · for REQ-VAD-2627-00001 · RFQ-2627-00001 Approval Approved by Vikram Joshi under rule PO-01 v1 Vikram Joshi · Head - Procurement Approved 10 Nov 2026 Rule PO-01 version 1… | [13-proc_head-checks-the-order-s-figures-and-approves-it.jpg](part/13-proc_head-checks-the-order-s-figures-and-approves-it.jpg) |
| 14 | Rohan Mehta, Buyer | finds the approved order to send | ← Purchase orders PO-VAD-2627-00001 · Western Industrial Supplies Approved · ₹45,498.56 · for REQ-VAD-2627-00001 · RFQ-2627-00001 Approval Approved by Vikram Joshi under rule PO-01 v1 Vikram Joshi · Head - Procurement Approved 10 Nov 2026 Rule PO-01 version 1… | [14-buyer-finds-the-approved-order-to-send.jpg](part/14-buyer-finds-the-approved-order-to-send.jpg) |
| 15 | Rohan Mehta, Buyer | sends the order to the supplier | ← Purchase orders PO-VAD-2627-00001 · Western Industrial Supplies Sent · ₹45,498.56 · for REQ-VAD-2627-00001 · RFQ-2627-00001 Approval Approved by Vikram Joshi under rule PO-01 v1 Vikram Joshi · Head - Procurement Approved 10 Nov 2026 Rule PO-01 version 1 Sup… | [15-buyer-sends-the-order-to-the-supplier.jpg](part/15-buyer-sends-the-order-to-the-supplier.jpg) |
| 16 | Rohan Mehta, Buyer | records the supplier's acknowledgement, delivery promised 2026-11-11 | ← Purchase orders PO-VAD-2627-00001 · Western Industrial Supplies Acknowledged · ₹45,498.56 · for REQ-VAD-2627-00001 · RFQ-2627-00001 Approval Approved by Vikram Joshi under rule PO-01 v1 Vikram Joshi · Head - Procurement Approved 10 Nov 2026 Rule PO-01 versi… | [16-buyer-records-the-supplier-s-acknowledgement-delivery-.jpg](part/16-buyer-records-the-supplier-s-acknowledgement-delivery-.jpg) |
| 17 | Imran Pasha, Storekeeper | sees the order in the work waiting for them and posts the receipt for 64 of 129 | ← Goods receipt GRN-VAD-2627-00001 for PO-VAD-2627-00001 · 10 Nov 2026 · received by U024 Challan DC/2611/0518 · deliver to PLT-VAD ITEM RECEIVED ACCEPTED REJECTED Ball bearing 6205-2RS ITM-00068 64 NOS 64 0 | [17-store-sees-the-order-in-the-work-waiting-for-them-and-.jpg](part/17-store-sees-the-order-in-the-work-waiting-for-them-and-.jpg) |
| 18 | Imran Pasha, Storekeeper | sees the order still waiting for its balance | Procurement overview RFQs, requisitions and spend across all categories — live from the database. My work · Store Orders with goods to come 1 PO-VAD-2627-00001 Western Industrial Supplies 0 open RFQs 0 pending requisitions 0 vendors 0 purchase orders Needs at… | [18-store-sees-the-order-still-waiting-for-its-balance.jpg](part/18-store-sees-the-order-still-waiting-for-its-balance.jpg) |
| 19 | Sunita Patil, Accounts | sees the goods waiting for an invoice and enters the supplier's invoice | ← Invoices PI-2627-000001 · Western Industrial Supplies Approved for payment · ₹22,573 · supplier's number WIS/26-27/0511 · 6 Nov 2026 ORDER LINE ITEM QTY PRICE TAXABLE MATCH PO-VAD-2627-00001 / 1 Ball bearing 6205-2RS 64 NOS ₹298.90 ₹19,129.60 matched Taxabl… | [19-ap-sees-the-goods-waiting-for-an-invoice-and-enters.jpg](part/19-ap-sees-the-goods-waiting-for-an-invoice-and-enters.jpg) |
| 20 | the daily jobs | run on 12 Nov 2026, a day after the promised date: the supplier has delivered 64 of 129 | chaser: [('PO-VAD-2627-00001', 'balance')]; alert: goods on PO-VAD-2627-00001 are late |  |
| 21 | Rohan Mehta, Buyer | sees that the balance is late: the alert and the chaser to send | Procurement overview RFQs, requisitions and spend across all categories — live from the database. My work · Buyer Alerts for my role medium Goods on PO-VAD-2627-00001 are 1 day(s) late 1 line(s) were promised by 11 Nov 2026 and are not all received. 0 open RF… | [21-buyer-sees-that-the-balance-is-late-the-alert-and-the-.jpg](part/21-buyer-sees-that-the-balance-is-late-the-alert-and-the-.jpg) |
| 22 | Imran Pasha, Storekeeper | sees the order in the work waiting for them and posts the receipt for 65 of 129 | ← Goods receipt GRN-VAD-2627-00002 for PO-VAD-2627-00001 · 10 Nov 2026 · received by U024 Challan DC/2611/0544 · deliver to PLT-VAD ITEM RECEIVED ACCEPTED REJECTED Ball bearing 6205-2RS ITM-00068 65 NOS 65 0 | [22-store-sees-the-order-in-the-work-waiting-for-them-and-.jpg](part/22-store-sees-the-order-in-the-work-waiting-for-them-and-.jpg) |
| 23 | Sunita Patil, Accounts | sees the goods waiting for an invoice and enters the supplier's invoice | ← Invoices PI-2627-000002 · Western Industrial Supplies Approved for payment · ₹22,926 · supplier's number WIS/26-27/0563 · 12 Nov 2026 ORDER LINE ITEM QTY PRICE TAXABLE MATCH PO-VAD-2627-00001 / 1 Ball bearing 6205-2RS 65 NOS ₹298.90 ₹19,428.50 matched Taxab… | [23-ap-sees-the-goods-waiting-for-an-invoice-and-enters.jpg](part/23-ap-sees-the-goods-waiting-for-an-invoice-and-enters.jpg) |
| 24 | Anil Deshpande, Finance Manager | on 14 Dec 2026 finds the invoice that falls due in the work waiting for them | Payments Payment runs: what is due, approved by the owner, paid through the bank Plan a payment run pays everything due within days; small suppliers first include advances on orders See what is due Send the run for approval No payment runs yet. | [24-finance-on-14-dec-2026-finds-the-invoice-that-falls-due-.jpg](part/24-finance-on-14-dec-2026-finds-the-invoice-that-falls-due-.jpg) |
| 25 | Anil Deshpande, Finance Manager | previews what is due and sends a payment run for the owner's approval | ← Payments PR-2627-0001 Waiting for approval · ₹45,499 · run date 14 Dec 2026 Waiting for the owner to approve: Pay 2 invoice(s) to 1 supplier(s), 45,499.00 in all, on 14 Dec 2026 (under Admin → Approvals). Nothing is paid until then. Western Industrial Suppl… | [25-finance-previews-what-is-due-and-sends-a-payment-run-for.jpg](part/25-finance-previews-what-is-due-and-sends-a-payment-run-for.jpg) |
| 26 | Rajesh Kulkarni, Managing Director | finds 'Pay ' in the work waiting for them | Approvals Actions an agent or service proposed. Nothing runs until you approve it. Pay 2 invoice(s) to 1 supplier(s), 45,499.00 in all, on 14 Dec 2026 Proposed by user 74 · 10 Nov 2026, 11:28 pm high { "run_no": "PR-2627-0001" } Note (optional) Approve Edit f… | [26-owner-finds-pay-in-the-work-waiting-for-them.jpg](part/26-owner-finds-pay-in-the-work-waiting-for-them.jpg) |
| 27 | Rajesh Kulkarni, Managing Director | approves the payment run | Approvals Actions an agent or service proposed. Nothing runs until you approve it. Nothing is waiting for you. | [27-owner-approves-the-payment-run.jpg](part/27-owner-approves-the-payment-run.jpg) |
| 28 | Anil Deshpande, Finance Manager | sees the approved run waiting for its bank file | ← Payments PR-2627-0001 Approved · ₹45,499 · run date 14 Dec 2026 Western Industrial Supplies PAY-2627-000001 · NEFT ₹45,499 PI-2627-000001 WIS/26-27/0511 ₹22,573 PI-2627-000002 WIS/26-27/0563 ₹22,926 Make the bank file | [28-finance-sees-the-approved-run-waiting-for-its-bank-file.jpg](part/28-finance-sees-the-approved-run-waiting-for-its-bank-file.jpg) |
| 29 | Anil Deshpande, Finance Manager | makes the bank file for the approved run | ← Payments PR-2627-0001 Bank file made · ₹45,499 · run date 14 Dec 2026 Western Industrial Supplies PAY-2627-000001 · NEFT ₹45,499 PI-2627-000001 WIS/26-27/0511 ₹22,573 PI-2627-000002 WIS/26-27/0563 ₹22,926 Show the bank file The bank has paid this run paymen… | [29-finance-makes-the-bank-file-for-the-approved-run.jpg](part/29-finance-makes-the-bank-file-for-the-approved-run.jpg) |
| 30 | Anil Deshpande, Finance Manager | records that the bank has paid, on 2026-12-14 | ← Payments PR-2627-0001 Paid · ₹45,499 · run date 14 Dec 2026 Western Industrial Supplies PAY-2627-000001 · NEFT · bank reference UTR26121400010001 · paid 14 Dec 2026 ₹45,499 PI-2627-000001 WIS/26-27/0511 ₹22,573 PI-2627-000002 WIS/26-27/0563 ₹22,926 Show the… | [30-finance-records-that-the-bank-has-paid-on-2026-12-14.jpg](part/30-finance-records-that-the-bank-has-paid-on-2026-12-14.jpg) |
| 31 | Rohan Mehta, Buyer | sees that the order is paid and closed in the notifications | Procurement overview RFQs, requisitions and spend across all categories — live from the database. My work · Buyer Nothing needs your action right now. 0 open RFQs 0 pending requisitions 0 vendors 0 purchase orders Needs attention 2 low View all Surfaced autom… | [31-buyer-sees-that-the-order-is-paid-and-closed-in-the-no.jpg](part/31-buyer-sees-that-the-order-is-paid-and-closed-in-the-no.jpg) |

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
- the order is bigger than one delivery
- goods received on the order line so far: 64.0000
- order status after the receipt: part received: part_received
- stock that has arrived at the delivery place equals what was received: 64.0000
- the order line is part received, the balance is still to come: part_received
- invoice for the first delivery: status (price and quantity agree with the order and what was received): approved
- invoice for the first delivery: price before tax and GST are the order's, the total is rounded to the rupee: (Decimal('19129.60'), Decimal('0.00'), Decimal('0.00'), Decimal('3443.33'), Decimal('22573.00'))
- invoice for the first delivery: the round-off line: 0.07
- invoice for the first delivery: due date from the supplier's payment terms, the day the goods were accepted (2026-11-06): 2026-12-21
- invoice for the first delivery: tax held back at source (far below the yearly limit): 0.00
- invoice for the first delivery was entered by the accounts clerk: 71
- the balance chaser: the supplier is not emailed on its own (reminders are set to propose), a task is made for the buyer: ([], [('PO-VAD-2627-00001', 'balance')])
- no mail went to the supplier yet: 0
- the buyer's task names the order and the balance, with the chaser written out
- goods received on the order line so far: 129.0000
- order status after the receipt: complete: received
- stock that has arrived at the delivery place equals what was received: 129.0000
- no more chasing once the order is complete: ([], [])
- the late-delivery alert is still open until the next daily run: 1
- the next daily run resolves the late-delivery alert: the balance has come: 0
- invoice for the balance: status (price and quantity agree with the order and what was received): approved
- invoice for the balance: price before tax and GST are the order's, the total is rounded to the rupee: (Decimal('19428.50'), Decimal('0.00'), Decimal('0.00'), Decimal('3497.13'), Decimal('22926.00'))
- invoice for the balance: the round-off line: 0.37
- invoice for the balance: due date from the supplier's payment terms, the day the goods were accepted (2026-11-12): 2026-12-27
- invoice for the balance: tax held back at source (far below the yearly limit): 0.00
- invoice for the balance was entered by the accounts clerk: 71
- both invoices together are the whole order: invoiced equals ordered and received: (Decimal('129.0000'), Decimal('129.0000'))
- both invoices matched on the quantity received so far: no blocked or held invoice: ['matched']
- the order is not closed yet: nothing is paid: received
- the second delivery was accepted on the day it came: 2026-11-12
- payment run status: proposed
- the payment: the invoice total less tax held back, no interest (one payment for all the invoices of the supplier): (Decimal('45499.00'), 'SUP-0006', Decimal('0.00'), Decimal('0.00'))
- the person who proposed the run cannot approve it (refused): 403
- no bank file before the owner approves (refused): 409
- payment run status after the owner approves: approved
- the bank file has one payment line: 2
- bank file line: the supplier's verified account and exactly the approved amount: ('000774658904', 'UBIN0962150', Decimal('45499.00'))
- invoice status of PI-2627-000001: paid
- invoice status of PI-2627-000002: paid
- order status: closed
- budget actual booked for PI-2627-000001: the price before tax: 19129.60
- budget actual booked for PI-2627-000002: the price before tax: 19428.50
- the supplier was told what was paid, with the bank reference (mails): 1
- order lines are closed with the order: ['closed']
- the audit log has a row for each of the 12 actions
- the audit log holds 58 rows in all, for 33 kinds of action
- the audit log, in order, with who did it: masters.import by service:system; user.create by service:system; role.grant by service:system; request.create by user:68; replenishment.run by user:68; request.submit by user:68; task.create by agent:supplier_comms,service:system; request.approve by user:78; request.approved by user:78; task.complete by user:75,user:78; rfq.create by service:sourcing; sourcing.decide by service:sourcing; proposal.create by service:sourcing,user:74; email.send by service:system; rfq.send by user:68; proposal.approved by user:68,user:73; quote.enter by user:68; rfq.award by user:68; po.create by service:ordering; po.submit by service:ordering; po.approve by user:75; po.approved by user:75; po.send by user:68; po.acknowledge by user:68; grn.post by user:70; invoice.enter by user:71; invoice.match by user:71; supplier.chase by agent:supplier_comms; payment_run.propose by user:74; payment_run.approve by user:73; payment_run.file by user:74; po.close by user:74; payment_run.paid by user:74
- audit: 'request.submit' (requester) was done by Rohan Mehta, Buyer: {'user:68'}
- audit: 'request.approve' (approver) was done by Prakash Gowda, Head of Stores: {'user:78'}
- audit: 'po.approve' (approver) was done by Vikram Joshi, Head of Procurement: {'user:75'}
- audit: 'payment_run.approve' (approver) was done by Rajesh Kulkarni, Managing Director: {'user:73'}
- audit: 'grn.post' (receiver) was done by Imran Pasha, Storekeeper: {'user:70'}
- audit: 'payment_run.propose' (payer) was done by Anil Deshpande, Finance Manager: {'user:74'}
- audit: 'payment_run.paid' (payer) was done by Anil Deshpande, Finance Manager: {'user:74'}
- audit: 'po.send' was done by Rohan Mehta, Buyer: {'user:68'}
- audit: 'invoice.enter' was done by Sunita Patil, Accounts: {'user:71'}
- audit: 'payment_run.file' was done by Anil Deshpande, Finance Manager: {'user:74'}
- separation of duties: requester, approver, receiver, payer are different people in this case
- the winning supplier is Western Industrial Supplies, paid once for the whole order
