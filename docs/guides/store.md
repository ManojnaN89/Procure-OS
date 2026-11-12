# Store guide

You receive the goods and say what is wrong with them.

## What you see when you log in

**Dashboard** → **My work**: **Orders with goods to come** and **Receipts waiting for inspection**.

## Receive goods

1. Open **Goods receipt**. The orders waiting for goods are listed.
2. Click **Receive goods** on the order that arrived.
3. For each line enter the quantity received, the quantity rejected and why. Add the delivery challan number and the supplier invoice number. The person who asked for the goods and the buyer are told what arrived, and what was rejected.
4. Click **Post receipt**. Stock goes up by the accepted quantity.

More than the tolerance allows over the order is refused. The tolerance is set per item category by the Admin. A part delivery leaves the order open for the rest; the buyer is reminded if the supplier's date passes. A laptop or other serialised item needs one serial number per unit; a serial received before is refused.

When the supplier has recorded a dispatch, the order says "On the way": what was shipped, when, the vehicle or LR/AWB number and when it should arrive. You also get a notification. It is only what the supplier says: count what actually arrives.

## Inspect

Items marked for inspection wait. Open the receipt and click **Record inspection** with the result.

## Issue stock and count the shelf

Open **Inventory**. On the row of the item click **Issue** to take stock out for use: enter the quantity and who it went to (a department, a machine or a job). You cannot issue more than the books show.

After counting the shelf click **Count**: enter what you counted and why it differs. The system works out the difference and corrects the books. Both are kept in the audit log with your name.

## Material requests for production orders

Each production order (or each department's new joiners, with their kits) imported from the ERP gets a material request: one line per material, with the quantity needed.

1. Open **Material requests** and click **Run store check**. Free stock is set aside for the orders needed soonest first; stock already set aside for another order is never taken. What is still short goes for approval as one purchase request per order, priced at the contract price, else the last price paid, else the standard price. Stock that comes in later (a receipt, a count that finds more, a cancelled order) is set aside by itself, soonest first, and you get a notice; a purchase request not yet with the buyer is made smaller by as much.
2. When production starts, click **Issue for production** on the order. Everything set aside is issued. What is short stays on the request: when it arrives it is set aside for the order, then issue it.
3. If the order is called off, click **Cancel** and say why. Its stock goes at once to the next orders that need it. Tell the buyer about any purchase request made only for it.

**Inventory** shows how much of each item is set aside. Stock set aside cannot be issued for anything else.

## Goods with no order

Open **Receive without order**: enter the supplier, item, quantity, place and why, then **Record and ask the purchase manager**. Nothing goes into stock yet. Once the buyer has raised the order and it is sent, click **Post the receipt**. If it is rejected, send the goods back and click **Mark returned**. Services are not received here: the person who asked confirms them. **Exceptions** lists everything stuck that needs a person, oldest first, with the reason, who must act, the next step and the age in working days; tick **Only mine** for what waits on you.

## What you cannot do

You cannot enter the invoice or approve the order for the same goods. Someone else does those steps.

## If you are stuck

If something is wrong with a receipt you have already posted, tell the buyer and the AP clerk before the invoice is matched.
