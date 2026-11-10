# How a purchase flows

From a stock shortage to a paid invoice. Each step has one person's name on it, and nobody can do two steps of the same case.

| # | What happens | Who | Where |
|---|---|---|---|
| 1 | Stock is at its reorder point, or someone asks for something | Buyer, Requester | **Requisitions** → **Check stock levels**, or the **Assistant** |
| 2 | The request is approved by your approval rules. Small ones approve themselves | Approver | **Requisitions** → **Approve** |
| 3 | Find a price: a contract price, or a request for quotation to suppliers | Buyer | **Requisitions** → **Find suppliers** |
| 4 | Quotes come back by email, the buyer checks them and awards | Buyer | **RFQs** → **Award the selected suppliers** |
| 5 | The order is approved, then sent to the supplier | Approver, Buyer | **Purchase orders** → **Send to supplier** |
| 6 | Goods arrive and are checked | Store | **Goods receipt** → **Receive goods** |
| 7 | The supplier's invoice is entered and matched to the order and the receipt | AP | **Invoices** → **Enter an invoice** |
| 8 | Invoices due are put in a payment run, and the owner approves it | Finance, Approver | **Payments** → **Send the run for approval** |
| 9 | The bank pays, the order closes, the supplier gets the payment advice | Finance | **Payments** → **The bank has paid this run** |

## Your start page

Everyone starts at **Dashboard**. The **My work** panel lists what needs you, by name. If it says nothing needs you, nothing does.

## Who is kept apart

The person who asked for something does not approve it. The person who entered an invoice does not release its block. The person who proposed a payment run does not approve it. The person who added a supplier document does not verify it. The screens do not offer you these buttons, and the server refuses them if you try another way.

## When something is blocked

Open the item. It says why and who can fix it. If it needs a decision, the decision shows up in that person's **My work**. The **Assistant** can also tell you where a case is.
