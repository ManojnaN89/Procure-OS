# The whole application for a real user: the demo script from low stock to a paid invoice in a browser, every role guide followed as a new user, and every screen swept for errors, overflow and raw codes

Result: **passed**

## The 13 steps of the flow

| Step | Shown at | Picture |
|---|---|---|
| 1. Demand intake | #90: clicks Load from the ERP, chooses production_order_line.csv and clicks Load the file | [journey-01-demand-intake.jpg](steps/journey-01-demand-intake.jpg) |
| 2. Stock check | #91: clicks Run store check: what is short goes for approval | [journey-02-stock-check.jpg](steps/journey-02-stock-check.jpg) |
| 3. Requisition | #92: opens REQ-CKN-2627-00005 for MO-1: the shortfall lines, the estimate and the budget check | [journey-03-requisition.jpg](steps/journey-03-requisition.jpg) |
| 4. Approval | #94: opens the link in the email for REQ-CKN-2627-00005 and clicks Approve | [journey-04-approval.jpg](steps/journey-04-approval.jpg) |
| 5. Buying route | #95: opens REQ-CKN-2627-00005: each line shows its buying route and why | [journey-05-buying-route.jpg](steps/journey-05-buying-route.jpg) |
| 6. Quotes and supplier selection | #40: sees Shree Wires and Electricals Pvt Ltd recommended (the lowest total cost) and clicks Select these suppliers | [journey-06-quotes-and-supplier-selection.jpg](steps/journey-06-quotes-and-supplier-selection.jpg) |
| 7. Purchase order | #46: checks PO-CKN-2627-00004 and clicks Approve | [journey-07-purchase-order.jpg](steps/journey-07-purchase-order.jpg) |
| 8. Confirmation | #53: opens PO-CKN-2627-00003: Supplier's answer shows the new date; clicks Accept | [journey-08-confirmation.jpg](steps/journey-08-confirmation.jpg) |
| 9. Goods receipt | #60: sees the receipt waiting for inspection; keeps every seal and clicks Record inspection | [journey-09-goods-receipt.jpg](steps/journey-09-goods-receipt.jpg) |
| 10. Invoice | #65: clicks Check and enter | [journey-10-invoice.jpg](steps/journey-10-invoice.jpg) |
| 11. Three-way match | #69: opens the blocked invoice: the reason and the Next: line; clicks Explain why and what to do | [journey-11-three-way-match.jpg](steps/journey-11-three-way-match.jpg) |
| 12. Payment | #75: opens Dashboard: Approved runs: make the bank file; clicks Make the bank file and Show the bank file | [journey-12-payment.jpg](steps/journey-12-payment.jpg) |
| 13. Reconcile and close | #77: sees in the notifications that the orders are paid and closed | [journey-13-reconcile-and-close.jpg](steps/journey-13-reconcile-and-close.jpg) |
| 13. Reconcile and close: small suppliers | #72: opens Reconciliation, Small suppliers: the micro supplier's invoice and its legal deadline | [journey-13-1-small-suppliers.jpg](steps/journey-13-1-small-suppliers.jpg) |
| 13. Reconcile and close: order trace | #78: opens Reconciliation, Order trace; traces PO-CKN-2627-00001 from the request to the payment | [journey-13-2-order-trace.jpg](steps/journey-13-2-order-trace.jpg) |
| 13. Reconcile and close: received not invoiced | #79: opens Reconciliation, Received not invoiced as of 21 Dec 2026: only the seals' price difference | [journey-13-3-received-not-invoiced.jpg](steps/journey-13-3-received-not-invoiced.jpg) |
| 13. Reconcile and close: bank lines | #80: opens Reconciliation, Bank lines: the bank's charge, which no payment fits | [journey-13-4-bank-lines.jpg](steps/journey-13-4-bank-lines.jpg) |
| 13. Reconcile and close: supplier statement | #81: opens Reconciliation, Supplier statements; chooses the supplier and its statement; Import and match | [journey-13-5-supplier-statement.jpg](steps/journey-13-5-supplier-statement.jpg) |

| # | Who | Did | The screen then showed | Picture |
|---|---|---|---|---|
| 1 | Neha Kulkarni, Buyer (buyer@) | opens Requests: nothing yet, and says what to do next | Requests What people have asked for, and where each request is in approval Check stock levels Export Search this list All 0 No requests yet. Next: click Check stock levels to see what has fallen to its reorder point. | [01-buyer-opens-requests-nothing-yet-and-says-what-to-do-n.jpg](journey/01-buyer-opens-requests-nothing-yet-and-says-what-to-do-n.jpg) |
| 2 | Neha Kulkarni, Buyer (buyer@) | clicks Check stock levels: a preview, nothing is created yet | Requests What people have asked for, and where each request is in approval Check stock levels Export Stock check 1 item at or below their reorder point would become 1 request, about ₹2,310 in all. Of these: 1 approves automatically. Left out: 3 — Nothing to o… | [02-buyer-clicks-check-stock-levels-a-preview-nothing-is-c.jpg](journey/02-buyer-clicks-check-stock-levels-a-preview-nothing-is-c.jpg) |
| 3 | Neha Kulkarni, Buyer (buyer@) | clicks Create and submit | Requests What people have asked for, and where each request is in approval Check stock levels Export Created 1 request: 1 approved automatically, 0 waiting for approval. Search this list Approved 1 All 1 REQUEST AMOUNT REQUESTED BY STATUS V-belt B-42 REQ-CKN-… | [03-buyer-clicks-create-and-submit.jpg](journey/03-buyer-clicks-create-and-submit.jpg) |
| 4 | Rahul Deshmukh, Production Manager (production@) | opens Requests and clicks New request | ← Requests New request Say what you need. It is saved as a draft; you submit it for approval when it is right. Item Choose an item… Ball bearing 6204 (RM-06) Copper winding wire (RM-08) Enamel paint (RM-11) Export carton (PK-01) Hydraulic oil (SP-03) Impeller… | [04-production-opens-requests-and-clicks-new-request.jpg](journey/04-production-opens-requests-and-clicks-new-request.jpg) |
| 5 | Rahul Deshmukh, Production Manager (production@) | chooses Copper winding wire, 120, and clicks Save as draft | Requests Copper winding wire Draft · REQ-CKN-2627-00002 · ₹1,10,400 · Rahul Deshmukh · deliver to PLT-CKN · needed by 13 Nov 2026 1 Request 2 Approval 3 Price 4 Order 5 Receipt 6 Invoice 7 Payment Now: A draft: the person who raised it submits it for approval… | [05-production-chooses-copper-winding-wire-120-and-clicks-save-.jpg](journey/05-production-chooses-copper-winding-wire-120-and-clicks-save-.jpg) |
| 6 | Rahul Deshmukh, Production Manager (production@) | checks the line and clicks Submit for approval: it waits for Meera Iyer | Requests Copper winding wire Pending · REQ-CKN-2627-00002 · ₹1,10,400 · Rahul Deshmukh · deliver to PLT-CKN · needed by 13 Nov 2026 ✓ Request 2 Approval 3 Price 4 Order 5 Receipt 6 Invoice 7 Payment Now: Waiting for Meera Iyer to approve Approval Waiting for … | [06-production-checks-the-line-and-clicks-submit-for-approval-i.jpg](journey/06-production-checks-the-line-and-clicks-submit-for-approval-i.jpg) |
| 7 | Rahul Deshmukh, Production Manager (production@) | opens Requests and clicks New request | ← Requests New request Say what you need. It is saved as a draft; you submit it for approval when it is right. Item Choose an item… Ball bearing 6204 (RM-06) Copper winding wire (RM-08) Enamel paint (RM-11) Export carton (PK-01) Hydraulic oil (SP-03) Impeller… | [07-production-opens-requests-and-clicks-new-request.jpg](journey/07-production-opens-requests-and-clicks-new-request.jpg) |
| 8 | Rahul Deshmukh, Production Manager (production@) | chooses Mechanical seal 18 mm, 120, and clicks Save as draft | Requests Mechanical seal 18 mm Draft · REQ-CKN-2627-00003 · ₹11,400 · Rahul Deshmukh · deliver to PLT-CKN · needed by 13 Nov 2026 1 Request 2 Approval 3 Price 4 Order 5 Receipt 6 Invoice 7 Payment Now: A draft: the person who raised it submits it for approval… | [08-production-chooses-mechanical-seal-18-mm-120-and-clicks-sav.jpg](journey/08-production-chooses-mechanical-seal-18-mm-120-and-clicks-sav.jpg) |
| 9 | Rahul Deshmukh, Production Manager (production@) | checks the line and clicks Submit for approval: it waits for Vikram Joshi | Requests Mechanical seal 18 mm Pending · REQ-CKN-2627-00003 · ₹11,400 · Rahul Deshmukh · deliver to PLT-CKN · needed by 13 Nov 2026 ✓ Request 2 Approval 3 Price 4 Order 5 Receipt 6 Invoice 7 Payment Now: Waiting for Vikram Joshi to approve Approval Waiting fo… | [09-production-checks-the-line-and-clicks-submit-for-approval-i.jpg](journey/09-production-checks-the-line-and-clicks-submit-for-approval-i.jpg) |
| 10 | Rahul Deshmukh, Production Manager (production@) | opens Requests and clicks New request | ← Requests New request Say what you need. It is saved as a draft; you submit it for approval when it is right. Item Choose an item… Ball bearing 6204 (RM-06) Copper winding wire (RM-08) Enamel paint (RM-11) Export carton (PK-01) Hydraulic oil (SP-03) Impeller… | [10-production-opens-requests-and-clicks-new-request.jpg](journey/10-production-opens-requests-and-clicks-new-request.jpg) |
| 11 | Rahul Deshmukh, Production Manager (production@) | chooses Impeller 1 HP, 200, and clicks Save as draft | Requests Impeller 1 HP Draft · REQ-CKN-2627-00004 · ₹44,000 · Rahul Deshmukh · deliver to PLT-CKN · needed by 13 Nov 2026 1 Request 2 Approval 3 Price 4 Order 5 Receipt 6 Invoice 7 Payment Now: A draft: the person who raised it submits it for approval Approva… | [11-production-chooses-impeller-1-hp-200-and-clicks-save-as-dra.jpg](journey/11-production-chooses-impeller-1-hp-200-and-clicks-save-as-dra.jpg) |
| 12 | Rahul Deshmukh, Production Manager (production@) | checks the line and clicks Submit for approval: it waits for Vikram Joshi | Requests Impeller 1 HP Pending · REQ-CKN-2627-00004 · ₹44,000 · Rahul Deshmukh · deliver to PLT-CKN · needed by 13 Nov 2026 ✓ Request 2 Approval 3 Price 4 Order 5 Receipt 6 Invoice 7 Payment Now: Waiting for Vikram Joshi to approve Approval Waiting for approv… | [12-production-checks-the-line-and-clicks-submit-for-approval-i.jpg](journey/12-production-checks-the-line-and-clicks-submit-for-approval-i.jpg) |
| 13 | Vikram Joshi, Plant Head (planthead@) | opens Dashboard: Requests waiting for my approval lists both | Good morning, Vikram Joshi What needs you today, and where the purchases in progress stand. My work · Approver 2 things need you Start with the first of requests waiting for my approval, or pick any item below. Review Requests waiting for my approval 2 See al… | [13-planthead-opens-dashboard-requests-waiting-for-my-approval.jpg](journey/13-planthead-opens-dashboard-requests-waiting-for-my-approval.jpg) |
| 14 | Vikram Joshi, Plant Head (planthead@) | clicks REQ-CKN-2627-00003 in My work: Approve, Approve less, Return for changes and Reject are offered | Requests Mechanical seal 18 mm Pending · REQ-CKN-2627-00003 · ₹11,400 · Rahul Deshmukh · deliver to PLT-CKN · needed by 13 Nov 2026 ✓ Request 2 Approval 3 Price 4 Order 5 Receipt 6 Invoice 7 Payment Now: Waiting for Vikram Joshi to approve Approval Waiting fo… | [14-planthead-clicks-req-ckn-2627-00003-in-my-work-approve-app.jpg](journey/14-planthead-clicks-req-ckn-2627-00003-in-my-work-approve-app.jpg) |
| 15 | Vikram Joshi, Plant Head (planthead@) | opens Requests, Needs my decision, ticks both and clicks Approve selected | Requests What people have asked for, and where each request is in approval Approved 3 Waiting for approval 1 All 4 REQUEST AMOUNT REQUESTED BY STATUS Impeller 1 HP REQ-CKN-2627-00004 ₹44,000 Rahul Deshmukh Approved Mechanical seal 18 mm REQ-CKN-2627-00003 ₹11… | [15-planthead-opens-requests-needs-my-decision-ticks-both-and-.jpg](journey/15-planthead-opens-requests-needs-my-decision-ticks-both-and-.jpg) |
| 16 | Meera Iyer, Director (director@) | opens the link in the email: it shows the request and decides nothing yet | Request REQ-CKN-2627-00002 Rahul Deshmukh (Production) asks for 1 line worth ₹ 1,10,400.00 in all, for delivery at Plant, Chakan (Pune). Winding wire for the December pumps ITEM QUANTITY AMOUNT Copper winding wire 120 KGS ₹ 1,10,400.00 Estimated total ₹ 1,10,… | [16-director-opens-the-link-in-the-email-it-shows-the-request.jpg](journey/16-director-opens-the-link-in-the-email-it-shows-the-request.jpg) |
| 17 | Meera Iyer, Director (director@) | clicks Approve on that page | Request REQ-CKN-2627-00002 Approved. The buyer takes it from here. You can close this page. | [17-director-clicks-approve-on-that-page.jpg](journey/17-director-clicks-approve-on-that-page.jpg) |
| 18 | Neha Kulkarni, Buyer (buyer@) | opens the approved request REQ-CKN-2627-00003 and clicks Find suppliers | Requests Mechanical seal 18 mm Approved · REQ-CKN-2627-00003 · ₹11,400 · Rahul Deshmukh · deliver to PLT-CKN · needed by 13 Nov 2026 ✓ Request ✓ Approval 3 Price 4 Order 5 Receipt 6 Invoice 7 Payment Now: Approved: a buyer finds a price (a contract price, or … | [18-buyer-opens-the-approved-request-req-ckn-2627-00003-an.jpg](journey/18-buyer-opens-the-approved-request-req-ckn-2627-00003-an.jpg) |
| 19 | Rahul Deshmukh, Production Manager (production@) | sees in the notifications that the copper wire request was approved | Good morning, Rahul Deshmukh What needs you today, and where the purchases in progress stand. New request My work · Requester Nothing needs you. Next: raise a request for something you need. New request Where things stand 0 Waiting for approval 0 Being priced… | [19-production-sees-in-the-notifications-that-the-copper-wire-r.jpg](journey/19-production-sees-in-the-notifications-that-the-copper-wire-r.jpg) |
| 20 | Rahul Deshmukh, Production Manager (production@) | asks the Assistant: where is my request? | Assistant Ask where something stands, or say what you need in plain words. It only answers, or drafts something for you to check. Assistant ready Where is my request? P Your request REQ-CKN-2627-00002 is approved; the buyer takes it from here. Agent trail No … | [20-production-asks-the-assistant-where-is-my-request.jpg](journey/20-production-asks-the-assistant-where-is-my-request.jpg) |
| 21 | Neha Kulkarni, Buyer (buyer@) | opens Dashboard: Sent orders waiting for the supplier's acknowledgement includes PO-CKN-2627-00001; opens it | Purchase orders PO-CKN-2627-00001 · Deccan Castings Pvt Ltd Sent Touchless · ₹46,200 · for REQ-CKN-2627-00004 · contract CNT-0001 ✓ Request ✓ Approval ✓ Price 4 Order 5 Receipt 6 Invoice 7 Payment Now: Sent: waiting for the supplier to confirm and give a deli… | [21-buyer-opens-dashboard-sent-orders-waiting-for-the-supp.jpg](journey/21-buyer-opens-dashboard-sent-orders-waiting-for-the-supp.jpg) |
| 22 | Neha Kulkarni, Buyer (buyer@) | sees PO-CKN-2627-00001 marked Touchless, with why, and sent with no buyer | Purchase orders PO-CKN-2627-00001 · Deccan Castings Pvt Ltd Sent Touchless · ₹46,200 · for REQ-CKN-2627-00004 · contract CNT-0001 ✓ Request ✓ Approval ✓ Price 4 Order 5 Receipt 6 Invoice 7 Payment Now: Sent: waiting for the supplier to confirm and give a deli… | [22-buyer-sees-po-ckn-2627-00001-marked-touchless-with-why.jpg](journey/22-buyer-sees-po-ckn-2627-00001-marked-touchless-with-why.jpg) |
| 23 | Neha Kulkarni, Buyer (buyer@) | opens Dashboard: RFQs waiting to be sent includes RFQ-2627-00001 | Approvals Actions an agent or service proposed. Nothing runs until you approve it. Send RFQ-2627-00003 to 3 suppliers: a person confirms it first (automation setting “Send a request for quotation”: propose) Proposed by service sourcing · 21 Nov 2026, 08:51 am… | [23-buyer-opens-dashboard-rfqs-waiting-to-be-sent-includes.jpg](journey/23-buyer-opens-dashboard-rfqs-waiting-to-be-sent-includes.jpg) |
| 24 | Neha Kulkarni, Buyer (buyer@) | clicks Approve to send the quotation request RFQ-2627-00001 | Approvals Actions an agent or service proposed. Nothing runs until you approve it. Send RFQ-2627-00003 to 3 suppliers: a person confirms it first (automation setting “Send a request for quotation”: propose) Proposed by service sourcing · 21 Nov 2026, 08:51 am… | [24-buyer-clicks-approve-to-send-the-quotation-request-rfq.jpg](journey/24-buyer-clicks-approve-to-send-the-quotation-request-rfq.jpg) |
| 25 | Neha Kulkarni, Buyer (buyer@) | opens Dashboard: RFQs waiting to be sent includes RFQ-2627-00002 | Approvals Actions an agent or service proposed. Nothing runs until you approve it. Send RFQ-2627-00003 to 3 suppliers: a person confirms it first (automation setting “Send a request for quotation”: propose) Proposed by service sourcing · 21 Nov 2026, 08:51 am… | [25-buyer-opens-dashboard-rfqs-waiting-to-be-sent-includes.jpg](journey/25-buyer-opens-dashboard-rfqs-waiting-to-be-sent-includes.jpg) |
| 26 | Neha Kulkarni, Buyer (buyer@) | clicks Approve to send the quotation request RFQ-2627-00002 | Approvals Actions an agent or service proposed. Nothing runs until you approve it. Send RFQ-2627-00003 to 3 suppliers: a person confirms it first (automation setting “Send a request for quotation”: propose) Proposed by service sourcing · 21 Nov 2026, 08:51 am… | [26-buyer-clicks-approve-to-send-the-quotation-request-rfq.jpg](journey/26-buyer-clicks-approve-to-send-the-quotation-request-rfq.jpg) |
| 27 | Neha Kulkarni, Buyer (buyer@) | opens Dashboard: RFQs waiting to be sent includes RFQ-2627-00003 | Approvals Actions an agent or service proposed. Nothing runs until you approve it. Send RFQ-2627-00003 to 3 suppliers: a person confirms it first (automation setting “Send a request for quotation”: propose) Proposed by service sourcing · 21 Nov 2026, 08:51 am… | [27-buyer-opens-dashboard-rfqs-waiting-to-be-sent-includes.jpg](journey/27-buyer-opens-dashboard-rfqs-waiting-to-be-sent-includes.jpg) |
| 28 | Neha Kulkarni, Buyer (buyer@) | clicks Approve to send the quotation request RFQ-2627-00003 | Approvals Actions an agent or service proposed. Nothing runs until you approve it. Nothing is waiting for you. New proposals appear here as agents and services make them. | [28-buyer-clicks-approve-to-send-the-quotation-request-rfq.jpg](journey/28-buyer-clicks-approve-to-send-the-quotation-request-rfq.jpg) |
| 29 | Western Industrial Supplies (supplier) | replies to RFQ-2627-00001 by email: 205 a belt | The mail is stored with the quotation request and read into a draft quote that waits for the buyer. |  |
| 30 | Neha Kulkarni, Buyer (buyer@) | opens RFQ-2627-00001: the reply read from email is waiting as a draft | ← Quotations Maintenance spares: 1 item for REQ-CKN-2627-00001 RFQ-2627-00001 · Sent · for REQ-CKN-2627-00001 · reply by 30 Nov 2026 · sent 21 Nov 2026 · deliver to Plant, Chakan (Pune) · minimum quotes 2 (rule: category MRO) ✓ Request ✓ Approval 3 Price 4 Or… | [30-buyer-opens-rfq-2627-00001-the-reply-read-from-email-i.jpg](journey/30-buyer-opens-rfq-2627-00001-the-reply-read-from-email-i.jpg) |
| 31 | Neha Kulkarni, Buyer (buyer@) | checks the reply against the email and confirms the quote | RFQ-2627-00001 Loading… | [31-buyer-checks-the-reply-against-the-email-and-confirms-.jpg](journey/31-buyer-checks-the-reply-against-the-email-and-confirms-.jpg) |
| 32 | Neha Kulkarni, Buyer (buyer@) | sees one quote where two are wanted: writes the reason and clicks Select these suppliers | ← Quotations Maintenance spares: 1 item for REQ-CKN-2627-00001 RFQ-2627-00001 · Awarded · for REQ-CKN-2627-00001 · reply by 30 Nov 2026 · sent 21 Nov 2026 · deliver to Plant, Chakan (Pune) · minimum quotes 2 (rule: category MRO) ✓ Request ✓ Approval ✓ Price 4… | [32-buyer-sees-one-quote-where-two-are-wanted-writes-the-r.jpg](journey/32-buyer-sees-one-quote-where-two-are-wanted-writes-the-r.jpg) |
| 33 | Neha Kulkarni, Buyer (buyer@) | clicks Enter a quote for Gujarat Bearings and Seals: 94, and Save the quote | RFQ-2627-00002 Loading… | [33-buyer-clicks-enter-a-quote-for-gujarat-bearings-and-se.jpg](journey/33-buyer-clicks-enter-a-quote-for-gujarat-bearings-and-se.jpg) |
| 34 | Neha Kulkarni, Buyer (buyer@) | clicks Enter a quote for Deccan Castings Pvt Ltd: 99, and Save the quote | RFQ-2627-00002 Loading… | [34-buyer-clicks-enter-a-quote-for-deccan-castings-pvt-ltd.jpg](journey/34-buyer-clicks-enter-a-quote-for-deccan-castings-pvt-ltd.jpg) |
| 35 | Neha Kulkarni, Buyer (buyer@) | clicks Enter a quote for Shree Wires and Electricals Pvt Ltd: 97, and Save the quote | RFQ-2627-00002 Loading… | [35-buyer-clicks-enter-a-quote-for-shree-wires-and-electri.jpg](journey/35-buyer-clicks-enter-a-quote-for-shree-wires-and-electri.jpg) |
| 36 | Neha Kulkarni, Buyer (buyer@) | sees Gujarat Bearings and Seals recommended (the lowest total cost) and clicks Select these suppliers | ← Quotations Raw materials: 1 item for REQ-CKN-2627-00003 RFQ-2627-00002 · Awarded · for REQ-CKN-2627-00003 · reply by 13 Nov 2026 · sent 21 Nov 2026 · deliver to Plant, Chakan (Pune) · minimum quotes 2 (rule: category RAW) ✓ Request ✓ Approval ✓ Price 4 Orde… | [36-buyer-sees-gujarat-bearings-and-seals-recommended-the-.jpg](journey/36-buyer-sees-gujarat-bearings-and-seals-recommended-the-.jpg) |
| 37 | Neha Kulkarni, Buyer (buyer@) | clicks Enter a quote for Shree Wires and Electricals Pvt Ltd: 905, and Save the quote | RFQ-2627-00003 Loading… | [37-buyer-clicks-enter-a-quote-for-shree-wires-and-electri.jpg](journey/37-buyer-clicks-enter-a-quote-for-shree-wires-and-electri.jpg) |
| 38 | Neha Kulkarni, Buyer (buyer@) | clicks Enter a quote for Pune Copper Co.: 915, and Save the quote | RFQ-2627-00003 Loading… | [38-buyer-clicks-enter-a-quote-for-pune-copper-co-915-and-.jpg](journey/38-buyer-clicks-enter-a-quote-for-pune-copper-co-915-and-.jpg) |
| 39 | Neha Kulkarni, Buyer (buyer@) | clicks Enter a quote for Deccan Castings Pvt Ltd: 930, and Save the quote | RFQ-2627-00003 Loading… | [39-buyer-clicks-enter-a-quote-for-deccan-castings-pvt-ltd.jpg](journey/39-buyer-clicks-enter-a-quote-for-deccan-castings-pvt-ltd.jpg) |
| 40 | Neha Kulkarni, Buyer (buyer@) | sees Shree Wires and Electricals Pvt Ltd recommended (the lowest total cost) and clicks Select these suppliers | ← Quotations Raw materials: 1 item for REQ-CKN-2627-00002 RFQ-2627-00003 · Awarded · for REQ-CKN-2627-00002 · reply by 30 Nov 2026 · sent 21 Nov 2026 · deliver to Plant, Chakan (Pune) · minimum quotes 2 (rule: category RAW) ✓ Request ✓ Approval ✓ Price 4 Orde… | [40-buyer-sees-shree-wires-and-electricals-pvt-ltd-recomme.jpg](journey/40-buyer-sees-shree-wires-and-electricals-pvt-ltd-recomme.jpg) |
| 41 | Sanjay Rao, Purchase Manager (purchasemanager@) | opens Dashboard: Orders waiting for my approval includes PO-CKN-2627-00002 | Purchase orders PO-CKN-2627-00002 · Western Industrial Supplies Pending · ₹2,660.90 · for REQ-CKN-2627-00001 · RFQ-2627-00001 ✓ Request ✓ Approval ✓ Price 4 Order 5 Receipt 6 Invoice 7 Payment Now: Waiting for Sanjay Rao to approve the order Approval · Purcha… | [41-purchasemanager-opens-dashboard-orders-waiting-for-my-approval-i.jpg](journey/41-purchasemanager-opens-dashboard-orders-waiting-for-my-approval-i.jpg) |
| 42 | Sanjay Rao, Purchase Manager (purchasemanager@) | checks PO-CKN-2627-00002 and clicks Approve | Purchase orders PO-CKN-2627-00002 · Western Industrial Supplies Approved · ₹2,660.90 · for REQ-CKN-2627-00001 · RFQ-2627-00001 ✓ Request ✓ Approval ✓ Price 4 Order 5 Receipt 6 Invoice 7 Payment Now: Approved: a buyer sends it to the supplier ITEM QTY PRICE GS… | [42-purchasemanager-checks-po-ckn-2627-00002-and-clicks-approve.jpg](journey/42-purchasemanager-checks-po-ckn-2627-00002-and-clicks-approve.jpg) |
| 43 | Sanjay Rao, Purchase Manager (purchasemanager@) | opens Dashboard: Orders waiting for my approval includes PO-CKN-2627-00003 | Purchase orders PO-CKN-2627-00003 · Gujarat Bearings and Seals Pending · ₹13,310.40 · for REQ-CKN-2627-00003 · RFQ-2627-00002 ✓ Request ✓ Approval ✓ Price 4 Order 5 Receipt 6 Invoice 7 Payment Now: Waiting for Sanjay Rao to approve the order Approval · Purcha… | [43-purchasemanager-opens-dashboard-orders-waiting-for-my-approval-i.jpg](journey/43-purchasemanager-opens-dashboard-orders-waiting-for-my-approval-i.jpg) |
| 44 | Sanjay Rao, Purchase Manager (purchasemanager@) | checks PO-CKN-2627-00003 and clicks Approve | Purchase orders PO-CKN-2627-00003 · Gujarat Bearings and Seals Approved · ₹13,310.40 · for REQ-CKN-2627-00003 · RFQ-2627-00002 ✓ Request ✓ Approval ✓ Price 4 Order 5 Receipt 6 Invoice 7 Payment Now: Approved: a buyer sends it to the supplier ITEM QTY PRICE GS… | [44-purchasemanager-checks-po-ckn-2627-00003-and-clicks-approve.jpg](journey/44-purchasemanager-checks-po-ckn-2627-00003-and-clicks-approve.jpg) |
| 45 | Sanjay Rao, Purchase Manager (purchasemanager@) | opens Dashboard: Orders waiting for my approval includes PO-CKN-2627-00004 | Purchase orders PO-CKN-2627-00004 · Shree Wires and Electricals Pvt Ltd Pending · ₹1,28,148 · for REQ-CKN-2627-00002 · RFQ-2627-00003 ✓ Request ✓ Approval ✓ Price 4 Order 5 Receipt 6 Invoice 7 Payment Now: Waiting for Sanjay Rao to approve the order Approval … | [45-purchasemanager-opens-dashboard-orders-waiting-for-my-approval-i.jpg](journey/45-purchasemanager-opens-dashboard-orders-waiting-for-my-approval-i.jpg) |
| 46 | Sanjay Rao, Purchase Manager (purchasemanager@) | checks PO-CKN-2627-00004 and clicks Approve | Purchase orders PO-CKN-2627-00004 · Shree Wires and Electricals Pvt Ltd Approved · ₹1,28,148 · for REQ-CKN-2627-00002 · RFQ-2627-00003 ✓ Request ✓ Approval ✓ Price 4 Order 5 Receipt 6 Invoice 7 Payment Now: Approved: a buyer sends it to the supplier ITEM QTY … | [46-purchasemanager-checks-po-ckn-2627-00004-and-clicks-approve.jpg](journey/46-purchasemanager-checks-po-ckn-2627-00004-and-clicks-approve.jpg) |
| 47 | Neha Kulkarni, Buyer (buyer@) | opens the touchless order PO-CKN-2627-00001, already sent to the supplier | Purchase orders PO-CKN-2627-00001 · Deccan Castings Pvt Ltd Sent Touchless · ₹46,200 · for REQ-CKN-2627-00004 · contract CNT-0001 ✓ Request ✓ Approval ✓ Price 4 Order 5 Receipt 6 Invoice 7 Payment Now: Sent: waiting for the supplier to confirm and give a deli… | [47-buyer-opens-the-touchless-order-po-ckn-2627-00001-alre.jpg](journey/47-buyer-opens-the-touchless-order-po-ckn-2627-00001-alre.jpg) |
| 48 | Deccan Castings Pvt Ltd (supplier, no login) | opens the link in the email for PO-CKN-2627-00001, enters 2026-11-12 and clicks Accept the order | Order PO-CKN-2627-00001 accepted Thank you. The buyer has been told. When you ship, record the dispatch here (we have emailed you this link too): record a dispatch. | [48-supplier-SUP-001-opens-the-link-in-the-email-for-po-ckn-2627-0000.jpg](journey/48-supplier-SUP-001-opens-the-link-in-the-email-for-po-ckn-2627-0000.jpg) |
| 49 | Neha Kulkarni, Buyer (buyer@) | opens Dashboard: Approved orders to send includes PO-CKN-2627-00002; clicks Send to supplier | Purchase orders PO-CKN-2627-00002 · Western Industrial Supplies Sent · ₹2,660.90 · for REQ-CKN-2627-00001 · RFQ-2627-00001 ✓ Request ✓ Approval ✓ Price 4 Order 5 Receipt 6 Invoice 7 Payment Now: Sent: waiting for the supplier to confirm and give a delivery da… | [49-buyer-opens-dashboard-approved-orders-to-send-includes.jpg](journey/49-buyer-opens-dashboard-approved-orders-to-send-includes.jpg) |
| 50 | Neha Kulkarni, Buyer (buyer@) | clicks Record acknowledgement with the promised date 2026-11-12 | Purchase orders PO-CKN-2627-00002 · Western Industrial Supplies Acknowledged · ₹2,660.90 · for REQ-CKN-2627-00001 · RFQ-2627-00001 ✓ Request ✓ Approval ✓ Price ✓ Order 5 Receipt 6 Invoice 7 Payment Now: Confirmed: waiting for the goods to arrive at the store … | [50-buyer-clicks-record-acknowledgement-with-the-promised-.jpg](journey/50-buyer-clicks-record-acknowledgement-with-the-promised-.jpg) |
| 51 | Neha Kulkarni, Buyer (buyer@) | opens Dashboard: Approved orders to send includes PO-CKN-2627-00003; clicks Send to supplier | Purchase orders PO-CKN-2627-00003 · Gujarat Bearings and Seals Sent · ₹13,310.40 · for REQ-CKN-2627-00003 · RFQ-2627-00002 ✓ Request ✓ Approval ✓ Price 4 Order 5 Receipt 6 Invoice 7 Payment Now: Sent: waiting for the supplier to confirm and give a delivery da… | [51-buyer-opens-dashboard-approved-orders-to-send-includes.jpg](journey/51-buyer-opens-dashboard-approved-orders-to-send-includes.jpg) |
| 52 | Gujarat Bearings and Seals (supplier, no login) | opens the link for PO-CKN-2627-00003 and asks to deliver on 2026-11-15: Send the change to the buyer | Order PO-CKN-2627-00003 Thank you. The buyer has the change you asked for and will reply by email. Until then the order stands as it is. You can close this page. | [52-supplier-SUP-002-opens-the-link-for-po-ckn-2627-00003-and-asks-to.jpg](journey/52-supplier-SUP-002-opens-the-link-for-po-ckn-2627-00003-and-asks-to.jpg) |
| 53 | Neha Kulkarni, Buyer (buyer@) | opens PO-CKN-2627-00003: Supplier's answer shows the new date; clicks Accept | Purchase orders PO-CKN-2627-00003 · Gujarat Bearings and Seals Acknowledged · ₹13,310.40 · for REQ-CKN-2627-00003 · RFQ-2627-00002 ✓ Request ✓ Approval ✓ Price ✓ Order 5 Receipt 6 Invoice 7 Payment Now: Confirmed: waiting for the goods to arrive at the store … | [53-buyer-opens-po-ckn-2627-00003-supplier-s-answer-shows-.jpg](journey/53-buyer-opens-po-ckn-2627-00003-supplier-s-answer-shows-.jpg) |
| 54 | Neha Kulkarni, Buyer (buyer@) | opens Dashboard: Approved orders to send includes PO-CKN-2627-00004; clicks Send to supplier | Purchase orders PO-CKN-2627-00004 · Shree Wires and Electricals Pvt Ltd Sent · ₹1,28,148 · for REQ-CKN-2627-00002 · RFQ-2627-00003 ✓ Request ✓ Approval ✓ Price 4 Order 5 Receipt 6 Invoice 7 Payment Now: Sent: waiting for the supplier to confirm and give a del… | [54-buyer-opens-dashboard-approved-orders-to-send-includes.jpg](journey/54-buyer-opens-dashboard-approved-orders-to-send-includes.jpg) |
| 55 | Neha Kulkarni, Buyer (buyer@) | clicks Record acknowledgement with the promised date 2026-11-12 | Purchase orders PO-CKN-2627-00004 · Shree Wires and Electricals Pvt Ltd Acknowledged · ₹1,28,148 · for REQ-CKN-2627-00002 · RFQ-2627-00003 ✓ Request ✓ Approval ✓ Price ✓ Order 5 Receipt 6 Invoice 7 Payment Now: Confirmed: waiting for the goods to arrive at th… | [55-buyer-clicks-record-acknowledgement-with-the-promised-.jpg](journey/55-buyer-clicks-record-acknowledgement-with-the-promised-.jpg) |
| 56 | Sunil Pawar, Storekeeper (store@) | opens Goods receipt, clicks Receive goods on PO-CKN-2627-00001, enters 120 and clicks Post receipt | ← Goods receipt GRN-CKN-2627-00001 for PO-CKN-2627-00001 · 21 Nov 2026 · received by U02 Challan DC/2611/0001 · deliver to PLT-CKN ITEM RECEIVED ACCEPTED REJECTED Impeller 1 HP RM-03 120 NOS 120 0 | [56-store-opens-goods-receipt-clicks-receive-goods-on-po-c.jpg](journey/56-store-opens-goods-receipt-clicks-receive-goods-on-po-c.jpg) |
| 57 | Neha Kulkarni, Buyer (buyer@) | is told in the app that part of the goods arrived | Good morning, Neha Kulkarni What needs you today, and where the purchases in progress stand. My work · Buyer Nothing needs you. Next: check which stock is at its reorder point. Check stock levels Where things stand 0 Waiting for approval 0 Being priced 0 Orde… | [57-buyer-is-told-in-the-app-that-part-of-the-goods-arrive.jpg](journey/57-buyer-is-told-in-the-app-that-part-of-the-goods-arrive.jpg) |
| 58 | Sunil Pawar, Storekeeper (store@) | opens Goods receipt, clicks Receive goods on PO-CKN-2627-00001, enters 80 and clicks Post receipt | ← Goods receipt GRN-CKN-2627-00002 for PO-CKN-2627-00001 · 21 Nov 2026 · received by U02 Challan DC/2611/0002 · deliver to PLT-CKN ITEM RECEIVED ACCEPTED REJECTED Impeller 1 HP RM-03 80 NOS 80 0 | [58-store-opens-goods-receipt-clicks-receive-goods-on-po-c.jpg](journey/58-store-opens-goods-receipt-clicks-receive-goods-on-po-c.jpg) |
| 59 | Sunil Pawar, Storekeeper (store@) | opens Goods receipt, clicks Receive goods on PO-CKN-2627-00003, enters 120.0000 and clicks Post receipt | ← Goods receipt GRN-CKN-2627-00003 for PO-CKN-2627-00003 · 21 Nov 2026 · received by U02 · waiting for inspection Challan DC/2611/0003 · deliver to PLT-CKN ITEM RECEIVED ACCEPTED REJECTED Mechanical seal 18 mm RM-07 120 NOS Record inspection | [59-store-opens-goods-receipt-clicks-receive-goods-on-po-c.jpg](journey/59-store-opens-goods-receipt-clicks-receive-goods-on-po-c.jpg) |
| 60 | Sunil Pawar, Storekeeper (store@) | sees the receipt waiting for inspection; keeps every seal and clicks Record inspection | ← Goods receipt GRN-CKN-2627-00003 for PO-CKN-2627-00003 · 21 Nov 2026 · received by U02 · inspection passed Challan DC/2611/0003 · deliver to PLT-CKN ITEM RECEIVED ACCEPTED REJECTED Mechanical seal 18 mm RM-07 120 NOS 120 0 | [60-store-sees-the-receipt-waiting-for-inspection-keeps-ev.jpg](journey/60-store-sees-the-receipt-waiting-for-inspection-keeps-ev.jpg) |
| 61 | Sunil Pawar, Storekeeper (store@) | opens Goods receipt, clicks Receive goods on PO-CKN-2627-00004, enters 120.0000 and clicks Post receipt | ← Goods receipt GRN-CKN-2627-00004 for PO-CKN-2627-00004 · 21 Nov 2026 · received by U02 Challan DC/2611/0004 · deliver to PLT-CKN ITEM RECEIVED ACCEPTED REJECTED Copper winding wire RM-08 120 KGS 120 0 | [61-store-opens-goods-receipt-clicks-receive-goods-on-po-c.jpg](journey/61-store-opens-goods-receipt-clicks-receive-goods-on-po-c.jpg) |
| 62 | Sunil Pawar, Storekeeper (store@) | opens Goods receipt, clicks Receive goods on PO-CKN-2627-00002, enters 11.0000 and clicks Post receipt | ← Goods receipt GRN-CKN-2627-00005 for PO-CKN-2627-00002 · 21 Nov 2026 · received by U02 Challan DC/2611/0002 · deliver to PLT-CKN ITEM RECEIVED ACCEPTED REJECTED V-belt B-42 SP-01 11 NOS 11 0 | [62-store-opens-goods-receipt-clicks-receive-goods-on-po-c.jpg](journey/62-store-opens-goods-receipt-clicks-receive-goods-on-po-c.jpg) |
| 63 | Rahul Deshmukh, Production Manager (production@) | is told in the app that the goods for their request arrived | Good morning, Rahul Deshmukh What needs you today, and where the purchases in progress stand. New request My work · Requester Nothing needs you. Next: raise a request for something you need. New request Where things stand 0 Waiting for approval 0 Being priced… | [63-production-is-told-in-the-app-that-the-goods-for-their-requ.jpg](journey/63-production-is-told-in-the-app-that-the-goods-for-their-requ.jpg) |
| 64 | Priya Shah, AP Executive (ap@) | opens Dashboard (goods waiting for an invoice), then Invoices: Upload an invoice, SW-26-27-1101.pdf with its e-invoice QR code | Invoices Supplier invoices, matched to the order and the goods received. Blocked ones wait for a person. Upload an invoice Enter an invoice Export Invoices read from documents (1) Shree Wires and Electricals Pvt Ltd · SW/26-27/1101 · 6 Nov 2026 · ₹1,28,148 · … | [64-ap-opens-dashboard-goods-waiting-for-an-invoice-the.jpg](journey/64-ap-opens-dashboard-goods-waiting-for-an-invoice-the.jpg) |
| 65 | Priya Shah, AP Executive (ap@) | clicks Check and enter | Invoices PI-2627-000001 · Shree Wires and Electricals Pvt Ltd Approved for payment · ₹1,28,148 · supplier's number SW/26-27/1101 · 6 Nov 2026 ✓ Request ✓ Approval ✓ Price ✓ Order ✓ Receipt ✓ Invoice 7 Payment Now: Matched and approved: finance puts it in the … | [65-ap-clicks-check-and-enter.jpg](journey/65-ap-clicks-check-and-enter.jpg) |
| 66 | Priya Shah, AP Executive (ap@) | opens Invoices, clicks Enter an invoice: DC/2611/0207, 200 at 220.00, and Enter and match | Invoices PI-2627-000002 · Deccan Castings Pvt Ltd Approved for payment · ₹46,200 · supplier's number DC/2611/0207 · 6 Nov 2026 ✓ Request ✓ Approval ✓ Price ✓ Order ✓ Receipt ✓ Invoice 7 Payment Now: Matched and approved: finance puts it in the next payment ru… | [66-ap-opens-invoices-clicks-enter-an-invoice-dc-2611-0.jpg](journey/66-ap-opens-invoices-clicks-enter-an-invoice-dc-2611-0.jpg) |
| 67 | Priya Shah, AP Executive (ap@) | opens Invoices, clicks Enter an invoice: WIS/2611/0031, 11.000 at 205, and Enter and match | Invoices PI-2627-000003 · Western Industrial Supplies Approved for payment · ₹2,661 · supplier's number WIS/2611/0031 · 6 Nov 2026 ✓ Request ✓ Approval ✓ Price ✓ Order ✓ Receipt ✓ Invoice 7 Payment Now: Matched and approved: finance puts it in the next paymen… | [67-ap-opens-invoices-clicks-enter-an-invoice-wis-2611-.jpg](journey/67-ap-opens-invoices-clicks-enter-an-invoice-wis-2611-.jpg) |
| 68 | Priya Shah, AP Executive (ap@) | opens Invoices, clicks Enter an invoice: GBS/2611/0418, 120 at 105.28, and Enter and match | Invoices PI-2627-000004 · Gujarat Bearings and Seals Blocked · ₹14,908 · supplier's number GBS/2611/0418 · 6 Nov 2026 ✓ Request ✓ Approval ✓ Price ✓ Order ✓ Receipt 6 Invoice 7 Payment Now: Blocked by the match: accounts fixes it, or asks another person to re… | [68-ap-opens-invoices-clicks-enter-an-invoice-gbs-2611-.jpg](journey/68-ap-opens-invoices-clicks-enter-an-invoice-gbs-2611-.jpg) |
| 69 | Priya Shah, AP Executive (ap@) | opens the blocked invoice: the reason and the Next: line; clicks Explain why and what to do | Invoices PI-2627-000004 · Gujarat Bearings and Seals Blocked · ₹14,908 · supplier's number GBS/2611/0418 · 6 Nov 2026 ✓ Request ✓ Approval ✓ Price ✓ Order ✓ Receipt 6 Invoice 7 Payment Now: Blocked by the match: accounts fixes it, or asks another person to re… | [69-ap-opens-the-blocked-invoice-the-reason-and-the-nex.jpg](journey/69-ap-opens-the-blocked-invoice-the-reason-and-the-nex.jpg) |
| 70 | Priya Shah, AP Executive (ap@) | clicks Ask to pay it anyway, writes the reason and clicks Send | Invoices PI-2627-000004 · Gujarat Bearings and Seals Blocked · ₹14,908 · supplier's number GBS/2611/0418 · 6 Nov 2026 ✓ Request ✓ Approval ✓ Price ✓ Order ✓ Receipt 6 Invoice 7 Payment Now: Blocked by the match: accounts fixes it, or asks another person to re… | [70-ap-clicks-ask-to-pay-it-anyway-writes-the-reason-an.jpg](journey/70-ap-clicks-ask-to-pay-it-anyway-writes-the-reason-an.jpg) |
| 71 | Sanjay Rao, Purchase Manager (purchasemanager@) | opens Dashboard: Blocked invoices to release; Admin, Approvals, clicks Approve | Approvals Actions an agent or service proposed. Nothing runs until you approve it. Nothing is waiting for you. New proposals appear here as agents and services make them. | [71-purchasemanager-opens-dashboard-blocked-invoices-to-release-admi.jpg](journey/71-purchasemanager-opens-dashboard-blocked-invoices-to-release-admi.jpg) |
| 72 | Anil Mehta, Finance Manager (finance@) | opens Reconciliation, Small suppliers: the micro supplier's invoice and its legal deadline | Reconciliation The books and the flow agree: what was received and not invoiced, supplier statements, bank lines and small suppliers' deadlines Export Order trace Received not invoiced Small suppliers Supplier statements Bank lines Late, or due within days Sh… | [72-finance-opens-reconciliation-small-suppliers-the-micro-s.jpg](journey/72-finance-opens-reconciliation-small-suppliers-the-micro-s.jpg) |
| 73 | Anil Mehta, Finance Manager (finance@) | opens Dashboard: Invoices due: plan a payment run; Payments, See what is due, Send the run for approval | ← Payments PR-2627-0001 Waiting for approval · ₹1,91,917 · run date 21 Dec 2026 ✓ Request ✓ Approval ✓ Price ✓ Order ✓ Receipt ✓ Invoice 7 Payment Now: Waiting for the owner to approve the run Waiting for the owner to approve: Pay 4 invoices to 4 suppliers, ₹… | [73-finance-opens-dashboard-invoices-due-plan-a-payment-run-.jpg](journey/73-finance-opens-dashboard-invoices-due-plan-a-payment-run-.jpg) |
| 74 | Meera Iyer, Director (director@) | opens Dashboard: Payment runs to approve; Admin, Approvals, clicks Approve | Approvals Actions an agent or service proposed. Nothing runs until you approve it. Nothing is waiting for you. New proposals appear here as agents and services make them. | [74-director-opens-dashboard-payment-runs-to-approve-admin-ap.jpg](journey/74-director-opens-dashboard-payment-runs-to-approve-admin-ap.jpg) |
| 75 | Anil Mehta, Finance Manager (finance@) | opens Dashboard: Approved runs: make the bank file; clicks Make the bank file and Show the bank file | ← Payments PR-2627-0001 Bank file made · ₹1,91,917 · run date 21 Dec 2026 ✓ Request ✓ Approval ✓ Price ✓ Order ✓ Receipt ✓ Invoice 7 Payment Now: Bank file made: finance marks the run paid once the bank has paid Gujarat Bearings and Seals PAY-2627-000001 · NE… | [75-finance-opens-dashboard-approved-runs-make-the-bank-file.jpg](journey/75-finance-opens-dashboard-approved-runs-make-the-bank-file.jpg) |
| 76 | Anil Mehta, Finance Manager (finance@) | chooses the bank's statement and clicks Import the bank statement | ← Payments PR-2627-0001 Paid · ₹1,91,917 · run date 21 Dec 2026 ✓ Request ✓ Approval ✓ Price ✓ Order ✓ Receipt ✓ Invoice ✓ Payment Done: The bank has paid; the suppliers have their payment advice Gujarat Bearings and Seals PAY-2627-000001 · NEFT · bank refere… | [76-finance-chooses-the-bank-s-statement-and-clicks-import-t.jpg](journey/76-finance-chooses-the-bank-s-statement-and-clicks-import-t.jpg) |
| 77 | Neha Kulkarni, Buyer (buyer@) | sees in the notifications that the orders are paid and closed | Good morning, Neha Kulkarni What needs you today, and where the purchases in progress stand. My work · Buyer Nothing needs you. Next: check which stock is at its reorder point. Check stock levels Where things stand 0 Waiting for approval 0 Being priced 0 Orde… | [77-buyer-sees-in-the-notifications-that-the-orders-are-pa.jpg](journey/77-buyer-sees-in-the-notifications-that-the-orders-are-pa.jpg) |
| 78 | Anil Mehta, Finance Manager (finance@) | opens Reconciliation, Order trace; traces PO-CKN-2627-00001 from the request to the payment | Reconciliation The books and the flow agree: what was received and not invoiced, supplier statements, bank lines and small suppliers' deadlines Export Order trace Received not invoiced Small suppliers Supplier statements Bank lines Trace one document Any numb… | [78-finance-opens-reconciliation-order-trace-traces-po-ckn-2.jpg](journey/78-finance-opens-reconciliation-order-trace-traces-po-ckn-2.jpg) |
| 79 | Priya Shah, AP Executive (ap@) | opens Reconciliation, Received not invoiced as of 21 Dec 2026: only the seals' price difference | Reconciliation The books and the flow agree: what was received and not invoiced, supplier statements, bank lines and small suppliers' deadlines Export Order trace Received not invoiced Small suppliers Supplier statements Bank lines As of Show Goods accepted (… | [79-ap-opens-reconciliation-received-not-invoiced-as-of.jpg](journey/79-ap-opens-reconciliation-received-not-invoiced-as-of.jpg) |
| 80 | Anil Mehta, Finance Manager (finance@) | opens Reconciliation, Bank lines: the bank's charge, which no payment fits | Reconciliation The books and the flow agree: what was received and not invoiced, supplier statements, bank lines and small suppliers' deadlines Export Order trace Received not invoiced Small suppliers Supplier statements Bank lines Bank statement lines no pay… | [80-finance-opens-reconciliation-bank-lines-the-bank-s-charg.jpg](journey/80-finance-opens-reconciliation-bank-lines-the-bank-s-charg.jpg) |
| 81 | Priya Shah, AP Executive (ap@) | opens Reconciliation, Supplier statements; chooses the supplier and its statement; Import and match | Reconciliation The books and the flow agree: what was received and not invoiced, supplier statements, bank lines and small suppliers' deadlines Export Order trace Received not invoiced Small suppliers Supplier statements Bank lines Import a supplier's stateme… | [81-ap-opens-reconciliation-supplier-statements-chooses.jpg](journey/81-ap-opens-reconciliation-supplier-statements-chooses.jpg) |
| 82 | Meera Iyer, Director (director@) | opens the Control tower: everything has moved to its last stage | Control tower Where every case stands, how long it has been there (in working days), and the numbers behind it All locations PLT-CKN PLT-SND STAGE CASES VALUE 0-2 DAYS 3-7 DAYS 8-14 DAYS 15+ DAYS OLDEST Request waiting for approval 0 ₹0 — — — — — Approved, no… | [82-director-opens-the-control-tower-everything-has-moved-to-.jpg](journey/82-director-opens-the-control-tower-everything-has-moved-to-.jpg) |
| 83 | Meera Iyer, Director (director@) | opens the Dashboard: Where things stand | Good morning, Meera Iyer What needs you today, and where the purchases in progress stand. My work · Approver Nothing needs you. Next: look at where the purchases in progress stand. Open the control tower Where things stand 0 Waiting for approval 0 Being price… | [83-director-opens-the-dashboard-where-things-stand.jpg](journey/83-director-opens-the-dashboard-where-things-stand.jpg) |
| 84 | Admin, System Administrator (admin@) | opens Admin, Company setup, Policies; saves 'Days suppliers get to quote' with a reason | Company setup Who the people are and what they may do, who approves what, how things are bought, and the company's policies. Every change is logged with its reason. Reason for the change you are about to make (needed for every change) People Roles Approvers A… | [84-admin-opens-admin-company-setup-policies-saves-days-su.jpg](journey/84-admin-opens-admin-company-setup-policies-saves-days-su.jpg) |
| 85 | Admin, System Administrator (admin@) | a second administrator approves the change (D-22); it applies | Company setup Who the people are and what they may do, who approves what, how things are bought, and the company's policies. Every change is logged with its reason. Reason for the change you are about to make (needed for every change) People Roles Approvers A… | [85-admin-a-second-administrator-approves-the-change-d-22-.jpg](journey/85-admin-a-second-administrator-approves-the-change-d-22-.jpg) |
| 86 | Admin, System Administrator (admin@) | opens Audit: the change is there with its reason | Audit Every change, who made it, and why. The log cannot be edited. Entity Entity id Action Actor id Search Clear WHEN WHO ACTION ON WHY 21 Nov 2026, 08:53 am user 392 setup.policy policy default_rfq_response_days Suppliers asked for a day more to quote Detai… | [86-admin-opens-audit-the-change-is-there-with-its-reason.jpg](journey/86-admin-opens-audit-the-change-is-there-with-its-reason.jpg) |
| 87 | Admin, System Administrator (admin@) | opens Company setup, History: the same change is listed with its reason | Company setup Who the people are and what they may do, who approves what, how things are bought, and the company's policies. Every change is logged with its reason. Reason for the change you are about to make (needed for every change) People Roles Approvers A… | [87-admin-opens-company-setup-history-the-same-change-is-l.jpg](journey/87-admin-opens-company-setup-history-the-same-change-is-l.jpg) |
| 88 | Sunil Pawar, Storekeeper (store@) | opens Material requests: MO-1 and MO-2 from the ERP, nothing set aside yet | Material requests What each production order needs from the store: set aside, issued, and still short Run store check Sets free stock aside, earliest need-by first, and sends what is short for approval. Load from the ERP MR-CKN-2627-00001 · MO-1 (FG-01) · nee… | [88-store-opens-material-requests-mo-1-and-mo-2-from-the-e.jpg](journey/88-store-opens-material-requests-mo-1-and-mo-2-from-the-e.jpg) |
| 89 | Sunil Pawar, Storekeeper (store@) | clicks Load from the ERP, chooses production_order.csv and clicks Load the file | Material requests What each production order needs from the store: set aside, issued, and still short Run store check Sets free stock aside, earliest need-by first, and sends what is short for approval. Load from the ERP Loaded 1 row; 0 changed; 0 loaded befo… | [89-store-clicks-load-from-the-erp-chooses-production-orde.jpg](journey/89-store-clicks-load-from-the-erp-chooses-production-orde.jpg) |
| 90 | Sunil Pawar, Storekeeper (store@) | clicks Load from the ERP, chooses production_order_line.csv and clicks Load the file | Material requests What each production order needs from the store: set aside, issued, and still short Load from the ERP A CSV or Excel file in our template or with your ERP's column names. Rows loaded before are skipped; every row left out is listed with its … | [90-store-clicks-load-from-the-erp-chooses-production-orde.jpg](journey/90-store-clicks-load-from-the-erp-chooses-production-orde.jpg) |
| 91 | Sunil Pawar, Storekeeper (store@) | clicks Run store check: what is short goes for approval | Material requests What each production order needs from the store: set aside, issued, and still short Run store check Sets free stock aside, earliest need-by first, and sends what is short for approval. Load from the ERP Store check done. Purchase requests fo… | [91-store-clicks-run-store-check-what-is-short-goes-for-ap.jpg](journey/91-store-clicks-run-store-check-what-is-short-goes-for-ap.jpg) |
| 92 | Vikram Joshi, Plant Head (planthead@) | opens REQ-CKN-2627-00005 for MO-1: the shortfall lines, the estimate and the budget check | Requests Motor body casting 1 HP and 2 more items Pending · REQ-CKN-2627-00005 · ₹36,130 · Automatic restock · deliver to PLT-CKN · needed by 5 Dec 2026 ✓ Request 2 Approval 3 Price 4 Order 5 Receipt 6 Invoice 7 Payment Now: Waiting for Vikram Joshi to approv… | [92-planthead-opens-req-ckn-2627-00005-for-mo-1-the-shortfall-.jpg](journey/92-planthead-opens-req-ckn-2627-00005-for-mo-1-the-shortfall-.jpg) |
| 93 | Sunil Pawar, Storekeeper (store@) | clicks Issue for production on MO-1, then Issue what is set aside | Material requests What each production order needs from the store: set aside, issued, and still short Run store check Sets free stock aside, earliest need-by first, and sends what is short for approval. Load from the ERP Issued: RM-03 50, RM-05 50, RM-06 40, … | [93-store-clicks-issue-for-production-on-mo-1-then-issue-w.jpg](journey/93-store-clicks-issue-for-production-on-mo-1-then-issue-w.jpg) |
| 94 | Vikram Joshi, Plant Head (planthead@) | opens the link in the email for REQ-CKN-2627-00005 and clicks Approve | Request REQ-CKN-2627-00005 Approved. The buyer takes it from here. You can close this page. | [94-planthead-opens-the-link-in-the-email-for-req-ckn-2627-000.jpg](journey/94-planthead-opens-the-link-in-the-email-for-req-ckn-2627-000.jpg) |
| 95 | Neha Kulkarni, Buyer (buyer@) | opens REQ-CKN-2627-00005: each line shows its buying route and why | Requests Motor body casting 1 HP and 2 more items Approved · REQ-CKN-2627-00005 · ₹36,130 · Automatic restock · deliver to PLT-CKN · needed by 5 Dec 2026 ✓ Request ✓ Approval ✓ Price 4 Order 5 Receipt 6 Invoice 7 Payment Now: Order PO-CKN-2627-00005 has been … | [95-buyer-opens-req-ckn-2627-00005-each-line-shows-its-buy.jpg](journey/95-buyer-opens-req-ckn-2627-00005-each-line-shows-its-buy.jpg) |

## What the data showed at the end

- no request exists after the preview: 0
- the V-belt request is 11.000 up to the maximum at the item price, and approves itself (under Rs 10,000): (Decimal('2310.00'), 'approved')
- the form made a draft of Copper winding wire: draft
- REQ-CKN-2627-00002 of 110400.00 waits for Meera Iyer (the dataset's rule for that value): ('submitted', Decimal('110400.00'))
- the form made a draft of Mechanical seal 18 mm: draft
- REQ-CKN-2627-00003 of 11400.00 waits for Vikram Joshi (the dataset's rule for that value): ('submitted', Decimal('11400.00'))
- the form made a draft of Impeller 1 HP: draft
- REQ-CKN-2627-00004 of 44000.00 waits for Vikram Joshi (the dataset's rule for that value): ('submitted', Decimal('44000.00'))
- Production Manager is refused POST /api/requisitions/REQ-CKN-2627-00003/approve (403)
- Buyer is refused POST /api/requisitions/REQ-CKN-2627-00003/approve (403)
- System Administrator is refused POST /api/requisitions/REQ-CKN-2627-00003/approve (403)
- Director is refused POST /api/requisitions/REQ-CKN-2627-00003/approve (409)
- Purchase Manager is refused POST /api/requisitions/REQ-CKN-2627-00003/approve (409)
- both selected requests are approved by the plant head: ['approved', 'approved']
- the copper wire request is still waiting for the director: submitted
- the director was emailed a link to approve the copper wire request
- opening the link decided nothing: submitted
- the link approved the request, once: approved
- the audit log says it came by an emailed link: 1
- the same link does not work a second time
- Find suppliers made a quotation request for the seals: 1
- the production manager was also told by email
- three quotation requests (V-belts, seals, copper wire), none sent before a buyer approves: ['REQ-CKN-2627-00001', 'REQ-CKN-2627-00002', 'REQ-CKN-2627-00003']
- the impellers went straight to an order on the castings contract, approved by the rule (under Rs 1,00,000) and sent to the supplier touchless: [('SUP-001', Decimal('44000.00'), 'sent', True)]
- no quotation request was mailed yet; the only supplier mail is the touchless order, to its supplier: [('sales@deccancastings.example', True)]
- RFQ-2627-00001 asked the suppliers the dataset gives: ['SUP-006']
- RFQ-2627-00002 asked the suppliers the dataset gives: ['SUP-001', 'SUP-002', 'SUP-003']
- RFQ-2627-00003 asked the suppliers the dataset gives: ['SUP-001', 'SUP-003', 'SUP-005']
- one quotation email per supplier asked: ['sales@deccancastings.example', 'sales@gujaratbearings.example', 'sales@shreewires.example']
- the emailed quote became a quote, with the supplier's own price: [Decimal('205.00')]
- V-belt quotation request awarded: awarded
- RFQ-2627-00002 awarded: awarded
- RFQ-2627-00003 awarded: awarded
- one order per supplier awarded: ['SUP-001', 'SUP-002', 'SUP-003', 'SUP-006']
- Deccan Castings Pvt Ltd: 200 x 220.00 before tax: 44000.00
- PO-CKN-2627-00001 of 44000.00: sent touchless: sent
- Western Industrial Supplies: 11.000 x 205 before tax: 2255.00
- PO-CKN-2627-00002 of 2255.00: waits for the purchase manager: submitted
- Gujarat Bearings and Seals: 120 x 94 before tax: 11280.00
- PO-CKN-2627-00003 of 11280.00: waits for the purchase manager: submitted
- Shree Wires and Electricals Pvt Ltd: 120 x 905 before tax: 108600.00
- PO-CKN-2627-00004 of 108600.00: waits for the purchase manager: submitted
- PO-CKN-2627-00001: CGST and SGST (supplier in state 27, plant in 27)
- PO-CKN-2627-00002: CGST and SGST (supplier in state 27, plant in 27)
- PO-CKN-2627-00003: IGST only (supplier in state 24, plant in 27)
- PO-CKN-2627-00004: CGST and SGST (supplier in state 27, plant in 27)
- Buyer is refused POST /api/purchase-orders/PO-CKN-2627-00002/approve (403)
- Buyer is refused POST /api/purchase-orders/PO-CKN-2627-00003/approve (403)
- Buyer is refused POST /api/purchase-orders/PO-CKN-2627-00004/approve (403)
- every order is approved (the touchless one already sent): [('PO-CKN-2627-00001', 'sent'), ('PO-CKN-2627-00002', 'approved'), ('PO-CKN-2627-00003', 'approved'), ('PO-CKN-2627-00004', 'approved')]
- the order document is a real PDF
- PO-CKN-2627-00001 is acknowledged by the supplier's link: acknowledged
- the change waits for the buyer: the order is unchanged: (1, 'sent')
- PO-CKN-2627-00003 is version 2 with the new date, and acknowledged by accepting the change: (2, 'acknowledged', datetime.date(2026, 11, 15))
- the requester and the buyer were told of the change
- every order is acknowledged: ['acknowledged']
- every supplier was mailed its order with the PDF attached
- Buyer is refused POST /api/purchase-orders/PO-CKN-2627-00001/receive (403)
- AP Executive is refused POST /api/purchase-orders/PO-CKN-2627-00001/receive (403)
- after 120 of 200 the order is still open for the rest: part_received
- stock of the impellers: what the books had plus the 120 that arrived: 180.0000
- PO-CKN-2627-00001 is fully received: received
- Buyer is refused POST /api/purchase-orders/PO-CKN-2627-00003/receive (403)
- AP Executive is refused POST /api/purchase-orders/PO-CKN-2627-00003/receive (403)
- the seals wait for inspection: nothing counts yet: acknowledged
- PO-CKN-2627-00003 is fully received: received
- Buyer is refused POST /api/purchase-orders/PO-CKN-2627-00004/receive (403)
- AP Executive is refused POST /api/purchase-orders/PO-CKN-2627-00004/receive (403)
- PO-CKN-2627-00004 is fully received: received
- Buyer is refused POST /api/purchase-orders/PO-CKN-2627-00002/receive (403)
- AP Executive is refused POST /api/purchase-orders/PO-CKN-2627-00002/receive (403)
- PO-CKN-2627-00002 is fully received: received
- stock after all receipts: what the books had plus what each order brought: {'RM-03': Decimal('260.0000'), 'SP-01': Decimal('15.0000'), 'RM-07': Decimal('180.0000'), 'RM-08': Decimal('170.0000')}
- one supplier awarded issues e-invoices
- the e-invoice was read by code alone: no model call: 0
- invoice of SUP-001 matched the order and the receipt: approved for payment: approved
- invoice of SUP-006 matched the order and the receipt: approved for payment: approved
- invoice of SUP-003 matched the order and the receipt: approved for payment: approved
- the SUP-002 invoice, 12% above the order price, is blocked: blocked
- the micro supplier is due within the days agreed in writing (at most 45 from acceptance)
- control tower: one invoice is blocked: 1
- control tower: the invoices that matched wait for payment: 3
- the clerk who entered it cannot release it (refused): 403
- control tower after the release: nothing blocked, every invoice waits for payment: (0, 4)
- the released invoice is approved for payment: approved
- the match result names a releaser who is not the person who entered the invoice
- small suppliers: each unpaid invoice of a micro or small supplier, due the agreed days (at most 45) after acceptance: [('PI-2627-000004', datetime.date(2026, 12, 21))]
- one payment per supplier: ['SUP-001', 'SUP-002', 'SUP-003', 'SUP-006']
- the micro supplier comes first in the run: SUP-002
- the proposer cannot approve the run: 403
- no bank file before the director approves: 409
- the run is approved: approved
- the bank file has a header and one line per supplier: 5
- every invoice is paid: ['paid']
- every order is closed: ['closed']
- every payment carries the bank's reference
- each supplier paid got a payment advice with the bank reference: ['sales@deccancastings.example', 'sales@gujaratbearings.example', 'sales@shreewires.example', 'sales@westernindustrial.example']
- the trace of PO-CKN-2627-00001: its request, the order, both deliveries, the invoice and the payment: {'request': ['REQ-CKN-2627-00004'], 'po': ['PO-CKN-2627-00001'], 'goods_receipt': ['GRN-CKN-2627-00001', 'GRN-CKN-2627-00002'], 'invoice': ['PI-2627-000002'], 'payment': ['PAY-2627-000003']}
- the contract order came in two deliveries (120, then 80): 2
- received not invoiced: nothing; invoiced and not received: the 1353.60 the seals were invoiced above the order: (Decimal('0'), Decimal('1353.60'), ['PO-CKN-2627-00003'])
- bank lines left open: only the charge; every payment's line matched: [('CHG000001', Decimal('17.70'))]
- the statement: the payment matched, the invoice differs, nothing only on one side: [(1, 1, 0, 0)]
- the invoice on it: ours 46200.00 (200 at 220.00 plus GST), the difference the planted 1180: [('PI-2627-000002', Decimal('46200.00'), Decimal('1180.00'))]
- control tower at the end: nothing is left in any stage: 0
- KPIs: every stage of the cycle counted the four requests: [4]
- the second administrator's approval applies the change
- Production Manager is refused PUT /api/setup/policies/default_rfq_response_days (403)
- Buyer is refused PUT /api/setup/policies/default_rfq_response_days (403)
- Storekeeper is refused PUT /api/setup/policies/default_rfq_response_days (403)
- AP Executive is refused PUT /api/setup/policies/default_rfq_response_days (403)
- Finance Manager is refused PUT /api/setup/policies/default_rfq_response_days (403)
- the audit log names who did each step
- the person who planned the run did not approve it
- the person who entered the invoices did not release the block
- the person who received the goods did not enter an invoice
- every document the journey made has an audit row
- one material request per production order, MO-3 loaded on screen: ['MO-1', 'MO-2', 'MO-3', 'MRP-2027-01/PLT-CKN']
- each line sets aside what the oracle says (earliest need-by first, after the journey's receipts): {'MO-1': {'RM-01': Decimal('0.0000'), 'RM-03': Decimal('50.0000'), 'RM-07': Decimal('50.0000'), 'RM-05': Decimal('50.0000'), 'RM-06': Decimal('40.0000'), 'RM-08': Decimal('90.0000'), 'RM-09': Decimal('50.0000'), 'RM-10': Decimal('50.0000'), 'RM-11': Decimal('10.0000'), 'PK-01': Decimal('20.0000')}, 'MO-2': {'RM-02': Decimal('0.0000'), 'RM-04': Decimal('0.0000'), 'RM-06': Decimal('0.0000'), 'PK-01': Decimal('0.0000'), 'RM-05': Decimal('10.0000'), 'RM-07': Decimal('100.0000'), 'RM-08': Decimal('80.0000'), 'RM-09': Decimal('10.0000'), 'RM-10': Decimal('100.0000'), 'RM-11': Decimal('15.0000')}, 'MRP-2027-01/PLT-CKN': {'RM-01': Decimal('0.0000'), 'RM-05': Decimal('0.0000'), 'RM-06': Decimal('0.0000'), 'RM-08': Decimal('0.0000'), 'RM-09': Decimal('0.0000'), 'PK-01': Decimal('0.0000'), 'RM-03': Decimal('120.0000'), 'RM-07': Decimal('30.0000'), 'RM-10': Decimal('50.0000'), 'RM-11': Decimal('5.0000')}, 'MO-3': {'RM-01': Decimal('0.0000'), 'RM-05': Decimal('0.0000'), 'RM-06': Decimal('0.0000'), 'RM-07': Decimal('0.0000'), 'RM-08': Decimal('0.0000'), 'RM-09': Decimal('0.0000'), 'RM-10': Decimal('0.0000'), 'RM-11': Decimal('0.0000'), 'PK-01': Decimal('0.0000'), 'RM-03': Decimal('20.0000')}}
- MO-1: its request buys the shortfall at contract, else last price paid, else standard price: {'RM-01': (Decimal('50.0000'), Decimal('650.0000')), 'RM-06': (Decimal('60.0000'), Decimal('38.0000')), 'PK-01': (Decimal('30.0000'), Decimal('45.0000'))}
- MO-2: its request buys the shortfall at contract, else last price paid, else standard price: {'RM-02': (Decimal('100.0000'), Decimal('480.0000')), 'RM-04': (Decimal('100.0000'), Decimal('160.0000')), 'RM-05': (Decimal('90.0000'), Decimal('180.0000')), 'RM-06': (Decimal('200.0000'), Decimal('38.0000')), 'RM-08': (Decimal('30.0000'), Decimal('905.0000')), 'RM-09': (Decimal('90.0000'), Decimal('85.0000')), 'PK-01': (Decimal('100.0000'), Decimal('45.0000'))}
- MRP-2027-01/PLT-CKN: its request buys the shortfall at contract, else last price paid, else standard price: {'RM-01': (Decimal('120.0000'), Decimal('650.0000')), 'RM-05': (Decimal('120.0000'), Decimal('180.0000')), 'RM-06': (Decimal('240.0000'), Decimal('38.0000')), 'RM-07': (Decimal('90.0000'), Decimal('94.0000')), 'RM-08': (Decimal('216.0000'), Decimal('905.0000')), 'RM-09': (Decimal('120.0000'), Decimal('85.0000')), 'RM-10': (Decimal('70.0000'), Decimal('60.0000')), 'RM-11': (Decimal('19.0000'), Decimal('350.0000')), 'PK-01': (Decimal('120.0000'), Decimal('45.0000'))}
- MO-3: its request buys the shortfall at contract, else last price paid, else standard price: {'RM-01': (Decimal('20.0000'), Decimal('650.0000')), 'RM-05': (Decimal('20.0000'), Decimal('180.0000')), 'RM-06': (Decimal('40.0000'), Decimal('38.0000')), 'RM-07': (Decimal('20.0000'), Decimal('94.0000')), 'RM-08': (Decimal('36.0000'), Decimal('905.0000')), 'RM-09': (Decimal('20.0000'), Decimal('85.0000')), 'RM-10': (Decimal('20.0000'), Decimal('60.0000')), 'RM-11': (Decimal('4.0000'), Decimal('350.0000')), 'PK-01': (Decimal('20.0000'), Decimal('45.0000'))}
- REQ-CKN-2627-00005 waits for the Plant Head (Rs 36130.00): ('submitted', Decimal('36130.00'))
- the issue moved exactly what MO-1 held: {'RM-08': Decimal('90.0000'), 'PK-01': Decimal('20.0000'), 'RM-05': Decimal('50.0000'), 'RM-03': Decimal('50.0000'), 'RM-10': Decimal('50.0000'), 'RM-06': Decimal('40.0000'), 'RM-11': Decimal('10.0000'), 'RM-09': Decimal('50.0000'), 'RM-07': Decimal('50.0000')}
- the Plant Head was emailed a link for REQ-CKN-2627-00005
- REQ-CKN-2627-00005 approved by the link: approved
- every line of REQ-CKN-2627-00005 has a buying route and its rule
- the lines with a contract take the contract route: ['RM-01']
- the contract lines of REQ-CKN-2627-00005 became an order at once
- Production Manager: Dashboard at laptop 1440x900: no blank panel, error, raw code or sideways scroll
- Production Manager: Requests at laptop 1440x900: no blank panel, error, raw code or sideways scroll
- Production Manager: Confirm services at laptop 1440x900: no blank panel, error, raw code or sideways scroll
- Production Manager: Assistant at laptop 1440x900: no blank panel, error, raw code or sideways scroll
- Production Manager: Help at laptop 1440x900: no blank panel, error, raw code or sideways scroll
- Production Manager: Dashboard at phone 390x844: no blank panel, error, raw code or sideways scroll
- Production Manager: Requests at phone 390x844: no blank panel, error, raw code or sideways scroll
- Production Manager: Confirm services at phone 390x844: no blank panel, error, raw code or sideways scroll
- Production Manager: Assistant at phone 390x844: no blank panel, error, raw code or sideways scroll
- Production Manager: Help at phone 390x844: no blank panel, error, raw code or sideways scroll
- Plant Head: Dashboard at laptop 1440x900: no blank panel, error, raw code or sideways scroll
- Plant Head: Requests at laptop 1440x900: no blank panel, error, raw code or sideways scroll
- Plant Head: Quotations at laptop 1440x900: no blank panel, error, raw code or sideways scroll
- Plant Head: Purchase orders at laptop 1440x900: no blank panel, error, raw code or sideways scroll
- Plant Head: Receive without order at laptop 1440x900: no blank panel, error, raw code or sideways scroll
- Plant Head: Invoices at laptop 1440x900: no blank panel, error, raw code or sideways scroll
- Plant Head: Payments at laptop 1440x900: no blank panel, error, raw code or sideways scroll
- Plant Head: Vendors at laptop 1440x900: no blank panel, error, raw code or sideways scroll
- Plant Head: Contracts at laptop 1440x900: no blank panel, error, raw code or sideways scroll
- Plant Head: Exceptions at laptop 1440x900: no blank panel, error, raw code or sideways scroll
- Plant Head: Control tower at laptop 1440x900: no blank panel, error, raw code or sideways scroll
- Plant Head: Assistant at laptop 1440x900: no blank panel, error, raw code or sideways scroll
- Plant Head: Help at laptop 1440x900: no blank panel, error, raw code or sideways scroll
- Plant Head: admin Home at laptop 1440x900: no blank panel, error, raw code or sideways scroll
- Plant Head: admin Approvals at laptop 1440x900: no blank panel, error, raw code or sideways scroll
- Plant Head: admin Home at laptop 1440x900: no blank panel, error, raw code or sideways scroll
- Plant Head: admin Approvals at laptop 1440x900: no blank panel, error, raw code or sideways scroll
- Plant Head: Dashboard at phone 390x844: no blank panel, error, raw code or sideways scroll
- Plant Head: Requests at phone 390x844: no blank panel, error, raw code or sideways scroll
- Plant Head: Quotations at phone 390x844: no blank panel, error, raw code or sideways scroll
- Plant Head: Purchase orders at phone 390x844: no blank panel, error, raw code or sideways scroll
- Plant Head: Receive without order at phone 390x844: no blank panel, error, raw code or sideways scroll
- Plant Head: Invoices at phone 390x844: no blank panel, error, raw code or sideways scroll
- Plant Head: Payments at phone 390x844: no blank panel, error, raw code or sideways scroll
- Plant Head: Vendors at phone 390x844: no blank panel, error, raw code or sideways scroll
- Plant Head: Contracts at phone 390x844: no blank panel, error, raw code or sideways scroll
- Plant Head: Exceptions at phone 390x844: no blank panel, error, raw code or sideways scroll
- Plant Head: Control tower at phone 390x844: no blank panel, error, raw code or sideways scroll
- Plant Head: Assistant at phone 390x844: no blank panel, error, raw code or sideways scroll
- Plant Head: Help at phone 390x844: no blank panel, error, raw code or sideways scroll
- Plant Head: admin Home at phone 390x844: no blank panel, error, raw code or sideways scroll
- Plant Head: admin Approvals at phone 390x844: no blank panel, error, raw code or sideways scroll
- Plant Head: admin Home at phone 390x844: no blank panel, error, raw code or sideways scroll
- Plant Head: admin Approvals at phone 390x844: no blank panel, error, raw code or sideways scroll
- Director: Dashboard at laptop 1440x900: no blank panel, error, raw code or sideways scroll
- Director: Requests at laptop 1440x900: no blank panel, error, raw code or sideways scroll
- Director: Quotations at laptop 1440x900: no blank panel, error, raw code or sideways scroll
- Director: Purchase orders at laptop 1440x900: no blank panel, error, raw code or sideways scroll
- Director: Receive without order at laptop 1440x900: no blank panel, error, raw code or sideways scroll
- Director: Invoices at laptop 1440x900: no blank panel, error, raw code or sideways scroll
- Director: Payments at laptop 1440x900: no blank panel, error, raw code or sideways scroll
- Director: Vendors at laptop 1440x900: no blank panel, error, raw code or sideways scroll
- Director: Contracts at laptop 1440x900: no blank panel, error, raw code or sideways scroll
- Director: Exceptions at laptop 1440x900: no blank panel, error, raw code or sideways scroll
- Director: Control tower at laptop 1440x900: no blank panel, error, raw code or sideways scroll
- Director: Assistant at laptop 1440x900: no blank panel, error, raw code or sideways scroll
- Director: Help at laptop 1440x900: no blank panel, error, raw code or sideways scroll
- Director: admin Home at laptop 1440x900: no blank panel, error, raw code or sideways scroll
- Director: admin Approvals at laptop 1440x900: no blank panel, error, raw code or sideways scroll
- Director: admin Home at laptop 1440x900: no blank panel, error, raw code or sideways scroll
- Director: admin Approvals at laptop 1440x900: no blank panel, error, raw code or sideways scroll
- Director: Dashboard at phone 390x844: no blank panel, error, raw code or sideways scroll
- Director: Requests at phone 390x844: no blank panel, error, raw code or sideways scroll
- Director: Quotations at phone 390x844: no blank panel, error, raw code or sideways scroll
- Director: Purchase orders at phone 390x844: no blank panel, error, raw code or sideways scroll
- Director: Receive without order at phone 390x844: no blank panel, error, raw code or sideways scroll
- Director: Invoices at phone 390x844: no blank panel, error, raw code or sideways scroll
- Director: Payments at phone 390x844: no blank panel, error, raw code or sideways scroll
- Director: Vendors at phone 390x844: no blank panel, error, raw code or sideways scroll
- Director: Contracts at phone 390x844: no blank panel, error, raw code or sideways scroll
- Director: Exceptions at phone 390x844: no blank panel, error, raw code or sideways scroll
- Director: Control tower at phone 390x844: no blank panel, error, raw code or sideways scroll
- Director: Assistant at phone 390x844: no blank panel, error, raw code or sideways scroll
- Director: Help at phone 390x844: no blank panel, error, raw code or sideways scroll
- Director: admin Home at phone 390x844: no blank panel, error, raw code or sideways scroll
- Director: admin Approvals at phone 390x844: no blank panel, error, raw code or sideways scroll
- Director: admin Home at phone 390x844: no blank panel, error, raw code or sideways scroll
- Director: admin Approvals at phone 390x844: no blank panel, error, raw code or sideways scroll
- Purchase Manager: Dashboard at laptop 1440x900: no blank panel, error, raw code or sideways scroll
- Purchase Manager: Requests at laptop 1440x900: no blank panel, error, raw code or sideways scroll
- Purchase Manager: Quotations at laptop 1440x900: no blank panel, error, raw code or sideways scroll
- Purchase Manager: Purchase orders at laptop 1440x900: no blank panel, error, raw code or sideways scroll
- Purchase Manager: Receive without order at laptop 1440x900: no blank panel, error, raw code or sideways scroll
- Purchase Manager: Invoices at laptop 1440x900: no blank panel, error, raw code or sideways scroll
- Purchase Manager: Payments at laptop 1440x900: no blank panel, error, raw code or sideways scroll
- Purchase Manager: Vendors at laptop 1440x900: no blank panel, error, raw code or sideways scroll
- Purchase Manager: Contracts at laptop 1440x900: no blank panel, error, raw code or sideways scroll
- Purchase Manager: Exceptions at laptop 1440x900: no blank panel, error, raw code or sideways scroll
- Purchase Manager: Control tower at laptop 1440x900: no blank panel, error, raw code or sideways scroll
- Purchase Manager: Assistant at laptop 1440x900: no blank panel, error, raw code or sideways scroll
- Purchase Manager: Help at laptop 1440x900: no blank panel, error, raw code or sideways scroll
- Purchase Manager: admin Home at laptop 1440x900: no blank panel, error, raw code or sideways scroll
- Purchase Manager: admin Approvals at laptop 1440x900: no blank panel, error, raw code or sideways scroll
- Purchase Manager: admin Home at laptop 1440x900: no blank panel, error, raw code or sideways scroll
- Purchase Manager: admin Approvals at laptop 1440x900: no blank panel, error, raw code or sideways scroll
- Purchase Manager: Dashboard at phone 390x844: no blank panel, error, raw code or sideways scroll
- Purchase Manager: Requests at phone 390x844: no blank panel, error, raw code or sideways scroll
- Purchase Manager: Quotations at phone 390x844: no blank panel, error, raw code or sideways scroll
- Purchase Manager: Purchase orders at phone 390x844: no blank panel, error, raw code or sideways scroll
- Purchase Manager: Receive without order at phone 390x844: no blank panel, error, raw code or sideways scroll
- Purchase Manager: Invoices at phone 390x844: no blank panel, error, raw code or sideways scroll
- Purchase Manager: Payments at phone 390x844: no blank panel, error, raw code or sideways scroll
- Purchase Manager: Vendors at phone 390x844: no blank panel, error, raw code or sideways scroll
- Purchase Manager: Contracts at phone 390x844: no blank panel, error, raw code or sideways scroll
- Purchase Manager: Exceptions at phone 390x844: no blank panel, error, raw code or sideways scroll
- Purchase Manager: Control tower at phone 390x844: no blank panel, error, raw code or sideways scroll
- Purchase Manager: Assistant at phone 390x844: no blank panel, error, raw code or sideways scroll
- Purchase Manager: Help at phone 390x844: no blank panel, error, raw code or sideways scroll
- Purchase Manager: admin Home at phone 390x844: no blank panel, error, raw code or sideways scroll
- Purchase Manager: admin Approvals at phone 390x844: no blank panel, error, raw code or sideways scroll
- Purchase Manager: admin Home at phone 390x844: no blank panel, error, raw code or sideways scroll
- Purchase Manager: admin Approvals at phone 390x844: no blank panel, error, raw code or sideways scroll
- Buyer: Dashboard at laptop 1440x900: no blank panel, error, raw code or sideways scroll
- Buyer: Requests at laptop 1440x900: no blank panel, error, raw code or sideways scroll
- Buyer: Quotations at laptop 1440x900: no blank panel, error, raw code or sideways scroll
- Buyer: Purchase orders at laptop 1440x900: no blank panel, error, raw code or sideways scroll
- Buyer: Goods receipt at laptop 1440x900: no blank panel, error, raw code or sideways scroll
- Buyer: Receive without order at laptop 1440x900: no blank panel, error, raw code or sideways scroll
- Buyer: Vendors at laptop 1440x900: no blank panel, error, raw code or sideways scroll
- Buyer: Contracts at laptop 1440x900: no blank panel, error, raw code or sideways scroll
- Buyer: Inventory at laptop 1440x900: no blank panel, error, raw code or sideways scroll
- Buyer: Exceptions at laptop 1440x900: no blank panel, error, raw code or sideways scroll
- Buyer: Control tower at laptop 1440x900: no blank panel, error, raw code or sideways scroll
- Buyer: Assistant at laptop 1440x900: no blank panel, error, raw code or sideways scroll
- Buyer: Help at laptop 1440x900: no blank panel, error, raw code or sideways scroll
- Buyer: admin Home at laptop 1440x900: no blank panel, error, raw code or sideways scroll
- Buyer: admin Approvals at laptop 1440x900: no blank panel, error, raw code or sideways scroll
- Buyer: admin Home at laptop 1440x900: no blank panel, error, raw code or sideways scroll
- Buyer: admin Approvals at laptop 1440x900: no blank panel, error, raw code or sideways scroll
- Buyer: Dashboard at phone 390x844: no blank panel, error, raw code or sideways scroll
- Buyer: Requests at phone 390x844: no blank panel, error, raw code or sideways scroll
- Buyer: Quotations at phone 390x844: no blank panel, error, raw code or sideways scroll
- Buyer: Purchase orders at phone 390x844: no blank panel, error, raw code or sideways scroll
- Buyer: Goods receipt at phone 390x844: no blank panel, error, raw code or sideways scroll
- Buyer: Receive without order at phone 390x844: no blank panel, error, raw code or sideways scroll
- Buyer: Vendors at phone 390x844: no blank panel, error, raw code or sideways scroll
- Buyer: Contracts at phone 390x844: no blank panel, error, raw code or sideways scroll
- Buyer: Inventory at phone 390x844: no blank panel, error, raw code or sideways scroll
- Buyer: Exceptions at phone 390x844: no blank panel, error, raw code or sideways scroll
- Buyer: Control tower at phone 390x844: no blank panel, error, raw code or sideways scroll
- Buyer: Assistant at phone 390x844: no blank panel, error, raw code or sideways scroll
- Buyer: Help at phone 390x844: no blank panel, error, raw code or sideways scroll
- Buyer: admin Home at phone 390x844: no blank panel, error, raw code or sideways scroll
- Buyer: admin Approvals at phone 390x844: no blank panel, error, raw code or sideways scroll
- Buyer: admin Home at phone 390x844: no blank panel, error, raw code or sideways scroll
- Buyer: admin Approvals at phone 390x844: no blank panel, error, raw code or sideways scroll
- Storekeeper: Dashboard at laptop 1440x900: no blank panel, error, raw code or sideways scroll
- Storekeeper: Material requests at laptop 1440x900: no blank panel, error, raw code or sideways scroll
- Storekeeper: Requests at laptop 1440x900: no blank panel, error, raw code or sideways scroll
- Storekeeper: Purchase orders at laptop 1440x900: no blank panel, error, raw code or sideways scroll
- Storekeeper: Goods receipt at laptop 1440x900: no blank panel, error, raw code or sideways scroll
- Storekeeper: Receive without order at laptop 1440x900: no blank panel, error, raw code or sideways scroll
- Storekeeper: Confirm services at laptop 1440x900: no blank panel, error, raw code or sideways scroll
- Storekeeper: Inventory at laptop 1440x900: no blank panel, error, raw code or sideways scroll
- Storekeeper: Exceptions at laptop 1440x900: no blank panel, error, raw code or sideways scroll
- Storekeeper: Assistant at laptop 1440x900: no blank panel, error, raw code or sideways scroll
- Storekeeper: Help at laptop 1440x900: no blank panel, error, raw code or sideways scroll
- Storekeeper: Dashboard at phone 390x844: no blank panel, error, raw code or sideways scroll
- Storekeeper: Material requests at phone 390x844: no blank panel, error, raw code or sideways scroll
- Storekeeper: Requests at phone 390x844: no blank panel, error, raw code or sideways scroll
- Storekeeper: Purchase orders at phone 390x844: no blank panel, error, raw code or sideways scroll
- Storekeeper: Goods receipt at phone 390x844: no blank panel, error, raw code or sideways scroll
- Storekeeper: Receive without order at phone 390x844: no blank panel, error, raw code or sideways scroll
- Storekeeper: Confirm services at phone 390x844: no blank panel, error, raw code or sideways scroll
- Storekeeper: Inventory at phone 390x844: no blank panel, error, raw code or sideways scroll
- Storekeeper: Exceptions at phone 390x844: no blank panel, error, raw code or sideways scroll
- Storekeeper: Assistant at phone 390x844: no blank panel, error, raw code or sideways scroll
- Storekeeper: Help at phone 390x844: no blank panel, error, raw code or sideways scroll
- AP Executive: Dashboard at laptop 1440x900: no blank panel, error, raw code or sideways scroll
- AP Executive: Purchase orders at laptop 1440x900: no blank panel, error, raw code or sideways scroll
- AP Executive: Invoices at laptop 1440x900: no blank panel, error, raw code or sideways scroll
- AP Executive: Payments at laptop 1440x900: no blank panel, error, raw code or sideways scroll
- AP Executive: Reconciliation at laptop 1440x900: no blank panel, error, raw code or sideways scroll
- AP Executive: Exceptions at laptop 1440x900: no blank panel, error, raw code or sideways scroll
- AP Executive: Assistant at laptop 1440x900: no blank panel, error, raw code or sideways scroll
- AP Executive: Help at laptop 1440x900: no blank panel, error, raw code or sideways scroll
- AP Executive: Dashboard at phone 390x844: no blank panel, error, raw code or sideways scroll
- AP Executive: Purchase orders at phone 390x844: no blank panel, error, raw code or sideways scroll
- AP Executive: Invoices at phone 390x844: no blank panel, error, raw code or sideways scroll
- AP Executive: Payments at phone 390x844: no blank panel, error, raw code or sideways scroll
- AP Executive: Reconciliation at phone 390x844: no blank panel, error, raw code or sideways scroll
- AP Executive: Exceptions at phone 390x844: no blank panel, error, raw code or sideways scroll
- AP Executive: Assistant at phone 390x844: no blank panel, error, raw code or sideways scroll
- AP Executive: Help at phone 390x844: no blank panel, error, raw code or sideways scroll
- Finance Manager: Dashboard at laptop 1440x900: no blank panel, error, raw code or sideways scroll
- Finance Manager: Purchase orders at laptop 1440x900: no blank panel, error, raw code or sideways scroll
- Finance Manager: Invoices at laptop 1440x900: no blank panel, error, raw code or sideways scroll
- Finance Manager: Payments at laptop 1440x900: no blank panel, error, raw code or sideways scroll
- Finance Manager: Reconciliation at laptop 1440x900: no blank panel, error, raw code or sideways scroll
- Finance Manager: Vendors at laptop 1440x900: no blank panel, error, raw code or sideways scroll
- Finance Manager: Exceptions at laptop 1440x900: no blank panel, error, raw code or sideways scroll
- Finance Manager: Control tower at laptop 1440x900: no blank panel, error, raw code or sideways scroll
- Finance Manager: Assistant at laptop 1440x900: no blank panel, error, raw code or sideways scroll
- Finance Manager: Help at laptop 1440x900: no blank panel, error, raw code or sideways scroll
- Finance Manager: admin Home at laptop 1440x900: no blank panel, error, raw code or sideways scroll
- Finance Manager: admin Approvals at laptop 1440x900: no blank panel, error, raw code or sideways scroll
- Finance Manager: admin Home at laptop 1440x900: no blank panel, error, raw code or sideways scroll
- Finance Manager: admin Approvals at laptop 1440x900: no blank panel, error, raw code or sideways scroll
- Finance Manager: Dashboard at phone 390x844: no blank panel, error, raw code or sideways scroll
- Finance Manager: Purchase orders at phone 390x844: no blank panel, error, raw code or sideways scroll
- Finance Manager: Invoices at phone 390x844: no blank panel, error, raw code or sideways scroll
- Finance Manager: Payments at phone 390x844: no blank panel, error, raw code or sideways scroll
- Finance Manager: Reconciliation at phone 390x844: no blank panel, error, raw code or sideways scroll
- Finance Manager: Vendors at phone 390x844: no blank panel, error, raw code or sideways scroll
- Finance Manager: Exceptions at phone 390x844: no blank panel, error, raw code or sideways scroll
- Finance Manager: Control tower at phone 390x844: no blank panel, error, raw code or sideways scroll
- Finance Manager: Assistant at phone 390x844: no blank panel, error, raw code or sideways scroll
- Finance Manager: Help at phone 390x844: no blank panel, error, raw code or sideways scroll
- Finance Manager: admin Home at phone 390x844: no blank panel, error, raw code or sideways scroll
- Finance Manager: admin Approvals at phone 390x844: no blank panel, error, raw code or sideways scroll
- Finance Manager: admin Home at phone 390x844: no blank panel, error, raw code or sideways scroll
- Finance Manager: admin Approvals at phone 390x844: no blank panel, error, raw code or sideways scroll
- System Administrator: Dashboard at laptop 1440x900: no blank panel, error, raw code or sideways scroll
- System Administrator: Requests at laptop 1440x900: no blank panel, error, raw code or sideways scroll
- System Administrator: Purchase orders at laptop 1440x900: no blank panel, error, raw code or sideways scroll
- System Administrator: Invoices at laptop 1440x900: no blank panel, error, raw code or sideways scroll
- System Administrator: Payments at laptop 1440x900: no blank panel, error, raw code or sideways scroll
- System Administrator: Reconciliation at laptop 1440x900: no blank panel, error, raw code or sideways scroll
- System Administrator: Vendors at laptop 1440x900: no blank panel, error, raw code or sideways scroll
- System Administrator: Inventory at laptop 1440x900: no blank panel, error, raw code or sideways scroll
- System Administrator: Exceptions at laptop 1440x900: no blank panel, error, raw code or sideways scroll
- System Administrator: Control tower at laptop 1440x900: no blank panel, error, raw code or sideways scroll
- System Administrator: Assistant at laptop 1440x900: no blank panel, error, raw code or sideways scroll
- System Administrator: Settings at laptop 1440x900: no blank panel, error, raw code or sideways scroll
- System Administrator: Help at laptop 1440x900: no blank panel, error, raw code or sideways scroll
- System Administrator: admin Home at laptop 1440x900: no blank panel, error, raw code or sideways scroll
- System Administrator: admin Company setup at laptop 1440x900: no blank panel, error, raw code or sideways scroll
- System Administrator: admin Automation at laptop 1440x900: no blank panel, error, raw code or sideways scroll
- System Administrator: admin Audit at laptop 1440x900: no blank panel, error, raw code or sideways scroll
- System Administrator: admin Agent runs at laptop 1440x900: no blank panel, error, raw code or sideways scroll
- System Administrator: admin Rule decisions at laptop 1440x900: no blank panel, error, raw code or sideways scroll
- System Administrator: admin Model calls at laptop 1440x900: no blank panel, error, raw code or sideways scroll
- System Administrator: admin Rules at laptop 1440x900: no blank panel, error, raw code or sideways scroll
- System Administrator: admin Users at laptop 1440x900: no blank panel, error, raw code or sideways scroll
- System Administrator: admin Home at laptop 1440x900: no blank panel, error, raw code or sideways scroll
- System Administrator: admin Company setup at laptop 1440x900: no blank panel, error, raw code or sideways scroll
- System Administrator: admin Automation at laptop 1440x900: no blank panel, error, raw code or sideways scroll
- System Administrator: admin Audit at laptop 1440x900: no blank panel, error, raw code or sideways scroll
- System Administrator: admin Agent runs at laptop 1440x900: no blank panel, error, raw code or sideways scroll
- System Administrator: admin Rule decisions at laptop 1440x900: no blank panel, error, raw code or sideways scroll
- System Administrator: admin Model calls at laptop 1440x900: no blank panel, error, raw code or sideways scroll
- System Administrator: admin Rules at laptop 1440x900: no blank panel, error, raw code or sideways scroll
- System Administrator: admin Users at laptop 1440x900: no blank panel, error, raw code or sideways scroll
- System Administrator: Dashboard at phone 390x844: no blank panel, error, raw code or sideways scroll
- System Administrator: Requests at phone 390x844: no blank panel, error, raw code or sideways scroll
- System Administrator: Purchase orders at phone 390x844: no blank panel, error, raw code or sideways scroll
- System Administrator: Invoices at phone 390x844: no blank panel, error, raw code or sideways scroll
- System Administrator: Payments at phone 390x844: no blank panel, error, raw code or sideways scroll
- System Administrator: Reconciliation at phone 390x844: no blank panel, error, raw code or sideways scroll
- System Administrator: Vendors at phone 390x844: no blank panel, error, raw code or sideways scroll
- System Administrator: Inventory at phone 390x844: no blank panel, error, raw code or sideways scroll
- System Administrator: Exceptions at phone 390x844: no blank panel, error, raw code or sideways scroll
- System Administrator: Control tower at phone 390x844: no blank panel, error, raw code or sideways scroll
- System Administrator: Assistant at phone 390x844: no blank panel, error, raw code or sideways scroll
- System Administrator: Settings at phone 390x844: no blank panel, error, raw code or sideways scroll
- System Administrator: Help at phone 390x844: no blank panel, error, raw code or sideways scroll
- System Administrator: admin Home at phone 390x844: no blank panel, error, raw code or sideways scroll
- System Administrator: admin Company setup at phone 390x844: no blank panel, error, raw code or sideways scroll
- System Administrator: admin Automation at phone 390x844: no blank panel, error, raw code or sideways scroll
- System Administrator: admin Audit at phone 390x844: no blank panel, error, raw code or sideways scroll
- System Administrator: admin Agent runs at phone 390x844: no blank panel, error, raw code or sideways scroll
- System Administrator: admin Rule decisions at phone 390x844: no blank panel, error, raw code or sideways scroll
- System Administrator: admin Model calls at phone 390x844: no blank panel, error, raw code or sideways scroll
- System Administrator: admin Rules at phone 390x844: no blank panel, error, raw code or sideways scroll
- System Administrator: admin Users at phone 390x844: no blank panel, error, raw code or sideways scroll
- System Administrator: admin Home at phone 390x844: no blank panel, error, raw code or sideways scroll
- System Administrator: admin Company setup at phone 390x844: no blank panel, error, raw code or sideways scroll
- System Administrator: admin Automation at phone 390x844: no blank panel, error, raw code or sideways scroll
- System Administrator: admin Audit at phone 390x844: no blank panel, error, raw code or sideways scroll
- System Administrator: admin Agent runs at phone 390x844: no blank panel, error, raw code or sideways scroll
- System Administrator: admin Rule decisions at phone 390x844: no blank panel, error, raw code or sideways scroll
- System Administrator: admin Model calls at phone 390x844: no blank panel, error, raw code or sideways scroll
- System Administrator: admin Rules at phone 390x844: no blank panel, error, raw code or sideways scroll
- System Administrator: admin Users at phone 390x844: no blank panel, error, raw code or sideways scroll
- Production Manager can do 'Ask for something' exactly as requester.md says: every name it uses was on their screens (5 names)
- Plant Head can do 'Approve a request' exactly as approver.md says: every name it uses was on their screens (11 names)
- Purchase Manager can do 'Approve an order' exactly as approver.md says: every name it uses was on their screens (7 names)
- Buyer can do 'Find a price' exactly as buyer.md says: every name it uses was on their screens (7 names)
- Storekeeper can do 'Receive goods' exactly as store.md says: every name it uses was on their screens (3 names)
- AP Executive can do 'Enter an invoice' exactly as ap-clerk.md says: every name it uses was on their screens (6 names)
- Finance Manager can do 'Plan a run' exactly as finance.md says: every name it uses was on their screens (3 names)
- System Administrator can do 'Where' exactly as admin.md says: every name it uses was on their screens (4 names)
