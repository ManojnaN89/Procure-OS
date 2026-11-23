# Approver guide

You decide requests, orders and the sensitive things that no program may decide alone.

## What you see when you log in
**Dashboard** → **My work**. Sections you may see: **Requests waiting for my approval**, **Orders waiting for my approval**, **Payment runs to approve**, **Blocked invoices to release**, **Supplier changes to approve**, **Orders held for a price or ceiling**, **RFQs waiting to be sent**.

## Approve a request
1. Click the request in **My work**, or open **Requests**.
2. Read the lines, the amount and the budget warning if there is one.
3. Click **Approve**, **Return for changes** or **Reject**. Returning and rejecting need a reason: write it and click **Confirm**.
4. To approve less than was asked, click **Approve less…**, lower the quantities, write the reason and click **Approve with these quantities**. A quantity can go down, not up, and the value follows it.

Several at once: under **Requests**, open **Needs my decision**, tick the requests and click **Approve selected**. Each is decided on its own, and anything that is not your turn is left alone and listed.

By email: the mail about a request that waits for you shows the lines, the estimated total, the budget and why it came to you, with Approve, Reject and "Send back for changes" buttons. Each opens a page about the request; rejecting or sending back asks for a reason there. Every decision from the email needs a one-time code, whatever the amount: click "Email me a code" on that page, type the six digits from the mail (it goes only to you; it works once, for 10 minutes, 5 tries) and click your decision. A forwarded link decides nothing. Or click "Open the request in the app", sign in and decide there with no code. The links work once and expire after a week. Opening one decides nothing.

If you do not decide, you get one reminder after a working day; after two working days the request also goes to your manager (or the next approver), who may then decide it.

## Approve an order
Orders are approved by tier, shown in the list of **Purchase orders**: up to Rs 5,00,000 the purchase manager, above that the director. Up to Rs 1,00,000 an order can also be approved by the company's touchless policy, never by the buyer. Open **Purchase orders**, click the order, then **Approve** or **Reject**. The panel says which version you are approving; if the order changed since you opened it, you are told "the order has changed since you opened it": open it again to see the change. Above Rs 5,00,000 approving needs a one-time code: click "Email me a code", type the six digits and click Approve (a code is for that version only; rejecting needs none). A change after approval that is material (a new delivery place, new payment terms, a higher quantity or price, a date moved more than the tolerance from the approved one, a higher total) comes back to you; anything else keeps your approval. Before approving a version that came back, open **Versions** on the order and **Compare** it with the one you approved: the changed values are marked. You never approve a change you made yourself: it goes to the next approver. You cannot approve an order you made or submitted yourself, at any value, nor as someone's deputy; it then goes to the next approver. Orders marked **Touchless** were issued without a person, under the touchless policy, up to Rs 1,00,000; the order page lists the facts that were checked. An order held for a price, a contract ceiling or a budget says why: the budget is checked again at the order's final price, and when the budget policy blocks, accepting it is your decision, under **Admin** → **Approvals**. An order over budget is never touchless.

## Approve an award
A buyer's proposed award of a period RFQ comes to the person who approves orders of its value (**My work** → **Awards waiting for you**). Open it under **Quotations**: the award panel shows the value with GST, the rule and a note on what is awarded and why (the figures are the engine's; the wording may be the assistant's, checked against them). Click **Approve award** or **Reject** (a reason is needed). Approving sends the award letter and the regret notes and makes a draft rate contract per supplier; it commits no money. You cannot decide an award you proposed, quoted or sent, and if a quote expired or changed since it was proposed, the award goes back to the buyer.

Once the supplier has accepted the contract's terms, a second person activates it (**My work** → **Contracts to activate**, then **Activate this contract**): never its maker and never whoever approved its award.

## Decide the rest
Payment runs, releasing a blocked invoice, supplier changes, held orders and goods received without an order (also on **Receive without order**) are under **Admin** → **Approvals**. Click **Approve** or **Reject**. Nothing runs until you do.

## Suppliers, contracts, prospects and market prices
**Vendors** → open a vendor → **Verify** a document after you compare it with the original. **Contracts** → open a draft → **Activate this contract**. A prospect's identity is checked on its vendor page (**Identity check**: method, the PAN or GST certificate scan, a note) by someone other than the buyer who registered or invited it. A flag (a contact, address or mail domain shared with another supplier) is cleared with a reason only after you have looked into it. Checking identity does not activate the supplier: activation is still its own approval. **Approved for** on a vendor page shows where a supplier may supply each category; a change asked by someone else waits for you in **Approvals**. Under **Contracts** → **Market prices**, verify or reject the benchmarks others entered (never your own).

## What you cannot do
You cannot decide what you asked for, entered or proposed. You cannot verify a document you added. The buttons are not shown, and the server refuses.

## If you are stuck
If you will be away, open **Settings** (the gear at the top) → **Away? Name a deputy**, pick an approver and the days, and click **Name deputy**. Approvals that come up meanwhile go to them. To change who approves for good, ask the Admin (**Company setup** → **Approvers**).

**Exceptions** lists everything stuck that needs a person, oldest first, with the reason, who must act, the next step and the age in working days; tick **Only mine** for what waits on you. Approvals you were reminded about, goods that came with no order and other decisions show there.

## Approving a forward buy
A forward buy the buyer accepted comes to the person the forward-buy rule names (the purchase manager). Open the request from your task: **Approve the extra quantity** makes a new request for it, which goes through its own approval and budget check; **Decline** needs a reason. The figures are worked out again first; if anything changed you see the new recommendation instead.
