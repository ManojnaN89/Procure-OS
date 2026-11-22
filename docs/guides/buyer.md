# Buyer guide

You turn approved requests into orders.

## What you see when you log in

**Dashboard** → **My work**: **Approved requests to source**, **RFQs waiting for quotes**, **RFQs with quotes to compare and award**, **Approved orders to send to the supplier**, **Sent orders waiting for the supplier's acknowledgement**, **Supplier replies to check**, **Supplier messages to read**.

## Find a price

1. **Requests** → click **Check stock levels** to draft requests for stock at its reorder point, then **Create and submit**.
2. Open an approved request and click **Find suppliers**. A contract price is used when there is one. Otherwise a request for quotation is made.
3. Sending the quotation request needs a person (unless the company made it automatic): **Admin** → **Approvals** → **Approve**.

## Quotes and supplier selection

Suppliers answer by email. Under **Quotations**, open the RFQ: each reply is read into a draft. Check it and click **Confirm this quote**, or **Dismiss**. To type one yourself click **Enter a quote**, then **Save the quote**. To print or post the request, click **RFQ (PDF)** next to a supplier: it has the RFQ number, the lines with their need-by dates, where to deliver, the terms and the date to quote by.

A quote that fails a must-have is dropped, and the table says why: it delivers after the need-by date, it has expired (a quote valid until today still counts), the supplier is not active or not approved for the category, or its GST details are missing. The rest are ranked on total cost: price times quantity plus freight and other charges. GST is shown but not counted when the company claims it back. Equal totals go to the earlier delivery, then the better vendor score. The company may instead compare on a weighted score of price, delivery, quality and payment terms; its weights are a versioned company rule.

When the cheapest supplier can supply only part, the app shows a **Split** row: it splits only when the saving beats the extra freight. Pick a row for each line and click **Select these suppliers**. A written reason is needed when you select anything but the recommended, cheapest qualifying quote (or the recommended split), or when fewer qualifying quotes came in than the buying route asked for. One order is made per supplier, with GST.

A price far below the other quotes or the last price paid shows "Ask the supplier": check it with the supplier before selecting. When a supplier asks a question, the task carries a drafted reply you can start from; nothing is sent until you send it.

## Orders

**Purchase orders** → open the order. Lines that share a supplier, the same payment and delivery terms and the same delivery place are on one order; a different set of terms (for example a contract line and a quoted line) or a line for another location makes a second order. The delivery place is printed on the order and in the email. The tier says who approves by the total: the purchase manager up to Rs 5,00,000, above that the director. You never approve an order you made, at any value. **Touchless** orders go out without you and without an approver: contract or safe-reorder lines up to Rs 1,00,000 and the touchless limit, an active supplier, the contract within its ceiling, prices at most 2% above the estimate, the budget fine, and touchless orders turned on. Single-source and emergency lines never do. Otherwise the **Touchless check** panel says why the order went to the purchase manager. **Open the order document (PDF)** shows what the supplier gets; it goes with the email. When it is approved click **Send to supplier**. When the supplier confirms click **Record acknowledgement** and enter the promised date. If the supplier writes about the order, the panel **Supplier messages** on the order shows what the system understood, and **Chase suppliers now** sends the reminders that are due.

## The supplier's answer

The order email has a link where the supplier accepts the order, declines it or asks for a new date, quantity or price; it may also reply by email. Nothing changes by itself: the panel **Supplier's answer** on the order lists each change, old value to new. Click **Accept**, or write a reason and click **Reject** (the supplier gets a new link). An accepted change makes a new version of the order and the requester is told; a price rise above 2% goes on to the purchase manager; an accepted decline cancels the order and gives you a task to source the line again. The panel also shows what the supplier says it has shipped. Reminders about the delivery date go to the supplier by themselves.

## Change or end an order

On the order page, the panel **Order document and changes** keeps the version and its history. Every change needs a reason.

- **Change quantity or date** → **Make a new version**: lower the quantity or move the date of a line. A lower total needs no new approval and the supplier gets the revised order by email. A higher total goes for approval again.
- **Cancel this order**: only before anything arrives or is invoiced. Tick **Order these lines again** if you want a different supplier or price; otherwise the need is dropped. The supplier and the requester are told.
- **Close what is still to come**: after a part delivery, when the supplier will not send the rest.

## Suppliers and contracts

**Vendors** → **Onboard vendor**: fill in the form and click **Save as draft**. The checks name any wrong PAN, GSTIN, Udyam number or bank detail. On the vendor page click **Add a document** for each paper (PAN, GST and Udyam certificates; a bank proof is filed for payment, not needed to activate) and **Attach** its scan. A different person verifies each document, and a person with approval rights activates the supplier. The bank account of a supplier already in use can only change with another person's approval. **Contracts** → **Store contract**, **Add a price**. A different person activates the contract. **Vendors** → **Register a prospect** saves a supplier that may quote before onboarding; the same PAN, GSTIN or bank account as any supplier (a blocked one too) is refused, and a shared phone, email, domain or address is flagged for review. On an RFQ, **Invite a prospect**. Its quote "does not count" and cannot be selected until someone else checks its identity; selecting it then is a conditional award (no order until it is activated; after 10 working days you choose again with a reason). A supplier or contract price may be limited to a region: other places go to quotation ("No contract here: …"). **Contracts** → **Market prices**: add or upload benchmarks (evidence only, verified by someone else). Strategic reviews land in **My work**; close each with a reason, or **Ask for a review** on a contract.

Changes from the ERP: when the ERP cuts or cancels a production order (or a joiner plan) whose material you already ordered or sent out for quotation, you get a case on **Exceptions** (kind "Demand changes to act on"), due in two working days (setting `demand_change_case_days`), escalating to your manager. It names the order, RFQ or request and how much is no longer needed. Revise or cancel the order, or keep the goods for stock, then **Close** the case with what you did (a note is needed for "kept for stock" and "not possible"). When an order is needed earlier than the supplier promised, the case asks you to get the supplier to deliver by the new date. Requests not yet with you are cut by the platform itself.

## What you cannot do, and if you are stuck

You cannot approve an order, verify a document you added, or activate a contract. An order held for a price waits for an Approver. Goods that came with no order show in **Receive without order**; once the purchase manager approves, click **Raise the order** and give the price agreed with the supplier, then send it as usual. A supplier with a missing or unverified document cannot be used: open the vendor to see which one. **Exceptions** lists everything stuck that needs a person, oldest first, with the reason, who must act, the next step and the age in working days; tick **Only mine** for what waits on you.
