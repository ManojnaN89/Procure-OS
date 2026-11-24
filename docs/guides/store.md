# Store guide

You receive the goods and say what is wrong with them.

## What you see when you log in

**Dashboard** → **My work**: **Orders with goods to come** and **Receipts waiting for inspection**.

## Receive goods

1. Open **Goods receipt**. The orders waiting for goods are listed, the one whose first line is due soonest first. Each line shows its due date: the date the supplier promised, else the date it is needed ("needed"); a line past its date and not all received is marked late.
2. Click **Receive goods** on the order that arrived.
3. For each line enter the quantity received, the quantity rejected and why. Add the delivery challan number and the supplier invoice number. The person who asked for the goods and the buyer are told what arrived, and what was rejected.
4. Click **Post receipt**. Stock goes up by the accepted quantity.

More than the tolerance allows over the order is refused. The tolerance is set per item category by the Admin. A part delivery leaves the order open for the rest; the buyer is reminded if the supplier's date passes. A laptop or other serialised item needs one serial number per unit; a serial received before is refused.

When the supplier has recorded a dispatch, the order says "On the way": what was shipped, when, the vehicle or LR/AWB number and when it should arrive. You also get a notification. It is only what the supplier says: count what actually arrives.

## Inspect

Items marked for inspection wait. They are in the store as **quarantine** (Inventory shows "20 in quarantine"): never set aside or issued, and not yet received on the order. Open the receipt and click **Record inspection** with the result: what passed becomes usable and is set aside at once for the orders that need it; what was rejected stays **blocked** with your reason until it goes back to the supplier.

## Issue stock and count the shelf

Open **Inventory**. On the row of the item click **Issue** to take stock out for use: enter the quantity and who it went to (a department, a machine or a job). You cannot issue more than the books show. After counting the shelf click **Count**: enter what you counted and why it differs. The system works out the difference and corrects the books. Count only usable stock: held lots are counted by their hold. Both are kept in the audit log with your name.

Safety stock is kept back: an issue that would use it is refused with "Only 2 is free; 2 is safety stock". Click **Ask to use safety stock** and say why; your manager (Vikram Joshi at Apex) approves it on My work, then the issue is posted. On **Material requests** a line short only because of safety stock has **Use safety stock** for the same request.

**Hold** on an Inventory row puts usable stock in quarantine or blocked, with a reason (damage, expiry, a suspect lot). If that stock was set aside, the order needed last gives it back and the store check buys again for it. **Holds** lists the held lots: **Release** sends a quantity back to usable (set aside at once) or, from quarantine, to blocked. Every open hold is also a held-stock case on **Exceptions**, with a due date: decide it there with the same buttons. Every hold and decision needs a reason.

## Material requests for production orders

The store check also runs by itself every morning at 05:30 and after an order is closed, cancelled, changed or acknowledged, or a request is rejected or cancelled; the requests it makes wait as drafts for you or the planner to submit (unless the company turns on sending them for approval). It does not buy again for a line whose last request was rejected: **Run store check** does. A supply promised after the need-by (less the receiving buffer) does not count for the order: the line shows "at risk 50" and "50 on PO-..., promised 8 Dec (late)", free stock goes to it first, it is never bought twice, and the buyer gets a case. **Explain** on a card writes why its lines are short, in plain words, when the company has switched explanations on; otherwise it shows the figures.

Each production order (or each department's new joiners, with their kits, or each MRP plan per plant, shown as "MRP plan") imported from the ERP gets a material request: one line per material, with the quantity needed. An MRP line marked "net" was already netted by the ERP: no free stock is set aside for it, it is bought in full.

1. Open **Material requests** and click **Run store check**. Free stock is set aside for the orders needed soonest first; stock already set aside for another order is never taken. What is still short goes for approval as one purchase request per order, priced at the contract price, else the last price paid, else the standard price. Stock that comes in later (a receipt, a count that finds more, a cancelled order) is set aside by itself, soonest first, and you get a notice; a purchase request not yet with the buyer is made smaller by as much.
2. When production starts, click **Issue for production** on the order. Everything set aside is issued. What is short stays on the request: when it arrives it is set aside for the order, then issue it.
3. If the order is called off, click **Cancel** and say why. Its stock goes at once to the next orders that need it. Purchase requests not yet with the buyer are cut (one left with nothing is cancelled); the buyer gets a case for anything already ordered or asked for.

Changes from the ERP: when the ERP sends an order again with a smaller quantity, a later or earlier date, or cancelled, **Load from the ERP** applies it and lists each change with what it did ("Changed: 3"); the card shows "Changed on 23 Nov: 100 -> 60". Less needed: requests not yet with the buyer are cut first, then stock set aside is released to the next orders. More needed or needed earlier: the store check runs again for those items (stock set aside for another order is never taken). If more was already issued than is now needed, you get a case on **Exceptions** ("issued too many"): return the extra to stock or keep it for production, then **Close** it there with what you did. **Inventory** shows how much of each item is set aside. Stock set aside cannot be issued for anything else.

## Goods with no order

Open **Receive without order**: enter the supplier, item, quantity, place and why, then **Record and ask the purchase manager**. Nothing goes into stock yet. Once the buyer has raised the order and it is sent, click **Post the receipt**. If it is rejected, send the goods back and click **Mark returned**. Services are not received here: the person who asked confirms them. **Exceptions** lists everything stuck that needs a person, oldest first, with the reason, who must act, the next step and the age in working days; tick **Only mine** for what waits on you.

## What you cannot do, and if you are stuck

You cannot enter the invoice or approve the order for the same goods. Someone else does those steps. If something is wrong with a receipt you have already posted, tell the buyer and the AP clerk before the invoice is matched.
