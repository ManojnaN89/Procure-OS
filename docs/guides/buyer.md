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

**Purchase orders** → open the order. Lines that share a supplier, the same payment and delivery terms and the same delivery place are on one order; a different set of terms (for example a contract line and a quoted line) or a line for another location makes a second order. The delivery place is printed on the order and in the email. The tier says who approves by the total: up to Rs 1,00,000 it is yours (your **Send to supplier** is the approval), then the purchase manager, above Rs 5,00,000 the director. **Touchless** orders go out without you: contract or safe-reorder lines within your tier and the touchless limit, an active supplier, the contract within its ceiling, prices at most 2% above the estimate, the budget fine, and touchless orders turned on. Single-source and emergency lines never do. Otherwise the **Touchless check** panel says why the order waits for you. **Open the order document (PDF)** shows what the supplier gets; it goes with the email. When it is approved click **Send to supplier**. When the supplier confirms click **Record acknowledgement** and enter the promised date. If the supplier writes about the order, the panel **Supplier messages** on the order shows what the system understood, and **Chase suppliers now** sends the reminders that are due.

## Change or end an order

On the order page, the panel **Order document and changes** keeps the version and its history. Every change needs a reason.

- **Change quantity or date** → **Make a new version**: lower the quantity or move the date of a line. A lower total needs no new approval and the supplier gets the revised order by email. A higher total goes for approval again.
- **Cancel this order**: only before anything arrives or is invoiced. Tick **Order these lines again** if you want a different supplier or price; otherwise the need is dropped. The supplier and the requester are told.
- **Close what is still to come**: after a part delivery, when the supplier will not send the rest.

## Suppliers and contracts

**Vendors** → **Onboard vendor**: fill in the form and click **Save as draft**. The checks name any wrong PAN, GSTIN, Udyam number or bank detail. On the vendor page click **Add a document** for each paper (PAN, bank proof, GST and Udyam certificates) and **Attach** its scan. A different person verifies each document, and a person with approval rights activates the supplier. The bank account of a supplier already in use can only change with another person's approval.

**Contracts** → **Store contract**, **Add a price**. A different person activates the contract.

## What you cannot do

You cannot approve an order, verify a document you added, or activate a contract.

## If you are stuck

An order held for a price waits for an Approver. A supplier with a missing or unverified document cannot be used: open the vendor to see which one.
